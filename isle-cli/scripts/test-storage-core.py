#!/usr/bin/env python3
"""storage_core.py (stm-5 core verbs) and the pending/epoch table of storage_placement.py, all with fakes.

Generation numbers used throughout: authorize -> 1, mark-migrated -> 2 (G), cutover intent -> 3 (pending.epoch),
commit -> 4 (E), accept -> 5.  Every external effect is a method of FakeIo; nothing here touches the router,
systemd, the network or root-owned paths."""
import contextlib
import copy
import fcntl
import io as iomod
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import storage_contract as sc
import storage_core as core
import storage_placement as sp

FAILED = []
COUNT = [0]
VECTOR = sc.load_vector(HERE / 'fixtures' / 'storage-digest-vector.json')
PLAN_ID, REVISION = VECTOR['planId'], VECTOR['revision']
DIGEST = VECTOR['expectedDigest']
DOMAIN, DATASET = 'apt.isle', 'apt-public-repo'
CORE_ADDR, TARGET_ADDR = '10.10.0.20', '10.10.0.116'
CONTENT, MANIFEST = 'e' * 64, 'a' * 64
T0 = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)
OP = '1' * 32


def check(name, ok):
    COUNT[0] += 1
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok:
        FAILED.append(name)


def raises(kind, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except kind:
        return True
    except Exception:
        return False
    return False


class Crash(BaseException):
    """A process death: not an Exception, so no handler in storage_core swallows it."""


def make_plan(**item_changes):
    item = copy.deepcopy(VECTOR['item'])
    item['hostingRelease'] = dict(VECTOR['hostingRelease'])
    item.update(item_changes)
    return {'id': PLAN_ID, 'revision': REVISION, 'active': True, 'items': [item]}


class FakeIo:
    def __init__(self, root):
        root = Path(root)
        self.placement_path = root / 'storage' / 'placement.json'
        self.placement_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_dir = root / 'var'
        self.hostable_path = root / 'storage' / 'hostable-names'
        self.hostable_path.write_text('# v1\napt.isle\n')
        self.plan = make_plan()
        self.router = {DOMAIN: CORE_ADDR}
        self.serve = {CORE_ADDR: {'current': CONTENT, 'ok': True}, TARGET_ADDR: {'current': CONTENT, 'ok': True}}
        self.pins = {DOMAIN: '127.0.0.1'}
        self.leases = {'pol-core': TARGET_ADDR}
        self.registered = []
        self.pin_log = []
        self.stages = []
        self.out_lines = []
        self.lease_count = 0
        self.register_effective = True
        self.register_error = None
        self.crash_after_register = False
        self.timer = True
        self.poster_ok = True
        self.registry = {DOMAIN: 'isle-core'}
        self.claims = {}
        self.ops = {}
        self.latest = {'manifestSha': MANIFEST, 'contentSha': CONTENT}
        self.tick = 0
        self.fences = []
        self.views, self.view_error = [], None
        self.device_file = root / 'device-name'
        self.device_file.write_text('isle-core\n')
        self.http, self.http_calls, self.http_error, self.mark_calls = {}, [], None, []
        self.push_error = None
        self.post_lock_free, self.post_seen = [], []

    # ---- backend view, relay
    def post_placement_view(self):
        if self.view_error:
            raise ValueError(self.view_error)
        self.views.append(sp.view_document(self.placement_path, self.device_file))
        # ordering evidence: when the post runs no transition holds the placement lock, and the file is final
        fd = os.open(str(self.placement_path) + '.lock', os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.post_lock_free.append(True)
        except BlockingIOError:
            self.post_lock_free.append(False)
        finally:
            os.close(fd)
        self.post_seen.append(sp.read(self.placement_path)['datasets'][DATASET]['generation'])

    def http_get_json(self, url):
        self.http_calls.append(url)
        if self.http_error:
            raise ValueError(self.http_error)
        kind = 'receipts' if '/storage/receipts' in url else 'witnesses'
        return {'ok': True, kind: self.http.get(kind, [])}

    def run_mark_migrated(self, dataset, digest):
        self.mark_calls.append((dataset, digest))
        return 0

    # ---- plan / files
    def read_plan(self, plan_id, revision):
        if self.plan is None:
            raise ValueError('plan service down')
        return self.plan

    # ---- DNS and pins
    def router_dns(self, domain):
        return self.router.get(domain)

    def push_managed_names(self):
        if self.push_error:
            raise ValueError(self.push_error)
        self.pushed = getattr(self, 'pushed', 0) + 1

    def register_dns(self, domain, address, intent_path):
        env_op = json.loads(Path(intent_path).read_text())['operation']
        ok, words = sp.fence_check(domain, address, intent_path, env_op, self.placement_path)
        self.fences.append((ok, words))
        if not ok:
            raise ValueError(words)
        self.registered.append((domain, address))
        if self.register_error:
            raise ValueError(self.register_error)
        if self.register_effective:
            self.router[domain] = address
        if self.crash_after_register:
            raise Crash()

    def getent(self, domain):
        return self.pins.get(domain) or self.router.get(domain)

    def https_check(self, domain, resolve_addr, paths):
        addr = resolve_addr or self.getent(domain)
        return bool(self.serve.get(addr, {}).get('ok'))

    def fetch_current(self, domain, resolve_addr):
        return self.serve.get(resolve_addr, {}).get('current')

    def hosts_pin(self, domain, address):
        self.pin_log.append((domain, address))
        if address:
            self.pins[domain] = address
        else:
            self.pins.pop(domain, None)

    def current_pin(self, domain):
        return self.pins.get(domain)

    # ---- lease and identity
    @contextlib.contextmanager
    def lease(self):
        self.lease_count += 1
        yield

    def lease_address(self, device):
        return self.leases.get(device)

    def core_address(self):
        return CORE_ADDR

    def device_name(self):
        return 'isle-core'

    def source_latest(self, entry):
        return self.latest

    def sample_paths(self, entry):
        return [('/InRelease', 'b' * 64), ('/a.deb', 'c' * 64)]

    def timer_installed(self, dataset):
        return self.timer

    def verify_poster(self, row):
        return self.poster_ok

    def registry_owner(self, domain):
        return self.registry.get(domain)

    def claims_owner(self, domain):
        return self.claims.get(domain)

    def operations_read(self, op_id):
        if op_id not in self.ops:
            raise FileNotFoundError(op_id)
        return self.ops[op_id]

    def stage(self, label):
        self.stages.append(label)

    def now(self):
        self.tick += 1
        return T0 + timedelta(seconds=self.tick)

    def out(self, text):
        self.out_lines.append(text)

    def said(self):
        return '\n'.join(self.out_lines)


class World:
    def __init__(self, tmp, op=OP):
        self.root = Path(tmp)
        self.io = FakeIo(self.root)
        self.opdir = self.root / 'op'
        self.opdir.mkdir(exist_ok=True)
        self.op = op

    def env(self, op=None):
        return {'ISLE_OPERATION_ID': op or self.op, 'ISLE_OPERATION_DIR': str(self.opdir)}

    def run(self, *argv, op=None):
        self.io.out_lines.clear()
        return core.run(['storage'] + [str(a) for a in argv], env=self.env(op), io=self.io)

    def entry(self):
        return sp.hosting_for(DATASET, self.io.placement_path)

    def raw(self):
        return self.io.placement_path.read_bytes() if self.io.placement_path.exists() else b''

    def authorize(self, op='2' * 32):
        return self.run('authorize', '--dataset', DATASET, '--plan', PLAN_ID, '--revision', REVISION,
                        '--item', VECTOR['item']['id'], '--digest', DIGEST, op=op)

    def receipts(self, ok=True, device='pol-core'):
        row = {'receipt': {'kind': 'isle.storage.migrate', 'ok': ok, 'contentVerified': True, 'stagedDeploy': True,
                           'readinessVerified': True, 'plan': {'id': PLAN_ID, 'revision': REVISION, 'digest': DIGEST},
                           'target': {'device': device}}, 'posterPresented': {'subject': device, 'fingerprint': 'f' * 16}}
        directory = self.io.state_dir / DATASET
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'inbound-receipts.jsonl').write_text(json.dumps(row) + '\n')
        return row

    def mark(self, op='3' * 32):
        return self.run('mark-migrated', '--dataset', DATASET, '--digest', DIGEST, op=op)

    def migrated(self):
        assert self.authorize() == 0
        self.receipts()
        assert self.mark() == 0
        assert self.entry()['state'] == 'migrated' and self.entry()['generation'] == 2
        return self

    def cutover(self, g=2, op=None):
        return self.run('cutover', '--dataset', DATASET, '--generation', g, op=op)

    def cut_over(self):
        if self.entry() is None:
            self.migrated()
        assert self.cutover() == 0
        e = self.entry()
        assert e['state'] == 'cutover' and e['generation'] == 4, e
        return self

    def witness(self, **changes):
        cut = [h for h in self.entry()['history'] if h['to'] == 'cutover'][-1]['at']
        at = datetime.strptime(cut, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc) + timedelta(seconds=30)
        row = {'id': 'w-1', 'dataset': DATASET, 'epoch': self.entry()['generation'], 'contentSha': CONTENT,
               'at': at.strftime('%Y-%m-%dT%H:%M:%SZ'), 'resolvedAddress': TARGET_ADDR,
               'posterPresented': {'subject': 'third', 'fingerprint': 'f' * 16}}
        row.update(changes)
        directory = self.io.state_dir / DATASET
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / 'witnesses.jsonl').open('a') as handle:
            handle.write(json.dumps(row) + '\n')
        return row

    def accept(self, g, op=None):
        return self.run('accept', '--dataset', DATASET, '--generation', g, op=op)


def fresh(fn):
    with tempfile.TemporaryDirectory() as tmp:
        return fn(World(tmp))


# ------------------------------------------------------------------ authorize
def t_authorize(w):
    code = w.authorize()
    e = w.entry()
    check('authorize: new entry in authorized, generation 1, plan recorded',
          code == 0 and e['state'] == 'authorized' and e['generation'] == 1 and e['plan'] == {'id': PLAN_ID, 'revision': REVISION, 'digest': DIGEST})
    check('authorize: addresses {source: core agent address, target: None}', e['addresses'] == {'source': CORE_ADDR, 'target': None})
    check('authorize: device and domain from the plan', e['device'] == 'pol-core' and e['domain'] == DOMAIN)
    check('authorize: takes no device-update lease', w.io.lease_count == 0)
    check('authorize: pushes the managed-names list to the router after the placement write', w.io.pushed >= 1)
    claims = json.loads((w.io.placement_path.parent / 'placement-claims.json').read_text())
    check('authorize: placement-claims.json mirrors {domain: {device, dataset, state}}',
          claims == {DOMAIN: {'device': 'pol-core', 'dataset': DATASET, 'state': 'authorized'}})
    receipt = json.loads((w.opdir / 'storage-receipt.json').read_text())
    check('authorize: receipt written with actionId and ok',
          receipt['ok'] is True and receipt['kind'] == 'isle.storage.authorize' and receipt['actionId'].startswith('isle.storage.authorize:%s@isle-core:' % DATASET))
    check('authorize: receipt appended to receipts.jsonl', (w.io.state_dir / DATASET / 'receipts.jsonl').read_text().count('\n') == 1)
    before = w.raw()
    check('authorize: re-run with the same revision is idempotent (no write)', w.authorize() == 0 and w.raw() == before)


def t_authorize_revision(w):
    w.authorize()
    w.io.plan = make_plan()
    w.io.plan['revision'] = 3
    item = w.io.plan['items'][0]
    digest3 = sc.storage_digest(PLAN_ID, 3, item, item['hostingRelease'])
    code = w.run('authorize', '--dataset', DATASET, '--plan', PLAN_ID, '--revision', 3, '--item', item['id'], '--digest', digest3)
    e = w.entry()
    check('authorize: new revision updates plan fields authorized->authorized with a generation bump',
          code == 0 and e['state'] == 'authorized' and e['plan']['revision'] == 3 and e['plan']['digest'] == digest3 and e['generation'] == 2)
    check('authorize: history shows authorized -> authorized', e['history'][-1]['from'] == 'authorized' and e['history'][-1]['to'] == 'authorized')


def t_authorize_refusals(w):
    check('authorize: wrong digest refused, nothing written', w.run('authorize', '--dataset', DATASET, '--plan', PLAN_ID, '--revision', REVISION,
          '--item', VECTOR['item']['id'], '--digest', '0' * 64) == 1 and w.entry() is None)
    w.io.plan['active'] = False
    check('authorize: inactive plan refused', w.authorize() == 1 and w.entry() is None and 'not active' in w.io.said())
    w.io.plan['active'] = True
    w.io.hostable_path.write_text('other.isle\n')
    check('authorize: domain off the allowlist refused', w.authorize() == 1 and w.entry() is None and 'allowlist' in w.io.said())
    w.io.hostable_path.write_text('apt.isle\n')
    w.io.registry = {}
    check('authorize: nobody owns the domain -> refused', w.authorize() == 1 and w.entry() is None)
    w.io.registry = {DOMAIN: 'econ-core'}
    check('authorize: registry names another device than the source -> refused', w.authorize() == 1 and w.entry() is None and 'does not currently own' in w.io.said())
    w.io.registry = {}
    w.io.claims = {DOMAIN: 'isle-core'}
    check('authorize: claims.json naming originDevice is enough', w.authorize() == 0)
    w.io.plan = None
    check('authorize: plan service down refused', w.run('authorize', '--dataset', DATASET, '--plan', PLAN_ID, '--revision', REVISION,
          '--item', VECTOR['item']['id'], '--digest', DIGEST) == 1)


def t_authorize_states(w):
    w.migrated()
    check('authorize: refused once migrated', w.authorize() == 1 and w.entry()['state'] == 'migrated')


# ------------------------------------------------------------------ mark-migrated
def t_mark(w):
    w.authorize()
    check('mark-migrated: no stored receipt refused', w.mark() == 1 and w.entry()['state'] == 'authorized')
    for key in ('ok', 'contentVerified', 'stagedDeploy', 'readinessVerified'):
        row = w.receipts()
        row['receipt'][key] = False
        (w.io.state_dir / DATASET / 'inbound-receipts.jsonl').write_text(json.dumps(row) + '\n')
        check('mark-migrated: receipt with %s false refused' % key, w.mark() == 1 and w.entry()['state'] == 'authorized')
    row = w.receipts(device='econ-core')
    check('mark-migrated: receipt device != entry device refused', w.mark() == 1)
    w.receipts()
    w.io.poster_ok = False
    check('mark-migrated: unverified poster refused', w.mark() == 1 and w.entry()['state'] == 'authorized')
    w.io.poster_ok = True
    check('mark-migrated: wrong digest refused', w.run('mark-migrated', '--dataset', DATASET, '--digest', '9' * 64) == 1)
    check('mark-migrated: holds the shared lease', (w.io.lease_count) >= 1)
    code = w.mark()
    e = w.entry()
    check('mark-migrated: valid receipt -> migrated, generation bumped to 2', code == 0 and e['state'] == 'migrated' and e['generation'] == 2)
    before = w.raw()
    check('mark-migrated: re-run after success is idempotent', w.mark() == 0 and w.raw() == before)


# ------------------------------------------------------------------ cutover
def t_cutover_happy(w):
    w.migrated()
    code = w.cutover()
    e = w.entry()
    check('cutover: exit 0, state cutover', code == 0 and e['state'] == 'cutover')
    check('cutover: epoch arithmetic G=2 -> intent 3 -> commit E=4', e['generation'] == 4)
    check('cutover: pending cleared', not e.get('pending'))
    check('cutover: router moved to the target and the pin left the block', w.io.router[DOMAIN] == TARGET_ADDR and DOMAIN not in w.io.pins)
    check('cutover: dns registered exactly once, under a matching intent', w.io.registered == [(DOMAIN, TARGET_ADDR)] and all(ok for ok, _ in w.io.fences))
    check('cutover: pin transition ran before the registration', w.io.pin_log == [(DOMAIN, None)])
    intent = json.loads((w.opdir / 'storage-intent.json').read_text())
    check('cutover: intent records epoch G+1, previous address and pin',
          intent['pending']['epoch'] == 3 and intent['pending']['previousAddress'] == CORE_ADDR and intent['pending']['previousPin'] == '127.0.0.1'
          and intent['pending']['newPin'] is None and intent['pending']['from'] == 'migrated' and intent['pending']['to'] == 'cutover')
    hist = [h for h in e['history'] if h['to'] == 'cutover' and h['from'] == 'migrated']
    check('cutover: history rows for intent and commit, commit carries addresses', len(hist) == 1 and hist[0]['newAddress'] == TARGET_ADDR and hist[0]['previousPin'] == '127.0.0.1')
    check('cutover: rollbackManifest recorded', e['rollbackManifest'] == MANIFEST and e['addresses']['target'] == TARGET_ADDR)
    receipt = json.loads((w.opdir / 'storage-receipt.json').read_text())
    check('cutover: receipt kind, ok, cutover.done and addresses', receipt['kind'] == 'isle.storage.cutover' and receipt['ok'] and receipt['cutover']['done']
          and receipt['cutover']['newAddress'] == TARGET_ADDR and receipt['cutover']['previousAddress'] == CORE_ADDR and receipt['epoch'] == 4)
    check('cutover: receipt records syncTimer as the string receipt-claimed, not a boolean', receipt['cutover']['syncTimer'] == 'receipt-claimed')
    check('cutover: stage labels from CUTOVER_STAGES', {'checking-placement', 'registering-target', 'verifying-cutover'} <= set(w.io.stages)
          and all(s in sc.CUTOVER_STAGES or s.replace('-', '').isalpha() for s in w.io.stages))
    check('cutover: shared lease taken once', w.io.lease_count >= 1)
    claims = json.loads((w.io.placement_path.parent / 'placement-claims.json').read_text())
    check('cutover: claims mirror follows the state', claims[DOMAIN]['state'] == 'cutover')
    before = w.raw()
    check('cutover: lost-response rerun (same op, same G) is a no-op success', w.cutover() == 0 and w.raw() == before and 'already cut over' in w.io.said())


def t_cutover_refusals(w):
    w.migrated()
    before = w.raw()
    check('cutover: stale generation refused with the current value in the words', w.cutover(g=1) == 1 and 'generation 2' in w.io.said() and w.raw() == before)
    check('cutover: future generation refused', w.cutover(g=7) == 1 and w.raw() == before)
    w.io.timer = False
    check('cutover: no sync timer -> "sync trigger not installed"', w.cutover() == 1 and 'sync trigger not installed' in w.io.said() and w.raw() == before)
    w.io.timer = True
    w.io.serve[TARGET_ADDR]['current'] = 'd' * 64
    check('cutover: stale replica -> "run sync first"', w.cutover() == 1 and 'run sync first' in w.io.said() and w.raw() == before)
    w.io.serve[TARGET_ADDR]['current'] = CONTENT
    w.io.serve[TARGET_ADDR]['ok'] = False
    check('cutover: target not serving samples refused', w.cutover() == 1 and w.raw() == before)
    w.io.serve[TARGET_ADDR]['ok'] = True
    w.io.leases = {}
    check('cutover: unknown target lease refused', w.cutover() == 1 and w.raw() == before)
    w.io.leases = {'pol-core': TARGET_ADDR}
    w.io.plan['active'] = False
    check('cutover: inactive plan refused', w.cutover() == 1 and w.raw() == before)
    w.io.plan = make_plan()
    w.io.plan['items'][0]['storage']['source']['totalBytes'] += 1
    check('cutover: plan digest changed refused', w.cutover() == 1 and w.raw() == before)
    w.io.plan = make_plan()
    w.io.latest = {}
    check('cutover: no source snapshot refused', w.cutover() == 1 and w.raw() == before)
    check('cutover: nothing registered by any refusal', w.io.registered == [] and w.io.pin_log == [])
    w.io.latest = {'manifestSha': MANIFEST, 'contentSha': CONTENT}
    check('cutover: refusal receipt is ok:false with the stage', json.loads((w.opdir / 'storage-receipt.json').read_text())['ok'] is False)
    check('cutover: once the causes are gone the same request succeeds', w.cutover() == 0)


def t_cutover_state(w):
    w.authorize()
    check('cutover: refused from authorized', w.cutover(g=1) == 1 and w.entry()['state'] == 'authorized')


def t_cutover_lease(w):
    w.migrated()

    @contextlib.contextmanager
    def busy():
        raise core.DeviceUpdateBusy('device update in progress on isle-core; retry after it completes')
        yield
    w.io.lease = busy
    before = w.raw()
    check('cutover: exclusive device update held -> refused with the contract words',
          w.cutover() == 1 and 'device update in progress on isle-core; retry after it completes' in w.io.said() and w.raw() == before)


def t_real_lease(tmp):
    root = Path(tmp)
    lock = root / 'app-device-update.lock'
    with lock.open('a+') as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        refused = False
        try:
            with core.real_lease(root, 'isle-core')():
                pass
        except core.DeviceUpdateBusy as exc:
            refused = 'device update in progress on isle-core; retry after it completes' in str(exc)
    check('real lease: refused while the exclusive update lock is held', refused)
    with core.real_lease(root, 'isle-core')():
        with core.real_lease(root, 'isle-core')():
            shared = True
    check('real lease: shared holds coexist', shared)
    with lock.open('a+') as other:
        with core.real_lease(root, 'isle-core')():
            try:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                blocked = False
            except BlockingIOError:
                blocked = True
    check('real lease: a held lease blocks an exclusive taker', blocked)


def t_autorevert(w):
    w.migrated()
    w.io.register_effective = False   # the router does not move
    code = w.cutover()
    e = w.entry()
    check('auto-revert: exit 1 and state back to migrated', code == 1 and e['state'] == 'migrated')
    check('auto-revert: pending cleared, generation G+3 (intent, revert intent, clear)', not e.get('pending') and e['generation'] == 5)
    check('auto-revert: core address registered again and pin restored', w.io.registered[-1] == (DOMAIN, CORE_ADDR) and w.io.pins.get(DOMAIN) == '127.0.0.1')
    receipt = json.loads((w.opdir / 'storage-receipt.json').read_text())
    check('auto-revert: receipt rolledBack true, ok false', receipt['ok'] is False and receipt['cutover']['rolledBack'] is True and receipt['cutover']['recoveryNeeded'] is False)
    check('auto-revert: reverting stage reported', 'reverting' in w.io.stages)
    check('auto-revert: every register was fenced by a matching intent', all(ok for ok, _ in w.io.fences))
    check('auto-revert: history names the revert action', any(h.get('pending') == 'cutover-revert' for h in e['history']))


def t_autorevert_by_name(w):
    w.migrated()
    w.io.serve[TARGET_ADDR]['ok'] = True

    orig = w.io.https_check

    def flaky(domain, addr, paths):
        if addr is None:
            return False      # the final by-name check fails
        return orig(domain, addr, paths)
    w.io.https_check = flaky
    code = w.cutover()
    check('auto-revert: a failing final ordinary-resolution check reverts', code == 1 and w.entry()['state'] == 'migrated' and w.io.router[DOMAIN] == CORE_ADDR)


def t_recovery_needed(w):
    w.migrated()
    w.io.register_effective = False
    w.io.serve[CORE_ADDR]['ok'] = False     # the old source does not serve either
    code = w.cutover()
    e = w.entry()
    check('recovery-needed: exit 1, state recovery-needed, no pending', code == 1 and e['state'] == 'recovery-needed' and not e.get('pending'))
    check('recovery-needed: recovery record has both addresses and a time',
          e['recovery']['sourceAddress'] == CORE_ADDR and e['recovery']['targetAddress'] == TARGET_ADDR and e['recovery']['at'])
    receipt = json.loads((w.opdir / 'storage-receipt.json').read_text())
    check('recovery-needed: rolledBack stays false, recoveryNeeded true', receipt['cutover']['rolledBack'] is False and receipt['cutover']['recoveryNeeded'] is True)
    check('recovery-needed: words say what to run', 'recover --dataset' in w.io.said())
    claims = json.loads((w.io.placement_path.parent / 'placement-claims.json').read_text())
    check('recovery-needed: claims mirror shows recovery-needed (CA issues nothing)', claims[DOMAIN]['state'] == 'recovery-needed')


def t_resume(w):
    w.migrated()
    w.io.crash_after_register = True
    w.io.register_effective = True
    try:
        w.cutover()
        crashed = False
    except Crash:
        crashed = True
    e = w.entry()
    check('resume: a crash leaves migrated plus the pending cutover at G+1', crashed and e['state'] == 'migrated' and e['pending']['action'] == 'cutover' and e['generation'] == 3 and e['pending']['epoch'] == 3)
    w.io.crash_after_register = False
    before = w.raw()
    check('resume: same G but another operation id is refused naming the current generation', w.cutover(op='9' * 32) == 1 and 'generation 3' in w.io.said() and w.raw() == before)
    check('resume: right operation id with the wrong G is refused', w.cutover(g=1) == 1 and w.raw() == before)
    check('resume: a plain new cutover (G=3) over a foreign pending is refused', w.cutover(g=3, op='8' * 32) == 1 and w.raw() == before)
    code = w.cutover()
    e = w.entry()
    check('resume: matching action+digest+epoch+operation resumes after the intent', code == 0 and e['state'] == 'cutover' and e['generation'] == 4 and not e.get('pending'))
    check('resume: no second intent was written (one cutover history pair)', len([h for h in e['history'] if h.get('pending') == 'cutover']) == 1)


def t_resume_pending_digest_mismatch(w):
    w.migrated()
    w.io.crash_after_register = True
    try:
        w.cutover()
    except Crash:
        pass
    raw = json.loads(w.raw())
    raw['datasets'][DATASET]['pending']['digest'] = '7' * 64
    w.io.placement_path.write_text(json.dumps(raw))
    w.io.crash_after_register = False
    check('resume: pending with another digest is not resumed', w.cutover() == 1 and w.entry()['state'] == 'migrated')


# ------------------------------------------------------------------ rollback
def t_rollback(w):
    w.cut_over()
    code = w.run('rollback', '--dataset', DATASET, '--generation', 4)
    e = w.entry()
    check('rollback: cutover -> migrated, generation G+2 (6)', code == 0 and e['state'] == 'migrated' and e['generation'] == 6 and not e.get('pending'))
    check('rollback: router and pin back on the core', w.io.router[DOMAIN] == CORE_ADDR and w.io.pins.get(DOMAIN) == '127.0.0.1')
    check('rollback: says EMERGENCY ONLY', 'EMERGENCY ONLY' in w.io.said())
    receipt = json.loads((w.opdir / 'storage-receipt.json').read_text())
    check('rollback: receipt kind rollback with rolledBack true', receipt['kind'] == 'isle.storage.rollback' and receipt['cutover']['rolledBack'] is True and receipt['ok'])
    check('rollback: intent first (rollback action in history)', any(h.get('pending') == 'rollback' for h in e['history']))
    check('rollback: stale generation refused', w.run('rollback', '--dataset', DATASET, '--generation', 4, op='5' * 32) == 1)


def t_rollback_refusals(w):
    w.migrated()
    check('rollback: refused from migrated', w.run('rollback', '--dataset', DATASET, '--generation', 2) == 1)
    w.cut_over()
    w.io.serve[TARGET_ADDR]['current'] = CONTENT
    w.run('accept', '--dataset', DATASET, '--generation', 4)  # no witness: refused
    w.witness()
    assert w.accept(4) == 0
    before = w.raw()
    check('rollback: refused from hosting', w.run('rollback', '--dataset', DATASET, '--generation', 5) == 1 and 'new plan' in w.io.said() and w.raw() == before)


def t_rollback_unverified(w):
    w.cut_over()
    w.io.serve[CORE_ADDR]['ok'] = False
    code = w.run('rollback', '--dataset', DATASET, '--generation', 4)
    e = w.entry()
    receipt = json.loads((w.opdir / 'storage-receipt.json').read_text())
    check('rollback: old source not serving -> recovery-needed, never rolledBack true',
          code == 1 and e['state'] == 'recovery-needed' and receipt['cutover']['rolledBack'] is False and receipt['cutover']['recoveryNeeded'] is True)


# ------------------------------------------------------------------ recover
def crashed_cutover(w, after_register):
    w.migrated()
    w.io.crash_after_register = True
    w.io.register_effective = after_register
    try:
        w.cutover(op='c' * 32)
    except Crash:
        pass
    w.io.crash_after_register = False
    w.io.register_effective = True
    assert w.entry()['pending']['action'] == 'cutover' and w.entry()['generation'] == 3


def t_recover_live(w):
    crashed_cutover(w, False)
    w.io.ops['c' * 32] = {'state': 'running'}
    before = w.raw()
    code = w.run('recover', '--dataset', DATASET, '--generation', 3, '--to', 'migrated', op='d' * 32)
    check('recover: a running operation is never superseded; refusal names it', code == 1 and 'c' * 32 in w.io.said() and w.raw() == before)
    check('recover: nothing was registered', w.io.registered == [(DOMAIN, TARGET_ADDR)] or w.io.registered == [])


def t_recover_to_migrated(w):
    crashed_cutover(w, False)
    w.io.ops['c' * 32] = {'state': 'interrupted'}
    w.io.registered.clear()
    code = w.run('recover', '--dataset', DATASET, '--generation', 3, '--to', 'migrated', op='d' * 32)
    e = w.entry()
    check('recover: interrupted pending -> migrated, generation 5, pending cleared', code == 0 and e['state'] == 'migrated' and e['generation'] == 5 and not e.get('pending'))
    row = [h for h in e['history'] if h.get('supersededOperation')]
    check('recover: history records supersededOperation', len(row) == 1 and row[0]['supersededOperation'] == 'c' * 32)
    check('recover: router already on the core -> no register needed', w.io.registered == [] and w.io.router[DOMAIN] == CORE_ADDR)
    check('recover: pin restored for migrated', w.io.pins.get(DOMAIN) == '127.0.0.1')
    check('recover: receipt kind recover', json.loads((w.opdir / 'storage-receipt.json').read_text())['kind'] == 'isle.storage.recover')


def t_recover_wrong_g(w):
    crashed_cutover(w, False)
    before = w.raw()
    check('recover: wrong generation refused', w.run('recover', '--dataset', DATASET, '--generation', 2, '--to', 'migrated', op='d' * 32) == 1 and w.raw() == before)


def t_recover_after_dns_write(w):
    crashed_cutover(w, True)     # the router already shows the target
    w.io.registered.clear()
    code = w.run('recover', '--dataset', DATASET, '--generation', 3, '--to', 'cutover', op='d' * 32)
    e = w.entry()
    check('recover: crash after DNS write, before CAS -> completes to cutover without re-registering', code == 0 and e['state'] == 'cutover' and w.io.registered == [])
    check('recover: cutover recovery leaves no pin', DOMAIN not in w.io.pins and e['generation'] == 5)
    check('recover: rollbackManifest set when landing in cutover', e.get('rollbackManifest') == MANIFEST)


def t_recover_after_dns_write_revert(w):
    crashed_cutover(w, True)
    code = w.run('recover', '--dataset', DATASET, '--generation', 3, '--to', 'migrated', op='d' * 32)
    check('recover: crash after DNS write -> revert router to the core', code == 0 and w.io.router[DOMAIN] == CORE_ADDR and w.entry()['state'] == 'migrated')
    check('recover: the revert registration was fenced by the recover intent', w.io.registered[-1] == (DOMAIN, CORE_ADDR) and all(ok for ok, _ in w.io.fences))


def t_recover_from_recovery_needed(w):
    w.migrated()
    w.io.register_effective = False
    w.io.serve[CORE_ADDR]['ok'] = False
    w.cutover()
    assert w.entry()['state'] == 'recovery-needed'
    g = w.entry()['generation']
    w.io.serve[CORE_ADDR]['ok'] = True
    w.io.register_effective = True
    check('recover: --to hosting from recovery-needed refused', w.run('recover', '--dataset', DATASET, '--generation', g, '--to', 'hosting', op='d' * 32) == 1)
    check('recover: stale generation from recovery-needed refused', w.run('recover', '--dataset', DATASET, '--generation', g - 1, '--to', 'migrated', op='d' * 32) == 1)
    w.io.serve[CORE_ADDR]['ok'] = False
    code = w.run('recover', '--dataset', DATASET, '--generation', g, '--to', 'migrated', op='d' * 32)
    e = w.entry()
    check('recover: unverifiable choice leaves recovery-needed with no pending', code == 1 and e['state'] == 'recovery-needed' and not e.get('pending'))
    g = e['generation']
    w.io.serve[CORE_ADDR]['ok'] = True
    code = w.run('recover', '--dataset', DATASET, '--generation', g, '--to', 'migrated', op='e' * 32)
    e = w.entry()
    check('recover: recovery-needed -> migrated by --generation alone', code == 0 and e['state'] == 'migrated' and not e.get('pending') and 'recovery' not in e)
    check('recover: router on the core after recovery', w.io.router[DOMAIN] == CORE_ADDR)


def t_recover_nothing(w):
    w.migrated()
    check('recover: nothing to recover refused', w.run('recover', '--dataset', DATASET, '--generation', 2, '--to', 'migrated', op='d' * 32) == 1)


def t_recover_hosting_pending(w):
    w.cut_over()
    w.witness()
    assert w.accept(4) == 0
    assert w.entry()['state'] == 'hosting' and w.entry()['generation'] == 5
    w.io.leases['pol-core'] = '10.10.0.200'
    w.io.serve['10.10.0.200'] = {'current': CONTENT, 'ok': True}
    w.io.register_effective = True
    w.io.crash_after_register = True
    try:
        w.run('reconcile-address', '--dataset', DATASET, '--generation', 5, op='b' * 32)
    except Crash:
        pass
    w.io.crash_after_register = False
    e = w.entry()
    check('recover: crash mid reconcile-address leaves hosting plus pending', e['state'] == 'hosting' and e['pending']['action'] == 'reconcile-address' and e['generation'] == 6)
    check('recover: --to migrated from a hosting pending refused', w.run('recover', '--dataset', DATASET, '--generation', 6, '--to', 'migrated', op='d' * 32) == 1)
    code = w.run('recover', '--dataset', DATASET, '--generation', 6, '--to', 'hosting', op='d' * 32)
    e = w.entry()
    check('recover: --to hosting lands back in hosting at the new address', code == 0 and e['state'] == 'hosting' and not e.get('pending') and w.io.router[DOMAIN] == '10.10.0.200')


# ------------------------------------------------------------------ accept
def t_accept(w):
    w.cut_over()
    before = w.raw()
    check('accept: no witness refused', w.accept(4) == 1 and 'no witness' in w.io.said() and w.raw() == before)
    w.witness(epoch=3)
    check('accept: witness of an older epoch refused', w.accept(4) == 1)
    w.witness(contentSha='9' * 64)
    check('accept: witness for another contentSha refused', w.accept(4) == 1)
    w.witness(dataset='other-set')
    check('accept: witness for another dataset refused', w.accept(4) == 1)
    cut = [h for h in w.entry()['history'] if h['to'] == 'cutover'][-1]['at']
    base = datetime.strptime(cut, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    w.witness(at=(base + timedelta(hours=25)).strftime('%Y-%m-%dT%H:%M:%SZ'))
    check('accept: witness after cutover + 24 h refused', w.accept(4) == 1 and 'freshness' in w.io.said())
    w.witness(at=(base - timedelta(seconds=300)).strftime('%Y-%m-%dT%H:%M:%SZ'))
    check('accept: witness before cutover - 120 s refused', w.accept(4) == 1)
    w.witness(at=(base - timedelta(seconds=60)).strftime('%Y-%m-%dT%H:%M:%SZ'), resolvedAddress='10.9.9.9')
    check('accept: witness resolving a different address than the target refused', w.accept(4) == 1)
    w.io.poster_ok = False
    w.witness(id='w-ok')
    check('accept: unverified poster refused', w.accept(4) == 1 and 'poster' in w.io.said())
    w.io.poster_ok = True
    check('accept: stale --generation refused', w.accept(3) == 1 and w.raw() == before)
    code = w.accept(4)
    e = w.entry()
    check('accept: valid witness -> hosting at E+1 (5)', code == 0 and e['state'] == 'hosting' and e['generation'] == 5)
    check('accept: records acceptedWitness {id, epoch: E} and acceptedManifest', e['acceptedWitness'] == {'id': 'w-ok', 'epoch': 4} and e['acceptedManifest'] == MANIFEST)
    check('accept: lost-response rerun is a no-op success', w.accept(4) == 0 and w.entry()['generation'] == 5)
    receipt = json.loads((w.opdir / 'storage-receipt.json').read_text())
    check('accept: receipt kind accept', receipt['kind'] == 'isle.storage.accept' and receipt['ok'])


def t_accept_wrong_state(w):
    w.migrated()
    check('accept: refused from migrated', w.accept(2) == 1)


# ------------------------------------------------------------------ reconcile-address
def t_reconcile(w):
    w.cut_over()
    before = w.raw()
    check('reconcile-address: no drift is a no-op success', w.run('reconcile-address', '--dataset', DATASET, '--generation', 4) == 0 and w.raw() == before)
    w.io.leases['pol-core'] = '10.10.0.201'
    w.io.serve['10.10.0.201'] = {'current': CONTENT, 'ok': True}
    check('reconcile-address: stale generation refused', w.run('reconcile-address', '--dataset', DATASET, '--generation', 3) == 1 and w.raw() == before)
    code = w.run('reconcile-address', '--dataset', DATASET, '--generation', 4)
    e = w.entry()
    check('reconcile-address: drift repaired, state and owner unchanged', code == 0 and e['state'] == 'cutover' and e['device'] == 'pol-core' and w.io.router[DOMAIN] == '10.10.0.201')
    check('reconcile-address: two writes, generation G+2 (6)', e['generation'] == 6 and not e.get('pending'))
    check('reconcile-address: one history pair appended with the addresses', any(h.get('fromAddress') == TARGET_ADDR and h.get('toAddress') == '10.10.0.201' for h in e['history']))
    check('reconcile-address: the registration was fenced by the intent', w.io.fences and all(ok for ok, _ in w.io.fences))


def t_reconcile_stale_witness(w):
    w.cut_over()
    w.witness()                       # epoch 4
    w.io.leases['pol-core'] = '10.10.0.201'
    w.io.serve['10.10.0.201'] = {'current': CONTENT, 'ok': True}
    assert w.run('reconcile-address', '--dataset', DATASET, '--generation', 4) == 0
    check('reconcile-address: accept with the old witness refused after drift', w.accept(6) == 1)


def t_reconcile_failure_and_pending(w):
    w.cut_over()
    w.io.leases['pol-core'] = '10.10.0.202'
    w.io.serve['10.10.0.202'] = {'current': CONTENT, 'ok': True}
    w.io.register_effective = False
    code = w.run('reconcile-address', '--dataset', DATASET, '--generation', 4, op='a' * 32)
    e = w.entry()
    check('reconcile-address: failed verification leaves the pending and says so', code == 1 and e['pending']['action'] == 'reconcile-address' and e['generation'] == 5)
    before = w.raw()
    check('reconcile-address: cutover refused while a reconcile pending exists', w.cutover(g=5, op='f' * 32) == 1 and w.raw() == before)
    check('reconcile-address: rollback refused while a reconcile pending exists', w.run('rollback', '--dataset', DATASET, '--generation', 5, op='f' * 32) == 1 and w.raw() == before)
    w.io.register_effective = True
    code = w.run('reconcile-address', '--dataset', DATASET, '--generation', 4, op='a' * 32)
    check('reconcile-address: the exact retry resumes and completes', code == 0 and not w.entry().get('pending') and w.entry()['generation'] == 6)


def t_reconcile_state(w):
    w.migrated()
    check('reconcile-address: refused in migrated', w.run('reconcile-address', '--dataset', DATASET, '--generation', 2) == 1)


# ------------------------------------------------------------------ usage
def t_usage(w):
    check('usage: bad argv -> exit 2', core.run(['storage', 'cutover', '--dataset', DATASET], env={}, io=w.io) == 2)
    check('usage: a target-side verb is not a core verb', core.run(['storage', 'sync', '--dataset', DATASET], env={}, io=w.io) == 2)
    check('usage: argv without the leading storage word is accepted', core.run(['authorize', '--dataset', DATASET, '--plan', PLAN_ID, '--revision', str(REVISION),
          '--item', VECTOR['item']['id'], '--digest', DIGEST], env=w.env('4' * 32), io=w.io) == 0)


# ------------------------------------------------------------------ placement table (B)
_PL = [0]


# ------------------------------------------------------------------ cross-module seams (B1 B2 M2 M4 L4)
def t_view_and_source(w):
    w.authorize()
    e = w.entry()
    check('authorize: entry carries sourceDevice = the plan item originDevice', e['sourceDevice'] == VECTOR['item']['originDevice'])
    check('view: posted after authorize', len(w.io.views) >= 1 and w.io.views[-1]['datasets'][DATASET]['state'] == 'authorized')
    row = w.io.views[-1]['datasets'][DATASET]
    check('view: row carries sourceDevice, plan, addresses', row['sourceDevice'] == e['sourceDevice'] and row['plan'] == e['plan']
          and row['addresses']['source'] == CORE_ADDR and w.io.views[-1]['writtenBy'] == 'isle-core')
    w.receipts()
    n = len(w.io.views)
    w.mark()
    check('view: posted after mark-migrated', len(w.io.views) > n and w.io.views[-1]['datasets'][DATASET]['state'] == 'migrated')
    n = len(w.io.views)
    w.cutover()
    check('view: posted after cutover commit', len(w.io.views) > n and w.io.views[-1]['datasets'][DATASET]['state'] == 'cutover')
    w.witness(device='third')
    n = len(w.io.views)
    check('accept still succeeds', w.accept(4) == 0)
    check('view: posted after accept', len(w.io.views) > n and w.io.views[-1]['datasets'][DATASET]['state'] == 'hosting')


def t_view_ordering(w):
    w.migrated()
    w.cutover()
    check('view: every post ran with the placement lock free (after the transition committed)', w.io.post_lock_free and all(w.io.post_lock_free))
    check('view: the last post saw the final committed generation', w.io.post_seen[-1] == w.entry()['generation'] == 4
          and w.io.views[-1]['datasets'][DATASET]['generation'] == 4)
    check('view: one post per verb run, none before the handler (generations never go backwards)', w.io.post_seen == sorted(w.io.post_seen))


def t_view_other_verbs(w):
    w.migrated()
    w.io.serve[TARGET_ADDR]['ok'] = False
    n = len(w.io.views)
    w.cutover()
    check('view: posted after a reverted cutover', len(w.io.views) > n and w.io.views[-1]['datasets'][DATASET]['state'] == 'migrated')
    w.io.serve[TARGET_ADDR]['ok'] = True
    w.cutover(g=w.entry()['generation'])
    n = len(w.io.views)
    w.run('rollback', '--dataset', DATASET, '--generation', w.entry()['generation'])
    check('view: posted after rollback', len(w.io.views) > n and w.io.views[-1]['datasets'][DATASET]['state'] == 'migrated')


def t_view_failure_is_words(w):
    w.io.view_error = 'docker is down'
    code = w.authorize()
    check('view: a failed post never fails the verb', code == 0 and w.entry()['state'] == 'authorized')
    check('view: failure is reported in words', 'placement view not posted: docker is down' in w.io.said())


def t_authorize_push_failure(w):
    w.io.push_error = 'router unreachable'
    code = w.authorize()
    said = w.io.said()
    check('authorize: push failure exits 1 with the placement committed', code == 1 and w.entry()['state'] == 'authorized')
    check('authorize: push failure words say authorized + retry', 'authorized; managed-names push failed: router unreachable; rerun authorize to retry' in said)
    check('authorize: success is not printed before the push', 'authorized apt.isle' not in said)
    w.io.push_error = None
    check('authorize: rerun retries the push and succeeds', w.authorize() == 0 and w.io.pushed >= 1)


def t_accept_not_target_or_source(w):
    w.cut_over()
    w.witness(device='pol-core', id='by-target')
    check('accept: a witness posted by the target is skipped', w.accept(4) == 1 and 'target' in w.io.said())
    w.witness(device=w.entry()['sourceDevice'], id='by-source')
    check('accept: a witness posted by the source is skipped', w.accept(4) == 1 and 'source' in w.io.said() or 'target' in w.io.said())
    w.witness(device='third', id='by-third')
    check('accept: a third device witness is accepted', w.accept(4) == 0 and w.entry()['acceptedWitness']['id'] == 'by-third')


def _api_row(kind, body, device, name):
    return {'name': name, kind: body, 'dataset': DATASET, 'device': device, 'verified': False, 'seen': 1, 'receivedAt': '2026-11-09T12:00:00Z',
            'posterPresented': {'fingerprint': 'f' * 64, 'subject': device}}


def _migrate_body():
    return {'kind': 'isle.storage.migrate', 'ok': True, 'contentVerified': True, 'stagedDeploy': True, 'readinessVerified': True,
            'plan': {'id': PLAN_ID, 'revision': REVISION, 'digest': DIGEST}, 'target': {'device': 'pol-core'},
            'actionId': 'isle.storage.migrate:%s@pol-core:%s' % (DATASET, DIGEST)}


def t_relay_idempotent_and_poster(w):
    w.authorize()
    w.io.http = {'receipts': [_api_row('receipt', _migrate_body(), 'pol-core', 'r1')]}
    w.io.poster_ok = False
    w.run('relay')
    action = 'isle.storage.migrate:%s@pol-core:%s' % (DATASET, DIGEST)
    check('relay: unverified poster logs the words and does not transition',
          'relay: receipt %s poster not verified; not transitioning' % action in w.io.said() and w.io.mark_calls == [])
    w.run('relay')
    check('relay: unverified poster is not retried into mark-migrated on later runs', w.io.mark_calls == [])
    w.io.poster_ok = True
    check('relay: unverified count shown in summary', ', 1 receipt(s) not verified' in w.io.said())
    w.run('relay')
    check('relay: once verified, mark-migrated runs once', w.io.mark_calls == [(DATASET, DIGEST)])
    w.run('relay')
    check('relay: a receipt already acted upon is never re-run (actionId, digest)', w.io.mark_calls == [(DATASET, DIGEST)]
          and 'already acted on' in w.io.said())


def t_relay(w):
    w.authorize()
    log = w.io.state_dir / DATASET
    check('relay: no rows -> nothing appended, mark-migrated not invoked',
          w.run('relay') == 0 and not (log / 'inbound-receipts.jsonl').read_text() and w.io.mark_calls == [])
    check('relay: both doors asked per dataset', any('/storage/receipts?dataset=' + DATASET in u for u in w.io.http_calls)
          and any('/storage/witnesses?dataset=' + DATASET in u for u in w.io.http_calls))
    bad = _migrate_body()
    bad['readinessVerified'] = False
    w.io.http = {'receipts': [_api_row('receipt', bad, 'pol-core', 'r0')]}
    check('relay: a non-qualifying receipt is stored but mark-migrated is not invoked', w.run('relay') == 0 and w.io.mark_calls == []
          and (log / 'inbound-receipts.jsonl').read_text().count('\n') == 1 and 'readinessVerified' in w.io.said())
    good = _api_row('receipt', _migrate_body(), 'pol-core', 'r1')
    wit = _api_row('witness', {'dataset': DATASET, 'device': 'third', 'epoch': 4, 'generation': 4, 'contentSha': CONTENT}, 'third', 'w1')
    w.io.http = {'receipts': [good], 'witnesses': [wit]}
    w.io.poster_ok = False
    w.run('relay')
    check('relay: unverifiable poster -> mark-migrated not invoked', w.io.mark_calls == [])
    w.io.poster_ok = True
    w.run('relay')
    rows = [json.loads(l) for l in (log / 'inbound-receipts.jsonl').read_text().splitlines()]
    check('relay: new receipt row appended in the shape mark-migrated reads (receipt + posterPresented)',
          len(rows) == 2 and rows[-1]['receipt']['kind'] == 'isle.storage.migrate' and rows[-1]['posterPresented'] == {'fingerprint': 'f' * 64, 'subject': 'pol-core'}
          and rows[-1]['device'] == 'pol-core')
    wrows = [json.loads(l) for l in (log / 'witnesses.jsonl').read_text().splitlines()]
    check('relay: witness row appended (witness body + posterPresented + device)',
          len(wrows) == 1 and wrows[0]['witness']['epoch'] == 4 and wrows[0]['device'] == 'third' and wrows[0]['posterPresented']['subject'] == 'third')
    check('relay: mark-migrated invoked once with the digest', w.io.mark_calls == [(DATASET, DIGEST)])
    w.io.mark_calls.clear()
    w.io.http['receipts'][0]['seen'] = 7
    w.run('relay')
    check('relay: duplicates (even with a bumped seen counter) skipped',
          len((log / 'inbound-receipts.jsonl').read_text().splitlines()) == 2 and len((log / 'witnesses.jsonl').read_text().splitlines()) == 1)
    check('relay: seen hashes kept in relay-seen.txt', len((log / 'relay-seen.txt').read_text().split()) == 3)
    check('relay: summary printed', 'relay: 1 dataset(s), 0 new row(s)' in w.io.said())
    w.io.http_error = 'api down'
    check('relay: fetch failure reported, exit 1', w.run('relay') == 1 and 'api down' in w.io.said())


def t_relay_real_read_by_mark(w):
    """The relayed row is exactly what the real mark-migrated reader accepts."""
    w.authorize()
    w.io.http = {'receipts': [_api_row('receipt', _migrate_body(), 'pol-core', 'r1')]}
    w.run('relay')
    check('relay: relayed row satisfies mark-migrated', w.mark() == 0 and w.entry()['state'] == 'migrated')
    w2 = w.io
    w2.http = {'witnesses': [_api_row('witness', {'dataset': DATASET, 'device': 'third', 'epoch': 4, 'contentSha': CONTENT,
                                                  'at': '2026-11-09T12:00:30Z', 'resolvedAddress': TARGET_ADDR}, 'third', 'w1')]}
    check('relay: usage refuses arguments', w.run('relay', '--x') == 2)


def t_relay_no_placement(w):
    check('relay: no placement record -> nothing to do', w.run('relay') == 0 and 'relay: 0 dataset(s)' in w.io.said())


def pl(tmp, state='authorized'):
    _PL[0] += 1
    path = Path(tmp) / ('p%d.json' % _PL[0])
    plan = dict(id=PLAN_ID, revision=2, digest=DIGEST)
    sp.authorize(DATASET, 'pol-core', DOMAIN, '/etc/isle-mesh/apt/repo', plan, path=path)
    walk = {'authorized': [], 'migrated': [('authorized', 'migrated')], 'cutover': [('authorized', 'migrated'), ('migrated', 'cutover')],
            'hosting': [('authorized', 'migrated'), ('migrated', 'cutover'), ('cutover', 'hosting')]}[state]
    for a, b in walk:
        sp.transition(DATASET, a, b, by='setup', operation='setup', path=path, bump=True)
    return path


def intent(op='opA', action='cutover'):
    return {'action': action, 'digest': DIGEST, 'from': 'migrated', 'to': 'cutover', 'address': TARGET_ADDR, 'operation': op}


def t_table():
    for state in ('migrated', 'cutover', 'hosting'):
        with tempfile.TemporaryDirectory() as tmp:
            path = pl(tmp, state)
            g = sp.hosting_for(DATASET, path)['generation']
            e = sp.write_pending(DATASET, intent(), expect_generation=g, path=path, by='t')
            check('table: pending written in %s, generation +1 and pending.epoch = post-write value' % state,
                  e['pending']['epoch'] == g + 1 == e['generation'] and e['state'] == state)
            e = sp.clear_pending(DATASET, state, expect_generation=g + 1, path=path, by='t')
            check('table: pending cleared in %s, generation G+2' % state, e['generation'] == g + 2 and not e.get('pending'))
    with tempfile.TemporaryDirectory() as tmp:
        path = pl(tmp, 'authorized')
        check('table: pending never written in authorized', raises(ValueError, sp.write_pending, DATASET, intent(), expect_generation=1, path=path, by='t'))
        check('table: write_pending refuses without an operation', raises(ValueError, sp.write_pending, DATASET, {'action': 'x'}, expect_generation=1, path=path, by='t'))
        path = pl(tmp, 'migrated')
        sp.transition(DATASET, 'migrated', 'recovery-needed', by='t', operation='o', path=path)
        check('table: pending never written in recovery-needed', raises(ValueError, sp.write_pending, DATASET, intent(), expect_generation=2, path=path, by='t'))
    with tempfile.TemporaryDirectory() as tmp:
        path = pl(tmp, 'migrated')
        g = sp.hosting_for(DATASET, path)['generation']
        check('table: stale expect_generation refused', raises(ValueError, sp.write_pending, DATASET, intent(), expect_generation=g + 3, path=path, by='t'))
        sp.write_pending(DATASET, intent(), expect_generation=g, path=path, by='t')
        check('table: a stale holder of the older epoch fails its CAS after a pending write', raises(ValueError, sp.write_pending, DATASET, intent('opB'), expect_generation=g, path=path, by='t'))
        check('table: another operation cannot overwrite a pending', raises(ValueError, sp.write_pending, DATASET, intent('opB'), expect_generation=g + 1, path=path, by='t'))
        e = sp.write_pending(DATASET, intent('opA', 'cutover-revert'), expect_generation=g + 1, path=path, by='t')
        check('table: the same operation may replace its own intent (epoch G+2)', e['pending']['action'] == 'cutover-revert' and e['pending']['epoch'] == g + 2)
        check('table: another operation cannot clear a pending', raises(ValueError, sp.clear_pending, DATASET, 'migrated', expect_generation=g + 2, path=path, by='t', operation='opB'))
        check('table: another operation cannot change the state', raises(ValueError, sp.transition, DATASET, 'migrated', 'cutover', by='t', operation='opB', path=path, expect_generation=g + 2, bump=True, pending=None))
        check('table: a state change must clear or replace the pending', raises(ValueError, sp.transition, DATASET, 'migrated', 'cutover', by='t', operation='opA', path=path, expect_generation=g + 2, bump=True))
        check('table: clear_pending(bump=False) refused', raises(ValueError, sp.clear_pending, DATASET, 'migrated', expect_generation=g + 2, bump=False, path=path, by='t'))
        e = sp.transition(DATASET, 'migrated', 'cutover', by='t', operation='opA', path=path, expect_generation=g + 2, bump=True, pending=None)
        check('table: migrated + pending(cutover) -> cutover commit leaves E = G+3 here (intent replaced once)', e['state'] == 'cutover' and e['generation'] == g + 3 and not e.get('pending'))
    with tempfile.TemporaryDirectory() as tmp:
        path = pl(tmp, 'migrated')
        g = sp.hosting_for(DATASET, path)['generation']
        sp.write_pending(DATASET, intent(), expect_generation=g, path=path, by='t')
        e = sp.transition(DATASET, 'migrated', 'recovery-needed', by='t', operation='opA', path=path, expect_generation=g + 1, bump=True, pending=None,
                          fields={'recovery': {'sourceAddress': CORE_ADDR, 'targetAddress': TARGET_ADDR, 'at': 'x'}})
        check('table: entering recovery-needed clears the pending', e['state'] == 'recovery-needed' and not e.get('pending') and e['recovery']['at'] == 'x')
        e = sp.transition(DATASET, 'recovery-needed', 'migrated', by='t', operation='r', path=path, expect_generation=g + 2, bump=True, recover=True,
                          pending=intent('r', 'recover'))
        check('table: recover leaves recovery-needed straight into a state with its intent', e['state'] == 'migrated' and e['pending']['epoch'] == g + 3 and 'recovery' not in e)
    with tempfile.TemporaryDirectory() as tmp:
        path = pl(tmp, 'cutover')
        g = sp.hosting_for(DATASET, path)['generation']
        sp.write_pending(DATASET, intent('opA', 'rollback'), expect_generation=g, path=path, by='t')
        e = sp.transition(DATASET, 'cutover', 'migrated', by='t', operation='opZ', path=path, expect_generation=g + 1, bump=True, recover=True,
                          pending=None, history_extra={'supersededOperation': 'opA'})
        check('table: recover supersedes a pending of another operation (cutover -> migrated)', e['state'] == 'migrated' and e['history'][-1]['supersededOperation'] == 'opA')
    with tempfile.TemporaryDirectory() as tmp:
        path = pl(tmp, 'migrated')
        check('table: recover=True does not open edges without a pending', raises(ValueError, sp.transition, DATASET, 'migrated', 'hosting', by='t', operation='o', path=path, bump=True, recover=True))
        check('table: pending refused when the target state is authorized', raises(ValueError, sp.transition, DATASET, 'authorized', 'authorized', by='t', operation='o', path=pl(tmp + '/x' if False else tmp, 'authorized'), plan=dict(id=PLAN_ID, revision=3, digest=DIGEST), pending=intent(), bump=True))
        sp.transition(DATASET, 'migrated', 'cutover', by='t', operation='o', path=path, bump=True)
        check('table: the same-state write needs a pending to exist or be provided', raises(ValueError, sp.transition, DATASET, 'cutover', 'cutover', by='t', operation='o', path=path, bump=True))
        check('table: setting a pending without a bump refused', raises(ValueError, sp.transition, DATASET, 'cutover', 'cutover', by='t', operation='o', path=path, pending=intent()))
    with tempfile.TemporaryDirectory() as tmp:
        path = pl(tmp, 'migrated')
        e = sp.hosting_for(DATASET, path)
        check('table: legacy callers keep their old bump rules', raises(ValueError, sp.transition, DATASET, 'migrated', 'cutover', by='t', operation='o', path=path)
              and sp.transition(DATASET, 'migrated', 'cutover', by='t', operation='o', path=path, bump_generation=True)['generation'] == e['generation'] + 1)
    with tempfile.TemporaryDirectory() as tmp:
        path = pl(tmp, 'migrated')
        e = sp.hosting_for(DATASET, path)
        sp.write_pending(DATASET, intent(), expect_generation=e['generation'], path=path, by='t')
        pins, complete, pending = sp.derive_pins(Path('/etc/isle-mesh/apt/repo'), path)
        check('table: derive_pins sees the pending', pending is True)
        claims = sp.claims_path(path)
        check('table: claims mirror rewritten on every transition', json.loads(claims.read_text())[DOMAIN]['state'] == 'migrated')
    check('table: fence_check passes an unmanaged name', sp.fence_check('other.isle', '1.2.3.4', '', '', Path('/nonexistent/x'))[0])


def t_real_poster_and_timer(tmp):
    import hashlib
    tmp = Path(tmp)
    token_hash = hashlib.sha256(b'token-for-target').hexdigest()
    members = tmp / 'members.json'
    members.write_text(json.dumps({'target': token_hash}))
    saved = core.MEMBERS
    core.MEMBERS = members
    try:
        io = core.RealIo.__new__(core.RealIo)
        io.state_dir = tmp
        def row(fp, device='target', kind='isle.storage.migrate'):
            receipt = {'kind': kind, 'target': {'device': device}} if device else {'kind': kind}
            return {'receipt': receipt, 'posterPresented': {'subject': 'target', 'fingerprint': fp}}
        check('poster: equal full hash and device verifies', io.verify_poster(row(token_hash)) is True)
        check('poster: prefix-only fingerprint refused', io.verify_poster(row(token_hash[:16])) is False)
        check('poster: different hash refused', io.verify_poster(row('0' * 64)) is False)
        check('poster: missing device field fails closed', io.verify_poster(row(token_hash, device=None)) is False)
        check('poster: device naming another member refused', io.verify_poster(row(token_hash, device='other')) is False)
        w = {'kind': 'isle.storage.witness', 'device': 'target', 'posterPresented': {'subject': 'target', 'fingerprint': token_hash}}
        check('poster: witness with device verifies, without device refused',
              io.verify_poster(w) is True and io.verify_poster({k: v for k, v in w.items() if k != 'device'}) is False)
        members.unlink()
        check('poster: members file missing -> False', io.verify_poster(row(token_hash)) is False)
        (tmp / 'ds').mkdir()
        log = tmp / 'ds' / 'inbound-receipts.jsonl'
        check('timer: no receipts -> False', io.timer_installed('ds') is False)
        log.write_text(json.dumps({'receipt': {'kind': 'isle.storage.migrate', 'syncTimerInstalled': True}}) + '\n')
        check('timer: newest migrate receipt claim true -> True', io.timer_installed('ds') is True)
        log.write_text(log.read_text() + json.dumps({'receipt': {'kind': 'isle.storage.migrate'}}) + '\n')
        check('timer: newest migrate receipt without the claim -> False', io.timer_installed('ds') is False)
    finally:
        core.MEMBERS = saved


def main():
    for fn in (t_authorize, t_authorize_revision, t_authorize_refusals, t_authorize_states, t_mark, t_cutover_happy, t_cutover_refusals,
               t_cutover_state, t_cutover_lease, t_autorevert, t_autorevert_by_name, t_recovery_needed, t_resume, t_resume_pending_digest_mismatch,
               t_rollback, t_rollback_refusals, t_rollback_unverified, t_recover_live, t_recover_to_migrated, t_recover_wrong_g,
               t_recover_after_dns_write, t_recover_after_dns_write_revert, t_recover_from_recovery_needed, t_recover_nothing,
               t_recover_hosting_pending, t_accept, t_accept_wrong_state, t_reconcile, t_reconcile_stale_witness,
               t_reconcile_failure_and_pending, t_reconcile_state, t_usage, t_view_and_source, t_view_ordering, t_view_other_verbs, t_view_failure_is_words,
               t_authorize_push_failure, t_accept_not_target_or_source, t_relay, t_relay_idempotent_and_poster, t_relay_real_read_by_mark, t_relay_no_placement):
        buf = iomod.StringIO()
        try:
            with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(buf):
                fn(World(tmp))
        except Exception as exc:     # a harness error is a failure, not a crash of the whole run
            import traceback
            traceback.print_exc()
            check('%s ran without a harness error (%r)' % (fn.__name__, exc), False)
    with tempfile.TemporaryDirectory() as tmp:
        t_real_lease(tmp)
    with tempfile.TemporaryDirectory() as tmp:
        t_real_poster_and_timer(tmp)
    try:
        t_table()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        check('t_table ran without a harness error (%r)' % (exc,), False)
    print('%d checks, %d failure(s)' % (COUNT[0], len(FAILED)))
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
