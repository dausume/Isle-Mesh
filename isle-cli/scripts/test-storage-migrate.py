#!/usr/bin/env python3
"""Checks for storage_migrate.py (stm-4b): the eight stages with a fake network, a temp mount and a fake deploy.
Prints PASS/FAIL per check; exits nonzero on any failure."""
import collections
import contextlib
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import storage_contract as sc
import storage_migrate as sm
import storage_receive as sr
from storage_snapshot import encoded

FAILED = []
COUNT = [0]
UUID = '3bd28d7c-ba1e-4e6e-9a9f-23f684f4adf8'
PLAN_ID = 'ce23b61c3a444592a0e9274751f76854'
ITEM_ID = 'apt-repo-to-pol-core'
DATASET = 'apt-public-repo'
REL = 'isle/datasets/apt-public-repo'
DEVICE, ORIGIN = 'pol-core', 'isle-core'
SOURCE_ADDR, MY_ADDR = '10.1.2.3', '10.9.9.9'
VECTOR = sc.load_vector(HERE / 'fixtures' / 'storage-digest-vector.json')
FILES = {'Packages': b'package index', 'a/one.deb': b'deb one', 'b/two.deb': b'deb two bytes', 'z/last': b'the last file'}
DIRS = ['a', 'b', 'z']
NOW = lambda: '2026-11-09T12:00:00+00:00'
Vfs = collections.namedtuple('Vfs', 'f_bavail f_frsize f_blocks')
BIG = lambda fd: Vfs(10 ** 12, 1, 10 ** 12)
API = sm.API


def check(name, ok):
    COUNT[0] += 1
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok:
        FAILED.append(name)


def make_manifest(files, binding_changes=None, extra_entries=(), total=None):
    entries = [dict(path=d, kind='directory') for d in DIRS]
    entries += [dict(path=p, kind='file', size=len(b), sha256=hashlib.sha256(b).hexdigest()) for p, b in files.items()]
    entries += list(extra_entries)
    doc = dict(schemaVersion=1, kind='isle-content-snapshot',
               totalBytes=sum(len(b) for b in files.values()) if total is None else total,
               entries=sorted(entries, key=lambda e: e['path']))
    binding = dict(dataset=DATASET, sourceDevice=ORIGIN, takenAt='t', lockPath='/l', planId=None, itemId=None)
    binding.update(binding_changes or {})
    raw = encoded(dict(doc, binding=binding))
    return doc, raw


class FakeHttp:
    """Serves plan, view, devices, catalog, manifest, objects (source address) and readiness bytes (this device)."""

    def __init__(self, scenario):
        self.s = scenario
        self.calls = []
        self.posts = []
        self.post_status = 201
        self.post_error = None
        self.corrupt = set()

    def get_json(self, url, resolve=None):
        self.calls.append((url, resolve))
        route = self.s.routes.get(url)
        if isinstance(route, Exception):
            raise route
        if route is None:
            raise sm.HttpError('curl exit 22: The requested URL returned error: 404', 404)
        return copy.deepcopy(route)

    def get_bytes(self, url, resolve=None, max_bytes=None):
        self.calls.append((url, resolve))
        s = self.s
        if '/snapshot/manifests/' in url:
            if resolve != (s.domain, SOURCE_ADDR):
                raise sm.HttpError('wrong origin', None)
            return s.served_manifest
        if resolve != (s.domain, s.ready_addr):
            raise sm.HttpError('wrong readiness origin %r' % (resolve,), None)
        if s.readiness_down:
            raise sm.HttpError('connection refused', None)
        path = url.split(s.domain + '/', 1)[1]
        if path == '.well-known/isle-storage/current':
            return (s.served_current + '\n').encode()
        return s.local_files[path]

    def stream_to(self, url, sink, resolve=None, expected_size=None):
        self.calls.append((url, resolve))
        if resolve != (self.s.domain, SOURCE_ADDR):
            raise sm.HttpError('wrong origin', None)
        sha = url.rsplit('/', 1)[1]
        data = self.s.objects[sha]
        if sha in self.corrupt:
            data = b'x' * len(data)
        sink.write(data)

    def post_json(self, url, payload, token=None):
        self.posts.append((url, copy.deepcopy(payload), token))
        if self.post_error:
            raise self.post_error
        return self.post_status, 'text'


class Scenario:
    def __init__(self, files=FILES, req_dataset=DATASET, item_changes=None, storage_changes=None, manifest_binding=None,
                 manifest_extra=(), manifest_total=None, mountpoint=None, content_sha_wrong=False):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix='stm4b-'))
        self.mount = mountpoint or os.path.join(self.tmp, 'mnt')
        os.makedirs(os.path.join(self.tmp, 'mnt'))
        self.opdir = os.path.join(self.tmp, 'op')
        self.state = os.path.join(self.tmp, 'state')
        self.units = os.path.join(self.tmp, 'units')
        self.lease = os.path.join(self.tmp, 'ops', 'app-device-update.lock')
        self.enroll = os.path.join(self.tmp, 'member-enrollment.json')
        os.makedirs(self.opdir)
        Path(self.enroll).write_text(json.dumps(dict(device=DEVICE, token='T' * 64)))
        self.domain = 'apt.isle'
        self.doc, self.raw = make_manifest(files, manifest_binding, manifest_extra, manifest_total)
        self.manifest_sha = hashlib.sha256(self.raw).hexdigest()
        self.content_sha = sr.content_sha(self.doc)
        self.served_manifest = self.raw
        self.objects = {hashlib.sha256(b).hexdigest(): b for b in files.values()}
        self.local_files = dict(files)
        self.served_current = self.content_sha
        self.readiness_down = False
        self.identity = DEVICE
        item = copy.deepcopy(VECTOR['item'])
        item['storage']['source'].update(snapshotSha256=self.manifest_sha, totalBytes=self.doc['totalBytes'],
                                         entries=len(self.doc['entries']),
                                         contentSha256=('0' * 64) if content_sha_wrong else self.content_sha)
        item['storage']['target'].update(mountpoint=self.mount, filesystemUuid=UUID, relativePath=REL,
                                         capacityBytes=10 ** 9)
        item['storage'].update(storage_changes or {})
        item.update(item_changes or {})
        item['hostingRelease'] = dict(VECTOR['hostingRelease'])
        self.item = item
        self.release_sha = item['hostingRelease']['manifestSha256']
        self.digest = sc.storage_digest(PLAN_ID, 2, item, item['hostingRelease'])
        self.plan_url = '%s/api/islemesh/placement-plan?id=%s&revision=2' % (API, PLAN_ID)
        self.view_url = '%s/api/islemesh/storage/placement?dataset=%s' % (API, DATASET)
        self.routes = {
            self.plan_url: dict(id=PLAN_ID, revision=2, active=True, items=[item]),
            self.view_url: dict(state='authorized', generation=3, device=DEVICE, domain='apt.isle', plan=dict(digest=self.digest)),
            API + '/api/islemesh/devices': dict(devices=[dict(name=ORIGIN, agent_address=SOURCE_ADDR),
                                                         dict(name=DEVICE, agent_address=MY_ADDR)]),
            API + '/api/islemesh/catalog/isle-content-host': dict(entry=dict(install_plan=dict(release=dict(sha256=self.release_sha)))),
        }
        self.http = FakeHttp(self)
        self.deploys = []
        self.deploy_code = 0
        self.deploy_error = None
        self.req_dataset = req_dataset
        self.labels = []
        self.hook_extra = None
        self.probe_address = lambda: None
        self.ready_addr = MY_ADDR
        self.fs_uuid = UUID
        self.clock = [0.0]
        self.systemctl_calls, self.systemctl_fail, self.enabled = [], None, set()
        self.mount_state = 'active'

    # ---- fakes
    def findmnt(self, path):
        return {'filesystems': [{'target': self.mount, 'uuid': self.fs_uuid, 'fstype': 'ext4', 'options': 'rw,relatime',
                                 'source': '/dev/sda1'}]}

    def run_findmnt(self, argv, **kwargs):
        return types.SimpleNamespace(stdout=json.dumps(self.findmnt(None)), returncode=0)

    def deploy(self, app, extra):
        self.deploys.append((app, list(extra)))
        if self.deploy_error:
            raise self.deploy_error
        return self.deploy_code

    def systemctl(self, args):
        self.systemctl_calls.append(list(args))
        if self.systemctl_fail and list(args[:len(self.systemctl_fail)]) == self.systemctl_fail:
            return 1
        if args[0] == 'show':           # systemctl show -p Id,ActiveState <mount unit>
            unit = args[-1]
            if self.mount_state is None:
                return 1, ''
            return 0, 'Id=%s\nActiveState=%s\n' % (unit if self.mount_state != 'wrong-id' else 'other.mount',
                                                  'active' if self.mount_state == 'active' else self.mount_state)
        if args[0] == 'enable':
            self.enabled.update(a for a in args[1:] if not a.startswith('-'))
        if args[0] == 'is-enabled':
            return 0 if args[-1] in self.enabled else 1
        return 0

    def sleep(self, seconds):
        self.clock[0] += seconds

    def hook(self, label, info):
        self.labels.append(label)
        if self.hook_extra:
            self.hook_extra(label, info)

    def go(self, argv=None, capacity=None):
        argv = argv or ['storage', 'migrate', '--dataset', self.req_dataset, '--plan', PLAN_ID, '--revision', '2',
                        '--item', ITEM_ID, '--digest', self.digest]
        env = {'ISLE_OPERATION_DIR': self.opdir, 'ISLE_STORAGE_STATE_DIR': self.state, 'ISLE_STORAGE_UNIT_DIR': self.units,
               'ISLE_ENROLLMENT_FILE': self.enroll, 'ISLE_AGENT_ADDRESS': MY_ADDR}
        deps = sm.Deps(identity=lambda: self.identity, findmnt_json=self.findmnt, run=self.run_findmnt,
                       statvfs=capacity or os.statvfs, fstatvfs=BIG, deploy=self.deploy, lease_path=self.lease,
                       sleep=self.sleep, monotonic=lambda: self.clock[0], systemctl=self.systemctl,
                       probe_address=self.probe_address)
        import io as iomod
        buffer = iomod.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            self.rc = sm.run(argv, env=env, http=self.http, now=NOW, stage_hook=self.hook, deps=deps)
        self.output = buffer.getvalue()
        path = Path(self.opdir) / 'storage-receipt.json'
        self.receipt = json.loads(path.read_text()) if path.exists() else None
        return self

    # ---- helpers
    @property
    def dataset_path(self):
        return os.path.join(self.mount, REL)

    def disk_has(self, *parts):
        return os.path.lexists(os.path.join(self.dataset_path, *parts))

    def jsonl(self):
        path = Path(self.state) / DATASET / 'receipts.jsonl'
        return path.read_text().splitlines() if path.exists() else []

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


def refused(name, s, stage, words=None, retryable=None, disk_untouched=None):
    r = s.receipt or {}
    check(name + ': exit 1 and receipt ok false', s.rc == 1 and r.get('ok') is False)
    check(name + ': fails at ' + stage, r.get('failedStage') == stage and r['stages'].get(stage, {}).get('ok') is False)
    if words:
        check(name + ': words mention %r' % words, words in r.get('words', ''))
    if retryable is not None:
        check(name + ': retryable is %s' % retryable, r.get('retryable') is retryable)
    if disk_untouched:
        check(name + ': nothing written on the target disk', not s.disk_has() and os.listdir(s.mount) == [])
        check(name + ': no deploy', not s.deploys)


# ------------------------------------------------------------------ happy path

def happy_checks():
    with scenario() as s:
        s.go()
        r = s.receipt
        check('happy: exit 0', s.rc == 0)
        check('happy: receipt ok with every boolean true',
              r['ok'] is True and r['contentVerified'] and r['stagedDeploy'] and r['readinessVerified'] and r['hostDeployed']
              and r['digestRecomputed'] and r['retryable'] is False)
        check('happy: stage hook labels are exactly STAGES in order', s.labels == list(sc.STAGES))
        check('happy: receipt stages are exactly STAGES in order, all ok',
              list(r['stages']) == list(sc.STAGES) and all(v['ok'] for v in r['stages'].values()))
        check('happy: actionId, epoch and hashes',
              r['actionId'] == sc.action_id('migrate', DATASET, DEVICE, s.digest) and r['epoch'] == 3
              and r['contentSha'] == s.content_sha and r['manifestSha'] == s.manifest_sha and r['generation'] == s.content_sha)
        check('happy: receipt kind, schema, plan block, sourceRemovable false',
              r['kind'] == 'isle.storage.migrate' and r['schemaVersion'] == 2 and r['sourceRemovable'] is False
              and r['plan'] == dict(id=PLAN_ID, revision=2, itemId=ITEM_ID, digest=s.digest)
              and r['placement'] == dict(state='authorized', generation=3))
        check('happy: target and source blocks',
              r['target']['device'] == DEVICE and r['target']['filesystemUuidObserved'] == UUID
              and r['target']['mountSourceObserved'] == '/dev/sda1' and r['target']['path'] == s.dataset_path
              and r['target']['mount'] == s.mount and ':' in r['target']['stDev'] and r['target']['availableBytesAfter'] > 0
              and r['source']['origin'] == SOURCE_ADDR and r['source']['device'] == ORIGIN
              and r['source']['contentSha256'] == s.content_sha and r['source']['snapshotSha256'] == s.manifest_sha)
        check('happy: mountsBefore and mountsAfter are findmnt rows', r['mountsBefore'] and r['mountsAfter'])
        check('happy: hostingRelease recorded', r['hostingRelease']['name'] == 'isle-content-host')
        check('happy: receive stage counts', r['stages']['receiving-content']['fetched'] == 4
              and r['stages']['receiving-content']['bytes'] == s.doc['totalBytes'])
        binding = json.loads((Path(s.dataset_path) / 'binding.json').read_text())
        check('happy: binding.json on the disk', binding['dataset'] == DATASET and binding['currentGeneration'] == s.content_sha
              and binding['mountpoint'] == s.mount and binding['filesystemUuid'] == UUID and binding['plan']['digest'] == s.digest)
        copy_path = Path(s.state) / DATASET / 'binding.json'
        check('happy: root-fs binding copy equals the disk marker', json.loads(copy_path.read_text()) == binding)
        check('happy: current points at the generation', os.readlink(os.path.join(s.dataset_path, 'current')) == 'generations/' + s.content_sha)
        latest = json.loads((Path(s.dataset_path) / 'control' / 'latest.json').read_text())
        check('happy: promote used the placement generation', latest['epoch'] == 3 and latest['manifestSha'] == s.manifest_sha)
        unit = Path(s.units) / ('isle-storage-%s.service' % DATASET)
        check('happy: mount gate unit written, not enabled', unit.exists() and 'RequiresMountsFor=' + s.mount in unit.read_text()
              and 'isle storage check --dataset ' + DATASET in unit.read_text())
        check('happy: systemctl daemon-reload, enable hosting unit, enable --now timer (then is-enabled)',
              [c for c in s.systemctl_calls if c[0] != 'show'][:3] == [['daemon-reload'], ['enable', 'isle-storage-%s.service' % DATASET],
                                        ['enable', '--now', 'isle-storage-sync@%s.timer' % DATASET]])
        check('happy: receipt syncTimerInstalled is True', r['syncTimerInstalled'] is True)
        check('happy: root-fs binding copy carries plan.itemId', binding['plan']['itemId'] == ITEM_ID)
        check('happy: receipt appended to receipts.jsonl', len(s.jsonl()) == 1 and json.loads(s.jsonl()[0])['ok'] is True)
        check('happy: progress.json written', json.loads((Path(s.opdir) / 'progress.json').read_text())['bytes'] == s.doc['totalBytes'])
        check('happy: staged deploy with the bind', s.deploys == [('isle-content-host', ['--staged', '--bind', 'ISLE_DATASET_PATH=' + s.dataset_path])])
        check('happy: posted with the enrollment token, receipt carries none',
              len(s.http.posts) == 1 and s.http.posts[0][2] == 'T' * 64 and r['posted'] is True
              and 'T' * 64 not in json.dumps(r) and s.http.posts[0][0] == API + '/api/islemesh/storage/receipt')
        samples = [c[0] for c in s.http.calls if c[1] == ('apt.isle', MY_ADDR)]
        check('happy: stage 8 reads current plus 3 sorted samples (0, n//2, n-1) from this device',
              samples == ['https://apt.isle/.well-known/isle-storage/current', 'https://apt.isle/Packages',
                          'https://apt.isle/b/two.deb', 'https://apt.isle/z/last'])
        check('happy: every source fetch pins the source address, https and a .isle name',
              all(c[1] in (None, ('apt.isle', SOURCE_ADDR), ('apt.isle', MY_ADDR)) for c in s.http.calls)
              and all(c[0].startswith('https://') and '10.' not in c[0] for c in s.http.calls))
        check('happy: no probe file left behind', not any(n.startswith('.isle-probe') for n in os.listdir(os.path.dirname(s.dataset_path))))
        check('happy: receipt file equals what was appended apart from post fields',
              {k: v for k, v in json.loads(s.jsonl()[0]).items() if k not in ('posted',)} ==
              {k: v for k, v in r.items() if k not in ('posted',)})

    with scenario() as s:
        s.http.post_status = 404
        s.go()
        check('post 404: operation still succeeds', s.rc == 0 and s.receipt['ok'] is True)
        check('post 404: recorded as posted false with the error', s.receipt['posted'] is False and '404' in s.receipt['postError'])
    with scenario() as s:
        s.http.post_error = sm.HttpError('connection refused')
        s.go()
        check('post transport error does not fail the operation', s.rc == 0 and s.receipt['posted'] is False)
    with scenario() as s:
        os.unlink(s.enroll)
        s.go()
        check('missing enrollment file: succeeds with posted false and no post attempt',
              s.rc == 0 and s.receipt['posted'] is False and not s.http.posts)


# ------------------------------------------------------------------ stage 1

def stage1_checks():
    name = 'fetching-plan'
    with scenario() as s:
        s.routes[s.plan_url]['active'] = False
        s.go()
        refused('inactive plan', s, name, 'not active', disk_untouched=True)
        check('inactive plan: nothing recorded outside the journal dir', s.jsonl() == [] and not s.http.posts)
        check('inactive plan: only the first stage began', s.labels == ['fetching-plan'])
    with scenario() as s:
        s.identity = 'econ-core'
        s.go()
        refused('wrong device', s, name, 'names pol-core', disk_untouched=True)
    with scenario() as s:
        s.req_dataset = 'other-set'
        s.go()
        refused('dataset mismatch', s, name, 'other-set', disk_untouched=True)
    with scenario() as s:
        s.routes[s.plan_url]['items'][0]['app'] = 'something-else'
        s.go()
        refused('digest mismatch', s, name, 'plan changed since it was reviewed', disk_untouched=True)
    with scenario() as s:
        del s.routes[s.view_url]
        s.go()
        refused('placement view absent', s, name, 'placement view', disk_untouched=True)
    with scenario() as s:
        s.routes[s.view_url] = {}
        s.go()
        refused('placement view empty', s, name, 'failing closed', disk_untouched=True)
    with scenario() as s:
        s.routes[s.view_url]['state'] = 'cutover'
        s.go()
        refused('view state cutover', s, name, 'cutover', disk_untouched=True)
    with scenario() as s:
        s.routes[s.view_url]['plan'] = dict(digest='f' * 64)
        s.go()
        refused('view digest mismatch', s, name, 'different plan digest', disk_untouched=True)
    with scenario() as s:
        s.routes[s.view_url]['device'] = 'econ-core'
        s.go()
        refused('view names another device', s, name, disk_untouched=True)
    with scenario() as s:
        s.routes[API + '/api/islemesh/devices'] = dict(devices=[dict(name=ORIGIN)])
        s.go()
        refused('missing source address', s, name, 'Source address unknown', disk_untouched=True)
    with scenario() as s:
        s.routes[s.view_url]['state'] = 'migrated'
        s.go()
        check('view state migrated with the same digest is a legitimate re-run', s.rc == 0)
    with scenario(item_changes={'state': 'deferred'}) as s:
        s.go()
        refused('deferred item', s, name, 'not planned', disk_untouched=True)
    with scenario() as s:
        s.routes[s.plan_url]['items'][0]['storage']['source']['snapshotSha256'] = None
        s.go()
        refused('null snapshotSha256', s, name, 'no source snapshot', disk_untouched=True)
    with scenario() as s:
        s.routes[s.plan_url]['revision'] = 3
        s.go()
        refused('plan service returns another revision', s, name, disk_untouched=True)
    with scenario() as s:
        s.routes[s.plan_url] = sm.HttpError('boom', None)
        s.go()
        refused('plan fetch transport failure is retryable', s, name, retryable=True, disk_untouched=True)
    with scenario() as s:
        s.go(argv=['storage', 'migrate', '--dataset', DATASET])
        check('bad request refused before anything starts', s.rc == 2 and s.labels == [] and s.receipt is None)


# ------------------------------------------------------------------ stage 2

def stage2_checks():
    name = 'preflight-storage'
    with scenario() as s:
        s.go()
        check('mount unit active: stage 2 passes and asks systemctl show -p Id,ActiveState <unit>',
              s.rc == 0 and any(c[:3] == ['show', '-p', 'Id,ActiveState'] and c[3].endswith('.mount') for c in s.systemctl_calls))
    for state in ('inactive', 'failed', None, 'wrong-id'):
        with scenario() as s:
            s.mount_state = state
            s.go()
            refused('mount unit %s' % state, s, name, 'not an active systemd mount unit', disk_untouched=True)
    with scenario() as s:
        s.fs_uuid = 'wrong-uuid'
        s.go()
        refused('wrong filesystem uuid', s, name, 'UUID', disk_untouched=True)
        check('stage 2 refusal records nothing outside the journal dir', s.jsonl() == [] and not s.http.posts)
    with scenario() as s:
        os.makedirs(s.dataset_path)
        Path(s.dataset_path, 'binding.json').write_text(json.dumps(dict(dataset='other', filesystemUuid=UUID)))
        s.go()
        refused('binding.json of another dataset', s, name, 'another dataset')
    with scenario() as s:
        os.makedirs(s.dataset_path)
        Path(s.dataset_path, 'binding.json').write_text(json.dumps(dict(dataset=DATASET, filesystemUuid='other')))
        s.go()
        refused('binding.json of another filesystem', s, name, 'another dataset or filesystem')
    with scenario() as s:
        os.makedirs(s.dataset_path)
        Path(s.dataset_path, 'binding.json').write_text('{not json')
        s.go()
        refused('damaged binding.json', s, name, 'damaged')
    with scenario() as s:
        os.makedirs(s.dataset_path)
        Path(s.dataset_path, 'binding.json').write_text(json.dumps(dict(dataset=DATASET, filesystemUuid=UUID)))
        s.go()
        check('matching existing binding.json is accepted', s.rc == 0)
    with scenario(mountpoint='/') as s:
        s.go()
        refused('mountpoint is /', s, name, '/', disk_untouched=False)
    with scenario() as s:
        s.go(capacity=lambda p: types.SimpleNamespace(f_bavail=1, f_frsize=1, f_flag=0, f_blocks=10))
        refused('insufficient capacity', s, name, 'Insufficient', disk_untouched=True)
    with scenario() as s:
        unwritable = os.path.join(s.mount, 'isle')
        os.makedirs(unwritable)
        os.chmod(unwritable, 0o555)
        s.go()
        if os.geteuid() == 0:
            check('unwritable parent (skipped as root)', True)
        else:
            refused('unwritable dataset parent fails the write probe', s, name, 'not writable')


# ------------------------------------------------------------------ stage 3

def stage3_checks():
    name = 'fetching-manifest'
    with scenario() as s:
        s.served_manifest = s.raw + b' '
        s.go()
        refused('manifest sha mismatch', s, name, 'snapshotSha256')
        check('manifest refusal: dataset directory not created', not s.disk_has('store') and not s.disk_has('binding.json'))
    with scenario(manifest_binding={'dataset': 'other-set'}) as s:
        s.go()
        refused('binding dataset mismatch', s, name, 'another dataset')
    with scenario(manifest_binding={'planId': 'f' * 32}) as s:
        s.go()
        refused('binding planId mismatch', s, name, 'planId')
    with scenario(manifest_binding={'itemId': 'someone-else'}) as s:
        s.go()
        refused('binding itemId mismatch', s, name, 'itemId')
    with scenario(manifest_binding={'planId': PLAN_ID, 'itemId': ITEM_ID}) as s:
        s.go()
        check('matching planId/itemId in the binding accepted', s.rc == 0)
    with scenario(manifest_extra=[dict(path='../escape', kind='file', size=1, sha256='a' * 64)], manifest_total=None) as s:
        s.go()
        refused('unsafe manifest path', s, name, 'not acceptable')
    with scenario(manifest_extra=[dict(path='q?x', kind='file', size=0, sha256='b' * 64)]) as s:
        s.go()
        refused('manifest path with a question mark', s, name)
    with scenario(content_sha_wrong=True) as s:
        s.go()
        refused('contentSha256 differs from the plan', s, name, 'contentSha256')
    with scenario() as s:
        s.routes[s.plan_url]['items'][0]['storage']['source']['totalBytes'] = 5
        s.go()
        # the item changed after the digest was computed, so stage 1 catches it first
        refused('plan totalBytes edited after review', s, 'fetching-plan', 'changed since')
    with scenario(manifest_total=3) as s:
        s.go()
        refused('manifest totalBytes inconsistent', s, name, 'not acceptable')
    with scenario() as s:
        s.http.s.served_manifest = b'x' * 10
        s.go()
        refused('garbage manifest', s, name)
    with scenario() as s:
        s.go(capacity=None)
        check('capacity re-check uses the received numbers', s.rc == 0)


# ------------------------------------------------------------------ stage 4-6

def stage4_checks():
    with scenario() as s:
        victim = hashlib.sha256(FILES['a/one.deb']).hexdigest()
        s.http.corrupt.add(victim)
        s.go()
        refused('corrupted object', s, 'receiving-content', 'sha256', retryable=True)
        check('corrupted object: no binding written, current not promoted',
              not s.disk_has('binding.json') and not s.disk_has('current'))
        check('corrupted object: no deploy and stage 6 never began', not s.deploys and 'writing-binding' not in s.labels)
        check('corrupted object: failure recorded in jsonl and posted', len(s.jsonl()) == 1 and len(s.http.posts) == 1)
        check('corrupted object: staging cleaned up', not os.listdir(os.path.join(s.dataset_path, 'staging')))
    with scenario() as s:
        s.go()
        s.http.corrupt.clear()
        s.go()
        check('re-running over an existing replica succeeds (idempotent)', s.rc == 0 and s.receipt['stages']['receiving-content']['fetched'] == 0
              and s.receipt['stages']['receiving-content']['skipped'] == 4)


def systemctl_checks():
    for failing in (['daemon-reload'], ['enable', 'isle-storage-%s.service' % DATASET], ['enable', '--now']):
        with scenario() as s:
            s.systemctl_fail = failing
            s.go()
            refused('systemctl %s fails' % ' '.join(failing), s, 'writing-binding', 'systemctl', retryable=True)
            check('systemctl failure: no deploy and no syncTimerInstalled true',
                  not s.deploys and s.receipt.get('syncTimerInstalled') is not True)
    with scenario() as s:
        s.systemctl_fail = ['is-enabled']
        s.go()
        check('timer reported not enabled: stage passes, syncTimerInstalled False', s.rc == 0 and s.receipt['syncTimerInstalled'] is False)


def lease_checks():
    with scenario() as s:
        os.makedirs(os.path.dirname(s.lease))
        holder = open(s.lease, 'a+')
        fcntl.flock(holder, fcntl.LOCK_EX)
        try:
            s.go()
        finally:
            holder.close()
        refused('exclusive device-update lease', s, 'writing-binding',
                'device update in progress on pol-core; retry after it completes', retryable=True)
        check('lease refusal: content stages done, binding not written, no deploy',
              s.receipt['contentVerified'] is True and not s.disk_has('binding.json') and not s.disk_has('current') and not s.deploys)
        check('lease refusal: no unit, no root-fs binding', not os.path.exists(s.units) and not (Path(s.state) / DATASET / 'binding.json').exists())
    with scenario() as s:
        os.makedirs(os.path.dirname(s.lease))
        holder = open(s.lease, 'a+')
        fcntl.flock(holder, fcntl.LOCK_SH)
        try:
            s.go()
        finally:
            holder.close()
        check('another shared holder does not block stage 6', s.rc == 0)
    with scenario() as s:
        s.go()
        probe = open(s.lease, 'a+')
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            freed = True
        except BlockingIOError:
            freed = False
        probe.close()
        check('lease released when the operation ends', freed)


# ------------------------------------------------------------------ stage 7-8

def stage7_checks():
    name = 'deploying-host'
    with scenario() as s:
        def deactivate(label, info):
            if label == name:
                s.routes[s.plan_url]['active'] = False
        s.hook_extra = deactivate
        s.go()
        refused('plan deactivated before deploy', s, name, 'not active')
        check('currency failure: no deploy', not s.deploys)
    with scenario() as s:
        def move(label, info):
            if label == name:
                s.routes[s.view_url]['state'] = 'cutover'
        s.hook_extra = move
        s.go()
        refused('placement left authorized before deploy', s, name, 'cutover')
    with scenario() as s:
        def bump(label, info):
            if label == name:
                s.routes[s.view_url]['generation'] = 9
        s.hook_extra = bump
        s.go()
        refused('placement generation moved before deploy', s, name, 'generation')
    with scenario() as s:
        s.routes[API + '/api/islemesh/catalog/isle-content-host']['entry']['install_plan']['release']['sha256'] = 'e' * 64
        s.go()
        refused('release differs from the pin', s, name, 'differs from the pinned')
    with scenario() as s:
        del s.routes[API + '/api/islemesh/catalog/isle-content-host']
        s.go()
        refused('release not published (catalog 404)', s, name, 'not published')
    with scenario() as s:
        s.routes[API + '/api/islemesh/catalog/isle-content-host'] = dict(entry=dict(install_plan=dict(steps=[])))
        s.go()
        refused('catalog entry without release descriptor', s, name, 'not published')
    with scenario() as s:
        s.deploy_code = 3
        s.go()
        refused('deploy exit code non-zero', s, name, 'exit 3', retryable=True)
        check('failed deploy leaves hostDeployed false', s.receipt['hostDeployed'] is False and s.receipt['stagedDeploy'] is False)
    with tempfile.TemporaryDirectory() as tmp:
        calls = []
        doc = dict(ok=True, entry=dict(name='isle-content-host', install_plan=dict(ok=True, steps=[])))
        fake = types.SimpleNamespace(get_json=lambda url, resolve=None: doc)
        code = sm.default_deploy('isle-content-host', ['--staged', '--bind', 'ISLE_DATASET_PATH=/x'], http=fake,
                                 env=dict(ISLE_OPERATION_DIR=tmp),
                                 install=lambda *a, **k: calls.append((a, k)) or 0)
        check('default deploy calls install in-process, staged, with the bind',
              code == 0 and calls == [((str(Path(tmp, 'catalog.json')), 'isle-content-host'),
                                       dict(staged=True, binds=['ISLE_DATASET_PATH=/x']))])
        check('default deploy writes the catalog document for install',
              json.loads(Path(tmp, 'catalog.json').read_text()) == doc)
        check('default deploy install env: catalog.json mode 0600',
              oct(Path(tmp, 'catalog.json').stat().st_mode & 0o777) == '0o600')
        calls.clear()
        sm.default_deploy('isle-content-host', ['--staged'], http=fake, env=dict(ISLE_OPERATION_DIR=tmp),
                          install=lambda *a, **k: calls.append(k) or 0)
        check('default deploy passes no binds when none given', calls == [dict(staged=True, binds=[])])
        calls.clear()
        sm.default_deploy('isle-content-host', ['--staged'], http=fake, env=dict(ISLE_OPERATION_DIR=tmp),
                          install=lambda *a, **k: calls.append(k) or 0, served='apt.isle')
        check('default deploy forwards the served (placement) domain to install', calls == [dict(staged=True, binds=[], served='apt.isle')])
        bad = types.SimpleNamespace(get_json=lambda url, resolve=None: dict(ok=False))
        try:
            sm.default_deploy('a', [], http=bad, env=dict(ISLE_OPERATION_DIR=tmp), install=lambda *a, **k: 0)
            check('default deploy refuses a missing catalog entry', False)
        except ValueError as error:
            check('default deploy refuses a missing catalog entry', 'no such catalog entry' in str(error))
    with scenario() as s:
        del s.routes[API + '/api/islemesh/catalog/isle-content-host']
        s.deploy = sm.default_deploy
        s.go()
        refused('release not published (catalog 404, default deploy wired)', s, name, 'not published')
    # pol-core 2026-11-14: the staged host was registered as isle-content-host.isle only, so apt.isle had no server
    # block and no cert at the target.  With the default deploy wired, install() must get served=block.domain.
    import catalog_artifacts
    saved = catalog_artifacts.install
    got = []
    try:
        catalog_artifacts.install = lambda *a, **k: got.append(k) or 0
        with scenario() as s:
            s.routes[API + '/api/islemesh/catalog/isle-content-host'] = dict(ok=True, entry=dict(
                name='isle-content-host', install_plan=s.routes[API + '/api/islemesh/catalog/isle-content-host']['entry']['install_plan']))
            s.deploy = sm.default_deploy
            os.environ['ISLE_OPERATION_DIR'] = s.opdir    # default_deploy reads the real environment, as under operations.py
            s.go()
            check('default deploy wired: install gets served=apt.isle (the placement domain) and the dataset',
                  got and got[0].get('served') == 'apt.isle' and got[0].get('dataset') == DATASET and got[0].get('staged') is True)
    finally:
        catalog_artifacts.install = saved
        os.environ.pop('ISLE_OPERATION_DIR', None)
    text = (HERE / 'catalog_artifacts.py').read_text()
    check('install: the served domain overrides <instance>.isle and the registry domain, and is still validated',
          0 < text.index('if served:domain=served') - text.index("registry.json')") and
          text.index('if served:domain=served') < text.index("raise ValueError('Invalid registered domain')"))


def stage8_checks():
    name = 'verifying-readiness'
    with scenario() as s:
        s.local_files['b/two.deb'] = b'tampered bytes'
        s.go()
        refused('sample mismatch', s, name, 'b/two.deb')
        check('sample mismatch: readinessVerified false but staged deploy recorded',
              s.receipt['readinessVerified'] is False and s.receipt['stagedDeploy'] is True)
    with scenario() as s:
        s.served_current = 'd' * 64
        s.go()
        refused('served current differs', s, name, 'contentSha')
    with scenario() as s:
        s.readiness_down = True
        s.go()
        refused('host never ready', s, name, '60 seconds', retryable=True)
        check('not-ready waits on the injected clock only', s.clock[0] >= 60)
    with scenario() as s:
        calls = {'n': 0}
        original = s.http.get_bytes

        def flaky(url, resolve=None, max_bytes=None):
            if resolve == ('apt.isle', MY_ADDR):
                calls['n'] += 1
                if calls['n'] < 3:
                    raise sm.HttpError('starting', None)
            return original(url, resolve, max_bytes)
        s.http.get_bytes = flaky
        s.go()
        check('host that becomes ready within 60 s passes', s.rc == 0 and s.clock[0] > 0)
    # pol-core 2026-11-14 (phase A op 7366856f): the host cannot reach its own macvlan child, so readiness is probed
    # via the agent's isle-agent-net bridge; the receipt keeps the Isle-facing address and records the probe target.
    with scenario() as s:
        s.probe_address = lambda: '172.20.0.2'
        s.ready_addr = '172.20.0.2'
        s.go()
        st = s.receipt['stages'].get(name, {})
        check('readiness probes via the agent bridge and records address + probeAddress',
              s.rc == 0 and st.get('address') == MY_ADDR and st.get('probeAddress') == '172.20.0.2')
        check('readiness via the bridge: every readiness fetch resolved to the bridge, none to the macvlan address',
              not [c for c in s.http.calls if c[1] == ('apt.isle', MY_ADDR)])
    with scenario() as s:
        s.go()
        check('no verified bridge: readiness probes the agent address (probeAddress == address)',
              s.rc == 0 and s.receipt['stages'][name].get('probeAddress') == MY_ADDR)
    with scenario() as s:
        s.probe_address = lambda: 'not-an-ip'
        s.go()
        refused('readiness probe address not an IP', s, name, 'not an IP address', retryable=True)
    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / 'docker'
        saved_path = os.environ['PATH']
        os.environ['PATH'] = tmp + ':' + saved_path
        try:
            for driver, ip, rc, want in (('bridge', '172.20.0.2', 0, '172.20.0.2'), ('macvlan', '172.20.0.2', 0, None),
                                         ('bridge', '', 1, None), ('bridge', '127.0.0.1', 0, None), ('bridge', 'junk', 0, None)):
                fake.write_text('#!/bin/sh\nif [ "$1" = network ]; then echo %s; exit 0; fi\necho %s; exit %d\n' % (driver, ip, rc))
                fake.chmod(0o755)
                check('bridge address lookup: driver %s, inspect %r rc %d -> %r' % (driver, ip, rc, want),
                      sm.default_bridge_address() == want)
        finally:
            os.environ['PATH'] = saved_path


# ------------------------------------------------------------------ curl and helpers

class FakePopen:
    seen = []

    def __init__(self, argv, stdout=None, stderr=None):
        FakePopen.seen.append(argv)
        chunks = iter([b'payload', b''])
        self.stdout = types.SimpleNamespace(read=lambda n: next(chunks))
        self.stderr = types.SimpleNamespace(read=lambda: b'')
        self.code = 0

    def wait(self):
        return self.code

    def kill(self):
        pass


def curl_checks():
    curl = sm.CurlHttp(popen=FakePopen)
    data = curl.get_bytes('https://apt.isle/snapshot/manifests/x.json', resolve=('apt.isle', '10.1.2.3'), max_bytes=1000)
    argv = FakePopen.seen[-1]
    check('curl: returns the body', data == b'payload')
    check('curl: required flags present', all(f in argv for f in ('--fail', '--silent', '--show-error', '--max-redirs', '--cacert', '--max-time'))
          and argv[argv.index('--max-redirs') + 1] == '0' and argv[argv.index('--cacert') + 1] == '/etc/isle-mesh/ca/isle-root.crt')
    check('curl: never -k, resolve pinned, url last', '-k' not in argv and '--insecure' not in argv
          and argv[argv.index('--resolve') + 1] == 'apt.isle:443:10.1.2.3' and argv[-1].startswith('https://apt.isle/'))
    for label, url, resolve in (('bare IPv4', 'https://10.1.2.3/x', None), ('bare IPv6', 'https://[::1]/x', None),
                                ('http', 'http://apt.isle/x', None), ('non-isle name', 'https://example.com/x', None),
                                ('userinfo', 'https://u:p@apt.isle/x', None), ('odd port', 'https://apt.isle:8443/x', None),
                                ('resolve to a hostname', 'https://apt.isle/x', ('apt.isle', 'evil.example')),
                                ('resolve for another host', 'https://apt.isle/x', ('b.isle', '10.1.1.1'))):
        try:
            curl.get_bytes(url, resolve=resolve)
            check('curl refuses ' + label, False)
        except ValueError:
            check('curl refuses ' + label, True)
    try:
        curl.get_bytes('https://apt.isle/x', max_bytes=3)
        check('curl enforces max_bytes', False)
    except sm.HttpError:
        check('curl enforces max_bytes', True)
    seen = {}
    fake_run = lambda argv, input=None, capture_output=None: seen.update(argv=argv, input=input) or types.SimpleNamespace(
        returncode=0, stdout=b'ok\n404', stderr=b'')
    status, text = sm.CurlHttp(run=fake_run).post_json(API + '/api/islemesh/storage/receipt', {'a': 1}, 'SECRET')
    check('curl post: status parsed and token only on stdin', status == 404 and 'SECRET' not in ' '.join(seen['argv'])
          and b'SECRET' in seen['input'] and '--fail' not in seen['argv'])


def helper_checks():
    doc = {'entries': [dict(path=p, kind='file', size=1, sha256='a' * 64) for p in ('c', 'a', 'b', 'e', 'd')]
           + [dict(path='dir', kind='directory')]}
    check('samples: sorted, indices 0, n//2, n-1', [e['path'] for e in sm.sample_entries(doc)] == ['a', 'c', 'e'])
    check('samples: small manifests give fewer, no duplicates',
          [e['path'] for e in sm.sample_entries({'entries': [dict(path='x', kind='file')]})] == ['x']
          and sm.sample_entries({'entries': []}) == [])
    check('object url percent-encodes the path once', sm.object_url('apt.isle', 'a b/c#d') == 'https://apt.isle/a%20b/c%23d')
    check("object url encodes a literal '%' as %25 (a dpkg epoch file; the core's apt.isle serves .../bsdutils_1%253a... with 200)",
          sm.object_url('apt.isle', 'bsdutils_1%3a2.39_amd64.deb') == 'https://apt.isle/bsdutils_1%253a2.39_amd64.deb')
    check('release_sha reads the catalog entry shape', sm.release_sha(dict(entry=dict(install_plan=dict(release=dict(sha256='ab'))))) == 'ab'
          and sm.release_sha({}) is None and sm.release_sha(dict(entry=dict(install_plan={}))) is None)


def shell_checks():
    script = HERE / 'storage.sh'
    env = {k: v for k, v in os.environ.items() if k != 'ISLE_OPERATION_ID'}
    if os.geteuid() != 0:
        out = subprocess.run(['bash', str(script), 'migrate', '--dataset', DATASET], env=env, capture_output=True, text=True)
        check('storage.sh migrate without an operation id routes to operations.py', 'operation:' in out.stderr and out.returncode == 1)
    else:
        check('storage.sh routing to operations.py (skipped as root)', True)
    env['ISLE_OPERATION_ID'] = 'a' * 32
    out = subprocess.run(['bash', str(script), 'migrate', '--dataset', DATASET], env=env, capture_output=True, text=True)
    check('storage.sh migrate inside an operation runs storage_migrate.py', 'storage migrate:' in out.stderr and out.returncode == 2)
    out = subprocess.run(['bash', str(script), 'status', '--dataset', 'nope-not-there'], env=env, capture_output=True, text=True)
    check('storage.sh other verbs still reach storage_cli.py', 'storage migrate:' not in out.stderr)


def main():
    for group in (happy_checks, stage1_checks, stage2_checks, stage3_checks, stage4_checks, systemctl_checks, lease_checks,
                  stage7_checks, stage8_checks, curl_checks, helper_checks, shell_checks):
        group()
    print('\n%d checks, %d failed' % (COUNT[0], len(FAILED)))
    for name in FAILED:
        print('  FAILED: ' + name)
    sys.exit(1 if FAILED else 0)


if __name__ == '__main__':
    main()
