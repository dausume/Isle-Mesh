#!/usr/bin/env python3
"""What a device-backup of this machine holds, and how a Polari instance is held still while it is copied.

Items the isle CLI can name by itself:
  - the isle's settings folder and state folder, without the parts listed in FOLDERS;
  - the data volume of every Polari instance the isle CLI deployed on this machine: the isle's own instance
    (the container isle-polari-deploy.sh starts) and every instance record with the role `backend`.
Every other named Docker volume is listed as not covered, never dropped silently.

A running instance is copied only behind the engine's write gate (POST /api/quiesce): gate up, wait until the
engine reports its data flushed, copy, gate down.  If the gate cannot be raised the item fails.
"""
import json
import os
from pathlib import Path
import re

import storage_migrate

FOLDER = 'device-backups'
HOLD_PREFIX = 'device-backup-'
ISLE_BACKEND = 'prf-isle-backend'       # the isle's own instance (isle-polari-deploy.sh)
ISLE_API = 'https://api.polari.isle'
ISLE_API_LOCAL = ('api.polari.isle', '127.0.0.1')      # the same name asked on this machine, as storage_core does
DATA = '/data'
ANONYMOUS = re.compile(r'[0-9a-f]{64}\Z')
#: (item name, the Deps attribute naming the folder, ((path below it that is left out, why), ...))
FOLDERS = (
    ('isle-settings', 'settings', (('apt/repo', 'the package store: public packages, a placed dataset with its own copies'),)),
    ('isle-state', 'state', (('artifacts', 'release files: a release brings them'),
                             ('operations', 'operation journals: ruled out of device-backups'),
                             (FOLDER, 'the device-backups kept on this machine'))),
)


class ItemFailed(Exception):
    """One item could not be taken; the device-backup is incomplete."""


def measure(folder, left_out=()):
    """(bytes of the regular files below folder, entries that could not be read).  Links are not followed."""
    skip = {os.path.join(str(folder), rel) for rel in left_out}
    total, unread, stack = 0, 0, [str(folder)]
    while stack:
        here = stack.pop()
        try:
            entries = list(os.scandir(here))
        except OSError:
            unread += 1
            continue
        for entry in entries:
            if entry.path in skip:
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                unread += 1
    return total, unread


def _inspect(deps, kind, name):
    code, out = deps.docker([kind, 'inspect', name])
    try:
        rows = json.loads(out) if code == 0 else []
    except ValueError:
        rows = []
    return rows[0] if rows and isinstance(rows[0], dict) else None


def _volume_row(name, instance, volume, running, container, api, resolve=None):
    return dict(name='polari-' + instance, kind='polari-volume', instance=instance, volume=name, source=volume,
                running=running, container=container, api=api, resolve=resolve, exclude=[], leftOut=[])


def _instance(deps, instance, container, api, resolve, fallback_volume):
    """One instance: (item or None, why it is not covered or None)."""
    found = _inspect(deps, 'container', container)
    if found is None:
        volume = _inspect(deps, 'volume', fallback_volume) if fallback_volume else None
        if volume is None:
            return None, 'no container %s and no data volume on this machine' % container
        name, driver, path, running = fallback_volume, volume.get('Driver'), volume.get('Mountpoint'), False
    else:
        mounts = [m for m in found.get('Mounts') or [] if isinstance(m, dict) and m.get('Destination') == DATA and m.get('Type') == 'volume']
        if len(mounts) != 1:
            return None, 'container %s has no named volume at %s' % (container, DATA)
        name, driver, path = mounts[0].get('Name'), mounts[0].get('Driver'), mounts[0].get('Source')
        running = bool((found.get('State') or {}).get('Running'))
    if driver != 'local' or not isinstance(path, str) or not os.path.isabs(path) or not isinstance(name, str):
        return None, 'the data volume of %s is not a local Docker volume' % container
    return _volume_row(name, instance, path, running, container, api, resolve), None


def instances(deps):
    """(items, not covered) for the Polari instances the isle CLI deployed on this machine."""
    rows, missing = [], []
    if _inspect(deps, 'container', ISLE_BACKEND) is not None:
        item, why = _instance(deps, 'isle', ISLE_BACKEND, ISLE_API, ISLE_API_LOCAL, None)
        rows.append(item) if item else missing.append(dict(name='instance isle', why=why))
    try:
        records = sorted(Path(deps.polari_dir).glob('*/deployment.json'))
    except OSError:
        records = []
    for path in records:
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            missing.append(dict(name='instance ' + path.parent.name, why='its deployment record cannot be read'))
            continue
        name = path.parent.name
        if not isinstance(record, dict) or record.get('role') != 'backend':
            continue        # a frontend-only instance holds no data
        item, why = _instance(deps, name, 'prf-%s-backend' % name, str(record.get('backend_url') or '').rstrip('/'), None,
                              'prf-%s_backend-data' % name)
        rows.append(item) if item else missing.append(dict(name='instance ' + name, why=why))
    return rows, missing


def listing(deps):
    """Everything a device-backup of this machine holds: (items, not covered)."""
    items = []
    for name, attribute, left_out in FOLDERS:
        folder = Path(getattr(deps, attribute))
        if folder.is_dir():
            # every listed path is left out of the copy, also one that appears after this listing (the device-backups
            # folder is made after it); only those present now are shown
            items.append(dict(name=name, kind='folder', source=str(folder), exclude=[rel for rel, _ in left_out],
                              leftOut=[dict(path=rel, why=why) for rel, why in left_out if (folder / rel).exists()]))
    polari, missing = instances(deps)
    items += polari
    code, out = deps.docker(['volume', 'ls', '--format', '{{.Name}}'])
    if code != 0:
        missing.append(dict(name='Docker volumes', why='Docker did not answer, so its volumes were not listed'))
    else:
        covered = {item['volume'] for item in polari}
        for volume in sorted(set(out.split()) - covered):
            if not ANONYMOUS.match(volume):
                missing.append(dict(name='volume ' + volume, why='a named Docker volume that no isle-deployed Polari instance '
                                                                 'on this machine uses; nothing declares it to the isle CLI'))
    for item in items:
        item['sizeBytes'], item['unread'] = measure(item['source'], item['exclude'])
    return items, missing


# ---------------------------------------------------------------- the engine's write gate

class Gate:
    """The write gate of one running instance, over verified HTTPS."""

    def __init__(self, deps, item):
        self.deps, self.item, self.http = deps, item, deps.http()
        self.routes = [None] + ([tuple(item['resolve'])] if item.get('resolve') else [])

    def _each(self, call):
        error = None
        for route in self.routes:
            try:
                return call(route)
            except (storage_migrate.HttpError, ValueError, OSError) as exc:
                error = exc
        raise ItemFailed('instance %s did not answer at its API (%s)' % (self.item['instance'], error))

    def status(self):
        doc = self._each(lambda route: self.http.get_json(self.item['api'] + '/api/quiesce/status', resolve=route))
        if not isinstance(doc, dict) or not isinstance(doc.get('quiesced'), bool):
            raise ItemFailed('instance %s gave no write gate status' % self.item['instance'])
        return doc

    def _post(self, path, payload):
        return self._each(lambda route: self.http.post_json(self.item['api'] + path, payload, resolve=route))[0]

    def lower(self):
        code = self._post('/api/quiesce/release', {})
        if code != 200:
            raise ItemFailed('the write gate of instance %s was not lowered (HTTP %s)' % (self.item['instance'], code))

    def lower_if(self, move):
        """Lower the gate when it is up under this name; an interrupted wait must not leave it up."""
        status = self.status()
        if status['quiesced'] and status.get('moveName') == move:
            self.lower()

    def lower_stale(self):
        """Lower a gate that an interrupted device-backup left up.  Returns the name it was held under, or None."""
        status = self.status()
        held = str(status.get('moveName') or '')
        if status['quiesced'] and held.startswith(HOLD_PREFIX):
            self.lower()
            return held
        return None

    def raise_and_wait(self, move):
        """Gate up under the name `move`, then wait until the engine reports its data flushed."""
        status = self.status()
        held = str(status.get('moveName') or '')
        if status['quiesced'] and held != move:
            if not held.startswith(HOLD_PREFIX):
                raise ItemFailed('the writes of instance %s are already held for something else (%s); it was not touched'
                                 % (self.item['instance'], held or status.get('reason') or 'no name given'))
            self.lower()        # left up by an interrupted device-backup; the device-backup lock is ours, so none is running
        code = self._post('/api/quiesce', dict(reason='device-backup', moveName=move))
        if code != 200:
            raise ItemFailed('the write gate of instance %s was not raised (HTTP %s)' % (self.item['instance'], code))
        deadline = self.deps.time() + self.deps.hold_limit
        while True:
            status = self.status()
            receipt = status.get('receipt') if isinstance(status.get('receipt'), dict) else {}
            if not status['quiesced'] or status.get('moveName') != move:
                raise ItemFailed('the write gate of instance %s went down while its data was being flushed' % self.item['instance'])
            error = str(receipt.get('error') or '')
            if receipt.get('persisted') is True or 'stateless' in error:
                return dict(name=move, persisted=receipt.get('persisted') is True, flushSeconds=receipt.get('flushSeconds'))
            if error:
                raise ItemFailed('instance %s could not flush its data: %s' % (self.item['instance'], error[:200]))
            if self.deps.time() >= deadline:
                raise ItemFailed('instance %s had not flushed its data after %d minutes' % (self.item['instance'], self.deps.hold_limit // 60))
            self.deps.sleep(self.deps.poll_interval)
