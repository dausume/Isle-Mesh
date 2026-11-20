#!/usr/bin/env python3
"""`isle storage migrate`: the stage driver of the target side of a storage migration (stm-4b).

Runs stages 1-8 of docs/STORAGE-MIGRATION-CONTRACT.md section 2.2 in order and writes the
section 2.6 receipt.  Every outside effect is injected so the stages can be tested without a
network, a disk or a container runtime:

  http   get_json(url, resolve=None), get_bytes(url, resolve=None, max_bytes=None),
         stream_to(url, sink, resolve=None, expected_size=None), post_json(url, payload, token)
         (CurlHttp is the real one: curl with the isle root CA, never -k, never a bare IP, no redirects)
  deps   Deps(): identity, findmnt_json, run/statvfs (preflight), fstatvfs (receive), deploy,
         agent_address, lease_path, sleep, monotonic
  now    callable returning an ISO timestamp string
  stage_hook(label, info) is called once as each stage begins.

Nothing here decides ownership: the core-side `mark-migrated` reads the receipt (section 5).
Stages 1-2 refuse before anything is written on the target disk.  Stages 6-8 hold the
device-update lease SHARED (section 2.1); the multi-hour copy (stages 3-5) holds nothing.
"""
import contextlib
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import operations
import storage_contract as sc
import storage_mount
import storage_preflight
import storage_receive as sr
from storage_receive import ReceiveError

SCRIPTS = Path(__file__).resolve().parent
API_HOST = 'api.polari.isle'
API = 'https://' + API_HOST
CA_FILE = '/etc/isle-mesh/ca/isle-root.crt'
ENROLLMENT_FILE = '/etc/isle-mesh/ca/member-enrollment.json'
STATE_DIR = '/var/lib/isle-mesh/storage'
UNIT_DIR = '/etc/systemd/system'
MiB = 1024 * 1024
MANIFEST_MAX = 64 * MiB
READY_SECONDS = 60
TAKEN = 'already stored for this actionId and attempt'   # the core's 409 words for a number it already holds
TAKEN_TRIES = 50
PRE_DISK_STAGES = sc.STAGES[:2]  # stages 1-2 write nothing on the target disk


class HttpError(Exception):
    """A failed request; status is the HTTP status when known, else None (transport failure)."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- real HTTP: curl

def check_url(url):
    """Return the parsed URL when it is a trusted isle HTTPS name, else raise ValueError.
    Refused: other schemes, userinfo, fragments, ports other than 443 and any bare IP address."""
    parts = urlsplit(url)
    host = parts.hostname
    if parts.scheme != 'https' or not host or parts.username or parts.password or parts.fragment \
            or parts.port not in (None, 443):
        raise ValueError('Refusing URL (https with a .isle name only): %r' % url)
    bare = True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        bare = False
    if bare:
        raise ValueError('Refusing a bare IP address in a URL: %r' % url)
    if not host.endswith('.isle'):
        raise ValueError('Refusing a name outside .isle: %r' % host)
    return parts


class CurlHttp:
    """curl with the isle root CA.  --fail, --max-redirs 0, never -k, never a bare-IP URL."""

    def __init__(self, ca=CA_FILE, popen=subprocess.Popen, run=subprocess.run):
        self.ca, self.popen, self.run = ca, popen, run

    def _argv(self, url, resolve, max_time):
        parts = check_url(url)
        argv = ['curl', '--http1.1', '--fail', '--silent', '--show-error', '--max-redirs', '0',
                '--cacert', self.ca, '--connect-timeout', '10', '--max-time', str(int(max_time))]
        if resolve is not None:
            host, address = resolve
            ipaddress.ip_address(address)  # ValueError for anything that is not an address
            if host != parts.hostname:
                raise ValueError('--resolve host differs from the URL host.')
            argv += ['--resolve', '%s:443:%s' % (host, address)]
        return argv + [url]

    @staticmethod
    def _failure(code, stderr):
        text = (stderr or b'').decode('utf-8', 'replace').strip()
        found = re.search(r'returned error: (\d{3})', text)
        return HttpError('curl exit %s: %s' % (code, text[:300]), int(found.group(1)) if found else None)

    def _read(self, url, resolve, max_time, limit, sink=None):
        process = self.popen(self._argv(url, resolve, max_time), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        chunks, total = [], 0
        try:
            while True:
                block = process.stdout.read(MiB)
                if not block:
                    break
                total += len(block)
                if limit is not None and total > limit:
                    raise HttpError('Response is larger than the allowed %d bytes.' % limit)
                if sink is None:
                    chunks.append(block)
                else:
                    sink.write(block)
        except BaseException:
            process.kill()
            process.wait()
            raise
        stderr = process.stderr.read()
        code = process.wait()
        if code != 0:
            raise self._failure(code, stderr)
        return b''.join(chunks)

    def get_bytes(self, url, resolve=None, max_bytes=None):
        return self._read(url, resolve, 120, max_bytes)

    def get_json(self, url, resolve=None):
        try:
            return json.loads(self._read(url, resolve, 30, 16 * MiB))
        except ValueError as error:
            if isinstance(error, HttpError):
                raise
            raise HttpError('Response is not JSON: %s' % error)

    def stream_to(self, url, sink, resolve=None, expected_size=None):
        limit = expected_size
        max_time = max(600, (expected_size or 0) // 262144 + 120)
        self._read(url, resolve, max_time, limit, sink)

    def post_json(self, url, payload, token=None):
        """POST payload as JSON; return (status, text).  The token goes through curl's stdin config, never argv."""
        argv = self._argv(url, None, 60)
        argv = [a for a in argv if a not in ('--fail',)]
        with tempfile.NamedTemporaryFile('w', suffix='.json') as body:
            os.chmod(body.name, 0o600)
            json.dump(payload, body)
            body.flush()
            argv[-1:] = ['--request', 'POST', '--header', 'Content-Type: application/json',
                         '--data-binary', '@' + body.name, '--output', '-', '--write-out', '\n%{http_code}',
                         '--config', '-', url]
            config = ('header = "Authorization: Bearer %s"\n' % token) if token else ''
            result = self.run(argv, input=config.encode(), capture_output=True)
        if result.returncode != 0:
            raise self._failure(result.returncode, result.stderr)
        text, _, status = result.stdout.decode('utf-8', 'replace').rpartition('\n')
        return int(status), text[:500]


# ---------------------------------------------------------------- injectable dependencies

def default_identity():
    from member_preflight import identity
    return identity()


def default_agent_address():
    """This device's isle-facing agent address, via agent-address.sh."""
    result = subprocess.run(['bash', '-c', 'source "$1"; isle_agent_address', 'x', str(SCRIPTS / 'agent-address.sh')],
                            capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise ValueError('Cannot determine this device\'s agent address.')
    return result.stdout.strip()


def default_deploy(app, extra_args, *, http=None, env=None, install=None, dataset=None, served=None):
    """Staged install of `app`, in-process through catalog_artifacts.install.

    The migrate operation already runs root-journaled, so ISLE_OPERATION_ID / ISLE_OPERATION_DIR are in the
    environment (operations.py sets them; install() reads them for stage() and readiness.json).  The catalog
    entry is fetched the way store.sh does (GET /api/islemesh/catalog/<name>) and written to
    $ISLE_OPERATION_DIR/catalog.json, which install() reads.  extra_args is ['--staged', '--bind', 'K=V', ...];
    install() always runs staged and gets each K=V bind string; `served` is the placement domain the host must
    answer for (block.domain, e.g. apt.isle), forwarded so the deploy registers and certifies that name.
    A catalog HttpError propagates.
    """
    import catalog_artifacts
    install = install or catalog_artifacts.install
    env = os.environ if env is None else env
    binds = [extra_args[i + 1] for i, flag in enumerate(extra_args[:-1]) if flag == '--bind']
    document = (http or CurlHttp()).get_json('%s/api/islemesh/catalog/%s' % (API, quote(app)))
    if not (isinstance(document, dict) and document.get('ok') is not False and isinstance(document.get('entry'), dict)):
        raise ValueError('no such catalog entry: %s' % app)
    path = Path(env['ISLE_OPERATION_DIR']) / 'catalog.json'
    tmp = path.with_name(path.name + '.tmp')
    with os.fdopen(os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as handle:
        json.dump(document, handle)
    os.replace(str(tmp), str(path))
    return install(str(path), app, staged=True, binds=binds, **({'dataset': dataset} if dataset else {}),
                   **({'served': served} if served else {}))


def default_bridge_address():
    """The agent's isle-agent-net bridge address, or None.  A member's Isle-facing agent address is a macvlan child of
    this host's NIC, which the host itself can never reach (No route to host), so local probes go via the bridge.
    Same lookup and checks as hosts-reconcile.sh / app-deploy.sh."""
    try:
        driver = subprocess.run(['docker', 'network', 'inspect', 'isle-agent-net', '--format', '{{.Driver}}'],
                                capture_output=True, text=True, timeout=30)
        found = subprocess.run(['docker', 'inspect', 'isle-remote-agent', '--format',
                                '{{(index .NetworkSettings.Networks "isle-agent-net").IPAddress}}'],
                               capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if driver.returncode != 0 or driver.stdout.strip() != 'bridge' or found.returncode != 0:
        return None
    try:
        address = ipaddress.ip_address(found.stdout.strip())
    except ValueError:
        return None
    if address.version != 4 or address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local:
        return None
    return str(address)


def default_systemctl(args):
    """Run `systemctl <args>` and return its exit code (127 when systemctl is missing).
    `show` returns (exit code, stdout) instead, because its answer is the point."""
    try:
        if args and args[0] == 'show':
            done = subprocess.run(['systemctl', *args], timeout=120, capture_output=True, text=True)
            return done.returncode, done.stdout
        return subprocess.run(['systemctl', *args], timeout=120).returncode
    except (OSError, subprocess.SubprocessError):
        return 127


class Deps:
    """Everything outside the process that a stage touches.  Override by keyword in tests."""

    def __init__(self, **overrides):
        self.identity = default_identity
        self.findmnt_json = sr.default_findmnt_json
        self.run = subprocess.run          # for storage_preflight.inspect
        self.statvfs = os.statvfs          # for storage_preflight.inspect
        self.fstatvfs = os.fstatvfs        # for receive and the stage 3 capacity re-check
        self.deploy = default_deploy
        self.agent_address = default_agent_address
        self.probe_address = default_bridge_address   # local readiness probe target, or None
        self.lease_path = operations.ROOT / 'app-device-update.lock'
        self.systemctl = default_systemctl      # systemctl(args) -> exit code; `show` -> (exit code, stdout)
        self.sleep = time.sleep
        self.monotonic = time.monotonic
        for key, value in overrides.items():
            if not hasattr(self, key):
                raise TypeError('Unknown dependency: ' + key)
            setattr(self, key, value)


def default_now():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- pure helpers

def object_url(domain, path=None, sha=None):
    """Return an https URL on the served name.  Paths are percent-encoded here and only here."""
    if sha is not None:
        return 'https://%s/snapshot/objects/%s' % (domain, sha)
    return 'https://%s/%s' % (domain, quote(path, safe='/'))


def sample_entries(document):
    """Return three deterministic file entries: sorted by path, indices 0, n//2 and n-1 (fewer when n is small)."""
    files = sorted((e for e in document['entries'] if e.get('kind') == 'file'), key=lambda e: e['path'])
    picks = []
    for index in ([0, len(files) // 2, len(files) - 1] if files else []):
        if files[index] not in picks:
            picks.append(files[index])
    return picks


def release_sha(document):
    """Return the release descriptor sha256 of a catalog entry document, or None when it is not published."""
    entry = document.get('entry', document) if isinstance(document, dict) else {}
    plan = entry.get('install_plan') if isinstance(entry, dict) else None
    release = (plan or {}).get('release') if isinstance(plan, dict) else None
    if not isinstance(release, dict):
        release = entry.get('release') if isinstance(entry, dict) else None
    if not isinstance(release, dict):
        return None
    sha = release.get('sha256') or release.get('manifestSha256')
    return sha if isinstance(sha, str) else None


def _unwrap(document, key):
    inner = document.get(key) if isinstance(document, dict) else None
    return inner if isinstance(inner, dict) else document


def _fetch(call, what, stage, *args, **kwargs):
    """Run an http call; report a failure as a ReceiveError (retryable unless the server said 4xx)."""
    try:
        return call(*args, **kwargs)
    except HttpError as error:
        retryable = error.status is None or error.status >= 500
        raise ReceiveError('Cannot fetch %s: %s' % (what, error), retryable=retryable, stage=stage)
    except ValueError as error:
        raise ReceiveError('Cannot fetch %s: %s' % (what, error), stage=stage)


def fetch_plan(http, deps, req, stage, with_source=True):
    """Stage 1 (and the stage 7 currency check): return the verified plan item, digest and placement view."""
    def refuse(message):
        raise ReceiveError(message, stage=stage)
    try:
        device = deps.identity()
    except (ValueError, OSError) as error:
        refuse('This device\'s identity is not usable: %s' % error)
    query = 'id=%s&revision=%d' % (req['plan'], req['revision'])
    plan = _unwrap(_fetch(http.get_json, 'the plan', stage, '%s/api/islemesh/placement-plan?%s' % (API, query)), 'plan')
    if not isinstance(plan, dict) or plan.get('id') != req['plan'] or plan.get('revision') != req['revision']:
        refuse('The plan service did not return plan %s revision %d.' % (req['plan'], req['revision']))
    if plan.get('active') is not True:
        refuse('Plan %s revision %d is not active (superseded or inactive).' % (req['plan'], req['revision']))
    items = [i for i in plan.get('items') or [] if isinstance(i, dict) and i.get('id') == req['item']]
    if len(items) != 1:
        refuse('Plan item %s was not found in the plan.' % req['item'])
    item = items[0]
    if item.get('kind') != 'storage':
        refuse('Plan item %s is not a storage item.' % req['item'])
    if item.get('state') != 'planned':
        refuse('Plan item %s is %s, not planned.' % (req['item'], item.get('state')))
    if item.get('device') != device:
        refuse('This device is %s but the plan names %s.' % (device, item.get('device')))
    if (item.get('storage') or {}).get('dataset') != req['dataset']:
        refuse('The plan item is for dataset %s, not %s.' % ((item.get('storage') or {}).get('dataset'), req['dataset']))
    source_sha = ((item.get('storage') or {}).get('source') or {}).get('snapshotSha256')
    if source_sha is None:
        refuse('The plan has no source snapshot yet (snapshotSha256 is null).')
    try:
        block = sc.validate_storage_item(item)
        release = sc.validate_hosting_release(item.get('hostingRelease'))
        digest = sc.storage_digest(req['plan'], req['revision'], item, release)
    except ValueError as error:
        refuse('The plan item is not valid: %s' % error)
    if digest != req['digest']:
        refuse('The plan changed since it was reviewed (digest %s…, reviewed %s…).' % (digest[:12], req['digest'][:12]))
    view = _unwrap(_fetch(http.get_json, 'the placement view', stage,
                          '%s/api/islemesh/storage/placement?dataset=%s' % (API, quote(req['dataset']))), 'view')
    if not isinstance(view, dict) or not view.get('state'):
        refuse('No placement view for dataset %s: failing closed.' % req['dataset'])
    if view.get('state') not in ('authorized', 'migrated'):
        refuse('Placement of %s is %s, not authorized or migrated.' % (req['dataset'], view.get('state')))
    if view.get('device') != device or view.get('domain') != block['domain']:
        refuse('The placement view names %s for %s, not this device and the plan\'s domain.' % (view.get('device'), view.get('domain')))
    if (view.get('plan') or {}).get('digest') != digest:
        refuse('The placement entry was authorized for a different plan digest.')
    generation = view.get('generation')
    if type(generation) is not int or generation < 0:
        refuse('The placement view has no integer generation.')
    out = dict(device=device, item=item, block=block, release=release, digest=digest, view=view,
               generation=generation, address=None)
    if with_source:
        rows = _fetch(http.get_json, 'the device list', stage, API + '/api/islemesh/devices')
        rows = rows.get('devices', []) if isinstance(rows, dict) else rows
        found = [r for r in rows if isinstance(r, dict) and r.get('name') == item.get('originDevice')]
        address = found[0].get('agent_address') if found else None
        try:
            ipaddress.ip_address(address)
        except ValueError:
            refuse('Source address unknown for %s.' % item.get('originDevice'))
        out['address'] = address
    return out


# ---------------------------------------------------------------- the driver

class Migration:
    """State and stages of one migrate attempt."""

    def __init__(self, req, env, http, now, hook, deps):
        self.req, self.env, self.http, self.now, self.hook, self.deps = req, env, http, now, hook, deps
        self.op_id = env.get('ISLE_OPERATION_ID') or 'direct-%d' % os.getpid()
        self.op_dir = Path(env['ISLE_OPERATION_DIR']) if env.get('ISLE_OPERATION_DIR') else None
        self.state_dir = Path(env.get('ISLE_STORAGE_STATE_DIR') or STATE_DIR)
        self.unit_dir = Path(env.get('ISLE_STORAGE_UNIT_DIR') or UNIT_DIR)
        self.enrollment = Path(env.get('ISLE_ENROLLMENT_FILE') or ENROLLMENT_FILE)
        self.current = sc.STAGES[0]
        self.stack = contextlib.ExitStack()
        self.root = None
        self.p = None           # the verified plan (fetch_plan result)
        self.dataset_path = None
        self.rel = None
        self.address = None
        self.document = self.raw = self.manifest = self.content = None
        self.unit_path = None
        self.receipt = self._skeleton()

    # ---- bookkeeping
    def _attempt(self):
        with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
            value = json.loads((self.op_dir / 'status.json').read_text())['attempt']
            if type(value) is int and value >= 1:
                return value
        return 1

    def _skeleton(self):
        plan = dict(id=self.req['plan'], revision=self.req['revision'], itemId=self.req['item'], digest=self.req['digest'])
        receipt = sc.receipt_skeleton('isle.storage.migrate', self.op_id, self._attempt() if self.op_dir else 1,
                                      plan, self.req['dataset'], None)
        receipt.update(schemaVersion=2, ok=False, retryable=False, failedStage=None, epoch=None, contentSha=None,
                       manifestSha=None, posted=False, placement=None)
        return receipt

    def begin(self, label):
        self.current = label
        if self.env.get('ISLE_OPERATION_ID'):
            operations.stage(self.env['ISLE_OPERATION_ID'], label)
        if self.hook:
            self.hook(label, dict(operation=self.op_id, dataset=self.req['dataset']))

    def done(self, label, **extra):
        self.receipt['stages'][label] = dict(ok=True, at=self.now(), **extra)

    def mounts(self):
        path = self.dataset_path if self.dataset_path and os.path.isdir(self.dataset_path) else None
        path = path or (self.p['block']['target']['mountpoint'] if self.p else None)
        if not path:
            return []
        try:
            return list(self.deps.findmnt_json(path).get('filesystems', []))
        except (OSError, ValueError, subprocess.SubprocessError, AttributeError):
            return []

    # ---- stage 1
    def fetching_plan(self):
        self.begin('fetching-plan')
        self.p = fetch_plan(self.http, self.deps, self.req, self.current)
        p, r = self.p, self.receipt
        block = p['block']
        self.address = p['address']
        r.update(domain=block['domain'], digestRecomputed=True, hostingRelease=p['release'],
                 actionId=sc.action_id('migrate', block['dataset'], p['device'], p['digest']),
                 placement=dict(state=p['view']['state'], generation=p['generation']), epoch=p['generation'])
        r['source'].update(device=p['item']['originDevice'], snapshotSha256=block['source']['snapshotSha256'],
                           totalBytes=block['source']['totalBytes'], entries=block['source']['entries'],
                           origin=self.address, contentSha256=block['source'].get('contentSha256'))
        r['target'].update(device=p['device'], filesystemUuid=block['target']['filesystemUuid'])
        self.done('fetching-plan')

    # ---- stage 2
    def preflight_storage(self):
        self.begin('preflight-storage')
        block = self.p['block']
        target, refuse = block['target'], self._refuser()
        mount = target['mountpoint']
        if os.path.normpath(mount) == '/':
            refuse('The mountpoint is /: refusing the root filesystem.')
        self.dataset_path = mount.rstrip('/') + '/' + target['relativePath']
        required = sc.required_capacity(block['source']['totalBytes'])
        try:
            info = storage_preflight.inspect(self.dataset_path, target['filesystemUuid'], required,
                                             run=self.deps.run, statvfs=self.deps.statvfs)
        except (ValueError, OSError, subprocess.SubprocessError, KeyError) as error:
            refuse('Storage preflight refused: %s' % error)
        self._require_mount_unit(mount, refuse)
        self.rel = target['relativePath'].split('/')
        self.root = sr.MountRoot(mount, target['filesystemUuid'], self.deps.findmnt_json)
        self.stack.callback(self.root.close)
        try:
            binding = sr.read_binding(self.root, self.rel)
        except ValueError as error:
            refuse('binding.json is damaged: %s' % error)
        if binding is not None and (binding.get('dataset') != block['dataset']
                                    or binding.get('filesystemUuid') != target['filesystemUuid']):
            refuse('binding.json on the disk names another dataset or filesystem.')
        self._probe()
        rows = self.mounts()
        row = rows[0] if len(rows) == 1 else {}
        self.receipt['target'].update(mount=mount, stDev='%d:%d' % (os.major(self.root.st_dev), os.minor(self.root.st_dev)),
                                      path=self.dataset_path, availableBytesBefore=info['availableBytes'],
                                      filesystemUuidObserved=row.get('uuid'), mountSourceObserved=row.get('source'))
        self.receipt['mountsBefore'] = rows
        self.done('preflight-storage', availableBytes=info['availableBytes'], requiredBytes=required)

    def _require_mount_unit(self, mount, refuse):
        """Contract 2.2 stage 2: the mount must be an active systemd mount unit (BindsTo= in the gate needs one)."""
        unit = storage_mount.mount_unit_name(mount)
        try:
            answer = self.deps.systemctl(['show', '-p', 'Id,ActiveState', unit])
        except (OSError, subprocess.SubprocessError):
            answer = None
        fields = {}
        if isinstance(answer, tuple) and len(answer) == 2 and answer[0] == 0:
            for line in str(answer[1]).splitlines():
                key, _, value = line.partition('=')
                fields[key.strip()] = value.strip()
        if fields.get('ActiveState') != 'active' or fields.get('Id') != unit:
            refuse('mount %s is not an active systemd mount unit (fstab entries qualify through systemd-fstab-generator)' % mount)

    def _probe(self):
        """Create, fsync and unlink a file under the dataset parent, through directory descriptors."""
        name = '.isle-probe-%s' % re.sub(r'[^A-Za-z0-9_.-]', '', self.op_id)[:60]
        try:
            with self.root.opened(self.rel[:-1]) as parent:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                try:
                    os.write(fd, b'probe')
                    os.fsync(fd)
                finally:
                    os.close(fd)
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(name, dir_fd=parent)
        except OSError as error:
            raise ReceiveError('The target is not writable: %s' % error, stage=self.current)

    def _refuser(self, retryable=False):
        def refuse(message, retryable=retryable):
            raise ReceiveError(message, retryable=retryable, stage=self.current)
        return refuse

    # ---- stage 3
    def fetching_manifest(self):
        self.begin('fetching-manifest')
        block, refuse = self.p['block'], self._refuser()
        source = block['source']
        domain, sha = block['domain'], source['snapshotSha256']
        url = 'https://%s/snapshot/manifests/%s.json' % (domain, sha)
        raw = _fetch(self.http.get_bytes, 'the manifest', self.current, url, resolve=(domain, self.address),
                     max_bytes=MANIFEST_MAX)
        if len(raw) > MANIFEST_MAX:
            refuse('The manifest is larger than 64 MiB.')
        if sr.manifest_sha(raw) != sha:
            refuse('The manifest does not hash to the planned snapshotSha256.')
        try:
            document = json.loads(raw)
            sr.validate_manifest(document)  # schema, every path, totalBytes
        except ValueError as error:
            refuse('The manifest is not acceptable: %s' % error)
        binding = document.get('binding')
        if not isinstance(binding, dict) or binding.get('dataset') != block['dataset']:
            refuse('The manifest binding names another dataset.')
        for key, wanted in (('planId', self.req['plan']), ('itemId', self.req['item'])):
            if binding.get(key) is not None and binding.get(key) != wanted:
                refuse('The manifest binding %s does not match the request.' % key)
        content = sr.content_sha(document)
        if source.get('contentSha256') is not None and content != source['contentSha256']:
            refuse('The manifest content does not hash to the planned contentSha256.')
        if document['totalBytes'] != source['totalBytes']:
            refuse('The manifest totalBytes differ from the plan.')
        self._capacity(document)
        self.raw, self.document, self.manifest, self.content = raw, document, sha, content
        self.receipt.update(manifestSha=sha, contentSha=content)
        self.receipt['source']['contentSha256'] = content
        self.done('fetching-manifest', entries=len(document['entries']), totalBytes=document['totalBytes'])

    def _store_plan(self, document):
        try:
            with self.root.opened(self.rel + ['store'], create=False) as store:
                return sr.plan_receive(document, store)
        except FileNotFoundError:
            return sr.plan_receive(document, None)

    def _capacity(self, document):
        plan = self._store_plan(document)
        info = self.deps.fstatvfs(self.root.fd)
        need = plan['bytesToFetch'] + plan['largestFile'] + sr.headroom(info.f_blocks * info.f_frsize)
        if info.f_bavail * info.f_frsize < need:
            raise ReceiveError('Insufficient space: %d bytes free, %d needed (data, largest file, headroom).'
                               % (info.f_bavail * info.f_frsize, need), stage=self.current)

    # ---- stage 4
    def receiving_content(self):
        self.begin('receiving-content')
        domain = self.p['block']['domain']
        plan = self._store_plan(self.document)
        files_total = sum(1 for e in self.document['entries'] if e['kind'] == 'file')

        def fetch(entry, sink):
            self.http.stream_to(object_url(domain, sha=entry['sha256']), sink, resolve=(domain, self.address),
                                expected_size=entry['size'])

        def progress(done, total, files, files_all):
            if self.op_dir:
                operations.atomic(self.op_dir / 'progress.json', dict(
                    stage='receiving-content', bytes=done, totalBytes=total, files=files, filesTotal=files_all,
                    at=self.now()))
        sr.receive(self.root, self.rel, self.document, fetch, op_id=self.op_id, progress=progress,
                   statvfs=self.deps.fstatvfs)
        self.done('receiving-content', bytes=self.document['totalBytes'], files=files_total,
                  fetched=len(plan['toFetch']), skipped=len(plan['reuse']))

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
        self.stack.callback(handle.close)
        try:
            fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ReceiveError('device update in progress on %s; retry after it completes' % self.p['device'],
                               retryable=True, stage=self.current)

    def writing_binding(self):
        self.begin('writing-binding')
        p, block = self.p, self.p['block']
        self.take_lease()
        record = sc.binding_record(block, self.manifest,
                                   dict(id=self.req['plan'], revision=self.req['revision'], digest=p['digest']),
                                   p['item']['app'], self.now(), current_generation=self.content,
                                   item_id=self.req.get('item'))
        record['mountpoint'] = block['target']['mountpoint']
        # Contract section 3: current is swapped first; the marker names the generation after it.
        sr.promote(self.root, self.rel, self.content, self.raw, dataset=block['dataset'],
                   generation=p['generation'], op_id=self.op_id if re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', self.op_id) else None,
                   promoted_at=self.now())
        sr.write_binding(self.root, self.rel, record)
        copy_dir = self.state_dir / block['dataset']
        copy_dir.mkdir(parents=True, exist_ok=True)
        operations.atomic(copy_dir / 'binding.json', record)
        text = storage_mount.unit_text(block['dataset'], block['target']['mountpoint'], self.dataset_path,
                                       p['item']['app'], 'isle-' + p['item']['app'])
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        self.unit_path = self.unit_dir / ('isle-storage-%s.service' % block['dataset'])
        units = [(self.unit_path, text)]
        # The sync timer units (contract 2.5): written here and enabled below; the receipt's
        # syncTimerInstalled (what cutover step 2 checks) comes from sync_timer_installed().
        units += [(self.unit_dir / name, body) for name, body in
                  zip(storage_mount.sync_unit_names(block['dataset']), storage_mount.sync_timer_text(block['dataset']))]
        for path, body in units:
            temp = path.with_name(path.name + '.tmp-%d' % os.getpid())
            temp.write_text(body)
            os.chmod(temp, 0o644)
            os.replace(temp, path)
        self.enable_units(block['dataset'])
        self.done('writing-binding', unit=str(self.unit_path), syncUnits=[str(p) for p, _ in units[1:]],
                  binding=str(copy_dir / 'binding.json'))

    def enable_units(self, dataset):
        """daemon-reload, enable the hosting unit, enable --now the sync timer; a failure is retryable."""
        _, timer_name = storage_mount.sync_unit_names(dataset)
        for args in (['daemon-reload'], ['enable', 'isle-storage-%s.service' % dataset],
                     ['enable', '--now', timer_name]):
            code = self.deps.systemctl(args)
            if code != 0:
                raise ReceiveError('systemctl %s failed (exit %s).' % (' '.join(args), code),
                                   retryable=True, stage=self.current)
        self.receipt['syncTimerInstalled'] = storage_mount.sync_timer_installed(
            dataset, self.unit_dir,
            systemctl_is_enabled=lambda name: self.deps.systemctl(['is-enabled', '--quiet', name]) == 0)

    # ---- stage 7
    def deploying_host(self):
        self.begin('deploying-host')
        refuse = self._refuser()
        again = fetch_plan(self.http, self.deps, self.req, self.current, with_source=False)
        if again['digest'] != self.p['digest'] or again['generation'] != self.p['generation']:
            refuse('The placement moved during the migration (generation %s, was %s).'
                   % (again['generation'], self.p['generation']))
        app, pinned = self.p['item']['app'], self.p['release']['manifestSha256']
        try:
            published = release_sha(self.http.get_json('%s/api/islemesh/catalog/%s' % (API, quote(app))))
        except HttpError as error:
            if error.status != 404:
                raise ReceiveError('Cannot fetch the catalog entry: %s' % error, retryable=True, stage=self.current)
            published = None
        if published is None:
            refuse('Hosting release not published for %s.' % app)
        if published != pinned:
            refuse('The published release of %s differs from the pinned hostingRelease manifest.' % app)
        target = self.p['block']['target']
        check = sr.storage_check(target['mountpoint'], target['filesystemUuid'], target['relativePath'],
                                 findmnt_json=self.deps.findmnt_json,
                                 placement=dict(state=self.p['view']['state'], dataset=self.req['dataset'],
                                                plan=dict(digest=self.p['digest'])))
        if not (check['bound'] and check['mounted'] and check['uuidMatches'] and check['markerMatches']
                and check['placementAgrees'] and check['currentGeneration'] == self.content and not check['pointerAhead']):
            refuse('storage check fails for %s: %s' % (self.req['dataset'], json.dumps(check, sort_keys=True)))
        extra = ({'http': self.http, 'dataset': self.req['dataset'], 'served': self.p['block']['domain']}
                 if self.deps.deploy is default_deploy else {})
        code = self.deps.deploy(app, ['--staged', '--bind', 'ISLE_DATASET_PATH=' + self.dataset_path], **extra)
        if code != 0:
            refuse('Staged deploy of %s failed (exit %s).' % (app, code), retryable=True)
        self.receipt.update(hostDeployed=True, stagedDeploy=True)
        self.done('deploying-host', app=app, release=self.p['release'])

    # ---- stage 8
    def verifying_readiness(self):
        self.begin('verifying-readiness')
        refuse = self._refuser(retryable=True)
        domain = self.p['block']['domain']
        try:
            address = self.env.get('ISLE_AGENT_ADDRESS') or self.deps.agent_address()
            ipaddress.ip_address(address)
        except (ValueError, OSError, subprocess.SubprocessError):
            refuse('This device\'s agent address is unknown.')
        # Probe locally via the agent bridge (the host cannot reach its own macvlan child); the receipt still records
        # the Isle-facing address.  ISLE_PROBE_ADDRESS overrides; no verified bridge -> the agent address.
        probe = self.env.get('ISLE_PROBE_ADDRESS') or self.deps.probe_address() or address
        try:
            ipaddress.ip_address(probe)
        except ValueError:
            refuse('The readiness probe address %r is not an IP address.' % (probe,))
        resolve = (domain, probe)
        samples = sample_entries(self.document)
        deadline = self.deps.monotonic() + READY_SECONDS
        checked = []
        while True:
            try:
                current = self.http.get_bytes(object_url(domain, '.well-known/isle-storage/current'),
                                              resolve=resolve, max_bytes=4096)
                if current.decode('utf-8', 'replace').strip() != self.content:
                    refuse('The served /.well-known/isle-storage/current is not the received contentSha.')
                checked = []
                for entry in samples:
                    data = self.http.get_bytes(object_url(domain, entry['path']), resolve=resolve,
                                               max_bytes=entry['size'] + 1)
                    if len(data) != entry['size'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
                        refuse('Sample %s served from this device differs from the manifest.' % entry['path'])
                    checked.append(entry['path'])
                break
            except HttpError as error:
                if self.deps.monotonic() >= deadline:
                    refuse('The host was not ready within %d seconds: %s' % (READY_SECONDS, error))
                self.deps.sleep(2)
        self.receipt['readinessVerified'] = True
        self.done('verifying-readiness', samples=checked, address=address, probeAddress=probe)

    # ---- receipt
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
        if error is None:
            r['words'] = ('Replica of %s is ready on %s: content verified, host deployed staged, readiness verified; '
                          'awaiting cutover.' % (self.req['dataset'], self.p['device']))
        else:
            r['words'] = 'Migration of %s stopped at %s: %s%s' % (
                self.req['dataset'], self.current, error, ' (retry is safe).' if error.retryable else '.')
        if self.op_dir:
            operations.atomic(self.op_dir / 'storage-receipt.json', r)
        if sc.STAGES.index(self.current) < len(PRE_DISK_STAGES) and error is not None:
            return  # stages 1-2 refused: nothing recorded outside the journal directory
        r['attempt'] = max(r['attempt'], self._recorded_attempt(r.get('actionId')) + 1)
        self._record(r)
        self._post(r)
        if self.op_dir:
            operations.atomic(self.op_dir / 'storage-receipt.json', r)

    def _recorded_attempt(self, action):
        """The highest attempt this device already recorded for the action, whatever operation made it; 0 if none.
        The core keeps one receipt per action and attempt and refuses a different one.  The same migrate command
        typed again is a new operation whose own count starts at 1, so the receipt counts per action instead."""
        highest = 0
        with contextlib.suppress(OSError):
            for line in (self.state_dir / self.req['dataset'] / 'receipts.jsonl').read_bytes().splitlines():
                with contextlib.suppress(ValueError, AttributeError):
                    row = json.loads(line)
                    if action and row.get('actionId') == action and type(row.get('attempt')) is int:
                        highest = max(highest, row['attempt'])
        return highest

    def _record(self, receipt):
        try:
            folder = self.state_dir / self.req['dataset']
            folder.mkdir(parents=True, exist_ok=True)
            with (folder / 'receipts.jsonl').open('ab') as out:
                out.write(json.dumps(receipt, sort_keys=True).encode() + b'\n')
                out.flush()
                os.fsync(out.fileno())
        except OSError as error:
            receipt['recordError'] = str(error)

    def _post(self, receipt):
        token = None
        try:
            token = json.loads(self.enrollment.read_text()).get('token')
            if not isinstance(token, str) or not token:
                raise ValueError('the enrollment file has no token')
            status, text = self.http.post_json(API + '/api/islemesh/storage/receipt', receipt, token)
            first = receipt['attempt']
            while status == 409 and TAKEN in str(text) and receipt['attempt'] - first < TAKEN_TRIES:
                # The core holds another receipt under this number and this device's record of it is gone.
                receipt['attempt'] += 1
                status, text = self.http.post_json(API + '/api/islemesh/storage/receipt', receipt, token)
            if receipt['attempt'] != first:
                self._record(receipt)       # the number the core was offered last is the one kept here too
            receipt['posted'] = 200 <= status < 300
            if not receipt['posted']:
                receipt['postError'] = 'HTTP %d %s' % (status, str(text)[:200])
        except (OSError, ValueError, HttpError) as error:
            receipt['posted'] = False
            message = str(error)
            receipt['postError'] = message.replace(token, '***') if token else message

    def execute(self):
        error = None
        with self.stack:
            try:
                self.fetching_plan()
                self.preflight_storage()
                self.fetching_manifest()
                self.receiving_content()
                self.verifying_content()
                self.writing_binding()
                self.deploying_host()
                self.verifying_readiness()
            except ReceiveError as caught:
                error = caught
            except (OSError, ValueError, subprocess.SubprocessError) as caught:
                error = ReceiveError('%s: %s' % (type(caught).__name__, caught), retryable=True, stage=self.current)
            self.finish(error)
        if error is not None:
            print('storage migrate: ' + self.receipt['words'], file=sys.stderr)
            return 1
        print(self.receipt['words'])
        return 0


def run(argv, *, env=os.environ, http=None, now=None, stage_hook=None, deps=None):
    """Run the migrate stages for argv (['storage', 'migrate', ...] or ['migrate', ...]); return the exit code."""
    argv = list(argv)
    if argv[:1] == ['migrate']:
        argv = ['storage'] + argv
    try:
        req = sc.parse_request(argv)
        if req['verb'] != 'migrate':
            raise ValueError('Only the migrate verb runs here.')
    except ValueError as error:
        print('storage migrate: %s' % error, file=sys.stderr)
        return 2
    return Migration(req, env, http or CurlHttp(), now or default_now, stage_hook, deps or Deps()).execute()


if __name__ == '__main__':
    sys.exit(run(sys.argv[1:]))
