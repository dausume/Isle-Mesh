#!/usr/bin/env python3
"""Checks for storage_sync.py (stm-4c): replica current, incremental sync, refusals, lease, status.
Fake network, temp mount, fake findmnt.  Prints PASS/FAIL per check; exits nonzero on any failure."""
import collections
import contextlib
import copy
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import storage_cli
import storage_contract as sc
import storage_receive as sr
import storage_sync as ss
from storage_migrate import HttpError
from storage_snapshot import encoded

FAILED, COUNT = [], [0]
UUID = '11112222-3333-4444-8555-666677778888'
DATASET, REL = 'apt-public-repo', 'isle/datasets/apt-public-repo'
DEVICE, ORIGIN, SOURCE_ADDR = 'pol-core', 'isle-core', '10.1.2.3'
DOMAIN = 'apt.isle'
API = 'https://api.polari.isle'
V1 = {'Packages': b'package index', 'a/one.deb': b'deb one', 'b/two.deb': b'deb two bytes', 'z/last': b'the last file'}
V2 = dict(V1, **{'Packages': b'package index, newer', 'c/three.deb': b'deb three'})
DIRS = ['a', 'b', 'c', 'z']
Vfs = collections.namedtuple('Vfs', 'f_bavail f_frsize f_blocks')
BIG = lambda fd: Vfs(10 ** 12, 1, 10 ** 12)
TIMES = iter(range(10 ** 6))
PUBLISHED = '2026-11-09T12:00:00+00:00'


def check(name, ok):
    COUNT[0] += 1
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok:
        FAILED.append(name)


def manifest(files, dataset=DATASET):
    entries = [dict(path=d, kind='directory') for d in DIRS if any(p.startswith(d + '/') for p in files)]
    entries += [dict(path=p, kind='file', size=len(b), sha256=hashlib.sha256(b).hexdigest()) for p, b in files.items()]
    doc = dict(schemaVersion=1, kind='isle-content-snapshot', totalBytes=sum(len(b) for b in files.values()),
               entries=sorted(entries, key=lambda e: e['path']))
    raw = encoded(dict(doc, binding=dict(dataset=dataset, sourceDevice=ORIGIN, takenAt='t', lockPath='/l', planId=None, itemId=None)))
    return doc, raw


class Pub:
    """One published snapshot: manifest, shas, objects."""

    def __init__(self, files, dataset=DATASET):
        self.files = files
        self.doc, self.raw = manifest(files, dataset)
        self.manifest_sha = hashlib.sha256(self.raw).hexdigest()
        self.content_sha = sr.content_sha(self.doc)
        self.objects = {hashlib.sha256(b).hexdigest(): b for b in files.values()}

    def latest(self, at=PUBLISHED):
        return dict(manifestSha=self.manifest_sha, contentSha=self.content_sha, at=at, dataset=DATASET,
                    totalBytes=self.doc['totalBytes'], entries=len(self.doc['entries']))


class FakeHttp:
    def __init__(self, s):
        self.s, self.calls, self.posts = s, [], []

    def get_json(self, url, resolve=None):
        self.calls.append((url, resolve))
        route = self.s.routes.get(url)
        if isinstance(route, Exception):
            raise route
        if route is None:
            raise HttpError('curl exit 22: The requested URL returned error: 404', 404)
        return copy.deepcopy(route)

    def get_bytes(self, url, resolve=None, max_bytes=None):
        self.calls.append((url, resolve))
        if resolve != (DOMAIN, SOURCE_ADDR):
            raise HttpError('wrong origin', None)
        return self.s.served_manifest

    def stream_to(self, url, sink, resolve=None, expected_size=None):
        self.calls.append((url, resolve))
        if resolve != (DOMAIN, SOURCE_ADDR):
            raise HttpError('wrong origin', None)
        sink.write(self.s.pub.objects[url.rsplit('/', 1)[1]])

    def post_json(self, url, payload, token=None):
        self.posts.append(url)
        return 201, ''

    def fetched_objects(self):
        return sorted(c[0].rsplit('/', 1)[1] for c in self.calls if '/snapshot/objects/' in c[0])


class Scenario:
    """A replica already holding V1 (migrated), a source that now publishes `publish` (default V2)."""

    def __init__(self, seed=V1, publish=V2, state='migrated', binding=True, with_status=None):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix='stm4c-'))
        self.mount = os.path.join(self.tmp, 'mnt')
        self.opdir, self.state = os.path.join(self.tmp, 'op'), os.path.join(self.tmp, 'state')
        self.lease = os.path.join(self.tmp, 'ops', 'app-device-update.lock')
        for d in (self.mount, self.opdir, os.path.join(self.state, DATASET)):
            os.makedirs(d)
        self.fs_uuid = UUID
        self.seed = Pub(seed)
        self.pub = Pub(publish)
        self.served_manifest = self.pub.raw
        self.latest = self.pub.latest()
        self.rel = REL.split('/')
        self.bound = dict(schemaVersion=2, dataset=DATASET, filesystemUuid=UUID, mountpoint=self.mount, relativePath=REL,
                          currentGeneration=self.seed.content_sha, snapshotSha256=self.seed.manifest_sha, boundAt='t',
                          plan=dict(id='p', revision=2, itemId='item-1', digest='d' * 64), hostApp='isle-content-host')
        self.install_seed()
        if binding:
            (Path(self.state) / DATASET / 'binding.json').write_text(json.dumps(self.bound))
        (Path(self.state) / DATASET / 'receipts.jsonl').write_text(json.dumps(
            dict(kind='isle.storage.migrate', ok=True, at='t', source=dict(device=ORIGIN))) + '\n')
        if with_status:
            (Path(self.state) / DATASET / 'sync-status.json').write_text(json.dumps(with_status))
        self.routes = {
            API + '/api/islemesh/storage/placement?dataset=' + DATASET:
                dict(state=state, generation=5, device=DEVICE, domain=DOMAIN, plan=dict(digest='d' * 64), source_device=ORIGIN),
            API + '/api/islemesh/devices': dict(devices=[dict(name=ORIGIN, agent_address=SOURCE_ADDR)]),
            'https://%s/snapshot/latest.json' % DOMAIN: self.latest,
        }
        self.http = FakeHttp(self)
        self.labels = []
        self.clock = '2026-11-09T12:00:42+00:00'

    def install_seed(self):
        """Receive, verify and promote the seed snapshot the way migrate leaves the disk."""
        root = sr.MountRoot(self.mount, UUID, self.findmnt)
        try:
            objs = self.seed.objects
            sr.receive(root, self.rel, self.seed.doc,
                       lambda e, sink: sink.write(objs[e['sha256']]), op_id='seed', statvfs=BIG)
            sr.verify_generation(root, self.rel, self.seed.content_sha, self.seed.raw)
            sr.promote(root, self.rel, self.seed.content_sha, self.seed.raw, dataset=DATASET, generation=3, op_id='seed')
            sr.write_binding(root, self.rel, self.bound)
        finally:
            root.close()

    def findmnt(self, path):
        return {'filesystems': [{'target': self.mount, 'uuid': self.fs_uuid, 'fstype': 'ext4', 'source': '/dev/sda1'}]}

    def go(self, argv=None):
        argv = argv or ['storage', 'sync', '--dataset', DATASET]
        env = {'ISLE_OPERATION_DIR': self.opdir, 'ISLE_STORAGE_STATE_DIR': self.state}
        deps = ss.Deps(identity=lambda: DEVICE, findmnt_json=self.findmnt, fstatvfs=BIG, lease_path=self.lease)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            self.rc = ss.run(argv, env=env, http=self.http, now=lambda: self.clock,
                             stage_hook=lambda label, info: self.labels.append(label), deps=deps)
        self.output = buffer.getvalue()
        path = Path(self.opdir) / 'storage-receipt.json'
        self.receipt = json.loads(path.read_text()) if path.exists() else None
        sp = Path(self.state) / DATASET / 'sync-status.json'
        self.status = json.loads(sp.read_text()) if sp.exists() else None
        return self

    @property
    def dataset_path(self):
        return os.path.join(self.mount, REL)

    def current(self):
        return os.readlink(os.path.join(self.dataset_path, 'current'))

    def jsonl(self):
        return (Path(self.state) / DATASET / 'receipts.jsonl').read_text().splitlines()

    def close(self):
        for base, dirs, files in os.walk(self.tmp):
            for name in dirs + files:
                with contextlib.suppress(OSError):
                    os.chmod(os.path.join(base, name), 0o755 if name in dirs else 0o644)
        shutil.rmtree(self.tmp, ignore_errors=True)


@contextlib.contextmanager
def scenario(**kwargs):
    s = Scenario(**kwargs)
    try:
        yield s
    finally:
        s.close()


def refused(name, s, words, stage=None, untouched=True):
    r = s.receipt or {}
    check(name + ': exit 1', s.rc == 1)
    check(name + ': receipt ok false with the words', r.get('ok') is False and words in r.get('words', ''))
    if stage:
        check(name + ': fails at ' + stage, r.get('failedStage') == stage)
    if untouched:
        check(name + ': current unchanged', s.current() == 'generations/' + s.seed.content_sha)
    check(name + ': no POST', not s.http.posts)


def current_checks():
    with scenario(publish=V1) as s:
        s.go()
        check('current: exit 0 with "replica current"', s.rc == 0 and 'replica current' in s.output)
        check('current: nothing fetched but latest.json',
              [c[0] for c in s.http.calls if 'snapshot/' in c[0]] == ['https://apt.isle/snapshot/latest.json'])
        check('current: syncStatus written, lag 0, lastCommitted now',
              s.status['lagSeconds'] == 0 and s.status['lastCommitted'] == s.clock and s.status['lastPublished'] == PUBLISHED
              and s.status['error'] is None)
        check('current: lastReceived unchanged (none before)', s.status['lastReceived'] is None)
        check('current: receipt kind and replicaCurrent', s.receipt['kind'] == 'isle.storage.sync' and s.receipt['replicaCurrent'] is True)
        check('current: not appended to receipts.jsonl', len(s.jsonl()) == 1)
        check('current: latest.json fetched from the source address with SNI',
              s.http.calls and any(c == ('https://apt.isle/snapshot/latest.json', (DOMAIN, SOURCE_ADDR)) for c in s.http.calls))
    with scenario(publish=V1, with_status=dict(lastReceived='2026-11-08T00:00:00+00:00')) as s:
        s.go()
        check('current: lastReceived carried from the previous status', s.status['lastReceived'] == '2026-11-08T00:00:00+00:00')


def sync_checks():
    with scenario() as s:
        s.go()
        r = s.receipt
        check('sync: exit 0', s.rc == 0)
        check('sync: stage labels are SYNC_STAGES in order (no short-circuit)', s.labels == list(sc.SYNC_STAGES))
        check('sync: only changed objects fetched (incremental)',
              s.http.fetched_objects() == sorted([hashlib.sha256(V2['Packages']).hexdigest(), hashlib.sha256(V2['c/three.deb']).hexdigest()]))
        check('sync: fetches pin the source address', all(c[1] in (None, (DOMAIN, SOURCE_ADDR)) for c in s.http.calls))
        check('sync: current points at the new generation', s.current() == 'generations/' + s.pub.content_sha)
        check('sync: previous generation kept', os.path.isdir(os.path.join(s.dataset_path, 'generations', s.seed.content_sha)))
        check('sync: served bytes of changed file', (Path(s.dataset_path) / 'current' / 'Packages').read_bytes() == V2['Packages'])
        check('sync: unchanged file is the same inode as before',
              os.stat(os.path.join(s.dataset_path, 'generations', s.seed.content_sha, 'a/one.deb')).st_ino
              == os.stat(os.path.join(s.dataset_path, 'current', 'a/one.deb')).st_ino)
        latest = json.loads((Path(s.dataset_path) / 'control' / 'latest.json').read_text())
        check('sync: control/latest.json carries the placement generation as epoch',
              latest['epoch'] == 5 and latest['contentSha'] == s.pub.content_sha and latest['manifestSha'] == s.pub.manifest_sha)
        disk = json.loads((Path(s.dataset_path) / 'binding.json').read_text())
        copy_ = json.loads((Path(s.state) / DATASET / 'binding.json').read_text())
        check('sync: disk binding and root copy name the new currentGeneration',
              disk['currentGeneration'] == s.pub.content_sha and copy_ == disk and disk['plan']['digest'] == 'd' * 64)
        check('sync: lastCommitted is the promote time, lag is 42 s',
              s.status['lastCommitted'] == s.clock and s.status['lagSeconds'] == 42 and s.status['lastPublished'] == PUBLISHED)
        check('sync: status content shas',
              s.status['publishedContentSha'] == s.status['receivedContentSha'] == s.status['committedContentSha'] == s.pub.content_sha)
        check('sync: receipt fields', r['kind'] == 'isle.storage.sync' and r['ok'] and r['contentVerified'] and r['epoch'] == 5
              and r['contentSha'] == s.pub.content_sha and r['placement'] == dict(state='migrated', generation=5)
              and list(r['stages']) == list(sc.SYNC_STAGES) and r['source']['origin'] == SOURCE_ADDR)
        check('sync: actionId carries the plan digest, plan carries itemId',
              r['actionId'] == 'isle.storage.sync:%s@%s:%s' % (DATASET, DEVICE, 'd' * 64) and r['plan']['itemId'] == 'item-1'
              and r['plan']['digest'] == 'd' * 64)
        check('sync: words say fetched 2 reused 3', '2 objects fetched' in r['words'] and '3 reused' in r['words'])
        check('sync: receipt appended to receipts.jsonl', len(s.jsonl()) == 2 and json.loads(s.jsonl()[1])['kind'] == 'isle.storage.sync')
        check('sync: no POST', not s.http.posts)
        check('sync: a second run is "replica current"', s.go().rc == 0 and 'replica current' in s.output)
    for state in ('cutover', 'hosting'):
        with scenario(state=state) as s:
            s.go()
            check('sync allowed in ' + state, s.rc == 0 and s.current() == 'generations/' + s.pub.content_sha)
    with scenario() as s:
        view_url = API + '/api/islemesh/storage/placement?dataset=' + DATASET
        del s.routes[view_url]['source_device']
        s.go()
        check('source device falls back to the migrate receipt', s.rc == 0 and s.receipt['source']['device'] == ORIGIN)


def refusal_checks():
    view_url = API + '/api/islemesh/storage/placement?dataset=' + DATASET
    with scenario() as s:
        del s.routes[view_url]
        s.go()
        refused('view absent', s, 'the placement view', 'checking-placement')
        check('view absent: error in syncStatus, lastCommitted unchanged',
              s.status['error'] and 'the placement view' in s.status['error'] and s.status['lastCommitted'] is None)
        check('view absent: no failure row in receipts.jsonl', len(s.jsonl()) == 1)
    with scenario() as s:
        s.routes[view_url]['state'] = 'authorized'
        s.go()
        refused('state authorized', s, 'authorized', 'checking-placement')
        check('state authorized: nothing fetched from the source', not any('snapshot/' in c[0] for c in s.http.calls))
    with scenario() as s:
        s.routes[view_url]['device'] = 'someone-else'
        s.go()
        refused('view names another device', s, 'not this device', 'checking-placement')
    with scenario(binding=False) as s:
        s.go()
        check('binding copy missing: exit 1, no network, no status written',
              s.rc == 1 and 'No binding copy' in s.receipt['words'] and not s.http.calls and s.status is None)
    with scenario() as s:
        s.fs_uuid = 'ffffffff-0000-0000-0000-000000000000'
        s.go()
        refused('uuid mismatch', s, 'UUID', 'fetching-latest')
        check('uuid mismatch: retryable and syncStatus error', s.receipt['retryable'] is True and 'UUID' in s.status['error'])
    with scenario() as s:
        s.served_manifest = s.pub.raw + b' '
        s.go()
        refused('manifest sha mismatch', s, 'does not hash to the snapshotSha256', 'fetching-manifest')
        check('manifest sha mismatch: nothing fetched or created', s.http.fetched_objects() == []
              and not os.path.exists(os.path.join(s.dataset_path, 'generations', s.pub.content_sha)))
    with scenario() as s:
        s.latest['contentSha'] = '0' * 64
        s.routes['https://apt.isle/snapshot/latest.json'] = s.latest
        s.go()
        refused('content sha mismatch', s, 'does not hash to the contentSha', 'fetching-manifest')
    with scenario() as s:
        bad = Pub(V2, dataset='other-dataset')
        s.served_manifest, s.latest = bad.raw, bad.latest()
        s.routes['https://apt.isle/snapshot/latest.json'] = s.latest
        s.go()
        refused('manifest binding names another dataset', s, 'another dataset', 'fetching-manifest')
    with scenario() as s:
        s.routes['https://apt.isle/snapshot/latest.json'] = {'contentSha': 'zz'}
        s.go()
        refused('latest.json malformed', s, 'not a snapshot descriptor', 'fetching-latest')
    with scenario() as s:
        s.routes['https://apt.isle/snapshot/latest.json'] = HttpError('connection refused', None)
        s.go()
        refused('source unreachable', s, 'Cannot fetch', 'fetching-latest')
        check('source unreachable: retryable', s.receipt['retryable'] is True)
    with scenario() as s:
        s.routes[API + '/api/islemesh/devices'] = dict(devices=[])
        s.go()
        refused('source address unknown', s, 'Source address unknown', 'checking-placement')
    with scenario() as s:
        s.go(['storage', 'sync', '--dataset', '../x'])
        check('bad argv: exit 2', s.rc == 2 and not s.http.calls)
    with scenario() as s:
        s.http.stream_to = lambda url, sink, resolve=None, expected_size=None: sink.write(b'x' * 3)
        s.go()
        refused('corrupt object', s, 'receiving-content', 'receiving-content')
        check('corrupt object: failure row recorded, lastCommitted unchanged', len(s.jsonl()) == 2 and s.status['lastCommitted'] is None)


def lease_checks():
    with scenario() as s:
        os.makedirs(os.path.dirname(s.lease))
        holder = open(s.lease, 'a+')
        fcntl.flock(holder, fcntl.LOCK_EX)
        try:
            s.go()
        finally:
            holder.close()
        refused('lease held', s, 'device update in progress on pol-core; retry after it completes', 'promoting')
        check('lease held: retryable', s.receipt['retryable'] is True)
        check('lease held: new generation present but unpromoted',
              os.path.isdir(os.path.join(s.dataset_path, 'generations', s.pub.content_sha)))
        check('lease held: binding still names the old generation',
              json.loads((Path(s.dataset_path) / 'binding.json').read_text())['currentGeneration'] == s.seed.content_sha)
        check('lease held: lastReceived set, lastCommitted unchanged, error words',
              s.status['lastReceived'] and s.status['lastCommitted'] is None and 'device update in progress' in s.status['error'])
        holder = open(s.lease, 'a+')
        fcntl.flock(holder, fcntl.LOCK_SH)
        try:
            s.go()
        finally:
            holder.close()
        check('lease held shared by another storage stage: retry succeeds and reuses the received generation',
              s.rc == 0 and s.current() == 'generations/' + s.pub.content_sha and s.http.fetched_objects().count(
                  hashlib.sha256(V2['c/three.deb']).hexdigest()) == 1)
        probe = open(s.lease, 'a+')
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            released = True
        except BlockingIOError:
            released = False
        probe.close()
        check('lease released after promote', released)


def helper_checks():
    check('lag_seconds', ss.lag_seconds('2026-11-09T12:00:00Z', '2026-11-09T12:01:30+00:00') == 90
          and ss.lag_seconds('nope', 'x') is None and ss.lag_seconds('2026-11-09T12:00:10+00:00', '2026-11-09T12:00:00+00:00') == 0)
    check('SYNC_STAGES and RECEIPT_KINDS', sc.SYNC_STAGES[0] == 'checking-placement' and sc.SYNC_STAGES[-1] == 'promoting'
          and 'isle.storage.sync' in sc.RECEIPT_KINDS and set(sc.SYNC_STAGES) >= {'fetching-manifest', 'receiving-content', 'verifying-content'})
    with scenario() as s:
        s.go()
        out = storage_cli.status_document(s.state, DATASET)
        check('storage_cli status surfaces syncStatus', out['syncStatus']['lagSeconds'] == 42)
    with scenario() as s:
        os.unlink(os.path.join(s.state, DATASET, 'receipts.jsonl')) if False else None
        check('storage_cli status has no syncStatus before a sync', 'syncStatus' not in storage_cli.status_document(s.state, DATASET))


for group in (current_checks, sync_checks, refusal_checks, lease_checks, helper_checks):
    group()
print('%d failed: %s' % (len(FAILED), FAILED) if FAILED else 'all %d checks passed' % COUNT[0])
sys.exit(1 if FAILED else 0)
