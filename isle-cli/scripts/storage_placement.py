#!/usr/bin/env python3
"""Core-side placement record and snapshot-manifest publication (contract D1, section 6).

CLI verbs (contract 2.1): authorize, mark-migrated, accept, publish-manifest, after-publication, gc, status;
authorize-placement / accept-placement remain as aliases that print a deprecation note to stderr.
The record is the ownership authority: which device hosts a dataset after a storage move.
Entry: {dataset, domain, device, sourcePath, state, generation, plan{id,revision,digest},
history[{from,to,at,by,operation}], addresses{source,target}}.

States: authorized -> migrated -> cutover -> hosting, plus recovery-needed. Every change
goes through transition(), a compare-and-set on (state, generation) under one flock on
<record>.lock; the record is re-read under the lock, so a stale caller is refused.
Allowed transitions (anything else is a ValueError):
  authorized -> migrated        mark-migrated (target receipt, verified by the core CLI)
  migrated -> cutover           cutover; the ONLY transition that bumps the generation
  cutover -> migrated           rollback / failed-cutover revert
  cutover -> hosting            accept (needs the witness; checked by the caller)
  cutover|migrated -> recovery-needed   failure
  recovery-needed -> migrated|cutover   operator repair
Publishers keep writing their local repo, then call after_publication(); when an entry
exists, in ANY state, a manifest is published under repo/snapshot/ for the hosting device
to sync. publish-manifest also works with no record at all (prepare a source snapshot).
cutover/rollback verbs belong to the executor slice; they call transition().

Locking: publish_manifest() takes the publication lock unless called with
locked=True. after_publication() and the `after-publication` CLI pass locked=True:
their callers already hold it (flock on a new fd of the same file would deadlock).
The placement flock is a different file and never nests with it.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

import repository_lock
import storage_snapshot

DATASET_APT = 'apt-public-repo'
PLACEMENT = Path('/etc/isle-mesh/storage/placement.json')
DEVICE_NAME = Path('/etc/isle-mesh/device-name')
_geteuid = os.geteuid

DATASET_RE = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')
DEVICE_RE = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?')
DOMAIN_RE = re.compile(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+isle')
PLAN_ID_RE = re.compile(r'[a-f0-9]{32}')
ITEM_RE = re.compile(r'[a-zA-Z0-9_.-]{1,80}')
SHA_RE = re.compile(r'[a-f0-9]{64}')


def _stamp(now=None):
    return (now or datetime.now(timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ')


def _atomic_bytes(path, raw, mode=0o644):
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            os.fchmod(out.fileno(), mode); out.write(raw); out.flush(); os.fsync(out.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def read(path=PLACEMENT):
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return {'datasets': {}}
    try:
        record = json.loads(raw)
    except ValueError:
        raise ValueError('placement record is not valid JSON')
    if not isinstance(record, dict) or not isinstance(record.get('datasets'), dict):
        raise ValueError('placement record has no datasets object')
    return record


def hosting_for(dataset, path=PLACEMENT):
    entry = read(path)['datasets'].get(dataset)
    return entry if isinstance(entry, dict) else None


def _check(pattern, value, label):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError('invalid ' + label)


def _check_plan(plan):
    if not isinstance(plan, dict):
        raise ValueError('invalid plan')
    _check(PLAN_ID_RE, plan.get('id'), 'plan id')
    _check(SHA_RE, plan.get('digest'), 'plan digest')
    if type(plan.get('revision')) is not int or plan['revision'] < 1:
        raise ValueError('invalid plan revision')


def _validated(dataset, device, domain, source_path, plan):
    _check(DATASET_RE, dataset, 'dataset')
    _check(DEVICE_RE, device, 'device')
    _check(DOMAIN_RE, domain, 'domain')
    if not isinstance(source_path, str) or not os.path.isabs(source_path) or '..' in Path(source_path).parts:
        raise ValueError('invalid source path')
    _check_plan(plan)


STATES = ('authorized', 'migrated', 'cutover', 'hosting', 'recovery-needed')
TRANSITIONS = frozenset([
    ('authorized', 'migrated'), ('migrated', 'cutover'), ('cutover', 'migrated'), ('cutover', 'hosting'),
    ('cutover', 'recovery-needed'), ('migrated', 'recovery-needed'),
    ('recovery-needed', 'migrated'), ('recovery-needed', 'cutover')])
BUMPING = ('migrated', 'cutover')
PENDING_STATES = ('migrated', 'cutover', 'hosting')  # section 5: pending is only ever written here
CLAIM_STATES = ('authorized', 'migrated', 'cutover', 'hosting')  # the CA issues in these
CLAIMS_NAME = 'placement-claims.json'
FIELD_KEYS = frozenset(['acceptedManifest', 'acceptedWitness', 'rollbackManifest', 'recovery'])
_KEEP = object()


def _actor():
    return os.environ.get('SUDO_USER') or 'root'


@contextmanager
def _locked(path):
    """Hold an exclusive flock on <path>.lock (created 0644, never through a symlink)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path) + '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644)
    try:
        os.fchmod(fd, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # closing releases the lock


def _write(path, record):
    _atomic_bytes(path, (json.dumps(record, sort_keys=True, indent=2) + '\n').encode())


def _history(old, new, now, by, operation):
    return dict({'from': old, 'to': new}, at=_stamp(now), by=by, operation=operation)


def authorize(dataset, device, domain, source_path, plan, path=PLACEMENT, now=None, replace=False,
              by=None, operation='authorize', addresses=None, source_device=None):
    """Create the entry in state authorized, generation 1. Refuses an existing entry unless
    replace; a replacement keeps the history and continues the generation counter.
    source_device is the device the snapshot comes from (the plan item's originDevice); recorded as sourceDevice."""
    _validated(dataset, device, domain, source_path, plan)
    if source_device is not None:
        _check(DEVICE_RE, source_device, 'source device')
    path = Path(path)
    with _locked(path):
        record = read(path)
        old = record['datasets'].get(dataset)
        if old is not None and not replace:
            raise ValueError('placement already recorded for ' + dataset)
        old = old if isinstance(old, dict) else {}
        history = list(old.get('history') or [])
        generation = old['generation'] + 1 if type(old.get('generation')) is int else 1
        history.append(_history(old.get('state'), 'authorized', now, by or _actor(), operation))
        record['datasets'][dataset] = dict(
            dataset=dataset, domain=domain, device=device, sourcePath=source_path, state='authorized',
            generation=generation, plan=dict(id=plan['id'], revision=plan['revision'], digest=plan['digest']),
            history=history, addresses=dict(source=None, target=None), sourceDevice=source_device)
        if addresses is not None:
            _check_addresses(addresses)
            record['datasets'][dataset]['addresses'].update(addresses)
        _write(path, record)
        mirror_claims(path, record)
        return record['datasets'][dataset]


def _check_addresses(addresses):
    if not isinstance(addresses, dict) or not set(addresses) <= {'source', 'target'}:
        raise ValueError('invalid addresses')
    for value in addresses.values():
        if value is not None and (not isinstance(value, str) or not re.fullmatch(r'[^\s\x00]{1,100}', value)):
            raise ValueError('invalid address')


def claims_path(path=PLACEMENT):
    return Path(path).parent / CLAIMS_NAME


def mirror_claims(path=PLACEMENT, record=None):
    """Write <dir>/placement-claims.json {domain: {device, dataset, state}} from the record (section 5, CA).
    The certificate authority reads only this file. Never raises: the placement write is already
    durable and every storage verb re-mirrors, so a failure is reported and retried, not fatal."""
    try:
        record = record if record is not None else read(path)
        claims = {}
        for name, entry in sorted(record['datasets'].items()):
            if isinstance(entry, dict) and isinstance(entry.get('domain'), str) and isinstance(entry.get('device'), str):
                claims[entry['domain']] = dict(device=entry['device'], dataset=name, state=entry.get('state'))
        target = claims_path(path)
        raw = (json.dumps(claims, sort_keys=True, indent=2) + '\n').encode()
        try:
            if target.read_bytes() == raw:
                return claims
        except OSError:
            pass
        _atomic_bytes(target, raw)
        return claims
    except (OSError, ValueError) as exc:
        print('[storage] placement-claims.json not written: %s' % exc, file=sys.stderr)
        return None


def _check_pending(pending):
    if not isinstance(pending, dict) or not isinstance(pending.get('action'), str) or not pending['action'] \
            or not isinstance(pending.get('operation'), str) or not pending['operation']:
        raise ValueError('invalid pending intent')


def transition(dataset, from_state, to_state, *, by, operation, path=PLACEMENT, now=None,
               expect_generation=None, bump_generation=False, addresses=None,
               pending=_KEEP, bump=False, recover=False, plan=None, fields=None, history_extra=None):
    """Compare-and-set the entry from from_state to to_state under the placement flock.

    Legacy callers (no new keyword) behave exactly as before: only migrated -> cutover bumps, via bump_generation.
    The intent protocol (section 5) adds:
      bump=True            this write bumps the generation by one (setting or clearing pending requires it)
      pending=dict|None    set (epoch is stamped with the post-write generation) or clear the durable intent;
                           only ever written in migrated|cutover|hosting; entering recovery-needed clears it
      recover=True         the caller is `recover`: may supersede a pending of another operation and use the
                           recover edges (any state with pending, or recovery-needed -> migrated|cutover|hosting)
      plan=dict            authorized -> authorized with a new plan revision
      fields               acceptedManifest/acceptedWitness/rollbackManifest/recovery
      history_extra        extra keys for the history row
    A CAS that changes state (or clears) while a pending of another operation exists is refused unless recover."""
    _check(DATASET_RE, dataset, 'dataset')
    legacy_edge = (from_state, to_state) in TRANSITIONS
    if not legacy_edge and not (from_state in STATES and to_state in STATES):
        raise ValueError('transition %s -> %s is not allowed' % (from_state, to_state))
    if bump_generation and bump:
        raise ValueError('use bump or bump_generation, not both')
    if (from_state, to_state) == BUMPING and not (bump_generation or bump):
        raise ValueError('migrated -> cutover must bump the generation')
    if bump_generation and (from_state, to_state) != BUMPING:
        raise ValueError('only migrated -> cutover bumps the generation')
    for label, value in (('by', by), ('operation', operation)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError('transition needs a non-empty ' + label)
    if expect_generation is not None and (type(expect_generation) is not int or expect_generation < 1):
        raise ValueError('invalid expected generation')
    if addresses is not None:
        _check_addresses(addresses)
    if pending is not _KEEP and pending is not None:
        _check_pending(pending)
    if fields is not None and (not isinstance(fields, dict) or not set(fields) <= FIELD_KEYS):
        raise ValueError('invalid entry fields')
    if plan is not None:
        _check_plan(plan)
    path = Path(path)
    with _locked(path):
        record = read(path)
        entry = record['datasets'].get(dataset)
        if not isinstance(entry, dict):
            raise ValueError('no placement for ' + dataset)
        if entry.get('state') != from_state:
            raise ValueError('placement for %s is in state %s, not %s' % (dataset, entry.get('state'), from_state))
        generation = entry.get('generation')
        if type(generation) is not int or generation < 1:
            raise ValueError('placement for %s has no valid generation' % dataset)
        if expect_generation is not None and generation != expect_generation:
            raise ValueError('placement for %s is at generation %d, not %d' % (dataset, generation, expect_generation))
        current = entry.get('pending') if isinstance(entry.get('pending'), dict) else None
        if entry.get('pending') and current is None:
            raise ValueError('placement for %s has a malformed pending intent' % dataset)
        # the (state, pending) table of section 5
        edge_ok = legacy_edge
        if from_state == to_state and from_state in PENDING_STATES and (current is not None or isinstance(pending, dict)):
            edge_ok = True  # write or clear an intent in place
        if from_state == to_state == 'authorized' and plan is not None:
            edge_ok = True
        if recover and to_state in PENDING_STATES and (from_state == 'recovery-needed' and to_state != 'hosting'
                                                       or from_state in PENDING_STATES and current is not None):
            edge_ok = True
        if not edge_ok:
            raise ValueError('transition %s -> %s is not allowed' % (from_state, to_state))
        if isinstance(pending, dict):
            if to_state not in PENDING_STATES:
                raise ValueError('pending is only written in migrated, cutover or hosting')
        if current is not None and not recover and current.get('operation') != operation:
            raise ValueError('a pending %s of operation %s exists; only recover may supersede it'
                             % (current.get('action'), current.get('operation')))
        if current is not None and from_state != to_state and to_state != 'recovery-needed' and pending is _KEEP:
            raise ValueError('a pending intent must be cleared or replaced when the state changes')
        if to_state == 'authorized' and (pending is not _KEEP and pending is not None):
            raise ValueError('authorized never carries a pending')
        new_pending = current if pending is _KEEP else pending
        if to_state not in PENDING_STATES:
            new_pending = None  # entering recovery-needed (or authorized) clears it
        writes_pending = isinstance(pending, dict) or (pending is None and current is not None)
        if writes_pending and not (bump or bump_generation):
            raise ValueError('every write of pending bumps the generation')
        entry['state'] = to_state
        if bump or bump_generation:
            entry['generation'] = generation + 1
        if isinstance(new_pending, dict) and isinstance(pending, dict):
            new_pending = dict(new_pending, epoch=entry['generation'])  # the post-write value
        if new_pending is not None or 'pending' in entry:
            entry['pending'] = new_pending
        if plan is not None:
            entry['plan'] = dict(id=plan['id'], revision=plan['revision'], digest=plan['digest'])
        if addresses:
            entry['addresses'] = dict(entry.get('addresses') or {'source': None, 'target': None}, **addresses)
        if from_state == 'recovery-needed' and to_state != 'recovery-needed':
            entry.pop('recovery', None)
        for key, value in (fields or {}).items():
            if value is None:
                entry.pop(key, None)
            else:
                entry[key] = value
        row = _history(from_state, to_state, now, by, operation)
        if isinstance(pending, dict):
            row['pending'] = pending['action']
        row.update(history_extra or {})
        entry['history'] = list(entry.get('history') or []) + [row]
        _write(path, record)
        mirror_claims(path, record)
        return entry


def write_pending(dataset, pending, *, expect_generation, by=None, operation=None, path=PLACEMENT, now=None,
                  recover=False, history_extra=None):
    """Durable intent (section 5): CAS expecting expect_generation; state unchanged, generation + 1,
    pending.epoch = the post-write value. Refused in authorized and recovery-needed, and when a pending exists
    (unless recover)."""
    _check_pending(pending)
    entry = hosting_for(dataset, path)
    if entry is None:
        raise ValueError('no placement for ' + dataset)
    return transition(dataset, entry.get('state'), entry.get('state'), by=by or _actor(),
                      operation=operation or pending['operation'], path=path, now=now,
                      expect_generation=expect_generation, pending=pending, bump=True, recover=recover,
                      history_extra=history_extra)


def clear_pending(dataset, to_state, *, expect_generation, bump=True, by=None, operation=None, path=PLACEMENT,
                  now=None, recover=False, addresses=None, fields=None, history_extra=None):
    """Commit or revert: CAS expecting expect_generation (the pending.epoch); state becomes to_state, pending is
    cleared, generation + 1 (E = G+2 after the intent at G+1). to_state may equal the current state."""
    if bump is not True:
        raise ValueError('every write bumps the generation')
    entry = hosting_for(dataset, path)
    if entry is None:
        raise ValueError('no placement for ' + dataset)
    current = entry.get('pending') if isinstance(entry.get('pending'), dict) else {}
    return transition(dataset, entry.get('state'), to_state, by=by or _actor(),
                      operation=operation or current.get('operation') or 'clear-pending', path=path, now=now,
                      expect_generation=expect_generation, pending=None, bump=True, recover=recover,
                      addresses=addresses, fields=fields, history_extra=history_extra)


def domain_entry(domain, path=PLACEMENT):
    """The entry whose domain is `domain`, else None (read-only, lock-free: writers rename atomically)."""
    for entry in read(path)['datasets'].values():
        if isinstance(entry, dict) and entry.get('domain') == domain:
            return entry
    return None


PENDING_MATCH = ('action', 'digest', 'from', 'to', 'epoch', 'address', 'operation')


def fence_check(domain, address, intent_path, operation, path=PLACEMENT):
    """(ok, words) for dns-register.sh. An unmanaged name (no entry, or no placement file) passes untouched.
    A managed name passes only with an intent file naming this domain, this address and this operation whose
    pending equals the entry's stored pending (the router write re-reads it under placement.lock)."""
    try:
        entry = domain_entry(domain, path)
    except ValueError as exc:
        return False, 'managed name: refused (placement record unreadable: %s)' % exc
    if entry is None:
        return True, 'unmanaged'
    refuse = 'managed name: refused outside a storage intent'
    if not intent_path or not operation:
        return False, refuse
    try:
        intent = json.loads(Path(intent_path).read_bytes())
    except (OSError, ValueError):
        return False, refuse + ' (intent unreadable)'
    stored = entry.get('pending')
    if not isinstance(intent, dict) or not isinstance(stored, dict) or not isinstance(intent.get('pending'), dict):
        return False, refuse + ' (no pending intent)'
    if intent.get('domain') != domain or intent.get('address') != address or intent.get('operation') != operation:
        return False, refuse + ' (intent does not match this registration)'
    if stored.get('operation') != operation or stored.get('address') != address:
        return False, refuse + ' (stored pending belongs to another operation or address)'
    if any(intent['pending'].get(k) != stored.get(k) for k in PENDING_MATCH):
        return False, refuse + ' (stored pending differs from the intent)'
    return True, 'intent matches pending %s of operation %s' % (stored.get('action'), operation)


def device_name(path=DEVICE_NAME):
    name = Path(path).read_text().strip()
    _check(DEVICE_RE, name, 'device name')
    return name


BACKEND_CONTAINER = 'prf-isle-backend'
VIEW_FILE = 'isle-storage-placement-view.json'
# Same container-side pattern as catalog_publish.py (isle-catalog-releases.json): the file lands beside
# $DATABASE_PATH inside the backend container, written to a temp name and renamed so a reader never sees half.
_BACKEND_WRITE = '''import os,sys,tempfile
from pathlib import Path
p=Path(os.environ['DATABASE_PATH']).parent/sys.argv[1]
f=tempfile.NamedTemporaryFile(mode='w',dir=p.parent,delete=False,suffix='.tmp');f.write(sys.stdin.read());f.flush();os.fsync(f.fileno());f.close();os.chmod(f.name,0o644);os.replace(f.name,p)
'''


def post_view(document, run=subprocess.run, container=BACKEND_CONTAINER, name=VIEW_FILE):
    """Write the view document into the backend container's data dir (no network credential involved)."""
    done = run(['docker', 'exec', '-i', container, 'python3', '-c', _BACKEND_WRITE, name],
               input=json.dumps(document, sort_keys=True), text=True, capture_output=True, timeout=60)
    if done.returncode != 0:
        raise ValueError('placement view not written: ' + ((done.stderr or done.stdout or '').strip()[-200:] or 'docker exec failed'))


def view_document(path=PLACEMENT, device_path=DEVICE_NAME, now=None):
    """The file the backend serves (GET /storage/placement reads it; it refuses one older than 10 minutes, so the
    120 s push rewrites it even when nothing changed): {schemaVersion, writtenAt, writtenBy, datasets:{dataset: entry}}."""
    datasets = {}
    for name, e in sorted(read(path)['datasets'].items()):
        if not isinstance(e, dict):
            continue
        plan = e.get('plan') if isinstance(e.get('plan'), dict) else {}
        addr = e.get('addresses') if isinstance(e.get('addresses'), dict) else {}
        datasets[name] = dict(
            dataset=e.get('dataset', name), domain=e.get('domain'), device=e.get('device'),
            sourceDevice=e.get('sourceDevice'), state=e.get('state'), generation=e.get('generation'),
            plan=dict(id=plan.get('id'), revision=plan.get('revision'), digest=plan.get('digest')),
            pending=e.get('pending') if isinstance(e.get('pending'), dict) else None,
            addresses=dict(source=addr.get('source'), target=addr.get('target')))
    return dict(schemaVersion=1, writtenAt=_stamp(now), writtenBy=device_name(device_path), datasets=datasets)


DEB_RE = re.compile(r'[a-z0-9][a-z0-9+.-]*_[^_/\s]+_[a-z0-9-]+\.deb')


def immutable_path(relpath):
    """True when a repo-relative path is a candidate for hardlinking by name alone: under a by-hash
    directory, or a versioned Debian package. (A name carrying its own sha256 is checked in
    eligible_for_hardlink, which has the digest.) Everything else is copied."""
    parts = Path(relpath).parts
    if not parts:
        return False
    return 'by-hash' in parts[:-1] or bool(DEB_RE.fullmatch(parts[-1]))


VERIFY_LIMIT = 64 * 1024 * 1024


def eligible_for_hardlink(repo_root, relpath, sha256):
    """(ok, reason). Eligible only when the file is a regular file AND (its name contains its own sha256,
    OR it is under a by-hash directory, OR it is a versioned <name>_<version>_<arch>.deb). Everything else
    is copied. repo_root is a path or an open directory fd."""
    parts = Path(relpath).parts
    if not parts:
        return False, 'empty path'
    try:
        if isinstance(repo_root, int):
            info = os.lstat(relpath, dir_fd=repo_root)
        else:
            info = os.lstat(Path(repo_root) / relpath)
    except OSError as exc:
        return False, 'cannot stat: %s' % exc.strerror
    if not stat.S_ISREG(info.st_mode):
        return False, 'not a regular file'
    if isinstance(sha256, str) and SHA_RE.fullmatch(sha256) and sha256 in parts[-1]:
        return True, 'content-addressed name'
    if 'by-hash' in parts[:-1]:
        return True, 'under by-hash'
    if DEB_RE.fullmatch(parts[-1]):
        return True, 'versioned deb package'
    return False, 'mutable path: copied'


def _fsync_dir(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def _file_sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _integrity_error(sha):
    return ValueError('snapshot object %s does not match its name; repository integrity check failed' % sha)


def _verify_object(repo, objects, entry):
    """True when objects/<sha> exists and is intact; False when absent; ValueError when it differs."""
    sha, size = entry['sha256'], entry['size']
    target = objects / sha
    try:
        info = os.lstat(target)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_size != size:
        raise _integrity_error(sha)
    if size <= VERIFY_LIMIT:
        if _file_sha(target) != sha:
            raise _integrity_error(sha)
        return True
    try:
        source = os.lstat(repo / entry['path'])
    except OSError:
        return True
    if source.st_nlink > 1 and (source.st_dev, source.st_ino) != (info.st_dev, info.st_ino):
        raise _integrity_error(sha)
    return True


def _store_object(repo, objects, entry):
    """Create objects/<sha> once: hardlink eligible files, copy the rest. Never overwrite."""
    sha, size = entry['sha256'], entry['size']
    target = objects / sha
    if _verify_object(repo, objects, entry):
        return
    source = repo / entry['path']
    if eligible_for_hardlink(repo, entry['path'], sha)[0]:
        os.link(source, target, follow_symlinks=False)
        if os.lstat(target).st_size != size:
            os.unlink(target)
            raise ValueError('file changed while linking snapshot object: ' + entry['path'])
        return
    tmp = objects / ('.tmp-%d-%s' % (os.getpid(), sha))
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, 'wb') as out, open(source, 'rb') as src:
            for block in iter(lambda: src.read(1024 * 1024), b''):
                out.write(block)
            out.flush(); os.fsync(out.fileno())
        if _file_sha(tmp) != sha:
            raise ValueError('file changed while copying snapshot object: ' + entry['path'])
        os.rename(tmp, target)
    finally:
        if os.path.lexists(tmp): os.unlink(tmp)


def _write_manifest(repo, dataset, device, plan_id, item_id, now):
    repo = Path(repo)
    binding = dict(dataset=dataset, sourceDevice=device or device_name(), takenAt=_stamp(now),
                   lockPath=str(repository_lock.lock_path(repo)), planId=plan_id, itemId=item_id)
    content = storage_snapshot.inventory(repo)
    content_sha = hashlib.sha256(storage_snapshot.encoded(content)).hexdigest()
    raw = storage_snapshot.encoded(dict(content, binding=binding))
    sha = hashlib.sha256(raw).hexdigest()
    directory = repo / 'snapshot'
    objects = directory / 'objects'
    manifests = directory / 'manifests'
    for d in (directory, objects, manifests):
        d.mkdir(mode=0o755, exist_ok=True)
    files = [e for e in content['entries'] if e['kind'] == 'file']
    for entry in files:  # refuse the whole publication before writing anything
        _verify_object(repo, objects, entry)
    for entry in files:
        _store_object(repo, objects, entry)
    _fsync_dir(objects)
    target = manifests / (sha + '.json')
    if target.exists() and target.read_bytes() != raw:
        raise ValueError('existing snapshot manifest differs: ' + target.name)
    if not target.exists():
        _atomic_bytes(target, raw)
    latest = dict(manifestSha=sha, contentSha=content_sha, at=binding['takenAt'], dataset=dataset,
                  totalBytes=content['totalBytes'], entries=len(content['entries']))
    _atomic_bytes(directory / 'latest.json', (json.dumps(latest, sort_keys=True) + '\n').encode())
    return latest


def publish_manifest(repo, dataset=DATASET_APT, device=None, plan_id=None, item_id=None, now=None, locked=False):
    repo = Path(repo)
    if locked:
        return _write_manifest(repo, dataset, device, plan_id, item_id, now)
    with repository_lock.publication_lock(repo):
        return _write_manifest(repo, dataset, device, plan_id, item_id, now)


def _load(path):
    try:
        document = json.loads(path.read_bytes())
        for e in document['entries']:
            e.get('kind')
        return document
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ValueError('unreadable snapshot manifest: ' + path.name)


def _referenced(path):
    try:
        return {e['sha256'] for e in _load(path)['entries'] if e.get('kind') == 'file'}
    except (KeyError, TypeError, AttributeError):
        raise ValueError('unreadable snapshot manifest: ' + path.name)


def _taken_at(document):
    binding = document.get('binding')
    value = binding.get('takenAt') if isinstance(binding, dict) else None
    return value if isinstance(value, str) and value else None


def derive_pins(repo, placement_path=PLACEMENT, plans=None):
    """(pins, complete, pending). Pins every manifestSha the authority still needs: per placement entry whose
    sourcePath == str(repo) (all entries when sourcePath is absent) acceptedManifest, rollbackManifest,
    pending.manifestSha and plan.snapshotSha256; plus storage.source.snapshotSha256 of every kind=='storage' item
    of each plan dict in `plans`. complete is False when placement.json is unreadable or a plan/entry is
    malformed; pending is True when any entry carries a pending intent."""
    pins, complete, pending = set(), True, False

    def pin(value):
        nonlocal complete
        if value is None:
            return
        if isinstance(value, str) and SHA_RE.fullmatch(value):
            pins.add(value)
        else:
            complete = False

    try:
        datasets = read(placement_path)['datasets']
    except (OSError, ValueError):
        return pins, False, pending
    for entry in datasets.values():
        if not isinstance(entry, dict):
            complete = False
            continue
        if entry.get('sourcePath') is not None and entry['sourcePath'] != str(repo):
            continue
        pin(entry.get('acceptedManifest'))
        pin(entry.get('rollbackManifest'))
        if entry.get('pending'):
            pending = True
            if isinstance(entry['pending'], dict):
                pin(entry['pending'].get('manifestSha'))
        plan = entry.get('plan')
        if isinstance(plan, dict):
            pin(plan.get('snapshotSha256'))
    for plan in plans or ():
        items = plan.get('items') if isinstance(plan, dict) else None
        if not isinstance(items, list):
            complete = False
            continue
        for item in items:
            if not isinstance(item, dict):
                complete = False
            elif item.get('kind') == 'storage':
                try:
                    pin(item['storage']['source']['snapshotSha256'])
                except (KeyError, TypeError):
                    complete = False
    return pins, complete, pending


def _gc(repo, pins, keep_floor):
    directory = Path(repo) / 'snapshot'
    manifests, objects = directory / 'manifests', directory / 'objects'
    if not manifests.is_dir():
        return dict(kept=[], removedManifests=[], removedObjects=[])
    for pin in pins:
        _check(SHA_RE, pin, 'pin')
    existing = {p.name[:-5]: p for p in manifests.glob('*.json') if SHA_RE.fullmatch(p.name[:-5])}
    try:
        newest = json.loads((directory / 'latest.json').read_bytes()).get('manifestSha')
    except (OSError, ValueError, AttributeError):
        raise ValueError('latest.json is unreadable; no garbage collection')
    documents = {sha: _load(existing[sha]) for sha in sorted(existing)}  # every manifest must parse, kept or not
    for sha in documents:
        _referenced(existing[sha])
    # newest first by binding.takenAt (ISO strings sort chronologically); mtime only breaks ties and
    # orders manifests that carry no takenAt (those sort oldest)
    order = sorted(existing, key=lambda s: (_taken_at(documents[s]) or '', existing[s].stat().st_mtime_ns, s), reverse=True)
    if newest in existing:
        order.remove(newest); order.insert(0, newest)
    kept = set(order[:max(keep_floor, 0)]) | (set(pins) & set(existing))
    if newest in existing:
        kept.add(newest)
    live = set()
    for sha in kept:
        live |= _referenced(existing[sha])
    removed_manifests = sorted(set(existing) - kept)
    removed_objects = []
    if objects.is_dir():
        for p in sorted(objects.iterdir()):
            if p.name in live:
                continue
            if SHA_RE.fullmatch(p.name) or p.name.startswith('.tmp-'):
                removed_objects.append(p.name)
    for sha in removed_manifests:
        os.unlink(existing[sha])
    for name in removed_objects:
        os.unlink(objects / name)
    for d in (manifests, objects):
        if d.is_dir(): _fsync_dir(d)
    return dict(kept=sorted(kept), removedManifests=removed_manifests, removedObjects=removed_objects)


def gc(repo, pins=None, *, keep_floor=3, now=None, locked=False, authority_complete=True):
    """Retention by live references (F2). pins: explicit manifestShas (tests); the CLI always derives them with
    derive_pins. Keeps pins, latest.json's manifest and the newest keep_floor manifests (binding.takenAt order);
    drops other manifests and unreferenced objects."""
    if pins is None:
        raise ValueError('no pins given; no garbage collection')
    if authority_complete is not True:
        raise ValueError('reference enumeration incomplete; no garbage collection')
    pins = set(pins)
    if locked:
        return _gc(repo, pins, keep_floor)
    with repository_lock.publication_lock(Path(repo)):
        return _gc(repo, pins, keep_floor)


def gc_derived(repo, placement_path=PLACEMENT, plans=None, **kw):
    """The CLI path: derive pins under the publication lock and refuse on pending/incomplete authority."""
    repo = Path(repo)
    with repository_lock.publication_lock(repo):
        pins, complete, pending = derive_pins(repo, placement_path, plans)
        if pending:
            raise ValueError('a storage transition is pending; no garbage collection')
        if not complete:
            raise ValueError('reference enumeration incomplete; no garbage collection')
        return _gc(repo, pins, kw.get('keep_floor', 3))


def after_publication(repo, dataset=DATASET_APT, path=PLACEMENT, device=None, now=None):
    """Caller holds the publication lock. Never raises for ValueError/OSError."""
    try:
        entry = hosting_for(dataset, path)
        if entry is None:
            return {'published': False, 'words': 'no placement for ' + dataset}
        plan = entry.get('plan') if isinstance(entry.get('plan'), dict) else {}
        latest = publish_manifest(repo, dataset, device, plan.get('id'), None, now, locked=True)
        return dict(latest, published=True)
    except (ValueError, OSError) as exc:
        words = str(exc)
        print('[storage] manifest not published: ' + words)
        return {'published': False, 'words': words}


def _require_root():
    if _geteuid() != 0:
        raise PermissionError('run with sudo on the core')


def _parser():
    top = argparse.ArgumentParser(prog='storage_placement')
    sub = top.add_subparsers(dest='cmd', required=True)
    sub.add_parser('status')
    de = sub.add_parser('domain-entry')
    de.add_argument('--domain', required=True)
    de.add_argument('--placement', default=None)
    fc = sub.add_parser('fence-check')
    fc.add_argument('--domain', required=True)
    fc.add_argument('--address', required=True)
    fc.add_argument('--intent', default='')
    fc.add_argument('--operation', default='')
    fc.add_argument('--placement', default=None)
    _placement_args(sub.add_parser('authorize', aliases=['authorize-placement']))
    mark = sub.add_parser('mark-migrated')
    mark.add_argument('--dataset', required=True)
    mark.add_argument('--digest', required=True)
    accept = sub.add_parser('accept', aliases=['accept-placement'])
    accept.add_argument('--dataset', required=True)
    accept.add_argument('--generation', type=int, required=True)
    ve = sub.add_parser('view-export')
    ve.add_argument('--placement', default=None)
    ve.add_argument('--out', default=None)
    ve.add_argument('--device-file', default=None)
    ve.add_argument('--post', action='store_true', help='write into the backend container (docker exec), as the core')
    g = sub.add_parser('gc')
    g.add_argument('--repo', required=True)
    g.add_argument('--placement', default=None)
    g.add_argument('--plans', nargs='*', default=[])
    g.add_argument('--keep-floor', type=int, default=3)
    for name in ('publish-manifest', 'after-publication'):
        p = sub.add_parser(name)
        p.add_argument('--repo', required=True)
        p.add_argument('--dataset', default=DATASET_APT)
        if name == 'publish-manifest':
            for flag in ('device', 'plan-id', 'item-id'):
                p.add_argument('--' + flag)
    return top


def _placement_args(a):
    for flag in ('dataset', 'device', 'domain', 'source-path', 'plan-id', 'plan-digest'):
        a.add_argument('--' + flag, required=True)
    a.add_argument('--plan-revision', type=int, required=True)
    a.add_argument('--replace', action='store_true')


def mark_migrated(dataset, digest, path=PLACEMENT, now=None, bump=False, operation='mark-migrated', by=None):
    """authorized -> migrated, only when digest is the one the entry was authorized with.
    The core verb passes bump=True (every write bumps); the legacy CLI path keeps the generation."""
    _check(SHA_RE, digest, 'digest')
    entry = hosting_for(dataset, path)
    if entry is None:
        raise ValueError('no placement for ' + dataset)
    plan = entry.get('plan') if isinstance(entry.get('plan'), dict) else {}
    if plan.get('digest') != digest:
        raise ValueError('digest does not match the authorized plan for ' + dataset)
    return transition(dataset, 'authorized', 'migrated', by=by or _actor(), operation=operation, path=path, now=now, bump=bump)


ALIASES = {'authorize-placement': 'authorize', 'accept-placement': 'accept'}


def _load_plans(files):
    plans = []
    for name in files:
        try:
            plans.append(json.loads(Path(name).read_bytes()))
        except (OSError, ValueError):
            plans.append(None)  # derive_pins marks a malformed plan incomplete
    return plans


def main(argv):
    args = _parser().parse_args(argv)
    if args.cmd in ALIASES:
        print('storage: %s is deprecated; use %s' % (args.cmd, ALIASES[args.cmd]), file=sys.stderr)
        args.cmd = ALIASES[args.cmd]
    if args.cmd == 'status':
        result = read(PLACEMENT)
    elif args.cmd == 'domain-entry':
        result = domain_entry(args.domain, args.placement or PLACEMENT)
    elif args.cmd == 'view-export':
        result = view_document(args.placement or PLACEMENT, args.device_file or DEVICE_NAME)
        if args.post:
            _require_root()
            post_view(result)
            return 0
        if args.out:
            _atomic_bytes(Path(args.out), (json.dumps(result, sort_keys=True) + '\n').encode())
            return 0
    elif args.cmd == 'fence-check':
        ok, words = fence_check(args.domain, args.address, args.intent, args.operation, args.placement or PLACEMENT)
        if not ok:
            print(words, file=sys.stderr)
            return 3
        print(words)
        return 0
    elif args.cmd == 'authorize':
        _require_root()
        plan = dict(id=args.plan_id, revision=args.plan_revision, digest=args.plan_digest)
        result = authorize(args.dataset, args.device, args.domain, args.source_path, plan, path=PLACEMENT, replace=args.replace)
    elif args.cmd == 'mark-migrated':
        _require_root()
        result = mark_migrated(args.dataset, args.digest, path=PLACEMENT)
    elif args.cmd == 'accept':
        _require_root()
        result = transition(args.dataset, 'cutover', 'hosting', by=_actor(), operation='accept',
                            path=PLACEMENT, expect_generation=args.generation)
    elif args.cmd == 'publish-manifest':
        _require_root()
        result = publish_manifest(args.repo, args.dataset, args.device, args.plan_id, args.item_id)
    elif args.cmd == 'gc':
        _require_root()
        result = gc_derived(args.repo, args.placement or PLACEMENT, _load_plans(args.plans), keep_floor=args.keep_floor)
    else:  # lock already held by apt-repo.sh
        result = after_publication(Path(args.repo), args.dataset, path=PLACEMENT)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, ValueError) as exc:
        print('storage: ' + str(exc), file=sys.stderr)
        sys.exit(1)
