#!/usr/bin/env python3
"""`isle storage sync`: bring the replica of a dataset up to the source's latest snapshot (stm-4c).

Contract: docs/STORAGE-MIGRATION-CONTRACT.md section 2.5.  Runs as a journaled root operation (the dataset
lock is taken by operations.py before this starts) and from the root timer isle-storage-sync@<dataset>.timer.

Stages (storage_contract.SYNC_STAGES): checking-placement, fetching-latest, fetching-manifest,
receiving-content, verifying-content, promoting.  Only `promoting` holds the shared device-update lease; the
fetch holds nothing, so a device update is never refused for the whole transfer.  The previous generation
stays and keeps serving until `current` moves.  Nothing is POSTed: the receipt is local evidence
(ISLE_OPERATION_DIR/storage-receipt.json and receipts.jsonl).  Outside effects are injected exactly as in
storage_migrate (http, deps), which supplies CurlHttp, Deps and the shared helpers.
"""
import contextlib
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone

import operations
import storage_contract as sc
import storage_migrate as sm
import storage_receive as sr
from storage_migrate import CurlHttp, Deps, HttpError, default_now
from storage_receive import ReceiveError
from urllib.parse import quote

_fetch = sm._fetch  # private in storage_migrate; reused as is rather than copied
ALLOWED_STATES = ('migrated', 'cutover', 'hosting')
HEX64 = re.compile(r'[a-f0-9]{64}\Z')


def _parse_time(text):
    try:
        value = datetime.fromisoformat(str(text).replace('Z', '+00:00'))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def lag_seconds(published_at, committed_at):
    """Return (committed - published) in whole seconds (never negative), or None when either time is unreadable."""
    first, second = _parse_time(published_at), _parse_time(committed_at)
    if first is None or second is None:
        return None
    return max(0, int((second - first).total_seconds()))


def _read_json(path):
    try:
        value = json.loads(Path(path).read_bytes()[:sr.BINDING_MAX])
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


class Sync:
    """State and stages of one sync attempt."""

    def __init__(self, req, env, http, now, hook, deps):
        self.req, self.env, self.http, self.now, self.hook, self.deps = req, env, http, now, hook, deps
        self.dataset = req['dataset']
        self.op_id = env.get('ISLE_OPERATION_ID') or 'direct-%d' % os.getpid()
        self.op_dir = Path(env['ISLE_OPERATION_DIR']) if env.get('ISLE_OPERATION_DIR') else None
        self.state_dir = Path(env.get('ISLE_STORAGE_STATE_DIR') or sm.STATE_DIR)
        self.folder = self.state_dir / self.dataset
        self.current = sc.SYNC_STAGES[0]
        self.stack = contextlib.ExitStack()
        self.binding = self.view = self.root = self.rel = self.latest = None
        self.domain = self.address = self.device = None
        self.document = self.raw = self.content = self.manifest = None
        self.previous = None            # the contentSha current pointed at when this run began
        self.fetched = self.reused = 0
        self.disk_touched = False
        self.noop = False
        self.committed = False
        self.received_at = None
        self.committed_at = None
        self.receipt = self._skeleton()

    # ---- bookkeeping
    def _skeleton(self):
        receipt = sc.receipt_skeleton('isle.storage.sync', self.op_id, 1, {}, self.dataset, None)
        receipt.update(schemaVersion=2, ok=False, retryable=False, failedStage=None, epoch=None, contentSha=None,
                       manifestSha=None, posted=False, placement=None, replicaCurrent=False)
        return receipt

    def begin(self, label):
        self.current = label
        if self.env.get('ISLE_OPERATION_ID'):
            operations.stage(self.env['ISLE_OPERATION_ID'], label)
        if self.hook:
            self.hook(label, dict(operation=self.op_id, dataset=self.dataset))

    def done(self, label, **extra):
        self.receipt['stages'][label] = dict(ok=True, at=self.now(), **extra)

    def _refuse(self, message, retryable=False):
        raise ReceiveError(message, retryable=retryable, stage=self.current)

    def mounts(self):
        if not self.binding:
            return []
        try:
            return list(self.deps.findmnt_json(self.binding['mountpoint']).get('filesystems', []))
        except (OSError, ValueError, subprocess.SubprocessError, AttributeError, KeyError):
            return []

    # ---- stage 1
    def load_binding(self):
        """Read the root-filesystem binding copy; the sync target is bound to what the migration wrote."""
        record = _read_json(self.folder / 'binding.json')
        if record is None:
            self._refuse('No binding copy for %s under %s: run migrate first.' % (self.dataset, self.folder))
        mount, rel, uuid = record.get('mountpoint'), record.get('relativePath'), record.get('filesystemUuid')
        if record.get('dataset') != self.dataset or not all(isinstance(v, str) and v for v in (mount, rel, uuid)):
            self._refuse('The binding copy of %s is incomplete or names another dataset.' % self.dataset)
        if os.path.normpath(mount) == '/':
            self._refuse('The mountpoint is /: refusing the root filesystem.')
        self.binding = record
        self.rel = rel.split('/')

    def checking_placement(self):
        self.begin('checking-placement')
        try:
            self.device = self.deps.identity()
        except (ValueError, OSError) as error:
            self._refuse('This device\'s identity is not usable: %s' % error)
        view = sm._unwrap(_fetch(self.http.get_json, 'the placement view', self.current,
                                 '%s/api/islemesh/storage/placement?dataset=%s' % (sm.API, quote(self.dataset))), 'view')
        if not isinstance(view, dict) or not view.get('state'):
            self._refuse('No placement view for dataset %s: failing closed.' % self.dataset, retryable=True)
        if view['state'] not in ALLOWED_STATES:
            self._refuse('Placement of %s is %s; sync needs migrated, cutover or hosting.' % (self.dataset, view['state']))
        if view.get('device') not in (None, self.device):
            self._refuse('The placement view names %s as the host of %s, not this device (%s).'
                         % (view.get('device'), self.dataset, self.device))
        generation = view.get('generation')
        if type(generation) is not int or generation < 0:
            self._refuse('The placement view has no integer generation.')
        plan = self.binding.get('plan') or {}
        if (view.get('plan') or {}).get('digest') not in (None, plan.get('digest')):
            self._refuse('The placement entry is for a different plan digest than the binding.')
        self.domain = view.get('domain') or self.binding.get('domain')
        if not isinstance(self.domain, str) or self.binding.get('domain') not in (None, self.domain):
            self._refuse('The placement view and binding do not agree on the domain.')
        try:
            sm.check_url('https://%s/' % self.domain)
        except ValueError as error:
            self._refuse(str(error))
        source = view.get('source_device') or view.get('sourceDevice') or view.get('originDevice')
        if not source:  # fallback: the device the migrate receipt recorded as the source
            _, last = self._last_receipt()
            source = ((last or {}).get('source') or {}).get('device')
        rows = _fetch(self.http.get_json, 'the device list', self.current, sm.API + '/api/islemesh/devices')
        rows = rows.get('devices', []) if isinstance(rows, dict) else rows
        found = [r for r in rows if isinstance(r, dict) and r.get('name') == source] if source else []
        address = found[0].get('agent_address') if found else None
        try:
            ipaddress.ip_address(address)
        except ValueError:
            self._refuse('Source address unknown for %s.' % source, retryable=True)
        digest = plan.get('digest') or (view.get('plan') or {}).get('digest')
        if not isinstance(digest, str) or not sc.HEX64.match(digest):
            self._refuse('The binding has no plan digest to bind the sync action to.')
        self.view, self.address = view, address
        self.receipt.update(domain=self.domain, placement=dict(state=view['state'], generation=generation),
                            epoch=generation, plan=dict(plan),
                            actionId=sc.action_id('sync', self.dataset, self.device, digest))
        self.receipt['source'].update(device=source, origin=address)
        self.receipt['target'].update(device=self.device, filesystemUuid=self.binding['filesystemUuid'],
                                      mount=self.binding['mountpoint'])
        self.done('checking-placement', state=view['state'], generation=generation)

    def _last_receipt(self):
        from storage_cli import read_copies
        return read_copies(str(self.state_dir), self.dataset)

    # ---- stage 2
    def fetching_latest(self):
        self.begin('fetching-latest')
        latest = _fetch(self.http.get_json, 'snapshot/latest.json', self.current,
                        'https://%s/snapshot/latest.json' % self.domain, resolve=(self.domain, self.address))
        if not (isinstance(latest, dict) and HEX64.match(str(latest.get('contentSha')))
                and HEX64.match(str(latest.get('manifestSha'))) and isinstance(latest.get('at'), str)):
            self._refuse('latest.json from the source is not a snapshot descriptor.')
        if latest.get('dataset') not in (None, self.dataset):
            self._refuse('latest.json names dataset %s, not %s.' % (latest.get('dataset'), self.dataset))
        self.latest = latest
        self.open_root()
        self.previous = sr.current_generation(self.root, self.rel)
        self.receipt.update(manifestSha=latest['manifestSha'], contentSha=latest['contentSha'])
        self.receipt['source'].update(snapshotSha256=latest['manifestSha'], contentSha256=latest['contentSha'],
                                      totalBytes=latest.get('totalBytes') or 0, entries=latest.get('entries') or 0)
        self.done('fetching-latest', contentSha=latest['contentSha'], published=latest['at'], current=self.previous)

    def open_root(self):
        """MountRoot preflight: the mounted filesystem must carry the bound uuid before anything is read or written."""
        try:
            self.root = sr.MountRoot(self.binding['mountpoint'], self.binding['filesystemUuid'], self.deps.findmnt_json)
        except ReceiveError as error:
            error.stage = error.stage if error.stage != 'mount' else self.current
            raise
        self.stack.callback(self.root.close)
        self.receipt['target'].update(stDev='%d:%d' % (os.major(self.root.st_dev), os.minor(self.root.st_dev)),
                                      path=self.binding['mountpoint'].rstrip('/') + '/' + '/'.join(self.rel))
        self.receipt['mountsBefore'] = self.mounts()

    # ---- stage 3
    def fetching_manifest(self):
        self.begin('fetching-manifest')
        sha = self.latest['manifestSha']
        raw = _fetch(self.http.get_bytes, 'the manifest', self.current,
                     'https://%s/snapshot/manifests/%s.json' % (self.domain, sha), resolve=(self.domain, self.address),
                     max_bytes=sm.MANIFEST_MAX)
        if len(raw) > sm.MANIFEST_MAX:
            self._refuse('The manifest is larger than 64 MiB.')
        if sr.manifest_sha(raw) != sha:
            self._refuse('The manifest does not hash to the snapshotSha256 in latest.json.')
        try:
            document = json.loads(raw)
            sr.validate_manifest(document)
        except ValueError as error:
            self._refuse('The manifest is not acceptable: %s' % error)
        binding = document.get('binding')
        if not isinstance(binding, dict) or binding.get('dataset') != self.dataset:
            self._refuse('The manifest binding names another dataset.')
        content = sr.content_sha(document)
        if content != self.latest['contentSha']:
            self._refuse('The manifest content does not hash to the contentSha in latest.json.')
        plan = self._store_plan(document)
        info = self.deps.fstatvfs(self.root.fd)
        need = plan['bytesToFetch'] + plan['largestFile'] + sr.headroom(info.f_blocks * info.f_frsize)
        if info.f_bavail * info.f_frsize < need:
            self._refuse('Insufficient space: %d bytes free, %d needed (changed data, largest file, headroom).'
                         % (info.f_bavail * info.f_frsize, need))
        self.raw, self.document, self.manifest, self.content = raw, document, sha, content
        self.fetched, self.reused = len(plan['toFetch']), len(plan['reuse'])
        self.done('fetching-manifest', entries=len(document['entries']), totalBytes=document['totalBytes'],
                  bytesToFetch=plan['bytesToFetch'])

    def _store_plan(self, document):
        try:
            with self.root.opened(self.rel + ['store'], create=False) as store:
                return sr.plan_receive(document, store)
        except FileNotFoundError:
            return sr.plan_receive(document, None)

    # ---- stage 4
    def receiving_content(self):
        self.begin('receiving-content')
        self.disk_touched = True

        def fetch(entry, sink):
            self.http.stream_to(sm.object_url(self.domain, sha=entry['sha256']), sink,
                                resolve=(self.domain, self.address), expected_size=entry['size'])

        def progress(done, total, files, files_all):
            if self.op_dir:
                operations.atomic(self.op_dir / 'progress.json', dict(
                    stage='receiving-content', bytes=done, totalBytes=total, files=files, filesTotal=files_all,
                    at=self.now()))
        sr.receive(self.root, self.rel, self.document, fetch, op_id=self.op_id, progress=progress,
                   statvfs=self.deps.fstatvfs)
        self.received_at = self.now()
        self.done('receiving-content', bytes=self.document['totalBytes'], fetched=self.fetched, skipped=self.reused)

    # ---- stage 5
    def verifying_content(self):
        self.begin('verifying-content')
        sr.verify_generation(self.root, self.rel, self.content, self.raw)
        self.receipt['contentVerified'] = True
        self.receipt['generation'] = self.content
        self.done('verifying-content', contentSha=self.content)

    # ---- stage 6
    def take_lease(self):
        path = Path(self.deps.lease_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open('a+')
        try:
            fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            self._refuse('device update in progress on %s; retry after it completes' % self.device, retryable=True)
        return handle

    def promoting(self):
        self.begin('promoting')
        lease = self.take_lease()
        try:
            self.committed_at = self.now()
            op = self.op_id if re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', self.op_id) else None
            sr.promote(self.root, self.rel, self.content, self.raw, dataset=self.dataset,
                       generation=self.view['generation'], op_id=op, promoted_at=self.committed_at)
            record = dict(self.binding, currentGeneration=self.content, snapshotSha256=self.manifest)
            sr.write_binding(self.root, self.rel, record)
            self.folder.mkdir(parents=True, exist_ok=True)
            operations.atomic(self.folder / 'binding.json', record)
            self.committed = True
        finally:
            lease.close()
        self.done('promoting', contentSha=self.content, previousGeneration=self.previous)

    # ---- status and receipt
    def write_status(self, error=None):
        """Write sync-status.json.  A failure leaves lastCommitted and the committed contentSha unchanged."""
        old = _read_json(self.folder / 'sync-status.json') or {}
        now = self.now()
        status = dict(lastPublished=old.get('lastPublished'), lastReceived=old.get('lastReceived'),
                      lastCommitted=old.get('lastCommitted'), lagSeconds=old.get('lagSeconds'),
                      publishedContentSha=old.get('publishedContentSha'), receivedContentSha=old.get('receivedContentSha'),
                      committedContentSha=old.get('committedContentSha'), error=None)
        if self.latest is not None:
            status.update(lastPublished=self.latest['at'], publishedContentSha=self.latest['contentSha'])
        if error is None:
            if self.noop:
                status.update(lastCommitted=now, lagSeconds=0, committedContentSha=self.previous)
            else:
                status.update(lastReceived=self.received_at, receivedContentSha=self.content,
                              lastCommitted=self.committed_at, committedContentSha=self.content,
                              lagSeconds=lag_seconds(self.latest['at'], self.committed_at))
        else:
            if self.received_at and self.content:
                status.update(lastReceived=self.received_at, receivedContentSha=self.content)
            if self.latest is not None:
                status['lagSeconds'] = lag_seconds(self.latest['at'], now)
            status['error'] = str(error)
        status['checkedAt'] = now
        if self.folder.is_dir():
            operations.atomic(self.folder / 'sync-status.json', status)
        return status

    def finish(self, error=None):
        r = self.receipt
        if error is not None:
            r['stages'][self.current] = dict(ok=False, at=self.now(), error=str(error))
            r.update(retryable=bool(error.retryable), failedStage=self.current)
        r.update(ok=error is None, at=self.now())
        if self.root is not None and self.root.fd is not None:
            with contextlib.suppress(OSError, AttributeError):
                info = self.deps.fstatvfs(self.root.fd)
                r['target']['availableBytesAfter'] = info.f_bavail * info.f_frsize
            r['mountsAfter'] = self.mounts()
        short = (self.content or '')[:12]
        if error is not None:
            r['words'] = 'Sync of %s stopped at %s: %s%s' % (self.dataset, self.current, error,
                                                             ' (retry is safe).' if error.retryable else '.')
        elif self.noop:
            r.update(replicaCurrent=True)
            r['words'] = 'replica current: %s already holds %s.' % (self.dataset, self.latest['contentSha'][:12])
        else:
            r['words'] = ('Replica of %s synced: generation %s verified and promoted (%d objects fetched, %d reused); '
                          'previous generation kept.' % (self.dataset, short, self.fetched, self.reused))
        try:
            r['syncStatus'] = self.write_status(error)
        except OSError as caught:
            r['syncStatusError'] = str(caught)
        if self.op_dir:
            operations.atomic(self.op_dir / 'storage-receipt.json', r)
        # receipts.jsonl records disk work, not the every-10-minute "nothing to do" nor a refusal before any write
        # (those leave the operation receipt and sync-status.json)
        if self.noop or not (self.disk_touched or self.committed):
            return
        self._record(r)

    def _record(self, receipt):
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            with (self.folder / 'receipts.jsonl').open('ab') as out:
                out.write(json.dumps(receipt, sort_keys=True).encode() + b'\n')
                out.flush()
                os.fsync(out.fileno())
        except OSError as error:
            receipt['recordError'] = str(error)
            if self.op_dir:
                operations.atomic(self.op_dir / 'storage-receipt.json', receipt)

    def execute(self):
        error = None
        with self.stack:
            try:
                self.load_binding()
                self.checking_placement()
                self.fetching_latest()
                if self.previous == self.latest['contentSha']:
                    self.noop = True
                else:
                    self.fetching_manifest()
                    self.receiving_content()
                    self.verifying_content()
                    self.promoting()
            except ReceiveError as caught:
                error = caught
            except (OSError, ValueError, subprocess.SubprocessError) as caught:
                error = ReceiveError('%s: %s' % (type(caught).__name__, caught), retryable=True, stage=self.current)
            if self.binding is None:   # nothing known about the dataset: no folder to write into
                self.receipt['words'] = 'Sync of %s refused: %s' % (self.dataset, error)
                if self.op_dir:
                    self.receipt.update(ok=False, at=self.now(), failedStage=self.current)
                    operations.atomic(self.op_dir / 'storage-receipt.json', self.receipt)
            else:
                self.finish(error)
        if error is not None:
            print('storage sync: ' + self.receipt['words'], file=sys.stderr)
            return 1
        print(self.receipt['words'])
        return 0


def run(argv, *, env=os.environ, http=None, now=None, stage_hook=None, deps=None):
    """Run the sync stages for argv (['storage', 'sync', '--dataset', D] or ['sync', ...]); return the exit code."""
    argv = list(argv)
    if argv[:1] == ['sync']:
        argv = ['storage'] + argv
    try:
        req = sc.parse_request(argv)
        if req['verb'] != 'sync':
            raise ValueError('Only the sync verb runs here.')
    except ValueError as error:
        print('storage sync: %s' % error, file=sys.stderr)
        return 2
    return Sync(req, env, http or CurlHttp(), now or default_now, stage_hook, deps or Deps()).execute()


if __name__ == '__main__':
    sys.exit(run(sys.argv[1:]))
