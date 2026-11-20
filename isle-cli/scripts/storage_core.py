#!/usr/bin/env python3
"""Core-side journaled storage verbs (stm-5): authorize, mark-migrated, cutover, rollback, recover,
accept, reconcile-address.  docs/STORAGE-MIGRATION-CONTRACT.md sections 2.1, 2.3-2.5a, 5, 6.

`run(argv, *, env, io) -> int`.  `io` bundles every outside effect so tests run with fakes:

  placement_path, state_dir                          files (the placement record is the one authority)
  read_plan(plan_id, revision) -> plan dict          GET placement-plan
  router_dns(domain) -> address|None                 dig @router
  register_dns(domain, address, intent_path)         dns-register.sh with ISLE_STORAGE_INTENT=<json path>
  push_managed_names()                               dns-register.sh managed-names push (router skip list)
  managed_names() -> set|None                        dns-register.sh managed-names list; None = the router did not answer
  https_check(domain, resolve_addr|None, paths)      paths = [(path, sha256|None)]; None = ordinary resolution
  fetch_current(domain, resolve_addr) -> contentSha|None   /.well-known/isle-storage/current via --resolve
  hosts_pin(domain, address|None) / current_pin(domain)    managed /etc/hosts block (hosts-reconcile.sh)
  getent(domain) -> address|None                     ordinary resolution from the core
  lease()                                            context manager: LOCK_SH on app-device-update.lock
  lease_address(device), core_address()              the owner's current lease; the core agent address
  source_latest(entry) -> {manifestSha, contentSha}  the source snapshot/latest.json
  sample_paths(entry) -> [(path, sha256|None)]       the InRelease-equivalent plus 3 sampled entries
  timer_installed(dataset), verify_poster(row)       sync trigger report; poster against the CA members
  registry_owner(domain), claims_owner(domain)       who owns the domain today (the ownership check)
  post_placement_view()                              write the placement view into the backend container (docker exec)
  http_get_json(url) -> dict                         GET with the trusted CA, no auth (relay)
  run_mark_migrated(dataset, digest) -> exit code    `operations.py run storage mark-migrated` (relay)
  operations_read(op_id), stage(label), now(), device_name(), out(text)

The dataset lock is the operation's (operations.py); this module never takes it.  Every verb except
authorize holds the shared device-update lease for the whole verb.  Epoch arithmetic (section 5): the intent
write leaves G+1 with pending.epoch = G+1, the commit expects G+1 and leaves G+2.
"""
import contextlib
import fcntl
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit
import uuid

import operations
import storage_contract as sc
import storage_placement as sp

SCRIPTS = Path(__file__).resolve().parent
STATE_DIR = Path('/var/lib/isle-mesh/storage')
CA_FILE = '/etc/isle-mesh/ca/isle-root.crt'
MEMBERS = Path('/etc/isle-mesh/ca/enrollment/members.json')
CLAIMS = Path('/etc/isle-mesh/ca/enrollment/claims.json')
REGISTRY = Path('/etc/isle-mesh/agent/registry.json')
API = 'https://api.polari.isle'
CORE_PIN = '127.0.0.1'      # the core's own hairpin pin for a domain it serves (hosts-reconcile.sh)
SKEW = timedelta(seconds=120)
WITNESS_WINDOW = timedelta(hours=24)
CONTROL = '/.well-known/isle-storage/'


class Refusal(Exception):
    """A verb stopped. Nothing it names was written unless `after_write` says so."""

    def __init__(self, words, stage=None, retryable=False, code=None):
        super().__init__(words)
        self.words, self.stage, self.retryable, self.code = words, stage, retryable, code


class DeviceUpdateBusy(Refusal):
    pass


# ------------------------------------------------------------------ real effects

def _run(argv, env=None, timeout=60, **kw):
    return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout, **kw)


def real_lease(root=None, device='this device'):
    """LOCK_SH on <operations root>/app-device-update.lock; refuse when an exclusive holder has it."""
    @contextlib.contextmanager
    def lease():
        base = Path(root) if root else operations.ROOT
        base.mkdir(parents=True, exist_ok=True)
        with (base / 'app-device-update.lock').open('a+') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DeviceUpdateBusy('device update in progress on %s; retry after it completes' % device,
                                       stage='checking-placement', retryable=True)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
    return lease


class RealIo:
    """The production effects.  Each is a thin wrapper over a tool that already exists."""

    def __init__(self, env=None):
        self.env = dict(env if env is not None else os.environ)
        self.placement_path = sp.PLACEMENT
        self.state_dir = STATE_DIR
        self.lease = real_lease(device=self.device_name())

    def out(self, text):
        print(text, flush=True)

    def now(self):
        return datetime.now(timezone.utc)

    def device_name(self):
        try:
            return sp.device_name()
        except (OSError, ValueError):
            return 'this-device'

    def stage(self, label):
        ident = self.env.get('ISLE_OPERATION_ID')
        if ident:
            try:
                operations.stage(ident, label)
            except (OSError, ValueError, PermissionError, KeyError):
                pass

    def _curl(self, url, resolve=None, to=None, timeout=20):
        cmd = ['curl', '-sf', '--max-time', str(timeout), '--max-redirs', '0', '--cacert', CA_FILE]
        if resolve:
            cmd += ['--resolve', resolve]
        if to:
            cmd += ['-o', to]
        return _run(cmd + [url], timeout=timeout + 5)

    def read_plan(self, plan_id, revision):
        url = '%s/api/islemesh/placement-plan?id=%s&revision=%d' % (API, plan_id, revision)
        for resolve in (None, 'api.polari.isle:443:127.0.0.1'):
            done = self._curl(url, resolve)
            if done.returncode == 0:
                return json.loads(done.stdout)
        raise ValueError('cannot fetch plan %s revision %d' % (plan_id, revision))

    def router_dns(self, domain):
        import dns_publication
        done = _run(['dig', '+time=2', '+tries=1', '+short', '@' + dns_publication.resolver(), domain, 'A'])
        rows = [r for r in done.stdout.split() if r.replace('.', '').isdigit()]
        return rows[0] if rows else None

    def register_dns(self, domain, address, intent_path):
        env = dict(self.env, ISLE_STORAGE_INTENT=str(intent_path))
        done = _run(['bash', str(SCRIPTS / 'dns-register.sh'), 'register', domain, address], env=env, timeout=120)
        if done.returncode != 0:
            raise ValueError('dns register failed: ' + (done.stderr or done.stdout).strip()[-200:])

    def push_managed_names(self):
        done = _run(['bash', str(SCRIPTS / 'dns-register.sh'), 'managed-names', 'push'], env=self.env, timeout=120)
        if done.returncode != 0:
            raise ValueError('managed-names push failed: ' + (done.stderr or done.stdout).strip()[-200:])

    def managed_names(self):
        done = _run(['bash', str(SCRIPTS / 'dns-register.sh'), 'managed-names', 'list'], env=self.env, timeout=60)
        return set(done.stdout.split()) if done.returncode == 0 else None

    def https_check(self, domain, resolve_addr, paths):
        import tempfile
        for path, sha in paths:
            with tempfile.NamedTemporaryFile() as sink:
                done = self._curl('https://%s%s' % (domain, path), '%s:443:%s' % (domain, resolve_addr) if resolve_addr else None, sink.name)
                if done.returncode != 0:
                    return False
                if sha and hashlib.sha256(Path(sink.name).read_bytes()).hexdigest() != sha:
                    return False
        return True

    def fetch_current(self, domain, resolve_addr):
        import tempfile
        with tempfile.NamedTemporaryFile() as sink:
            done = self._curl('https://%s%scurrent' % (domain, CONTROL), '%s:443:%s' % (domain, resolve_addr), sink.name)
            return Path(sink.name).read_text().strip() if done.returncode == 0 else None

    def hosts_pin(self, domain, address):
        done = _run(['bash', str(SCRIPTS / 'hosts-reconcile.sh'), 'pin', domain, address or 'none'])
        if done.returncode != 0:
            raise ValueError('hosts pin failed: ' + (done.stderr or done.stdout).strip()[-200:])

    def current_pin(self, domain):
        done = _run(['bash', str(SCRIPTS / 'hosts-reconcile.sh'), 'pin-of', domain])
        return done.stdout.strip() or None if done.returncode == 0 else None

    def getent(self, domain):
        done = _run(['getent', 'hosts', domain])
        return done.stdout.split()[0] if done.returncode == 0 and done.stdout.split() else None

    def lease_address(self, device):
        key = '/etc/isle-mesh/router/ssh/isle_router_key'
        done = _run(['ssh', '-i', key, '-o', 'ConnectTimeout=8', '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null',
                     'root@192.168.1.1', 'cat /tmp/dhcp.leases'], timeout=30)
        if done.returncode != 0:
            return None
        want = ''.join(c if c.isalnum() or c == '-' else '-' for c in device.lower())[:63]
        for line in done.stdout.splitlines():
            row = line.split()
            if len(row) >= 4 and row[1].lower().startswith('02:00:00:00') and row[3].lower() == want:
                return row[2]
        return None

    def core_address(self):
        done = _run(['bash', '-c', 'source "%s/agent-address.sh"; isle_agent_address' % SCRIPTS])
        return done.stdout.strip() or None if done.returncode == 0 else None

    def source_latest(self, entry):
        try:
            return json.loads((Path(entry['sourcePath']) / 'snapshot' / 'latest.json').read_bytes())
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def sample_paths(self, entry):
        latest = self.source_latest(entry) or {}
        paths = [(CONTROL + 'latest.json', None)]
        try:
            manifest = json.loads((Path(entry['sourcePath']) / 'snapshot' / 'manifests' / (latest['manifestSha'] + '.json')).read_bytes())
            files = sorted((e for e in manifest['entries'] if e.get('kind') == 'file'), key=lambda e: e['sha256'])
            named = [e for e in files if e['path'] in ('InRelease', 'Release')]
            rest = [e for e in files if e not in named and e['size'] <= 1 << 20]
            for e in (named[:1] + rest[:3]):
                paths.append(('/' + e['path'], e['sha256']))
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return paths

    def timer_installed(self, dataset):
        for row in reversed(_jsonl(self.state_dir / dataset / 'inbound-receipts.jsonl')):
            receipt = row.get('receipt') if isinstance(row.get('receipt'), dict) else row
            if receipt.get('kind') == 'isle.storage.migrate':
                return receipt.get('syncTimerInstalled') is True
        return False

    def verify_poster(self, row):
        """The presented Bearer's subject must be a paired member, its fingerprint EQUAL to the member's stored
        token hash, and the subject must be the receipt's/witness's device.  Both sides use the same derivation
        (cert-authority.py authorize(): members[device] = sha256(token).hexdigest(), the full 64 hex; the backend's
        poster_fingerprint is the sha256 hex of the presented token), so the comparison is full-hash equality.
        Fails closed: a missing members file, subject, fingerprint or device field never verifies."""
        poster = row.get('posterPresented') if isinstance(row.get('posterPresented'), dict) else {}
        subject, fingerprint = poster.get('subject'), poster.get('fingerprint')
        try:
            members = json.loads(MEMBERS.read_text())
        except (OSError, ValueError):
            return False
        stored = members.get(subject) if isinstance(subject, str) and isinstance(members, dict) else None
        if not (isinstance(stored, str) and isinstance(fingerprint, str) and len(stored) == 64 and hmac.compare_digest(stored, fingerprint)):
            return False
        receipt = row.get('receipt') if isinstance(row.get('receipt'), dict) else row
        device = (receipt.get('target') or {}).get('device') if receipt.get('kind') == 'isle.storage.migrate' else receipt.get('device')
        return device is not None and device == subject

    def registry_owner(self, domain):
        try:
            apps = json.loads(REGISTRY.read_text()).get('apps', {})
        except (OSError, ValueError, AttributeError):
            return None
        return self.device_name() if any(isinstance(a, dict) and a.get('domain') == domain for a in apps.values()) else None

    def claims_owner(self, domain):
        try:
            owner = json.loads(CLAIMS.read_text()).get(domain)
        except (OSError, ValueError, AttributeError):
            return None
        return owner if isinstance(owner, str) else None

    def operations_read(self, op_id):
        return operations.read(op_id)

    def post_placement_view(self):
        """Placement view -> the backend container's data dir, atomically (storage_placement.post_view)."""
        # Ordering: the exclusive placement.lock is taken (and released) first, so a transition still inside
        # its compare-and-set has committed before the document is built; the docker exec happens after release.
        with sp._locked(Path(self.placement_path)):
            document = sp.view_document(self.placement_path)
        sp.post_view(document)

    def http_get_json(self, url):
        for resolve in (None, 'api.polari.isle:443:127.0.0.1'):
            done = self._curl(url, resolve)
            if done.returncode == 0:
                return json.loads(done.stdout)
        raise ValueError('cannot fetch ' + url)

    def run_mark_migrated(self, dataset, digest):
        env = {k: v for k, v in self.env.items() if not k.startswith('ISLE_OPERATION')}
        done = _run([sys.executable, str(SCRIPTS / 'operations.py'), 'run', 'storage', 'mark-migrated',
                     '--dataset', dataset, '--digest', digest], env=env, timeout=600)
        return done.returncode


# ------------------------------------------------------------------ helpers

def _jsonl(path):
    rows = []
    try:
        for line in Path(path).read_text().splitlines():
            if line.strip():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def _iso(value):
    return sp._stamp(value)


def _parse(text):
    try:
        return datetime.strptime(text, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        try:
            value = datetime.fromisoformat(text)
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None


def _unwrap_plan(plan):
    if isinstance(plan, dict) and 'items' not in plan and isinstance(plan.get('plan'), dict):
        plan = plan['plan']
    return plan


class Ctx:
    def __init__(self, req, env, io):
        self.req, self.env, self.io = req, env, io
        self.verb, self.dataset = req['verb'], req['dataset']
        self.op = env.get('ISLE_OPERATION_ID') or uuid.uuid4().hex
        self.op_dir = Path(env['ISLE_OPERATION_DIR']) if env.get('ISLE_OPERATION_DIR') else None
        self.path = Path(io.placement_path)
        self.device = io.device_name()
        self.by = '%s:%s' % (self.device, env.get('SUDO_USER') or 'root')
        self.entry = None
        self.digest = None
        self.words = ''
        self.extra = {}

    def now(self):
        return self.io.now()

    def stage(self, label):
        self.io.stage(label)

    def say(self, text):
        self.io.out(text)

    def load(self, states=None):
        self.entry = sp.hosting_for(self.dataset, self.path)
        if self.entry is None:
            raise Refusal('no placement for %s; nothing to do' % self.dataset, 'checking-placement')
        if states is not None and self.entry.get('state') not in states:
            raise Refusal('placement of %s is in state %s, not %s' % (self.dataset, self.entry.get('state'), '|'.join(states)),
                          'checking-placement')
        return self.entry

    def intent_file(self, pending):
        """The intent the fence reads (ISLE_STORAGE_INTENT). Written next to the operation journal."""
        base = self.op_dir if self.op_dir and self.op_dir.is_dir() else Path(self.io.state_dir) / self.dataset
        base.mkdir(parents=True, exist_ok=True)
        target = base / 'storage-intent.json'
        sp._atomic_bytes(target, (json.dumps(dict(domain=self.entry['domain'], address=pending['address'], operation=self.op,
                                                  pending=pending), sort_keys=True) + '\n').encode())
        return target

    def entry_state(self):
        return sp.hosting_for(self.dataset, self.path)['state']

    def attempt(self):
        try:
            return max(1, int(self.io.operations_read(self.op).get('attempt', 1)))
        except Exception:
            return 1

    def write_receipt(self, kind, ok, words, **extra):
        entry = sp.hosting_for(self.dataset, self.path) or self.entry or {}
        plan = entry.get('plan') if isinstance(entry.get('plan'), dict) else {'id': None, 'revision': None, 'digest': self.digest}
        receipt = sc.receipt_skeleton('isle.storage.cutover', self.op, self.attempt(), dict(plan, itemId=extra.pop('itemId', None)),
                                      self.dataset, entry.get('domain'))
        receipt.update(kind=kind, ok=bool(ok), words=words, at=_iso(self.now()), epoch=entry.get('generation'),
                       actionId=sc.action_id(kind.rsplit('.', 1)[-1], self.dataset, self.device, plan.get('digest') or self.digest or '0' * 64),
                       placement={'state': entry.get('state'), 'generation': entry.get('generation')},
                       digestRecomputed=bool(extra.pop('digestRecomputed', False)))
        receipt['target']['device'] = entry.get('device')
        receipt['source']['device'] = self.device
        receipt['cutover'] = dict(receipt.get('cutover') or {}, **extra.pop('cutover', {}))
        receipt.update(extra)
        raw = (json.dumps(receipt, sort_keys=True) + '\n')
        if self.op_dir and self.op_dir.is_dir():
            sp._atomic_bytes(self.op_dir / 'storage-receipt.json', raw.encode())
        directory = Path(self.io.state_dir) / self.dataset
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / 'receipts.jsonl').open('a') as log:
                log.write(raw)
        except OSError as exc:
            self.say('[storage] receipt not appended: %s' % exc)
        return receipt


def _pending_matches(pending, entry, action, op, generation):
    return (isinstance(pending, dict) and pending.get('action') == action and pending.get('operation') == op
            and pending.get('digest') == (entry.get('plan') or {}).get('digest')
            and pending.get('epoch') == entry.get('generation') == generation + 1)


def _stale(entry, generation, what):
    return Refusal('%s refused: placement of %s is %s%s at generation %s, not %d. Re-read the placement and use that generation%s.'
                   % (what, entry.get('dataset'), entry.get('state'),
                      ' with a pending %s (operation %s)' % (entry['pending'].get('action'), entry['pending'].get('operation'))
                      if isinstance(entry.get('pending'), dict) else '', entry.get('generation'), generation,
                      '; a pending of another operation is superseded only by recover' if entry.get('pending') else ''),
                   'checking-placement')


def _plan_item(ctx, plan_ref):
    """Re-fetch the plan; return (item, block, digest). Active, one storage item for the dataset, digest recomputed."""
    io = ctx.io
    try:
        plan = _unwrap_plan(io.read_plan(plan_ref['id'], plan_ref['revision']))
    except Exception as exc:
        raise Refusal('cannot fetch plan %s revision %s: %s' % (plan_ref['id'], plan_ref['revision'], exc), 'checking-placement', True)
    if not isinstance(plan, dict) or plan.get('active') is not True:
        raise Refusal('plan %s revision %s is not active' % (plan_ref['id'], plan_ref['revision']), 'checking-placement')
    return plan


def _item_for(plan, plan_ref, dataset, item_id=None):
    found = [i for i in plan.get('items') or [] if isinstance(i, dict) and i.get('kind') == 'storage'
             and (item_id is None or i.get('id') == item_id) and (i.get('storage') or {}).get('dataset') == dataset]
    if len(found) != 1:
        raise Refusal('plan %s has no single storage item for dataset %s' % (plan_ref['id'], dataset), 'checking-placement')
    item = found[0]
    try:
        block = sc.validate_storage_item(item)
        release = sc.validate_hosting_release(item.get('hostingRelease'))
        digest = sc.storage_digest(plan_ref['id'], plan_ref['revision'], item, release)
    except ValueError as exc:
        raise Refusal('the plan item is not valid: %s' % exc, 'checking-placement')
    return item, block, digest


# ------------------------------------------------------------------ authorize / mark-migrated

def core_itself():
    """The names that are never moved.  The user, 2026-11-14: "everything may move with the exception of the core
    itself ... do not make assumptions about what should not be allowed for moving".  So this is not a list of
    protected names: it holds the one name the isle itself calls the core, the address its members reach the core
    at (ISLE_CORE_API, which is API here).  Every other name may be placed when its source device owns it."""
    return {urlsplit(API).hostname}


def do_authorize(ctx):
    req, io = ctx.req, ctx.io
    ctx.stage('fetching-plan')
    ref = {'id': req['plan'], 'revision': req['revision']}
    plan = _plan_item(ctx, ref)
    item, block, digest = _item_for(plan, ref, ctx.dataset, req['item'])
    ctx.digest = digest
    if item.get('state') != 'planned':
        raise Refusal('plan item %s is %s, not planned' % (req['item'], item.get('state')), 'fetching-plan')
    if digest != req['digest']:
        raise Refusal('the plan changed since it was reviewed (digest %s, reviewed %s)' % (digest[:12], req['digest'][:12]), 'fetching-plan')
    if block['source']['snapshotSha256'] is None:
        raise Refusal('the plan has no published source snapshot yet (snapshotSha256 is null)', 'fetching-plan')
    ctx.stage('checking-ownership')
    domain = block['domain']
    if domain in core_itself():
        raise Refusal('%s is the core itself, and the core is the one thing that is never moved; nothing was written' % domain, 'checking-ownership')
    owners = set()
    if io.registry_owner(domain):
        owners.add(io.registry_owner(domain))
    if io.claims_owner(domain):
        owners.add(io.claims_owner(domain))
    if block['source']['device'] not in owners:
        raise Refusal('%s does not currently own %s (owners: %s); nothing was written'
                      % (block['source']['device'], domain, ', '.join(sorted(owners)) or 'none recorded'), 'checking-ownership')
    address = io.core_address()
    if not address:
        raise Refusal('the core agent address is unknown; nothing was written', 'checking-ownership', True)
    ctx.stage('writing-placement')
    target = item['device']
    ref3 = dict(id=ref['id'], revision=ref['revision'], digest=digest)
    existing = sp.hosting_for(ctx.dataset, ctx.path)
    for name, other in sp.read(ctx.path)['datasets'].items():
        if name != ctx.dataset and isinstance(other, dict) and other.get('domain') == domain:
            raise Refusal('dataset %s already holds %s; one placement per domain' % (name, domain), 'writing-placement')
    if existing is None:
        entry = sp.authorize(ctx.dataset, target, domain, block['source']['path'], ref3, path=ctx.path, now=ctx.now(),
                             by=ctx.by, operation=ctx.op, addresses={'source': address, 'target': None},
                             source_device=item.get('originDevice'))
        words = 'authorized %s for %s on %s (generation %d)' % (domain, ctx.dataset, target, entry['generation'])
    else:
        if existing.get('state') != 'authorized':
            raise Refusal('placement of %s is already %s; authorize applies only before the replica is marked migrated'
                          % (ctx.dataset, existing.get('state')), 'writing-placement')
        if (existing.get('device'), existing.get('domain')) != (target, domain):
            raise Refusal('the authorized placement names %s for %s; a new revision must keep the dataset, device and domain'
                          % (existing.get('device'), existing.get('domain')), 'writing-placement')
        if existing.get('plan') == ref3:
            ctx.entry = existing
            words = 'already authorized for this plan revision (generation %d)' % existing['generation']
            entry = existing
        else:
            entry = sp.transition(ctx.dataset, 'authorized', 'authorized', by=ctx.by, operation=ctx.op, path=ctx.path, now=ctx.now(),
                                  expect_generation=existing['generation'], bump=True, plan=ref3,
                                  addresses={'source': address})
            words = 'authorization updated to plan revision %d (generation %d)' % (ref['revision'], entry['generation'])
    ctx.entry = entry
    ctx.stage('pushing-managed-names')
    try:
        ctx.io.push_managed_names()
    except Exception as exc:
        # The placement is committed; say so, and let a rerun (idempotent) retry the push.
        failed = 'authorized; managed-names push failed: %s; rerun authorize to retry' % exc
        ctx.say(failed)
        ctx.write_receipt('isle.storage.authorize', False, failed, digestRecomputed=True, itemId=req['item'],
                          failedStage='pushing-managed-names', retryable=True)
        return 1
    ctx.say(words)
    ctx.write_receipt('isle.storage.authorize', True, words, digestRecomputed=True, itemId=req['item'])
    return 0


def qualifying_receipt(io, entry, dataset, digest):
    """The stored migrate receipt mark-migrated needs; raises Refusal naming what is missing. Also used by relay."""
    rows = [r for r in _jsonl(Path(io.state_dir) / dataset / 'inbound-receipts.jsonl')
            if ((r.get('receipt') if isinstance(r.get('receipt'), dict) else r).get('plan') or {}).get('digest') == digest]
    if not rows:
        raise Refusal('no stored migrate receipt for digest %s' % digest[:12], 'checking-receipt')
    row = rows[-1]
    receipt = row.get('receipt') if isinstance(row.get('receipt'), dict) else row
    held, wanted = receipt.get('plan') or {}, entry.get('plan') or {}
    if (held.get('id'), held.get('revision')) != (wanted.get('id'), wanted.get('revision')):
        raise Refusal('receipt is for plan %s rev %s, the placement names %s rev %s'
                      % (held.get('id'), held.get('revision'), wanted.get('id'), wanted.get('revision')), 'checking-receipt')
    for key in ('ok', 'contentVerified', 'stagedDeploy', 'readinessVerified'):
        if receipt.get(key) is not True:
            raise Refusal('the stored receipt does not show %s' % key, 'checking-receipt')
    if receipt.get('kind') != 'isle.storage.migrate':
        raise Refusal('the stored receipt is not a migrate receipt', 'checking-receipt')
    if (receipt.get('target') or {}).get('device') != entry.get('device'):
        raise Refusal('the receipt is for device %s, the placement names %s' % ((receipt.get('target') or {}).get('device'), entry.get('device')), 'checking-receipt')
    if not io.verify_poster(row):
        raise Refusal('the receipt poster could not be verified against the CA members', 'checking-receipt', code='poster')
    return row


def do_mark_migrated(ctx):
    req = ctx.req
    ctx.stage('checking-placement')
    entry = ctx.load()
    ctx.digest = req['digest']
    plan = entry.get('plan') or {}
    if entry['state'] == 'migrated' and plan.get('digest') == req['digest']:
        words = 'already migrated (generation %d)' % entry['generation']
        ctx.say(words)
        ctx.write_receipt('isle.storage.mark-migrated', True, words)
        return 0
    if entry['state'] != 'authorized':
        raise Refusal('placement of %s is %s, not authorized' % (ctx.dataset, entry['state']), 'checking-placement')
    if plan.get('digest') != req['digest']:
        raise Refusal('digest %s does not match the authorized plan' % req['digest'][:12], 'checking-placement')
    qualifying_receipt(ctx.io, entry, ctx.dataset, req['digest'])
    ctx.stage('marking-migrated')
    done = sp.mark_migrated(ctx.dataset, req['digest'], path=ctx.path, now=ctx.now(), bump=True, operation=ctx.op, by=ctx.by)
    ctx.entry = done
    words = 'marked %s migrated (generation %d); the cutover is next' % (ctx.dataset, done['generation'])
    ctx.say(words)
    ctx.write_receipt('isle.storage.mark-migrated', True, words)
    return 0


# ------------------------------------------------------------------ the DNS transitions

def _verify_address(ctx, address, paths, current=None):
    """Router shows `address`, the address serves the samples (and `current` contentSha when given)."""
    io = ctx.io
    domain = ctx.entry['domain']
    router = io.router_dns(domain)
    if router != address:
        return False, 'the router answers %s for %s, expected %s' % (router, domain, address)
    if not io.https_check(domain, router, paths):
        return False, 'https samples from %s failed' % router
    if current is not None and io.fetch_current(domain, router) != current:
        return False, '%s does not advertise contentSha %s' % (router, current[:12])
    return True, ''


def _intent(ctx, action, to_state, frm, address, *, previous_address=None, previous_pin=None, new_pin=None, **more):
    return dict(action=action, digest=ctx.entry['plan']['digest'], to=to_state, address=address, previousAddress=previous_address,
                previousPin=previous_pin, newPin=new_pin, at=_iso(ctx.now()), operation=ctx.op, **{'from': frm}, **more)


def _recovery_needed(ctx, expect, source_address, target_address, why):
    ctx.stage('recovery-needed')
    entry = sp.transition(ctx.dataset, ctx.entry_state(), 'recovery-needed', by=ctx.by, operation=ctx.op, path=ctx.path, now=ctx.now(),
                          expect_generation=expect, bump=True, pending=None,
                          fields={'recovery': dict(sourceAddress=source_address, targetAddress=target_address, at=_iso(ctx.now()))},
                          history_extra={'reason': why[:200]})
    ctx.entry = entry
    return entry


def _run_back_to_core(ctx, pending, kind, why, *, cutover_failed):
    """Auto-revert / rollback body: register the core address under the intent, verify the old source serves,
    clear to migrated.  Anything unverifiable ends in recovery-needed, never rolledBack: true."""
    io, entry = ctx.io, ctx.entry
    domain = entry['domain']
    core_addr, target_addr = pending['address'], pending.get('previousAddress')
    paths = io.sample_paths(entry)
    ctx.stage('reverting')
    try:
        io.hosts_pin(domain, pending.get('newPin'))
        io.register_dns(domain, core_addr, ctx.intent_file(pending))
        ok, why2 = _verify_address(ctx, core_addr, paths)
    except Exception as exc:
        ok, why2 = False, str(exc)
    before = pending['epoch']
    if ok:
        entry = sp.clear_pending(ctx.dataset, 'migrated', expect_generation=before, by=ctx.by, operation=ctx.op, path=ctx.path,
                                 now=ctx.now(), history_extra={'reverted': why[:200]})
        ctx.entry = entry
        words = 'rolled back to the core: %s. %s answers %s again and the old source serves (generation %d).' % (why, domain, core_addr, entry['generation'])
        ctx.say(words)
        ctx.write_receipt(kind, False if cutover_failed else True, words, cutover=dict(done=False, rolledBack=True, recoveryNeeded=False,
                          previousAddress=core_addr, newAddress=target_addr, at=_iso(ctx.now())))
        return 1 if cutover_failed else 0
    entry = _recovery_needed(ctx, before, core_addr, target_addr, why2)
    words = ('recovery-needed: %s; the old source could not be verified (%s). DNS writes for %s are frozen; '
             'run: sudo isle storage recover --dataset %s --generation %d --to migrated|cutover' % (why, why2, domain, ctx.dataset, entry['generation']))
    ctx.say(words)
    ctx.write_receipt(kind, False, words, cutover=dict(done=False, rolledBack=False, recoveryNeeded=True, previousAddress=core_addr,
                      newAddress=target_addr, at=_iso(ctx.now())))
    return 1


def _router_skips(ctx, domain):
    """Cutover reads the router's own managed-names list; it does not trust that authorize pushed it.  Without the
    name there, the router's join scan may register the name again for another device after the move.  A missing
    name is pushed once (the same push authorize makes; it changes no name's address) and read again."""
    io = ctx.io
    names = io.managed_names()
    if names is not None and domain not in names:
        ctx.say('the router\'s managed-names list does not hold %s; pushing the list' % domain)
        try:
            io.push_managed_names()
        except Exception as exc:
            raise Refusal('the router\'s managed-names list does not hold %s and the push failed: %s; no name was moved' % (domain, exc),
                          'checking-placement', True)
        names = io.managed_names()
    if names is None:
        raise Refusal('the router did not show its managed-names list; no name was moved', 'checking-placement', True)
    if domain not in names:
        raise Refusal('the router\'s managed-names list still does not hold %s after a push; no name was moved; '
                      'see: sudo isle dns managed-names list' % domain, 'checking-placement', True)


def do_cutover(ctx):
    req, io = ctx.req, ctx.io
    G = req['generation']
    ctx.stage('checking-placement')
    entry = ctx.load()
    ctx.digest = entry['plan']['digest']
    pending = entry.get('pending')
    domain = entry['domain']
    if isinstance(pending, dict):
        if pending.get('action') == 'cutover-revert' and pending.get('operation') == ctx.op and pending['epoch'] == entry['generation'] == G + 2 \
                and entry['state'] == 'migrated':
            return _run_back_to_core(ctx, pending, 'isle.storage.cutover', 'resumed revert', cutover_failed=True)
        if not _pending_matches(pending, entry, 'cutover', ctx.op, G) or entry['state'] != 'migrated':
            raise _stale(entry, G, 'cutover')
        resumed = True
    else:
        last = (entry.get('history') or [{}])[-1]
        if entry['state'] == 'cutover' and last.get('operation') == ctx.op and last.get('to') == 'cutover' and entry['generation'] == G + 2:
            words = 'already cut over (generation %d); %s is served by %s' % (entry['generation'], domain, entry['device'])
            ctx.say(words)
            ctx.write_receipt('isle.storage.cutover', True, words, cutover=dict(done=True, at=last.get('at'), rolledBack=False, recoveryNeeded=False,
                                           previousAddress=last.get('previousAddress'), newAddress=last.get('newAddress'),
                                           previousPin=last.get('previousPin'), newPin=last.get('newPin'),
                                           syncTimer='receipt-claimed'))   # all from the commit's history row (contract 2.3 step 8)
            return 0
        if entry['state'] != 'migrated' or entry['generation'] != G:
            raise _stale(entry, G, 'cutover')
        resumed = False
    latest = io.source_latest(entry) or {}
    if not resumed:
        plan = _plan_item(ctx, entry['plan'])
        item, block, digest = _item_for(plan, entry['plan'], ctx.dataset)
        if digest != entry['plan']['digest']:
            raise Refusal('the plan no longer matches the placement entry (digest %s, entry %s)' % (digest[:12], entry['plan']['digest'][:12]), 'checking-placement')
        if not latest.get('contentSha'):
            raise Refusal('the source has no published snapshot (latest.json); publish first', 'checking-placement')
        if not io.timer_installed(ctx.dataset):
            raise Refusal('sync trigger not installed: isle-storage-sync@%s.timer is not reported installed on %s' % (ctx.dataset, entry['device']), 'checking-placement')
        _router_skips(ctx, domain)
        target_addr = io.lease_address(entry['device'])
        core_addr = io.core_address()
        if not target_addr or not core_addr:
            raise Refusal('addresses unknown (target %s, core %s); nothing was changed' % (target_addr, core_addr), 'checking-placement', True)
        served = io.fetch_current(domain, target_addr)
        if served != latest['contentSha']:
            raise Refusal('the target serves %s, the source is at %s: run sync first' % ((served or 'nothing')[:12], latest['contentSha'][:12]), 'checking-placement', True)
        if not io.https_check(domain, target_addr, io.sample_paths(entry)):
            raise Refusal('the target at %s does not serve the samples; nothing was changed' % target_addr, 'checking-placement', True)
        ctx.stage('writing-intent')
        pending = _intent(ctx, 'cutover', 'cutover', 'migrated', target_addr, previous_address=core_addr,
                          previous_pin=io.current_pin(domain), new_pin=None, manifestSha=latest.get('manifestSha'))
        entry = sp.write_pending(ctx.dataset, pending, expect_generation=G, by=ctx.by, operation=ctx.op, path=ctx.path, now=ctx.now())
        ctx.entry = entry
        pending = entry['pending']
    else:
        ctx.say('resuming cutover %s: pending intent of this operation found' % ctx.op)
    target_addr, core_addr = pending['address'], pending.get('previousAddress')
    paths = io.sample_paths(entry)
    try:
        ctx.stage('moving-pin')
        io.hosts_pin(domain, pending.get('newPin'))
        ctx.stage('registering-target')
        io.register_dns(domain, target_addr, ctx.intent_file(pending))
        ctx.stage('verifying-cutover')
        ok, why = _verify_address(ctx, target_addr, paths, latest.get('contentSha'))
        if ok:
            name = io.getent(domain)
            if name != target_addr:
                ok, why = False, 'ordinary resolution from the core returns %s, expected %s' % (name, target_addr)
            elif not io.https_check(domain, None, paths):
                ok, why = False, 'https by name from the core failed the samples'
    except Exception as exc:
        ok, why = False, str(exc)
    if not ok:
        revert = _intent(ctx, 'cutover-revert', 'migrated', 'migrated', core_addr, previous_address=target_addr,
                         previous_pin=pending.get('newPin'), new_pin=pending.get('previousPin'))
        try:
            entry = sp.write_pending(ctx.dataset, revert, expect_generation=pending['epoch'], by=ctx.by, operation=ctx.op, path=ctx.path, now=ctx.now())
        except ValueError as exc:
            raise Refusal('cutover failed (%s) and the revert intent could not be written: %s' % (why, exc), 'reverting')
        ctx.entry = entry
        return _run_back_to_core(ctx, entry['pending'], 'isle.storage.cutover', why, cutover_failed=True)
    ctx.stage('committing')
    history = dict(previousAddress=core_addr, newAddress=target_addr, previousPin=pending.get('previousPin'), newPin=pending.get('newPin'))
    fields = {'rollbackManifest': latest['manifestSha']} if latest.get('manifestSha') else None
    try:
        entry = sp.clear_pending(ctx.dataset, 'cutover', expect_generation=pending['epoch'], by=ctx.by, operation=ctx.op, path=ctx.path,
                                 now=ctx.now(), addresses={'source': core_addr, 'target': target_addr}, fields=fields, history_extra=history)
    except ValueError as exc:
        raise Refusal('DNS is verified but the commit was refused: %s. Rerun the same operation to resume.' % exc, 'committing', True)
    ctx.entry = entry
    words = '%s is now served by %s at %s (generation %d). Next: witness from a third device, then accept.' % (domain, entry['device'], target_addr, entry['generation'])
    ctx.say(words)
    ctx.write_receipt('isle.storage.cutover', True, words, digestRecomputed=not resumed,
                      cutover=dict(done=True, at=_iso(ctx.now()), previousAddress=core_addr, newAddress=target_addr, rolledBack=False,
                                   recoveryNeeded=False, previousPin=pending.get('previousPin'), newPin=pending.get('newPin'),
                                   syncTimer='receipt-claimed'))  # the core cannot inspect pol's systemd: the newest migrate receipt's claim, never verified
    return 0


def _last_cutover_row(entry):
    rows = [r for r in entry.get('history') or [] if r.get('to') == 'cutover' and r.get('from') in ('migrated', 'recovery-needed')]
    return rows[-1] if rows else {}


def do_rollback(ctx):
    req, io = ctx.req, ctx.io
    G = req['generation']
    ctx.stage('checking-placement')
    entry = ctx.load()
    ctx.digest = entry['plan']['digest']
    domain = entry['domain']
    pending = entry.get('pending')
    if entry['state'] == 'hosting':
        raise Refusal('rollback from hosting is refused: an accepted placement is reversed only by a new plan', 'checking-placement')
    if isinstance(pending, dict):
        if not (_pending_matches(pending, entry, 'rollback', ctx.op, G) and entry['state'] == 'cutover'):
            raise _stale(entry, G, 'rollback')
        ctx.say('resuming rollback %s' % ctx.op)
    else:
        last = (entry.get('history') or [{}])[-1]
        if entry['state'] == 'migrated' and last.get('operation') == ctx.op and entry['generation'] == G + 2:
            words = 'already rolled back (generation %d)' % entry['generation']
            ctx.say(words)
            ctx.write_receipt('isle.storage.rollback', True, words, cutover=dict(done=False, rolledBack=True))
            return 0
        if entry['state'] != 'cutover' or entry['generation'] != G:
            raise _stale(entry, G, 'rollback')
        core_addr = io.core_address()
        if not core_addr:
            raise Refusal('the core agent address is unknown; nothing was changed', 'checking-placement', True)
        previous = _last_cutover_row(entry)
        pin = previous.get('previousPin') or CORE_PIN
        ctx.stage('writing-intent')
        pending = _intent(ctx, 'rollback', 'migrated', 'cutover', core_addr, previous_address=io.router_dns(domain),
                          previous_pin=io.current_pin(domain), new_pin=pin)
        entry = sp.write_pending(ctx.dataset, pending, expect_generation=G, by=ctx.by, operation=ctx.op, path=ctx.path, now=ctx.now())
        ctx.entry = entry
        pending = entry['pending']
    ctx.say('EMERGENCY ONLY: rolling %s back to the core' % domain)
    return _run_back_to_core(ctx, pending, 'isle.storage.rollback', 'rollback requested', cutover_failed=False)


# ------------------------------------------------------------------ recover

def _live(ctx, op_id):
    if not op_id or op_id == ctx.op:
        return False
    try:
        state = ctx.io.operations_read(op_id).get('state')
    except (OSError, ValueError, KeyError):
        return False  # no journal: no process can hold its lock
    return state == 'running'


def do_recover(ctx):
    req, io = ctx.req, ctx.io
    G, to = req['generation'], req['to']
    ctx.stage('checking-placement')
    entry = ctx.load()
    ctx.digest = entry['plan']['digest']
    domain = entry['domain']
    old = entry.get('pending') if isinstance(entry.get('pending'), dict) else None
    state = entry['state']
    resumable = False
    if state == 'recovery-needed':
        if entry['generation'] != G:
            raise _stale(entry, G, 'recover')
        if to == 'hosting':
            raise Refusal('--to hosting exists only for a pending reconcile-address in hosting', 'checking-placement')
    elif old is not None:
        resumable = (old.get('operation') == ctx.op and old.get('action') == 'recover' and old.get('to') == to
                     and old.get('epoch') == entry['generation'] == G + 1)
        if not resumable:
            if entry['generation'] != G:
                raise _stale(entry, G, 'recover')
            if _live(ctx, old.get('operation')):
                raise Refusal('operation %s is still running for %s; recover never steals from a live operation' % (old['operation'], ctx.dataset),
                              'checking-placement', True)
        if (state == 'hosting') != (to == 'hosting'):
            raise Refusal('cannot recover a pending in %s to %s (hosting is reversed only by a new plan)' % (state, to), 'checking-placement')
    else:
        raise Refusal('placement of %s is %s with no pending intent; nothing to recover' % (ctx.dataset, state), 'checking-placement')
    target_addr = io.lease_address(entry['device'])
    core_addr = io.core_address()
    owner = core_addr if to == 'migrated' else target_addr
    other = target_addr if to == 'migrated' else core_addr
    if not owner:
        raise Refusal('the %s address is unknown; nothing was changed' % ('core' if to == 'migrated' else 'target'), 'checking-placement', True)
    paths = io.sample_paths(entry)
    latest = io.source_latest(entry) or {}
    new_pin = None if to in ('cutover', 'hosting') else (_last_cutover_row(entry).get('previousPin') or CORE_PIN)
    if resumable:
        pending = old
    else:
        ctx.stage('writing-intent')
        extra = {'supersededOperation': old['operation']} if old else {}
        pending = _intent(ctx, 'recover', to, state, owner, previous_address=io.router_dns(domain), previous_pin=io.current_pin(domain),
                          new_pin=new_pin, origin=state, **extra)
        entry = sp.transition(ctx.dataset, state, to, by=ctx.by, operation=ctx.op, path=ctx.path, now=ctx.now(), expect_generation=G,
                              pending=pending, bump=True, recover=True, history_extra=extra)
        ctx.entry = entry
        pending = entry['pending']
    ctx.say('recovering %s to %s' % (ctx.dataset, to))
    try:
        ctx.stage('verifying-recovery')
        router = io.router_dns(domain)
        if router != owner:
            ctx.stage('registering-target' if to != 'migrated' else 'reverting')
            io.hosts_pin(domain, new_pin)
            io.register_dns(domain, owner, ctx.intent_file(pending))
        else:
            io.hosts_pin(domain, new_pin)
        ok, why = _verify_address(ctx, owner, paths, latest.get('contentSha') if to != 'migrated' else None)
        other_serves = bool(other) and bool(io.https_check(domain, other, paths))
    except Exception as exc:
        ok, why, other_serves = False, str(exc), False
    if not ok:
        if pending.get('origin') == 'recovery-needed':
            entry = sp.transition(ctx.dataset, to, 'recovery-needed', by=ctx.by, operation=ctx.op, path=ctx.path, now=ctx.now(),
                                  expect_generation=pending['epoch'], bump=True, pending=None,
                                  fields={'recovery': dict(sourceAddress=core_addr, targetAddress=target_addr, at=_iso(ctx.now()))})
            ctx.entry = entry
            words = 'recover to %s failed (%s); the placement stays recovery-needed at generation %d' % (to, why, entry['generation'])
        else:
            words = 'recover to %s failed (%s); the intent of this operation stays pending, run recover again' % (to, why)
        ctx.say(words)
        ctx.write_receipt('isle.storage.recover', False, words, cutover=dict(done=False, rolledBack=False, recoveryNeeded=True))
        return 1
    fields = {'rollbackManifest': latest['manifestSha']} if to == 'cutover' and latest.get('manifestSha') and not entry.get('rollbackManifest') else None
    entry = sp.clear_pending(ctx.dataset, to, expect_generation=pending['epoch'], by=ctx.by, operation=ctx.op, path=ctx.path, now=ctx.now(),
                             fields=fields, addresses={'source': core_addr} if core_addr else None)
    ctx.entry = entry
    words = 'recovered %s to %s (generation %d); %s answers %s' % (ctx.dataset, to, entry['generation'], domain, owner)
    ctx.say(words)
    ctx.write_receipt('isle.storage.recover', True, words, otherAddressServes=other_serves,
                      cutover=dict(done=to != 'migrated', newAddress=owner, rolledBack=False, recoveryNeeded=False))
    return 0


# ------------------------------------------------------------------ accept

def _witness(row):
    inner = row.get('witness') if isinstance(row.get('witness'), dict) else row
    return inner


def do_accept(ctx):
    req, io = ctx.req, ctx.io
    G = req['generation']
    ctx.stage('checking-placement')
    entry = ctx.load()
    ctx.digest = entry['plan']['digest']
    last = (entry.get('history') or [{}])[-1]
    if entry['state'] == 'hosting' and last.get('operation') == ctx.op and entry['generation'] == G + 1:
        words = 'already accepted (generation %d)' % entry['generation']
        ctx.say(words)
        ctx.write_receipt('isle.storage.accept', True, words)
        return 0
    if entry['state'] != 'cutover' or entry.get('pending') or entry['generation'] != G:
        raise _stale(entry, G, 'accept')
    latest = io.source_latest(entry) or {}
    cut = _parse(_last_cutover_row(entry).get('at'))
    if cut is None:
        raise Refusal('no cutover time in the placement history; cannot judge witness freshness', 'checking-witness')
    rows = [r for r in _jsonl(Path(io.state_dir) / ctx.dataset / 'witnesses.jsonl')]
    chosen, why = None, None
    for row in reversed(rows):
        w = _witness(row)
        if w.get('epoch') != entry['generation'] or w.get('dataset') != ctx.dataset:
            continue
        if w.get('device') is not None and w.get('device') in (entry.get('device'), entry.get('sourceDevice')):
            why = why or 'the witness was posted by %s, which is the %s of this placement; a third device must witness' \
                % (w['device'], 'target' if w['device'] == entry.get('device') else 'source')
            continue
        if w.get('contentSha') != latest.get('contentSha'):
            why = why or 'the witness saw contentSha %s, current is %s' % (str(w.get('contentSha'))[:12], str(latest.get('contentSha'))[:12])
            continue
        at = _parse(w.get('at'))
        if at is None or not (cut - SKEW <= at <= cut + WITNESS_WINDOW):
            why = why or 'the witness is outside the freshness window [cutover - 120 s, cutover + 24 h]'
            continue
        if not io.verify_poster(row):
            why = why or 'the witness poster could not be verified against the CA members'
            continue
        owner = io.lease_address(entry['device'])
        if w.get('resolvedAddress') is not None and owner and w['resolvedAddress'] != owner:
            why = why or 'the witness resolved %s, the target is %s' % (w['resolvedAddress'], owner)
            continue
        chosen = w
        break
    if chosen is None:
        raise Refusal('accept refused: ' + (why or 'no witness for epoch %d is stored' % entry['generation']), 'checking-witness')
    ident = chosen.get('id') or hashlib.sha256(json.dumps(chosen, sort_keys=True).encode()).hexdigest()[:16]
    ctx.stage('accepting')
    done = sp.transition(ctx.dataset, 'cutover', 'hosting', by=ctx.by, operation=ctx.op, path=ctx.path, now=ctx.now(), expect_generation=G, bump=True,
                         fields={'acceptedWitness': {'id': ident, 'epoch': entry['generation']}, 'acceptedManifest': latest.get('manifestSha')})
    ctx.entry = done
    words = '%s accepted: hosted by %s (generation %d, witness of epoch %d)' % (ctx.dataset, done['device'], done['generation'], entry['generation'])
    ctx.say(words)
    ctx.write_receipt('isle.storage.accept', True, words)
    return 0


# ------------------------------------------------------------------ reconcile-address

def do_reconcile_address(ctx):
    req, io = ctx.req, ctx.io
    G = req['generation']
    ctx.stage('checking-placement')
    entry = ctx.load()
    ctx.digest = entry['plan']['digest']
    domain, state = entry['domain'], entry['state']
    pending = entry.get('pending')
    if isinstance(pending, dict):
        if not (_pending_matches(pending, entry, 'reconcile-address', ctx.op, G) and state in ('cutover', 'hosting')):
            raise _stale(entry, G, 'reconcile-address')
        ctx.say('resuming reconcile-address %s' % ctx.op)
        owner = pending['address']
    else:
        if state not in ('cutover', 'hosting') or entry['generation'] != G:
            raise _stale(entry, G, 'reconcile-address')
        owner = io.lease_address(entry['device'])
        if not owner:
            raise Refusal('the lease of %s is unknown; nothing was changed' % entry['device'], 'checking-placement', True)
        router = io.router_dns(domain)
        if router == owner:
            words = 'the router already answers %s for %s; nothing to do' % (owner, domain)
            ctx.say(words)
            ctx.write_receipt('isle.storage.reconcile-address', True, words)
            return 0
        ctx.stage('writing-intent')
        pending = _intent(ctx, 'reconcile-address', state, state, owner, previous_address=router, **{'from-address': router, 'to-address': owner})
        entry = sp.write_pending(ctx.dataset, pending, expect_generation=G, by=ctx.by, operation=ctx.op, path=ctx.path, now=ctx.now())
        ctx.entry = entry
        pending = entry['pending']
    paths = io.sample_paths(entry)
    try:
        ctx.stage('registering-target')
        io.register_dns(domain, owner, ctx.intent_file(pending))
        ctx.stage('verifying-cutover')
        ok, why = _verify_address(ctx, owner, paths)
    except Exception as exc:
        ok, why = False, str(exc)
    if not ok:
        words = 'reconcile-address failed (%s); the intent stays pending: rerun this operation or use recover' % why
        ctx.say(words)
        ctx.write_receipt('isle.storage.reconcile-address', False, words)
        return 1
    entry = sp.clear_pending(ctx.dataset, state, expect_generation=pending['epoch'], by=ctx.by, operation=ctx.op, path=ctx.path, now=ctx.now(),
                             addresses={'target': owner}, history_extra={'fromAddress': pending.get('from-address'), 'toAddress': owner})
    ctx.entry = entry
    words = '%s now answers %s (owner and state unchanged; generation %d)' % (domain, owner, entry['generation'])
    ctx.say(words)
    ctx.write_receipt('isle.storage.reconcile-address', True, words,
                      cutover=dict(done=True, previousAddress=pending.get('from-address'), newAddress=owner, rolledBack=False))
    return 0


# ------------------------------------------------------------------ relay (B2, not journaled)

def _canon(row):
    return json.dumps(row, sort_keys=True, separators=(',', ':'))


def _relay_rows(doc, listkey, bodykey):
    """Backend door rows -> the local shape the readers take: {<bodykey>, posterPresented, device, dataset, name, receivedAt}.
    seen/verified are left out: they change on an idempotent re-post and must not make a row new."""
    rows = doc.get(listkey) if isinstance(doc, dict) else None
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or not isinstance(r.get(bodykey), dict):
            continue
        out.append({bodykey: r[bodykey], 'posterPresented': r.get('posterPresented') if isinstance(r.get('posterPresented'), dict) else {},
                    'device': r.get('device'), 'dataset': r.get('dataset'), 'name': r.get('name'), 'receivedAt': r.get('receivedAt')})
    return out


def _relay_append(directory, seen, seen_path, log_name, rows):
    added = 0
    with (directory / log_name).open('a') as log:
        for row in rows:
            key = hashlib.sha256(_canon(row).encode()).hexdigest()
            if key in seen:
                continue
            log.write(json.dumps(row, sort_keys=True) + '\n')
            seen.add(key)
            added += 1
        log.flush()
        os.fsync(log.fileno())
    if added:
        seen_path.write_text(''.join(k + '\n' for k in sorted(seen)))
    return added


def _row_body(io, dataset, digest):
    for r in reversed(_jsonl(Path(io.state_dir) / dataset / 'inbound-receipts.jsonl')):
        body = r.get('receipt') if isinstance(r.get('receipt'), dict) else r
        if (body.get('plan') or {}).get('digest') == digest:
            return body
    return {}


def do_relay(io):
    """Pull the backend's stored receipts and witnesses into the core's local evidence files, then mark-migrate
    every authorized entry whose stored receipt qualifies.  Evidence only; never writes the placement itself."""
    try:
        datasets = sorted(sp.read(Path(io.placement_path))['datasets'].items())
    except (OSError, ValueError) as exc:
        io.out('storage: relay: placement unreadable: %s' % exc)
        return 1
    failures, received, invoked, unverified = 0, 0, 0, 0
    for dataset, entry in datasets:
        if not isinstance(entry, dict):
            continue
        directory = Path(io.state_dir) / dataset
        seen_path = directory / 'relay-seen.txt'
        try:
            directory.mkdir(parents=True, exist_ok=True)
            seen = set(seen_path.read_text().split()) if seen_path.exists() else set()
            for listkey, bodykey, log_name in (('receipts', 'receipt', 'inbound-receipts.jsonl'), ('witnesses', 'witness', 'witnesses.jsonl')):
                url = '%s/api/islemesh/storage/%s?dataset=%s' % (API, listkey, dataset)
                received += _relay_append(directory, seen, seen_path, log_name, _relay_rows(io.http_get_json(url), listkey, bodykey))
        except Exception as exc:
            failures += 1
            io.out('storage: relay: %s: %s' % (dataset, exc))
            continue
        digest = (entry.get('plan') or {}).get('digest')
        if entry.get('state') != 'authorized' or not digest:
            continue
        try:
            row = qualifying_receipt(io, entry, dataset, digest)
        except Refusal as why:
            if why.code == 'poster':
                action = _row_body(io, dataset, digest).get('actionId')
                io.out('relay: receipt %s poster not verified; not transitioning' % action)
                unverified += 1
            else:
                io.out('relay: %s authorized, not yet migratable: %s' % (dataset, why.words))
            continue
        action = (row.get('receipt') if isinstance(row.get('receipt'), dict) else row).get('actionId') or ''
        acted_key = '%s %s' % (action, digest)
        acted_path = directory / 'relay-acted.txt'
        acted = set(acted_path.read_text().splitlines()) if acted_path.exists() else set()
        if acted_key in acted:
            io.out('relay: receipt %s for %s already acted on; not re-run' % (action or digest[:12], dataset))
            continue
        invoked += 1
        code = io.run_mark_migrated(dataset, digest)
        io.out('relay: mark-migrated %s %s' % (dataset, 'done' if code == 0 else 'exited %s' % code))
        if code == 0:
            with acted_path.open('a') as handle:
                handle.write(acted_key + '\n')
        else:
            failures += 1
    io.out('relay: %d dataset(s), %d new row(s), %d mark-migrated run(s)%s%s'
           % (len(datasets), received, invoked, ', %d failure(s)' % failures if failures else '',
              ', %d receipt(s) not verified' % unverified if unverified else ''))
    return 1 if failures else 0


HANDLERS = {'authorize': do_authorize, 'mark-migrated': do_mark_migrated, 'cutover': do_cutover, 'rollback': do_rollback,
            'recover': do_recover, 'accept': do_accept, 'reconcile-address': do_reconcile_address}
KIND = {'authorize': 'isle.storage.authorize', 'mark-migrated': 'isle.storage.mark-migrated', 'cutover': 'isle.storage.cutover',
        'rollback': 'isle.storage.rollback', 'recover': 'isle.storage.recover', 'accept': 'isle.storage.accept',
        'reconcile-address': 'isle.storage.reconcile-address'}


def run(argv, *, env=None, io=None):
    """Run one core verb; returns the exit code (0 done, 1 refused or failed, 2 usage)."""
    env = os.environ if env is None else env
    io = io or RealIo(env)
    argv = list(argv)
    if argv[:1] != ['storage']:
        argv = ['storage'] + argv
    if argv[1:2] == ['relay']:
        if argv[2:]:
            print('storage: relay takes no arguments', file=sys.stderr)
            return 2
        return do_relay(io)
    try:
        req = sc.parse_request(argv)
    except ValueError as exc:
        print('storage: ' + str(exc), file=sys.stderr)
        return 2
    handler = HANDLERS.get(req['verb'])
    if handler is None:
        print('storage: %s does not run on the core' % req['verb'], file=sys.stderr)
        return 2
    ctx = Ctx(req, env, io)
    if Path(ctx.path).exists():
        sp.mirror_claims(ctx.path)
    try:
        if req['verb'] == 'authorize':
            return handler(ctx)
        with io.lease():
            return handler(ctx)
    except Refusal as refusal:
        io.out('storage: ' + refusal.words)
        try:
            ctx.write_receipt(KIND[req['verb']], False, refusal.words, failedStage=refusal.stage, retryable=refusal.retryable)
        except Exception:
            pass
        return 1
    except (ValueError, OSError) as exc:
        io.out('storage: ' + str(exc))
        return 1
    finally:
        _post_view(io)   # strictly after the handler returned: every placement.lock transition has committed by now


def _post_view(io):
    """Hand the placement view to the backend after the verb; the placement write already happened, so a
    failure here is words only (the 120 s push repeats it)."""
    post = getattr(io, 'post_placement_view', None)
    if post is None:
        return
    try:
        post()
    except BaseException as exc:
        if not isinstance(exc, Exception):
            raise
        try:
            io.out('storage: placement view not posted: %s' % exc)
        except Exception:
            pass


def main(argv):
    if os.geteuid() != 0:
        print('storage: run with sudo on the core', file=sys.stderr)
        return 1
    return run(argv, env=os.environ, io=RealIo(os.environ))


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
