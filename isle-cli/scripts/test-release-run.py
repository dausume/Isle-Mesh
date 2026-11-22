#!/usr/bin/env python3
"""Unprivileged tests for release_run.py: fake Deps, temp dirs, no network, no docker, no sudo."""
import copy, fcntl, json, os, subprocess, sys, tempfile, time, traceback
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_run as rr
import release_intake as ri
import release_offer as ro

class NoIntake(ri.IntakeIO):
    """A core that cannot read its certificate authority or its router. Nothing here reaches a network, docker or
    systemd. (test-release-intake.py puts a fixture isle behind the same methods.)"""
    def members(self): return None
    def claims(self): return None
    def leases(self): return None
    def lease_of(self, leases, device): return None
    def fetch(self, domain, address, path, dest, limit, max_time=60): return 'no network in this test'
    def timer(self, on): return False
    def sleep(self, seconds): pass

class NoOffer(ro.OfferIO):
    """A member that is not paired and runs no agent. Nothing here reaches docker or a script."""
    def enrolled_device(self): return None
    def agent_image(self): return None
    def serving(self, name): return False
    def deploy(self, name, domain, compose_file): return 1, 'no docker in this test'
    def undeploy(self, name): return 0, ''
    def sleep(self, seconds): pass



def ossl(*args):
    p = subprocess.run(['openssl'] + [str(a) for a in args], capture_output=True, text=True)
    assert p.returncode == 0, (args, p.stderr)
    return p.stdout

class Pki:
    """A throw-away isle CA made with the real openssl: root (ca_dir layout) plus an intermediate, as certs.sh mints them."""
    def __init__(self, name):
        self.dir = Path(tempfile.mkdtemp(prefix='pki-' + name + '-'))
        ossl('ecparam', '-name', 'prime256v1', '-genkey', '-noout', '-out', self.dir / 'root_ca_key')
        ossl('req', '-x509', '-new', '-key', self.dir / 'root_ca_key', '-sha256', '-days', 3650, '-subj', '/CN=Test Root ' + name,
             '-addext', 'basicConstraints=critical,CA:TRUE,pathlen:1', '-addext', 'keyUsage=critical,keyCertSign,cRLSign', '-out', self.dir / 'isle-root.crt')
        self.int_key, self.int_crt = self.leaf('int', '/CN=Test Intermediate', 'basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n', root=True)

    def leaf(self, name, subj, ext, root=False):
        """A key + certificate issued by the root (root=True) or by the intermediate."""
        key, csr, crt, extf = (self.dir / (name + s) for s in ('.key', '.csr', '.crt', '.ext'))
        ossl('ecparam', '-name', 'prime256v1', '-genkey', '-noout', '-out', key)
        ossl('req', '-new', '-key', key, '-subj', subj, '-out', csr)
        extf.write_text(ext)
        ca, cak = (self.dir / 'isle-root.crt', self.dir / 'root_ca_key') if root else (self.int_crt, self.int_key)
        ossl('x509', '-req', '-in', csr, '-CA', ca, '-CAkey', cak, '-set_serial', '0x' + os.urandom(8).hex(), '-days', 365, '-sha256', '-extfile', extf, '-out', crt)
        return key, crt

APPROVAL_EXT = 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=codeSigning\n'
PKI, OTHER_PKI = Pki('main'), Pki('other')


APP, VER = 'board-engines', '2026.11.15-2'
IMG = 'sha256:' + '8c' * 32
MANIFEST = json.dumps({'compose': {'url': 'https://apt.isle/catalog/c.yml'},
                       'images': [{'archive': {'url': 'https://apt.isle/catalog/i.tar'}}]}, sort_keys=True).encode()
MSHA = rr.sha256_bytes(MANIFEST)
MURL = 'https://apt.isle/catalog/%s/%s/%s.json' % (APP, VER, MSHA)
OLDSHA = 'a' * 64
RELID = 'b' * 64


def make_packet(root, steps=None, devices=None, verify=None, mutate=None, sums_skip=(), publish=True):
    d = Path(root) / 'packet'
    d.mkdir(parents=True, exist_ok=True)
    (d / 'compose.yml').write_text('services: {}\n')
    (d / 'release.json').write_text(json.dumps({'schemaVersion': 1, 'name': APP, 'version': VER, 'compose': {'source': 'compose.yml'}}))
    doc = {'schemaVersion': 1, 'name': 'test run',
           'publish': [{'kind': 'catalog', 'release': 'release.json'}] if publish else [],
           'devices': devices if devices is not None else {'core1': steps if steps is not None else [
               {'verb': 'store-install', 'app': APP, 'version': VER, 'manifestSha256': MSHA, 'expectImageDigest': IMG}],
               'member1': [{'verb': 'update-apply', 'releaseId': RELID}]},
           'verify': verify if verify is not None else [
               {'url': 'https://board-engines.isle/capability'},
               {'url': 'https://twin.board-engines.isle/health', 'expectJsonContains': {'ok': True}}]}
    if mutate: mutate(doc)
    (d / 'release-run.json').write_text(json.dumps(doc))
    write_sums(d, sums_skip)
    return d


def write_sums(d, skip=()):
    """SHA256SUMS over every regular file under d (as `sha256sum` of the packet would), minus `skip`."""
    names = sorted(str(p.relative_to(d)) for p in d.rglob('*') if p.is_file() and not p.is_symlink() and p.name != 'SHA256SUMS')
    lines = ['%s  %s' % (rr.sha256_file(d / n), n) for n in names if n not in skip]
    (d / 'SHA256SUMS').write_text('\n'.join(lines) + '\n')

DEBS = {'isle-mesh-cli_1.deb': b'cli-bytes', 'isle-app-store_1.deb': b'store-bytes', 'polari-shell-core_1.deb': b'shell-bytes'}
RT_ARCHIVE = b'image-archive-bytes'
RT_URL = 'https://apt.isle/runtime/%s.tar' % rr.sha256_bytes(RT_ARCHIVE)
def runtime_doc():
    row = lambda c: {'container': c, 'image': {'ref': c + ':1', 'configDigest': 'sha256:' + '1' * 64, 'archive': {'source': 'images/backend.tar'}}}
    return {'runtimes': {'core': [row('prf-isle-backend')], 'remote': [row('prf-isle-backend'), row('isle-remote-agent')]}}

def add_debs(doc, d, names=None, entry=True):
    (d / 'debs').mkdir(exist_ok=True)
    for n in (names or DEBS): (d / 'debs' / n).write_bytes(DEBS.get(n, b'x'))
    if entry: doc['publish'].append({'kind': 'debs', 'dir': 'debs'})

def add_runtime(doc, d, manifest=None, entry=True):
    (d / 'runtime').mkdir(exist_ok=True); (d / 'runtime' / 'images').mkdir(exist_ok=True)
    (d / 'runtime' / 'images' / 'backend.tar').write_bytes(RT_ARCHIVE)
    (d / 'runtime' / 'manifest.json').write_text(json.dumps(manifest or runtime_doc()))
    if entry: doc['publish'].append({'kind': 'runtime', 'manifest': 'runtime/manifest.json'})

DEPS = {'libfoo_1_amd64.deb': b'foo-bytes', 'libbar_2%3a1_all.deb': b'bar-bytes'}     # a real set has names with an encoded colon
DEPS_DIR = 'dependencies/ubuntu-22.04'
def add_deps(doc, d, system='ubuntu-22.04', entry=True):
    """A dependency set as the suite's offline builder leaves one: the packages, the file that names the system, and notes."""
    full = d / DEPS_DIR; full.mkdir(parents=True, exist_ok=True)
    for n, b in DEPS.items(): (full / n).write_bytes(b)
    (full / 'CLOSURE_URIS.txt').write_text('where the packages came from\n')
    for name in ([system] if isinstance(system, str) else system): (full / (name + '.txt')).write_text(name + '\n')
    if entry: doc['publish'].append({'kind': 'dependencies', 'dir': DEPS_DIR})

def full_packet(t, **kw):
    """catalog + runtime + debs, listed in the WRONG order on purpose; core and member1 both update to "published"."""
    d = Path(t) / 'packet'
    def mutate(doc):
        doc['publish'].clear()
        add_debs(doc, d); add_runtime(doc, d); doc['publish'].insert(0, {'kind': 'catalog', 'release': 'release.json'})
        doc['devices'] = {'core1': [{'verb': 'store-install', 'app': APP, 'version': VER, 'manifestSha256': MSHA},
                                    {'verb': 'update-apply', 'releaseId': 'published'}],
                          'member1': [{'verb': 'update-apply', 'releaseId': 'published'}]}
        if kw.get('mutate'): kw['mutate'](doc)
    return make_packet(t, mutate=mutate, **{k: v for k, v in kw.items() if k != 'mutate'})


class Fake(rr.Deps):
    """Scripted world. now/time are a fake clock that sleep() advances."""
    def __init__(self, root, mode='core', device='core1'):
        self.clock, self.said, self.argv, self.events, self.reports = time.time() + 3600, [], [], [], []
        self.root, self.ca_dir, self.approval_dir = Path(root), PKI.dir, Path(root) / 'approval'
        self.device_list = [{'name': 'core1', 'agent_mode': 'core', 'is_mock': False}, {'name': 'member1', 'agent_mode': 'remote', 'is_mock': False},
                            {'name': 'member2', 'agent_mode': 'remote', 'is_mock': False}, {'name': 'mock1', 'agent_mode': 'remote', 'is_mock': True}]
        self.member_state, self.fact_run, self.devices_unreadable = {}, None, False
        self.polari_dir = Path(root) / 'polari'
        self.free, self.texts, self.runtime_published, self.apt_rc, self.runtime_rc = 10 ** 12, {}, None, 0, 0
        self.new_release, self.release_after, self.assets_after, self.missing_urls = 'ab' * 32, 0, 0, set()
        self.notice_stale, self.allow_module, self.module_writes, self.on_publish = False, False, True, None
        self.runs_dir = Path(root) / 'runs'
        self.setting_file, self.notice_setting_fails, self.updates_down = Path(root) / 'release' / 'start-setting.json', False, False
        self._mode, self._device, self.root_user, self.answer = mode, device, True, True
        self.notice = {'id': 'x' * 64, 'packages': [1], 'command': 'keep me'}
        self.files = {}
        self.descriptor_after = 0            # seconds until the descriptor names the new manifest
        self.assets_ok = True
        self.update_rcs, self.status_retryable, self.install_rc = [0], True, 0
        self.status_state, self.notice_fail_until, self.instance = 'failed', 0, None
        self.modules_ok_after, self.updates_body, self.http_log = 0, None, []
        self.inspect_image, self.publish_rc = IMG, 0
        self.deps_rc, self.deps_published, self.deps_after, self.deps_unlisted, self.deps_wrong, self.deps_others = 0, None, 0, set(), {}, []
        self.t0 = self.clock
        self.wait_limit, self.poll_interval, self.modules_limit = 900, 15, 300
        self.intake_io, self.offer_io = NoIntake(), NoOffer()
        self.intake_io.dir, self.offer_io.home = Path(root) / 'intake', Path(root) / 'offer'

    def say(self, line=''): self.said.append(line)
    def time(self): return self.clock
    def sleep(self, s): self.clock += s
    def now(self): return 'T%d' % int(self.clock - self.t0)
    def is_root(self): return self.root_user
    def mode(self): return self._mode
    def device(self): return self._device
    def confirm(self, prompt): self.events.append('confirm'); return self.answer
    def read_json(self, path): return copy.deepcopy(self.files[str(path)]) if str(path) in self.files else super().read_json(path) if str(path).startswith(str(self.runs_dir)) else None
    def read_notice(self): return copy.deepcopy(self.notice)
    def write_notice_run(self, record):
        if self.clock - self.t0 < self.notice_fail_until: raise RuntimeError('backend container is restarting')
        self.events.append('notice'); self.notice['run'] = copy.deepcopy(record)
    def write_notice_setting(self, record):
        if self.notice_setting_fails: raise RuntimeError('backend container is restarting')
        self.events.append('notice-setting'); self.notice['startSetting'] = copy.deepcopy(record)
    def write_notice_intake(self, record):
        if getattr(self, 'notice_intake_fails', 0) > 0: self.notice_intake_fails -= 1; raise RuntimeError('backend container is restarting')
        self.notice['intake'] = copy.deepcopy(record)
    def journal_allows(self, args): return self.allow_module
    def free_bytes(self, path): return self.free
    def read_text(self, path): return self.texts.get(str(path))
    def preload(self): pass
    def installed_instances(self, app):
        out = {}
        for path, rec in self.files.items():
            if path.startswith('/etc/isle-mesh/apps/') and path.endswith('/release.json') and rec.get('name') == app:
                out[path.split('/')[4]] = copy.deepcopy(rec)
        return out

    def run(self, argv, stream=False, timeout=None):
        self.argv.append(list(argv))
        joined = ' '.join(argv)
        if argv[0] == 'openssl': return super().run(argv, stream, timeout)
        if argv[:2] == ['systemctl', 'start']: self.reports.append(list(argv)); return 0, ''
        if argv[0] == 'bash' and argv[1].endswith('update.sh') and argv[2] == 'publish-runtime':
            self.events.append('publish-runtime'); self.publish_argv = getattr(self, 'publish_argv', []) + [argv]
            if self.runtime_rc: return self.runtime_rc, 'boom'
            m = json.loads(Path(argv[3]).read_text()); rows = {}
            for mode, items in m['runtimes'].items():
                rows[mode] = [{'container': i['container'], 'image': dict(i['image'], archive={'url': RT_URL, 'sha256': 'f' * 64, 'size': 5})} for i in items]
            self.runtime_published = rows
            if self.on_publish: self.on_publish('runtime')
            return 0, 'Runtime artifacts published.\n'
        if argv[0] == 'bash' and argv[1].endswith('apt-repo.sh') and argv[2:5] == ['publish', '--only-dependencies', '--dependencies-from']:
            self.events.append('publish-dependencies'); self.publish_argv = getattr(self, 'publish_argv', []) + [argv]
            if self.deps_rc: return self.deps_rc, 'boom'
            if self.on_publish: self.on_publish('dependencies')
            folder = Path(argv[5]); names = sorted(os.listdir(folder))          # the release the core announces is left as it is
            self.deps_published = {n: rr.sha256_file(folder / n) for n in names if n.endswith('.deb')}
            self.deps_others = [n for n in names if not n.endswith('.deb')]
            return 0, 'ok\n'
        if argv[0] == 'bash' and argv[1].endswith('apt-repo.sh') and argv[2] == 'publish':
            self.events.append('publish-debs'); self.publish_argv = getattr(self, 'publish_argv', []) + [argv]
            if self.apt_rc: return self.apt_rc, 'boom'
            if self.on_publish: self.on_publish('debs')
            files = sorted(os.listdir(argv[4]))
            if not self.notice_stale:
                run = self.notice.get('run')
                self.notice = {'id': self.new_release, 'publishedAt': datetime.fromtimestamp(self.clock, timezone.utc).isoformat(),
                               'packages': [{'name': n, 'version': '1', 'sha256': 'c' * 64, 'file': files[i % len(files)]} for i, n in enumerate(('isle-app-store', 'isle-mesh-cli', 'polari-shell-core'))]}
                if self.runtime_published: self.notice['runtimes'] = copy.deepcopy(self.runtime_published)
                if run: self.notice['run'] = run
            return 0, 'ok\n'
        if 'operations.py run polari module add' in joined:
            self.events.append('module-add'); self.module_argv = list(argv[3:])
            mod, inst = argv[6], argv[8]
            if self.module_writes:
                path = str(self.polari_dir / inst / 'deployment.json'); rec = self.files.get(path, {})
                rec['modules'] = ','.join(x for x in (rec.get('modules', ''), mod) if x); self.files[path] = rec
                self.texts[str(self.polari_dir / inst / 'docker-compose.yml')] = 'POLARI_MODULES=' + rec['modules'] + '\n'
            return 0, 'Operation: ' + 'e' * 32 + '\n'
        if 'operations.py run backup create' in joined:
            self.events.append('backup'); self.backup_argv = list(argv[3:])
            return getattr(self, 'backup_rc', 0), 'Operation: ' + 'b' * 32 + '\n'
        if 'operations.py run hotfix apply' in joined:
            self.events.append('hotfix'); self.hotfix_argv = list(argv[3:])
            return getattr(self, 'hotfix_rc', 0), 'Operation: ' + 'd' * 32 + '\n'
        if argv[0] == 'bash' and argv[2] == 'publish':
            self.events.append('publish')
            self.publish_argv = getattr(self, 'publish_argv', []) + [argv]
            if self.on_publish: self.on_publish('catalog')
            if self.publish_rc: return self.publish_rc, 'boom'
            self.published_at = self.clock
            return 0, json.dumps({'name': APP, 'release': {'url': MURL, 'sha256': MSHA, 'size': len(MANIFEST)}}) + '\n'
        if 'operations.py run store install' in joined:
            self.events.append('install')
            if not self.install_rc:
                self.files['/etc/isle-mesh/apps/%s/release.json' % (self.instance or argv[5])] = {'name': argv[5], 'version': VER, 'manifestSha256': MSHA}
            return self.install_rc, 'Operation: ' + 'c' * 32 + '\n'
        if 'operations.py run update apply' in joined:
            self.events.append('update')
            rc = self.update_rcs.pop(0)
            return rc, 'Operation: ' + 'd' * 32 + '\n'
        if 'operations.py retry' in joined:
            self.events.append('retry')
            rc = self.update_rcs.pop(0)
            return rc, 'Operation: ' + 'd' * 32 + '\n'
        if 'operations.py status' in joined: return 0, json.dumps({'state': self.status_state, 'retryable': self.status_retryable})
        if argv[:2] == ['docker', 'ps']: return 0, 'cont1\n'
        if argv[:2] == ['docker', 'inspect']: return 0, self.inspect_image + '\n'
        raise AssertionError('unexpected command ' + joined)

    def http(self, url, head=False):
        self.http_log.append((url, head))
        age = self.clock - self.t0
        if self.updates_down and url.endswith('/api/islemesh/updates'): return 0, b'no answer'
        if url.endswith('/api/islemesh/catalog/' + APP):
            sha = MSHA if age >= self.descriptor_after else OLDSHA
            return 200, json.dumps({'ok': True, 'entry': {'install_plan': {'release': {'sha256': sha, 'url': MURL}}}}).encode()
        if url == MURL: return 200, b'' if head else MANIFEST
        if url.endswith('/c.yml') or url.endswith('/i.tar'): return (200 if self.assets_ok else 404), b''
        if url.endswith('/api/islemesh/devices'):
            if self.devices_unreadable: return 503, b''
            rid, out = (self.notice.get('run') or {}).get('runId'), []
            for d in self.device_list:
                e = copy.deepcopy(d); st = self.member_state.get(d['name'], 'done')
                e.setdefault('last_seen', datetime.fromtimestamp(self.clock, timezone.utc).isoformat())   # reporting now unless a test says otherwise
                st = st(age) if callable(st) else st
                if st and rid and d.get('agent_mode') == 'remote':
                    e['capability_evidence'] = {'facts': {'isle.release_run': {'status': 'observed', 'value': json.dumps(
                        {'runId': self.fact_run or rid, 'serial': 1, 'state': st, 'step': 'step text', 'updatedAt': 'x'})}}}
                out.append(e)
            return 200, json.dumps({'ok': True, 'devices': out}).encode()
        if url == 'https://apt.isle/deps/jammy/InRelease': return (404 if self.deps_published is None else 200), b''
        if url.startswith('https://apt.isle/deps/'):
            if url != 'https://apt.isle/deps/jammy/Packages' or self.deps_published is None or age < self.deps_after: return 404, b''
            return 200, ''.join('Package: p\nFilename: ./%s\nSHA256: %s\nDescription: one line\n and: another\n\n' % (n, self.deps_wrong.get(n, sha))
                                for n, sha in self.deps_published.items() if n not in self.deps_unlisted).encode()
        if url.endswith('/api/modules/status'): return 200, json.dumps({'ok': age >= self.modules_ok_after}).encode()
        if url.endswith('/api/islemesh/updates') and self.notice.get('packages') and self.notice['packages'] != [1]:
            n = copy.deepcopy(self.notice)
            if age < self.release_after: n['id'] = 'old' + n['id'][3:]
            return 200, json.dumps({'ok': True, 'release': n}).encode()
        if url in self.missing_urls: return 404, b''
        if url.startswith('https://apt.isle/') and (url.endswith('.deb') or '/runtime/' in url): return (200 if age >= self.assets_after else 404), b''
        if url.endswith('/api/islemesh/updates'):
            extra = {k: self.notice[k] for k in ('startSetting', 'intake', 'identityProvider') if k in self.notice}
            return 200, json.dumps({'ok': True, 'release': {'id': 'r', **extra, **({'run': self.updates_body} if self.updates_body else {})}}).encode()
        if url == 'https://board-engines.isle/capability': return 200, b'{}'
        if url == 'https://twin.board-engines.isle/health': return 200, b'{"ok": true, "x": 1}'
        return 404, b''


RESULTS = []
def test(fn):
    try:
        fn(); RESULTS.append((fn.__name__, None)); print('PASS ' + fn.__name__)
    except Exception:
        RESULTS.append((fn.__name__, traceback.format_exc())); print('FAIL ' + fn.__name__ + '\n' + traceback.format_exc())
    return fn

def tmp(): return tempfile.TemporaryDirectory()
def refused(deps): return [l for l in deps.said if l.startswith('REFUSED')]
PUBS = [{'app': APP, 'version': VER, 'manifestSha256': MSHA, 'url': MURL}]
def signer(f):
    """A core-side Deps sharing f's CA, device list and clock but with its own approval key and serial."""
    s = Fake(f.root, 'core', 'core1')
    s.approval_dir, s.runs_dir, s.clock, s.device_list = f.root / 'signer-approval', f.root / 'signer-runs', f.clock, f.device_list
    return s

def announce(f, pk, devices=None):
    """The record a real core would publish (properly signed), for follow tests."""
    s = signer(f)
    approval, _ = rr.make_approval(s, pk.run_id, PUBS, devices if devices is not None else rr.resolve_devices(s, pk.doc, 'core1'))
    return {'runId': pk.run_id, 'name': 'test run', 'createdAt': 'T0', 'devices': pk.doc['devices'], 'verify': pk.doc['verify'],
            'publications': PUBS, 'record': pk.record, 'approval': approval}

def forged(f, pk, key, cert, serial=None, **over):
    """An announcement whose approval is signed by `key` (any key) with `cert` (any certificate text); payload fields overridable."""
    payload = {'type': 'isle-release-approval', 'version': 1, 'runId': pk.run_id, 'serial': serial or int(f.clock) + 5, 'approvedAt': rr.stamp(f.clock),
               'expiresAt': rr.stamp(f.clock + 86400 * 30), 'publications': PUBS, 'devices': ['core1', 'member1'], 'releaseId': None}
    payload.update(over)
    text = rr.canonical(payload)
    pf = f.root / 'forged-payload'; pf.write_text(text)
    ossl('dgst', '-sha256', '-sign', key, '-out', f.root / 'forged-sig', pf)
    import base64
    a = announce(f, pk)
    a['approval'] = {'payload': text, 'signature': base64.b64encode((f.root / 'forged-sig').read_bytes()).decode(), 'certificate': cert if 'BEGIN CERTIFICATE' in str(cert) else Path(cert).read_text()}
    return a

def good_key(f):
    """The key/cert of a legitimate approval (as the core's signer would have)."""
    s = signer(f); rr.ensure_approval_key(s)
    return s.approval_dir / 'key.pem', s.approval_dir / 'cert.pem'


# ---- check / validation
@test
def valid_packet_check():
    with tmp() as t:
        d = make_packet(t); f = Fake(t)
        assert rr.main(['check', str(d)], f) == 0, f.said
        text = '\n'.join(f.said)
        assert 'Device core1' in text and 'install app board-engines' in text and 'Run id:' in text and 'Packet is valid' in text

def expect_refusal(label, build, needle):
    with tmp() as t:
        d = build(t); f = Fake(t)
        code = rr.main(['check', str(d)], f)
        assert code != 0 and needle in ' '.join(f.said), '%s: %s %s' % (label, code, f.said)

@test
def refuse_unknown_key(): expect_refusal('key', lambda t: make_packet(t, mutate=lambda d: d.update(extra=1)), 'unknown key')
@test
def refuse_unknown_step_key():
    expect_refusal('stepkey', lambda t: make_packet(t, steps=[{'verb': 'update-apply', 'releaseId': RELID, 'x': 1}]), 'unknown key')
@test
def refuse_unknown_verb(): expect_refusal('verb', lambda t: make_packet(t, steps=[{'verb': 'rm-rf'}]), 'unknown step verb')
@test
def refuse_unknown_publish_kind():
    expect_refusal('kind', lambda t: make_packet(t, mutate=lambda d: d['publish'].append({'kind': 'shell', 'release': 'x'})), 'unknown kind')
@test
def refuse_bad_sha():
    expect_refusal('sha', lambda t: make_packet(t, steps=[{'verb': 'store-install', 'app': APP, 'version': VER, 'manifestSha256': 'ABC'}]), 'manifestSha256')
@test
def refuse_bad_release_id(): expect_refusal('relid', lambda t: make_packet(t, steps=[{'verb': 'update-apply', 'releaseId': 'f' * 63}]), 'releaseId')
@test
def refuse_bad_app_name():
    expect_refusal('app', lambda t: make_packet(t, steps=[{'verb': 'store-install', 'app': 'Bad App', 'version': VER, 'manifestSha256': MSHA}]), 'app')
@test
def refuse_bad_version():
    expect_refusal('ver', lambda t: make_packet(t, steps=[{'verb': 'store-install', 'app': APP, 'version': '1 2', 'manifestSha256': MSHA}]), 'version')
@test
def refuse_path_escape():
    expect_refusal('esc', lambda t: make_packet(t, mutate=lambda d: d['publish'].__setitem__(0, {'kind': 'catalog', 'release': '../release.json'})), 'leaves the packet')
@test
def refuse_absolute_path():
    expect_refusal('abs', lambda t: make_packet(t, mutate=lambda d: d['publish'].__setitem__(0, {'kind': 'catalog', 'release': '/etc/passwd'})), 'leaves the packet')
@test
def refuse_symlink_out():
    def build(t):
        d = make_packet(t)
        outside = Path(t) / 'outside.json'; outside.write_text((d / 'release.json').read_text())
        (d / 'release.json').unlink(); os.symlink(outside, d / 'release.json')
        return d
    expect_refusal('symlink', build, 'leaves the packet')
@test
def refuse_file_missing_from_sums(): expect_refusal('sums', lambda t: make_packet(t, sums_skip=('release.json',)), 'not listed in SHA256SUMS')
@test
def refuse_source_missing_from_sums(): expect_refusal('src', lambda t: make_packet(t, sums_skip=('compose.yml',)), 'not listed in SHA256SUMS')
@test
def refuse_checksum_mismatch():
    def build(t):
        d = make_packet(t); (d / 'compose.yml').write_text('changed'); return d
    expect_refusal('mismatch', build, 'checksum mismatch')
@test
def refuse_non_https(): expect_refusal('http', lambda t: make_packet(t, verify=[{'url': 'http://board-engines.isle/'}]), 'https://')
@test
def refuse_non_isle_host(): expect_refusal('host', lambda t: make_packet(t, verify=[{'url': 'https://example.com/'}]), '.isle host')
@test
def old_runtime_placeholder_is_a_packet_error():
    with tmp() as t:
        d = make_packet(t, mutate=lambda doc: doc.update(publish=[{'kind': 'runtime', 'releaseId': RELID}])); f = Fake(t)
        assert rr.main(['check', str(d)], f) == 1 and 'unknown key' in ' '.join(f.said)

@test
def run_id_stable_and_changes():
    with tmp() as t:
        d = make_packet(t); a = rr.load_packet(d).run_id; assert a == rr.load_packet(d).run_id
        (d / 'compose.yml').write_text('different'); write_sums(d)
        b = rr.load_packet(d).run_id; assert b != a
        doc = json.loads((d / 'release-run.json').read_text()); doc['name'] = 'other'
        (d / 'release-run.json').write_text(json.dumps(doc)); write_sums(d)
        assert rr.load_packet(d).run_id not in (a, b)


# ---- core run
@test
def core_run_happy_path_order_and_notice():
    with tmp() as t:
        d = make_packet(t); f = Fake(t)
        assert rr.main(['run', str(d)], f) == 0, f.said
        assert f.events == ['confirm', 'publish', 'install', 'notice'], f.events
        assert f.notice['command'] == 'keep me' and f.notice['packages'] == [1] and f.notice['id'] == 'x' * 64
        run = f.notice['run']
        assert run['runId'] == rr.load_packet(d).run_id and run['publications'] == [{'app': APP, 'version': VER, 'manifestSha256': MSHA, 'url': MURL}]
        assert rr.run_id_of(run['record']) == run['runId']
        rid = run['runId']; receipt = json.loads((f.runs_dir / rid / 'receipt.json').read_text())
        assert receipt['state'] == 'done' and [s['verb'] for s in receipt['steps']] == ['publish', 'wait-served', 'store-install', 'announce']
        assert receipt['steps'][2]['operationId'] == 'c' * 32 and len(receipt['verify']) == 2 and all(v['state'] == 'done' for v in receipt['verify'])
        assert oct((f.runs_dir / rid / 'receipt.json').stat().st_mode & 0o777) == '0o644'     # readable like the operations journal; it holds no secret
        assert any('sudo isle release follow' in l and 'member1' in l for l in f.said)

@test
def run_needs_root_and_core():
    with tmp() as t:
        d = make_packet(t); f = Fake(t); f.root_user = False
        assert rr.main(['run', str(d)], f) == 1 and 'sudo isle release run' in ' '.join(f.said) and not f.argv
        f = Fake(t, mode='remote'); assert rr.main(['run', str(d)], f) == 1 and 'core only' in ' '.join(f.said)
        f = Fake(t); f.answer = False; assert rr.main(['run', str(d)], f) == 1 and not f.argv

@test
def wait_served_waits_on_old_sha_then_proceeds():
    with tmp() as t:
        d = make_packet(t); f = Fake(t); f.descriptor_after = 100
        assert rr.main(['run', str(d), '--yes'], f) == 0, f.said
        waits = [l for l in f.said if l.startswith('wait-served') and 'still missing' in l]
        assert waits and 'names ' + OLDSHA[:12] in waits[0], f.said
        assert f.events.index('install') > f.events.index('publish')

@test
def wait_served_refuses_on_timeout_without_installing():
    with tmp() as t:
        d = make_packet(t); f = Fake(t); f.descriptor_after = 10 ** 6
        assert rr.main(['run', str(d), '--yes'], f) == 1
        assert 'install' not in f.events and 'update' not in f.events and 'notice' not in f.events
        line = refused(f)[0]; assert 'wait-served' in line and 'never appeared' in line and OLDSHA[:12] in line, line
        rid = rr.load_packet(d).run_id
        assert json.loads((f.runs_dir / rid / 'receipt.json').read_text())['state'] == 'refused'

@test
def wait_served_needs_assets_too():
    with tmp() as t:
        d = make_packet(t); f = Fake(t); f.assets_ok = False
        assert rr.main(['run', str(d), '--yes'], f) == 1 and 'c.yml answers 404' in refused(f)[0]

@test
def store_install_refuses_when_descriptor_names_another_sha():
    # The 02:18Z fault: a member installed the previous version right after publish.
    with tmp() as t:
        steps = [{'verb': 'store-install', 'app': APP, 'version': VER, 'manifestSha256': MSHA}]
        d = make_packet(t, steps={'member1': steps} and None, devices={'member1': steps}); f = Fake(t, 'remote', 'member1'); f.descriptor_after = 10 ** 6
        pk = rr.load_packet(d); f.updates_body = announce(f, pk)
        assert rr.main(['follow', '--yes'], f) == 1
        assert 'install' not in f.events and 'never appeared' in refused(f)[0], f.said

@test
def store_install_checks_version_and_image():
    with tmp() as t:
        d = make_packet(t); f = Fake(t); f.inspect_image = 'sha256:' + 'e' * 64
        assert rr.main(['run', str(d), '--yes'], f) == 1 and 'does not run the admitted image' in refused(f)[0] and 'c' * 32 in refused(f)[0]
        assert 'notice' not in f.events
    with tmp() as t:
        d = make_packet(t); f = Fake(t); f.install_rc = 1
        assert rr.main(['run', str(d), '--yes'], f) == 1 and 'store install failed' in refused(f)[0]

@test
def update_apply_retries_exactly_once():
    steps = [{'verb': 'update-apply', 'releaseId': RELID}]
    with tmp() as t:
        d = make_packet(t, devices={'core1': steps}, publish=False); f = Fake(t); f.update_rcs = [1, 0]; f.modules_ok_after = 40
        assert rr.main(['run', str(d), '--yes'], f) == 0, f.said
        assert f.events.count('update') == 1 and f.events.count('retry') == 1
        assert any('backend modules/status' in l for l in f.said)       # it waited for the backend first
    with tmp() as t:
        d = make_packet(t, devices={'core1': steps}, publish=False); f = Fake(t); f.update_rcs = [1, 1, 0]
        assert rr.main(['run', str(d), '--yes'], f) == 1
        assert f.events.count('retry') == 1 and 'again on the one retry' in refused(f)[0] and 'd' * 32 in refused(f)[0]
    with tmp() as t:
        d = make_packet(t, devices={'core1': steps}, publish=False); f = Fake(t); f.update_rcs = [1]; f.status_retryable = False
        assert rr.main(['run', str(d), '--yes'], f) == 1 and f.events.count('retry') == 0

@test
def update_apply_skips_when_already_on_release():
    steps = [{'verb': 'update-apply', 'releaseId': RELID}]
    with tmp() as t:
        d = make_packet(t, devices={'core1': steps}, publish=False); f = Fake(t); f.files[rr.DESIRED_RELEASE] = {'id': RELID}
        assert rr.main(['run', str(d), '--yes'], f) == 0 and 'update' not in f.events
        rid = rr.load_packet(d).run_id
        assert 'already on ' + RELID in json.loads((f.runs_dir / rid / 'receipt.json').read_text())['steps'][0]['detail']

@test
def module_add_refuses_unbuilt():
    with tmp() as t:
        d = make_packet(t, devices={'core1': [{'verb': 'module-add', 'instance': 'polari-x', 'module': 'mod-a'}]}, publish=False); f = Fake(t)
        assert rr.main(['check', str(d)], f) == 0
        assert rr.main(['run', str(d), '--yes'], f) == 1 and 'no journaled' in refused(f)[0]

@test
def failed_verify_fails_the_run():
    with tmp() as t:
        d = make_packet(t, verify=[{'url': 'https://twin.board-engines.isle/health', 'expectJsonContains': {'ok': False}}]); f = Fake(t)
        assert rr.main(['run', str(d), '--yes'], f) == 1 and 'verify' in refused(f)[0]

@test
def resume_skips_done_steps():
    with tmp() as t:
        d = make_packet(t); f = Fake(t); f.install_rc = 1
        assert rr.main(['run', str(d), '--yes'], f) == 1
        f2 = Fake(t); f2.t0 = f2.clock                   # same runs dir, install now works
        assert rr.main(['run', str(d), '--yes'], f2) == 0, f2.said
        assert 'publish' not in f2.events
        assert f2.events.count('install') == 1 and 'notice' in f2.events
    with tmp() as t:                                         # fully done receipt: nothing re-runs
        d = make_packet(t); f = Fake(t); assert rr.main(['run', str(d), '--yes'], f) == 0
        f2 = Fake(t); f2.notice = f.notice; assert rr.main(['run', str(d), '--yes'], f2) == 0
        assert [e for e in f2.events if e in ('publish', 'install')] == [] and not any(a[:2] == ['bash', str(f2.scripts / 'store.sh')] for a in f2.argv)


# ---- follow
def follower(t, **kw):
    d = make_packet(t); pk = rr.load_packet(d)
    f = Fake(t, 'remote', kw.get('device', 'member1')); f.updates_body = announce(f, pk)
    return d, pk, f

@test
def follow_happy_path():
    with tmp() as t:
        d, pk, f = follower(t)
        assert rr.main(['follow', '--yes'], f) == 0, f.said
        assert f.events == ['update'] and 'install' not in f.events
        receipt = json.loads((f.runs_dir / pk.run_id / 'receipt.json').read_text())
        assert receipt['device'] == 'member1' and receipt['state'] == 'done'
        f2 = Fake(t, 'remote', 'member1'); f2.updates_body = f.updates_body; f2.runs_dir = f.runs_dir
        assert rr.main(['follow', '--yes'], f2) == 0 and not f2.events and 'already completed' in ' '.join(f2.said)

@test
def follow_refuses_wrong_run_id():
    with tmp() as t:
        d, pk, f = follower(t); f.updates_body['runId'] = '0' * 64
        assert rr.main(['follow', '--yes'], f) == 1 and 'does not match' in refused(f)[0] and not f.events
    with tmp() as t:                                         # steps tampered after hashing
        d, pk, f = follower(t); f.updates_body['record']['packet']['devices']['member1'][0]['releaseId'] = 'f' * 64
        f.updates_body['devices'] = f.updates_body['record']['packet']['devices']
        assert rr.main(['follow', '--yes'], f) == 1 and not f.events

@test
def follow_refuses_unnamed_device_and_missing_run():
    with tmp() as t:
        d, pk, f = follower(t, device='stranger')
        assert rr.main(['follow', '--yes'], f) == 1 and 'does not name this device' in refused(f)[0] and not f.events
        f = Fake(t, 'remote', 'member1'); assert rr.main(['follow', '--yes'], f) == 1 and 'no release run' in refused(f)[0]
        f = Fake(t, 'core'); f.updates_body = {}; assert rr.main(['follow', '--yes'], f) == 1 and 'member only' in refused(f)[0]

@test
def follow_star_device_and_confirmation():
    with tmp() as t:
        d = make_packet(t, devices={'*': [{'verb': 'update-apply', 'releaseId': RELID}]}); pk = rr.load_packet(d)
        f = Fake(t, 'remote', 'anyone'); f.device_list.append({'name': 'anyone', 'agent_mode': 'remote', 'is_mock': False})
        f.updates_body = announce(f, pk); f.answer = False
        assert rr.main(['follow'], f) == 1 and f.events == ['confirm']      # declined: nothing ran
        f.events.clear(); f.answer = True
        assert rr.main(['follow'], f) == 0 and f.events == ['confirm', 'update']

@test
def status_reads_receipts_and_notice():
    with tmp() as t:
        d = make_packet(t); f = Fake(t); rr.main(['run', str(d), '--yes'], f)
        f.said.clear(); assert rr.main(['status'], f) == 0
        text = '\n'.join(f.said); assert 'done' in text and 'Announced by the core' in text and 'c' * 32 in text


# ---- safety properties
@test
def no_insecure_flags_and_no_shell():
    with tmp() as t:
        d = make_packet(t); f = Fake(t); f.update_rcs = [0]
        rr.main(['run', str(d), '--yes'], f)
        argvs = [a for a in f.argv]
        assert argvs and all(isinstance(a, list) and all(isinstance(x, str) for x in a) for a in argvs)
        assert not any(x in ('-k', '--insecure') for a in argvs for x in a)
    src = Path(rr.__file__).read_text()
    assert 'shell=True' not in src and "'-k'" not in src and 'os.system' not in src
    assert '--insecure' not in src and '"-k"' not in src

@test
def real_deps_curl_argv_is_verified_tls():
    import subprocess
    seen = []
    real = subprocess.run
    def fake_run(argv, **kw):
        seen.append(argv)
        class P: stdout = b'body\n200'; stderr = b''; returncode = 0
        return P()
    subprocess.run = fake_run
    try:
        assert rr.Deps().http('https://api.polari.isle/x') == (200, b'body')
        rr.Deps().http('https://apt.isle/x', head=True)
    finally: subprocess.run = real
    for argv in seen:
        assert argv[0] == 'curl' and '--fail' in argv and '--silent' in argv and '--show-error' in argv and '--max-time' in argv
        assert '-k' not in argv and '--insecure' not in argv
    assert '--head' in seen[1]

@test
def packet_strings_are_data_not_shell():
    with tmp() as t:
        weird = 'x;touch pwned'
        try:
            d = make_packet(t, mutate=lambda doc: doc['publish'].__setitem__(0, {'kind': 'catalog', 'release': weird}))
        except Exception: d = None
        f = Fake(t)
        if d: assert rr.main(['run', str(d), '--yes'], f) == 1 and not f.argv and not (Path(t) / 'pwned').exists()
        assert not os.path.exists('pwned')


# ---- review additions
@test
def store_install_finds_a_renamed_instance_and_refuses_an_old_one():
    steps = [{'verb': 'store-install', 'app': APP, 'version': VER, 'manifestSha256': MSHA, 'expectImageDigest': IMG}]
    with tmp() as t:                                         # store.sh named the local instance <app>-2
        d = make_packet(t, devices={'core1': steps}); f = Fake(t); f.instance = APP + '-2'
        assert rr.main(['run', str(d), '--yes'], f) == 0, f.said
        assert any('label=com.docker.compose.project=isle-%s-2' % APP in a for a in f.argv)
    with tmp() as t:                                         # the install left the PREVIOUS release in place
        d = make_packet(t, devices={'core1': steps}); f = Fake(t); f.install_rc = 0
        real = f.run
        def run(argv, stream=False, timeout=None):
            out = real(argv, stream, timeout)
            if 'install' in argv: f.files['/etc/isle-mesh/apps/%s/release.json' % APP] = {'name': APP, 'version': '2026.11.15-1', 'manifestSha256': OLDSHA}
            return out
        f.run = run
        assert rr.main(['run', str(d), '--yes'], f) == 1
        assert 'no local instance' in refused(f)[0] and '2026.11.15-1' in refused(f)[0] and 'notice' not in f.events

@test
def image_check_uses_what_the_installer_admitted():
    steps = [{'verb': 'store-install', 'app': APP, 'version': VER, 'manifestSha256': MSHA, 'expectImageDigest': IMG}]
    config = 'sha256:' + '5a' * 32
    with tmp() as t:   # a host whose docker reports the config digest: admitted.json says so, the packet names the manifest digest
        d = make_packet(t, devices={'core1': steps}); f = Fake(t); f.inspect_image = config
        f.files['%s/%s/admitted.json' % (rr.ARTIFACTS, MSHA)] = {'manifestSha256': MSHA, 'images': {'ref': config}}
        f.files['%s/%s/manifest.json' % (rr.ARTIFACTS, MSHA)] = {'images': [{'manifestDigest': IMG, 'configDigest': config}]}
        assert rr.main(['run', str(d), '--yes'], f) == 0, f.said
    with tmp() as t:   # the packet expects an image the published manifest does not name
        d = make_packet(t, devices={'core1': steps}); f = Fake(t)
        f.files['%s/%s/manifest.json' % (rr.ARTIFACTS, MSHA)] = {'images': [{'manifestDigest': 'sha256:' + '1' * 64, 'configDigest': config}]}
        assert rr.main(['run', str(d), '--yes'], f) == 1 and 'does not name it' in refused(f)[0]

@test
def verify_runs_only_where_it_proves_something():
    install = [{'verb': 'store-install', 'app': APP, 'version': VER, 'manifestSha256': MSHA}]
    scoped = [{'url': 'https://board-engines.isle/capability', 'device': 'member1'}]
    with tmp() as t:   # the core has no steps: it must not judge the member's app before the member has followed
        d = make_packet(t, devices={'member1': install}, verify=scoped); f = Fake(t)
        assert rr.main(['run', str(d), '--yes'], f) == 0, f.said
        assert ('https://board-engines.isle/capability', False) not in f.http_log
        pk = rr.load_packet(d); m = Fake(t, 'remote', 'member1'); m.runs_dir = Path(t) / 'member-runs'; m.updates_body = f.notice['run']
        assert rr.main(['follow', '--yes'], m) == 0, m.said
        assert ('https://board-engines.isle/capability', False) in m.http_log
    with tmp() as t:   # a check may only name a device of this run
        d = make_packet(t, devices={'member1': install}, verify=[{'url': 'https://board-engines.isle/capability', 'device': 'elsewhere'}]); f = Fake(t)
        assert rr.main(['check', str(d)], f) == 1 and 'device must be one of' in refused(f)[0]

@test
def resume_of_a_failed_update_retries_the_same_operation():
    steps = [{'verb': 'update-apply', 'releaseId': RELID}]
    with tmp() as t:
        d = make_packet(t, devices={'core1': steps}, publish=False); f = Fake(t); f.update_rcs = [1, 1]
        assert rr.main(['run', str(d), '--yes'], f) == 1
        # packages are in and desired-release.json already names the release, but reconcile failed
        f2 = Fake(t); f2.files[rr.DESIRED_RELEASE] = {'id': RELID}; f2.update_rcs = [0]
        assert rr.main(['run', str(d), '--yes'], f2) == 0, f2.said
        assert f2.events.count('retry') == 1 and 'update' not in f2.events
        assert ['python3', str(f2.scripts / 'operations.py'), 'retry', 'd' * 32] in f2.argv
    with tmp() as t:   # the earlier operation had in fact succeeded: nothing is run again
        d = make_packet(t, devices={'core1': steps}, publish=False); f = Fake(t); f.update_rcs = [1, 1]
        assert rr.main(['run', str(d), '--yes'], f) == 1
        f2 = Fake(t); f2.status_state = 'succeeded'
        assert rr.main(['run', str(d), '--yes'], f2) == 0 and 'retry' not in f2.events and 'update' not in f2.events

@test
def announce_waits_for_a_restarting_backend():
    with tmp() as t:
        d = make_packet(t); f = Fake(t); f.notice_fail_until = 40
        assert rr.main(['run', str(d), '--yes'], f) == 0, f.said
        assert any(l.startswith('announce') and 'restarting' in l for l in f.said) and 'run' in f.notice

@test
def notice_writer_keeps_keys_and_refuses_without_a_release():
    import subprocess
    with tmp() as t:
        env = dict(os.environ, DATABASE_PATH=str(Path(t) / 'db.sqlite')); notice = Path(t) / 'isle-release.json'
        p = subprocess.run([sys.executable, '-c', rr.NOTICE_WRITE], input='{"runId": "x"}', text=True, capture_output=True, env=env)
        assert p.returncode != 0 and not notice.exists(), 'must not create a notice holding only a run'
        notice.write_text(json.dumps({'id': 'i', 'packages': [1], 'command': 'c'}))
        p = subprocess.run([sys.executable, '-c', rr.NOTICE_WRITE], input='{"runId": "x"}', text=True, capture_output=True, env=env)
        assert p.returncode == 0, p.stderr
        assert json.loads(notice.read_text()) == {'id': 'i', 'packages': [1], 'command': 'c', 'run': {'runId': 'x'}}
        out = subprocess.run([sys.executable, '-c', rr.NOTICE_READ], text=True, capture_output=True, env=env).stdout
        assert json.loads(out)['run'] == {'runId': 'x'}

# ---- slice 2: approval, verification, auto follow, core wait, report_fact
def member(t, **kw):
    d = make_packet(t); pk = rr.load_packet(d)
    f = Fake(t, 'remote', kw.get('device', 'member1'))
    return d, pk, f

def nothing_ran(f, pk):
    assert not f.events and not (f.runs_dir / pk.run_id).exists(), (f.events, f.said)

def refused_by_approval(f, pk, needle=None, auto=False):
    code = rr.main(['follow'] + (['--auto'] if auto else ['--yes']), f)
    assert code == 1 and refused(f) and 'REFUSED: approval' in refused(f)[0], (code, f.said)
    if needle: assert needle in refused(f)[0], f.said
    nothing_ran(f, pk)

@test
def approval_good_is_accepted_and_executes():
    with tmp() as t:
        d, pk, f = member(t); f.updates_body = announce(f, pk)
        assert rr.main(['follow', '--yes'], f) == 0, f.said
        assert f.events == ['update']
        receipt = json.loads((f.runs_dir / pk.run_id / 'receipt.json').read_text())
        assert receipt['state'] == 'done' and type(receipt['serial']) is int
        acc = f.runs_dir / 'accepted-serial.json'
        assert json.loads(acc.read_text()) == {'serial': receipt['serial'], 'runId': pk.run_id} and oct(acc.stat().st_mode & 0o777) == '0o600'
        assert len(f.reports) == 1 and f.reports[0] == ['systemctl', 'start', '--no-block', 'isle-resource-report.service']

@test
def approval_payload_is_exact_canonical_text_and_signature_checks_with_openssl():
    with tmp() as t:
        d = make_packet(t); f = Fake(t); assert rr.main(['run', str(d), '--yes'], f) == 0, f.said
        ap = f.notice['run']['approval']; p = json.loads(ap['payload'])
        assert ap['payload'] == rr.canonical(p) and set(p) == {'type', 'version', 'runId', 'serial', 'approvedAt', 'expiresAt', 'publications', 'devices', 'releaseId'}
        assert p['releaseId'] is None and p['devices'] == ['core1', 'member1'] and p['publications'] == PUBS
        assert rr.unstamp(p['expiresAt']) - rr.unstamp(p['approvedAt']) == 30 * 86400
        import base64
        (t_ := Path(t)).joinpath('pl').write_text(ap['payload']); t_.joinpath('sg').write_bytes(base64.b64decode(ap['signature'])); t_.joinpath('c.pem').write_text(ap['certificate'])
        pub = ossl('x509', '-in', t_ / 'c.pem', '-noout', '-pubkey'); t_.joinpath('pub').write_text(pub)
        assert 'Verified OK' in ossl('dgst', '-sha256', '-verify', t_ / 'pub', '-signature', t_ / 'sg', t_ / 'pl')
        assert rr.cert_problem(f, t_ / 'c.pem', f.clock) is None

@test
def refuse_no_approval():
    with tmp() as t:
        d, pk, f = member(t); a = announce(f, pk); del a['approval']; f.updates_body = a
        refused_by_approval(f, pk, 'no signed approval')

@test
def refuse_payload_edited_after_signing():
    with tmp() as t:
        d, pk, f = member(t); a = announce(f, pk)
        a['approval']['payload'] = a['approval']['payload'].replace('"releaseId":null', '"releaseId":"' + 'e' * 64 + '"'); f.updates_body = a
        assert a['approval']['payload'] != announce(f, pk)['approval']['payload'] or True
        refused_by_approval(f, pk, 'signature does not verify')

@test
def refuse_signature_by_member_style_leaf_with_its_chain():
    # what enrollment mints: intermediate-signed, serverAuth, DNS SAN. The attacker presents leaf + intermediate.
    with tmp() as t:
        d, pk, f = member(t)
        key, crt = PKI.leaf('member-leaf', '/CN=member1.isle', 'subjectAltName=DNS:member1.isle\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n')
        f.updates_body = forged(f, pk, key, crt.read_text() + PKI.int_crt.read_text())
        refused_by_approval(f, pk, 'exactly one certificate')

@test
def refuse_signature_by_member_style_leaf_alone():
    with tmp() as t:
        d, pk, f = member(t)
        key, crt = PKI.leaf('member-leaf2', '/CN=member1.isle', 'subjectAltName=DNS:member1.isle\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n')
        f.updates_body = forged(f, pk, key, crt)
        refused_by_approval(f, pk, 'does not chain directly to the isle root')

@test
def refuse_approval_lookalike_issued_by_the_intermediate():
    # even CN=isle-release-approval + codeSigning is useless when it is the intermediate that signed it (no -untrusted)
    with tmp() as t:
        d, pk, f = member(t)
        key, crt = PKI.leaf('fake-approval', '/CN=isle-release-approval', APPROVAL_EXT)
        f.updates_body = forged(f, pk, key, crt)
        refused_by_approval(f, pk, 'does not chain directly')

@test
def refuse_approval_leaf_signed_by_another_root():
    with tmp() as t:
        d, pk, f = member(t)
        key, crt = OTHER_PKI.leaf('approval', '/CN=isle-release-approval', APPROVAL_EXT, root=True)
        f.updates_body = forged(f, pk, key, crt)
        refused_by_approval(f, pk, 'does not chain directly')

@test
def refuse_server_auth_leaf_signed_directly_by_the_right_root():
    with tmp() as t:
        d, pk, f = member(t)
        key, crt = PKI.leaf('direct-server', '/CN=isle-release-approval', 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:x.isle\n', root=True)
        f.updates_body = forged(f, pk, key, crt)
        refused_by_approval(f, pk, 'Code Signing only')
    with tmp() as t:     # codeSigning AND serverAuth is also refused
        d, pk, f = member(t)
        key, crt = PKI.leaf('direct-both', '/CN=isle-release-approval', APPROVAL_EXT.replace('codeSigning', 'codeSigning,serverAuth'), root=True)
        f.updates_body = forged(f, pk, key, crt)
        refused_by_approval(f, pk, 'Code Signing only')

@test
def refuse_wrong_subject_even_if_otherwise_perfect():
    with tmp() as t:
        d, pk, f = member(t)
        key, crt = PKI.leaf('wrong-cn', '/CN=someone-else', APPROVAL_EXT, root=True)
        f.updates_body = forged(f, pk, key, crt)
        refused_by_approval(f, pk, 'subject')

@test
def refuse_expired_approval():
    with tmp() as t:
        d, pk, f = member(t); f.updates_body = announce(f, pk)
        f.clock += 31 * 86400
        refused_by_approval(f, pk, 'expired')

@test
def refuse_lower_serial_and_equal_serial_for_another_run():
    with tmp() as t:
        d, pk, f = member(t); key, crt = good_key(f)
        f.runs_dir.mkdir(parents=True)
        (f.runs_dir / 'accepted-serial.json').write_text(json.dumps({'serial': 9000, 'runId': 'a' * 64}))
        f.updates_body = forged(f, pk, key, crt, serial=8999)
        refused_by_approval(f, pk, 'lower than the 9000')
        f2 = Fake(t, 'remote', 'member1'); f2.updates_body = forged(f2, pk, key, crt, serial=9000)
        refused_by_approval(f2, pk, 'different run')
        f3 = Fake(t, 'remote', 'member1'); f3.updates_body = forged(f3, pk, key, crt, serial=9001)    # higher is fine
        assert rr.main(['follow', '--yes'], f3) == 0, f3.said
        assert json.loads((f3.runs_dir / 'accepted-serial.json').read_text())['serial'] == 9001

@test
def equal_serial_same_run_is_accepted_again():
    with tmp() as t:
        d, pk, f = member(t); key, crt = good_key(f)
        f.runs_dir.mkdir(parents=True)
        (f.runs_dir / 'accepted-serial.json').write_text(json.dumps({'serial': 9000, 'runId': pk.run_id}))
        f.updates_body = forged(f, pk, key, crt, serial=9000)
        assert rr.main(['follow', '--yes'], f) == 0, f.said

@test
def refuse_payload_run_id_not_matching_the_record():
    with tmp() as t:
        d, pk, f = member(t); key, crt = good_key(f)
        f.updates_body = forged(f, pk, key, crt, runId='0' * 64)
        refused_by_approval(f, pk, 'is for run 000000000000')

@test
def failed_verification_does_not_record_the_serial():
    with tmp() as t:
        d, pk, f = member(t); a = announce(f, pk); a['approval']['payload'] += ' '; f.updates_body = a
        refused_by_approval(f, pk)
        assert not (f.runs_dir / 'accepted-serial.json').exists()

@test
def store_install_publications_come_from_the_signed_payload():
    # the unsigned notice says one manifest sha, the signed payload another: the step is refused against the signed one
    steps = [{'verb': 'store-install', 'app': APP, 'version': VER, 'manifestSha256': MSHA}]
    with tmp() as t:
        d = make_packet(t, devices={'member1': steps}); pk = rr.load_packet(d); f = Fake(t, 'remote', 'member1')
        a = announce(f, pk); a['publications'] = [dict(PUBS[0], manifestSha256='9' * 64)]     # unsigned lie that would pass or fail differently
        f.updates_body = a
        assert rr.main(['follow', '--yes'], f) == 0, f.said
    with tmp() as t:
        d = make_packet(t, devices={'member1': steps}); pk = rr.load_packet(d); f = Fake(t, 'remote', 'member1')
        s = signer(f); ap, _ = rr.make_approval(s, pk.run_id, [dict(PUBS[0], manifestSha256='9' * 64)], ['member1'])
        a = announce(f, pk); a['approval'] = ap; f.updates_body = a
        assert rr.main(['follow', '--yes'], f) == 1 and 'announces manifest 999999999999' in refused(f)[0] and 'install' not in f.events, f.said

# ---- device coverage
@test
def member_not_in_signed_devices_does_nothing_even_with_a_star_record():
    with tmp() as t:
        d = make_packet(t, devices={'*': [{'verb': 'update-apply', 'releaseId': RELID}]}); pk = rr.load_packet(d)
        f = Fake(t, 'remote', 'latecomer'); f.updates_body = announce(f, pk)          # signed when latecomer was not in the isle
        assert rr.main(['follow', '--auto'], f) == 0 and not f.said and not f.events and not (f.runs_dir / pk.run_id).exists()
        assert rr.main(['follow', '--yes'], f) == 1 and 'does not cover this device' in refused(f)[0] and not f.events
        f2 = Fake(t, 'remote', 'member2'); f2.updates_body = f.updates_body             # an existing remote is covered
        assert rr.main(['follow', '--yes'], f2) == 0 and f2.events == ['update'], f2.said

@test
def star_resolution_covers_remote_non_mock_devices_core_with_steps_and_waits_on_members_only():
    with tmp() as t:
        d = make_packet(t, devices={'*': [{'verb': 'update-apply', 'releaseId': RELID}]}, publish=False); f = Fake(t)
        f.device_list.append({'name': 'old-core', 'agent_mode': 'core', 'is_mock': False})
        assert rr.main(['run', str(d), '--yes'], f) == 0, f.said
        assert json.loads(f.notice['run']['approval']['payload'])['devices'] == ['core1', 'member1', 'member2']
        table = [l for l in f.said if l.startswith('  member')]
        assert len(table) == 2 and not any('core1' in l or 'mock1' in l for l in table), f.said
    with tmp() as t:                                       # core with no steps is left out
        d = make_packet(t, devices={'member1': [{'verb': 'update-apply', 'releaseId': RELID}]}, publish=False); f = Fake(t)
        assert rr.main(['run', str(d), '--yes'], f) == 0
        assert json.loads(f.notice['run']['approval']['payload'])['devices'] == ['member1']

@test
def run_refuses_to_approve_a_star_when_the_device_list_is_unreadable():
    with tmp() as t:
        d = make_packet(t, devices={'*': [{'verb': 'update-apply', 'releaseId': RELID}]}); f = Fake(t); f.devices_unreadable = True
        assert rr.main(['run', str(d), '--yes'], f) == 1
        assert 'cannot be resolved' in refused(f)[0] and 'publish' not in f.events and 'update' not in f.events and 'notice' not in f.events

# ---- core minting, serial, resume
@test
def approval_key_verb_mints_keeps_and_renews():
    with tmp() as t:
        f = Fake(t); assert rr.main(['approval-key'], f) == 0, f.said
        text = '\n'.join(f.said)
        assert 'CN=isle-release-approval' in text and 'Test Root main' in text and 'SHA-256' in text and 'PRIVATE' not in text
        ad = f.approval_dir
        assert oct(ad.stat().st_mode & 0o777) == '0o700' and oct((ad / 'key.pem').stat().st_mode & 0o777) == '0o600' and oct((ad / 'cert.pem').stat().st_mode & 0o777) == '0o644'
        info = ossl('x509', '-in', ad / 'cert.pem', '-noout', '-text')
        assert 'ecdsa-with-SHA256' in info and 'CA:FALSE' in info and 'Code Signing' in info and 'DNS:' not in info and 'TLS Web Server' not in info
        before = (ad / 'cert.pem').read_bytes()
        f2 = Fake(t); f2.approval_dir = ad; assert rr.main(['approval-key'], f2) == 0 and (ad / 'cert.pem').read_bytes() == before
        f3 = Fake(t); f3.approval_dir = ad; f3.clock += 720 * 86400          # within 30 days of expiry (730d): re-minted
        rr.ensure_approval_key(f3); assert (ad / 'cert.pem').read_bytes() != before
        f4 = Fake(t, 'remote'); assert rr.main(['approval-key'], f4) == 1 and 'core only' in ' '.join(f4.said)
        f5 = Fake(t); f5.root_user = False; assert rr.main(['approval-key'], f5) == 1
    with tmp() as t:     # a member (no root key) cannot mint
        f = Fake(t); f.ca_dir = Path(t) / 'memberca'; (f.ca_dir).mkdir(); (f.ca_dir / 'isle-root.crt').write_text((PKI.dir / 'isle-root.crt').read_text())
        assert rr.main(['approval-key'], f) == 1 and 'only the core' in refused(f)[0]

@test
def serial_grows_per_new_run_and_a_resumed_run_keeps_its_approval():
    with tmp() as t:
        d = make_packet(t); f = Fake(t); f.notice_fail_until = 10 ** 6            # announce never lands: run fails after signing
        assert rr.main(['run', str(d), '--yes'], f) == 1
        rid = rr.load_packet(d).run_id
        first = json.loads((f.runs_dir / rid / 'run.json').read_text())['approval']
        serial = json.loads(first['payload'])['serial']; assert json.loads((f.runs_dir / 'serial').read_text()) == serial
        f2 = Fake(t); f2.clock = f.clock + 600
        assert rr.main(['run', str(d), '--yes'], f2) == 0, f2.said
        assert f2.notice['run']['approval'] == first and json.loads((f2.runs_dir / 'serial').read_text()) == serial
        d2 = make_packet(Path(t) / 'two', mutate=lambda doc: doc.update(name='second run')); f3 = Fake(t); f3.clock = f.clock + 900
        assert rr.main(['run', str(d2), '--yes'], f3) == 0, f3.said
        assert json.loads(f3.notice['run']['approval']['payload'])['serial'] > serial

# ---- follow --auto
@test
def auto_is_quiet_when_there_is_nothing_to_do():
    with tmp() as t:
        d, pk, f = member(t)
        assert rr.main(['follow', '--auto'], f) == 0 and not f.said and not f.events         # no run announced
        d, pk, f = member(t, device='stranger'); f.updates_body = announce(f, pk)
        assert rr.main(['follow', '--auto'], f) == 0 and not f.said and not f.events         # run does not name us
        d, pk, f = member(t); f.updates_body = announce(f, pk)
        assert rr.main(['follow', '--auto'], f) == 0 and f.events == ['update'] and f.said[0].startswith('follow: running') and not refused(f)
        f2 = Fake(t, 'remote', 'member1'); f2.updates_body = f.updates_body
        assert rr.main(['follow', '--auto'], f2) == 0 and not f2.said and not f2.events      # already done here
        f3 = Fake(t, 'core'); f3.updates_body = f.updates_body
        assert rr.main(['follow', '--auto'], f3) == 0 and not f3.said                         # not a member

@test
def auto_halts_after_three_failures_and_a_manual_follow_resumes():
    with tmp() as t:
        d, pk, f0 = member(t); body = announce(f0, pk)
        for n in (1, 2, 3):
            f = Fake(t, 'remote', 'member1'); f.updates_body = body; f.update_rcs = [1]; f.status_retryable = False
            assert rr.main(['follow', '--auto'], f) == 1 and refused(f), (n, f.said)
            r = json.loads((f.runs_dir / pk.run_id / 'receipt.json').read_text())
            assert r['autoAttempts'] == n and bool(r.get('halted')) == (n == 3)
        assert r['state'] == 'failed' and r['halted'] is True and r['needs'] == 'sudo isle release follow'
        assert any('HALTED' in l and 'sudo isle release follow' in l for l in f.said)
        f = Fake(t, 'remote', 'member1'); f.updates_body = body
        assert rr.main(['follow', '--auto'], f) == 0 and not f.said and not f.events         # halted: quiet, nothing runs
        f = Fake(t, 'remote', 'member1'); f.updates_body = body; f.status_state = 'succeeded'
        assert rr.main(['status'], f) == 0 and 'HALTED' in ' '.join(f.said)
        f.said.clear(); assert rr.main(['follow', '--yes'], f) == 0, f.said                   # manual resumes
        r = json.loads((f.runs_dir / pk.run_id / 'receipt.json').read_text())
        assert r['state'] == 'done' and 'halted' not in r and 'needs' not in r and r['autoAttempts'] == 0

@test
def auto_verification_refusal_is_said_once_and_leaves_no_receipt():
    with tmp() as t:
        d, pk, f = member(t); a = announce(f, pk); del a['approval']; body = a
        f.updates_body = body
        assert rr.main(['follow', '--auto'], f) == 1 and 'no signed approval' in refused(f)[0]
        for _ in range(2):
            f = Fake(t, 'remote', 'member1'); f.updates_body = body
            assert rr.main(['follow', '--auto'], f) == 0 and not f.said and not f.events
        assert not (f.runs_dir / pk.run_id).exists() and not f.reports
        a2 = announce(f, pk); a2['approval']['payload'] += ' '; f = Fake(t, 'remote', 'member1'); f.updates_body = a2     # a different reason speaks again
        assert rr.main(['follow', '--auto'], f) == 1 and 'signature' in refused(f)[0]
        good = announce(f, pk); f = Fake(t, 'remote', 'member1'); f.updates_body = good
        assert rr.main(['follow', '--auto'], f) == 0 and f.events == ['update']

@test
def one_flock_for_manual_and_auto_follow():
    with tmp() as t:
        d, pk, f = member(t); f.updates_body = announce(f, pk)
        held = rr.Deps().try_lock(f.runs_dir / 'follow.lock'); assert held
        assert rr.main(['follow', '--auto'], f) == 0 and not f.said and not f.events
        assert rr.main(['follow', '--yes'], f) == 1 and 'another release follow is already running' in refused(f)[0] and not f.events
        held.close()
        f.said.clear(); assert rr.main(['follow', '--auto'], f) == 0 and f.events == ['update']

# ---- core wait
def run_with_members(t, state, extra=None, args=('--yes',)):
    d = make_packet(t); f = Fake(t); f.member_state = {'member1': state}
    if extra: extra(f)
    return rr.main(['run', str(d)] + list(args), f), f

@test
def wait_exit_0_when_all_done_and_prints_changes():
    with tmp() as t:
        code, f = run_with_members(t, lambda age: 'running' if age < 60 else 'done')
        assert code == 0, f.said
        lines = [l for l in f.said if l.startswith('member1:')]
        assert lines[0].startswith('member1: running') and lines[-1].startswith('member1: done') and len(lines) == 2
        assert any('Member results' in l for l in f.said)

@test
def wait_exit_1_when_a_member_failed():
    with tmp() as t:
        code, f = run_with_members(t, 'failed')
        assert code == 1 and any('Failed: member1' in l for l in f.said)

@test
def wait_exit_3_when_still_pending_and_names_them():
    with tmp() as t:
        code, f = run_with_members(t, None, args=('--yes', '--wait', '1'))
        text = ' '.join(f.said)
        assert code == 3 and 'pending when the wait ended: member1' in text and 'catch up on their timers' in text
        assert f.clock - f.t0 < 200                      # honoured the 1-minute limit
    with tmp() as t:                                     # a fact for a DIFFERENT run counts as pending
        code, f = run_with_members(t, 'done', extra=lambda f: setattr(f, 'fact_run', 'f' * 64), args=('--yes', '--wait', '1'))
        assert code == 3
    with tmp() as t:                                     # an unreadable device list never counts as done
        code, f = run_with_members(t, 'done', extra=lambda f: setattr(f, 'devices_unreadable', True), args=('--yes', '--wait', '1'))
        assert code == 3

@test
def no_wait_does_not_read_the_device_list_and_options_validate():
    with tmp() as t:
        code, f = run_with_members(t, None, args=('--yes', '--no-wait'))
        assert code == 0 and not any(u.endswith('/api/islemesh/devices') for u, _ in f.http_log)
        d = make_packet(t); f = Fake(t)
        assert rr.main(['run', str(d), '--wait', '5', '--no-wait'], f) == 2 and not f.argv
        assert rr.main(['run', str(d), '--wait', 'x'], f) == 2 and rr.main(['run', str(d), '--bogus'], f) == 2 and not f.argv

@test
def advisory_reports_never_make_a_member_skip_or_execute():
    # a member that "reports done" on the open channel still runs its own steps from the signed approval
    with tmp() as t:
        d, pk, f = member(t); f.updates_body = announce(f, pk); f.member_state = {'member1': 'done'}
        assert rr.main(['follow', '--yes'], f) == 0 and f.events == ['update']
        assert not any(u.endswith('/api/islemesh/devices') for u, _ in f.http_log)
    src = Path(rr.__file__).read_text(); assert 'ADVISORY ONLY' in src

@test
def status_on_the_core_shows_the_member_table():
    with tmp() as t:
        code, f = run_with_members(t, 'done'); f.said.clear()
        assert rr.main(['status'], f) == 0
        assert any(l.startswith('  member1') and 'done' in l for l in f.said) and 'Member reports' in ' '.join(f.said)

# ---- --expect
@test
def expect_must_match_the_packet_run_id():
    with tmp() as t:
        d = make_packet(t); rid = rr.load_packet(d).run_id
        f = Fake(t); assert rr.main(['check', str(d), '--expect', rid], f) == 0
        f = Fake(t); assert rr.main(['check', str(d), '--expect', '0' * 64], f) == 1 and 'expected run id' in refused(f)[0]
        f = Fake(t); assert rr.main(['run', str(d), '--yes', '--expect', '0' * 64], f) == 1 and not f.argv and 'expected run id' in refused(f)[0]
        f = Fake(t); assert rr.main(['run', str(d), '--yes', '--no-wait', '--expect', rid], f) == 0, f.said
        f = Fake(t); assert rr.main(['check', str(d), '--expect', 'abc'], f) == 2

# ---- report_fact
@test
def report_fact_none_running_failed_corrupt():
    with tmp() as t:
        rd = Path(t) / 'runs'
        assert rr.report_fact(str(rd)) is None                         # no directory
        rd.mkdir(); assert rr.report_fact(rd) is None                  # no receipt
        (rd / ('a' * 64)).mkdir(); (rd / ('a' * 64) / 'receipt.json').write_text('{not json')
        assert rr.report_fact(str(rd)) is None                         # corrupt
        run = rd / ('b' * 64); run.mkdir()
        (run / 'receipt.json').write_text(json.dumps({'runId': 'b' * 64, 'serial': 7, 'state': 'running', 'updatedAt': 'U1', 'steps': [
            {'state': 'done', 'summary': 'one', 'detail': ''}, {'state': 'running', 'summary': 'two ' + 'x' * 400, 'detail': ''}]}))
        out = rr.report_fact(str(rd)); assert isinstance(out, str)
        v = json.loads(out); assert v['runId'] == 'b' * 64 and v['serial'] == 7 and v['state'] == 'running' and v['updatedAt'] == 'U1' and len(v['step']) <= 200 and v['step'].startswith('two')
        run2 = rd / ('c' * 64); run2.mkdir()
        (run2 / 'receipt.json').write_text(json.dumps({'runId': 'c' * 64, 'state': 'refused', 'steps': [{'state': 'failed', 'summary': 'install x', 'detail': 'boom'}]}))
        os.utime(run2 / 'receipt.json', (time.time() + 100, time.time() + 100))
        v = json.loads(rr.report_fact(str(rd))); assert v['runId'] == 'c' * 64 and v['state'] == 'failed' and v['step'] == 'install x: boom' and v['serial'] is None
        assert set(v) == {'runId', 'serial', 'state', 'step', 'updatedAt'}
        assert rr.report_fact(None) is None                            # never raises

@test
def report_fact_reflects_a_real_follow_and_the_import_is_free_of_side_effects():
    with tmp() as t:
        d, pk, f = member(t); f.updates_body = announce(f, pk)
        assert rr.main(['follow', '--yes'], f) == 0
        v = json.loads(rr.report_fact(str(f.runs_dir)))
        assert v['runId'] == pk.run_id and v['state'] == 'done' and type(v['serial']) is int
    # importing (capabilities.py does it every 2 minutes) prints nothing, writes nothing, runs no command
    here = tempfile.mkdtemp()
    p = subprocess.run([sys.executable, '-I', '-c', 'import sys; sys.path.insert(0, %r); import release_run; print(callable(release_run.report_fact))' % str(Path(rr.__file__).parent)],
                       capture_output=True, text=True, cwd=here)
    assert p.returncode == 0 and p.stdout == 'True\n' and p.stderr == '' and os.listdir(here) == [], (p.stdout, p.stderr)


# ---- slice 2b: whole releases in one packet
def check_refusal(t, needle, **kw):
    d = full_packet(t, **kw) if not kw.pop('plain', False) else make_packet(t, **kw); f = Fake(t)
    code = rr.main(['check', str(d)], f)
    assert code == 1 and needle in ' '.join(f.said), (needle, f.said)

@test
def full_packet_checks_and_summary_says_the_id_is_assigned_at_publication():
    with tmp() as t:
        d = full_packet(t); f = Fake(t)
        assert rr.main(['check', str(d)], f) == 0, f.said
        text = '\n'.join(f.said)
        assert text.index('runtime images from') < text.index('package file(s) from') < text.index('catalog release')     # fixed order, not packet order
        assert 'release id does not exist yet' in text and 'assigned at publication' in text and 'core1' in text
        pk = rr.load_packet(d)
        assert [p['kind'] for p in pk.publications] == ['runtime', 'debs', 'catalog']
        assert 'runtime/images/backend.tar' in pk.files and 'debs/isle-mesh-cli_1.deb' in pk.files and 'runtime/manifest.json' in pk.files

@test
def packet_refuses_an_unlisted_deb_and_odd_files_in_the_debs_dir():
    with tmp() as t: check_refusal(t, 'not listed in SHA256SUMS', sums_skip=('debs/isle-app-store_1.deb',))
    with tmp() as t:
        def extra(doc): (Path(t) / 'packet' / 'debs').mkdir(exist_ok=True); (Path(t) / 'packet' / 'debs' / 'README').write_text('x')
        check_refusal(t, 'is not a .deb', mutate=lambda doc: extra(doc))
    with tmp() as t:
        def link(doc):
            (Path(t) / 'packet' / 'debs').mkdir(exist_ok=True); os.symlink('isle-mesh-cli_1.deb', Path(t) / 'packet' / 'debs' / 'alias.deb')
        check_refusal(t, 'not a regular file', mutate=link)
    with tmp() as t:
        def sub(doc): (Path(t) / 'packet' / 'debs' / 'nested').mkdir(parents=True, exist_ok=True)
        check_refusal(t, 'not a regular file', mutate=sub)
    with tmp() as t:        # a listed file whose bytes changed after SHA256SUMS was made
        d = full_packet(t); (d / 'debs' / 'isle-mesh-cli_1.deb').write_bytes(b'changed'); f = Fake(t)
        assert rr.main(['check', str(d)], f) == 1 and 'checksum mismatch' in ' '.join(f.said)

@test
def packet_refuses_missing_or_unlisted_runtime_archives_and_bad_manifests():
    with tmp() as t: check_refusal(t, 'not listed in SHA256SUMS', sums_skip=('runtime/images/backend.tar',))
    with tmp() as t:
        d = full_packet(t); (d / 'runtime' / 'images' / 'backend.tar').unlink(); write_sums(d); f = Fake(t)
        assert rr.main(['check', str(d)], f) == 1 and 'not there' in ' '.join(f.said)
    with tmp() as t:
        d = full_packet(t); m = runtime_doc(); m['runtimes']['core'][0]['image']['archive']['source'] = '/etc/passwd'
        (d / 'runtime' / 'manifest.json').write_text(json.dumps(m)); write_sums(d); f = Fake(t)
        assert rr.main(['check', str(d)], f) == 1 and 'relative to the manifest' in ' '.join(f.said)
    with tmp() as t:
        d = full_packet(t); m = runtime_doc(); m['runtimes']['core'][0]['image']['archive']['source'] = '../../outside.tar'
        (d / 'runtime' / 'manifest.json').write_text(json.dumps(m)); write_sums(d); f = Fake(t)
        assert rr.main(['check', str(d)], f) == 1 and 'leaves the packet' in ' '.join(f.said)
    with tmp() as t:
        d = full_packet(t); (d / 'runtime' / 'manifest.json').write_text(json.dumps({'runtimes': {'core': 'x'}})); write_sums(d); f = Fake(t)
        assert rr.main(['check', str(d)], f) == 1 and 'must be a list' in ' '.join(f.said)

@test
def packet_refuses_two_entries_runtime_without_debs_and_unbound_published():
    with tmp() as t: check_refusal(t, 'at most one runtime, one debs and one dependencies', mutate=lambda doc: doc['publish'].append({'kind': 'debs', 'dir': 'debs'}))
    with tmp() as t: check_refusal(t, 'at most one runtime, one debs and one dependencies', mutate=lambda doc: doc['publish'].append({'kind': 'runtime', 'manifest': 'runtime/manifest.json'}))
    with tmp() as t:
        check_refusal(t, 'needs the debs entry too', mutate=lambda doc: doc.update(publish=[p for p in doc['publish'] if p['kind'] != 'debs']))
    with tmp() as t:        # "published" with no runtime/debs publication at all, and with only a catalog one
        steps = [{'verb': 'update-apply', 'releaseId': 'published'}]
        for pub in (False, True):
            d = make_packet(Path(t) / str(pub), devices={'member1': steps}, publish=pub); f = Fake(t)
            assert rr.main(['check', str(d)], f) == 1 and '"published" needs a runtime or debs publication' in ' '.join(f.said), f.said
    with tmp() as t:        # debs alone is fine and makes "published" legal
        d = Path(t) / 'packet'
        make_packet(t, devices={'member1': [{'verb': 'update-apply', 'releaseId': 'published'}]}, publish=False, mutate=lambda doc: add_debs(doc, d))
        assert rr.main(['check', str(d)], Fake(t)) == 0

PACKET_CORE_ORDER = ['confirm', 'publish-runtime', 'publish-debs', 'publish', 'install', 'update', 'notice']

KEPT_SHA = 'e' * 64
def kept_doc(**change):
    """The agent's image comes with the packet; the backend's is kept as the core publishes it (no archive)."""
    m = runtime_doc()
    archive = dict({'url': 'https://apt.isle/runtime/%s.tar' % KEPT_SHA, 'sha256': KEPT_SHA, 'size': 77}, **change)
    for mode in ('core', 'remote'): m['runtimes'][mode][0]['image']['archive'] = dict(archive)
    return m

def kept_packet(t, manifest=None):
    d = full_packet(t); (d / 'runtime' / 'manifest.json').write_text(json.dumps(manifest or kept_doc())); write_sums(d)
    return d

@test
def a_runtime_row_without_an_archive_names_the_published_file_and_the_summary_says_so():
    with tmp() as t:
        d = kept_packet(t); f = Fake(t)
        assert rr.main(['check', str(d)], f) == 0, f.said
        pub = rr.load_packet(d).publications[0]
        assert pub['archives'] == ['runtime/images/backend.tar']
        assert pub['kept'] == [{'mode': m, 'container': 'prf-isle-backend', 'sha256': KEPT_SHA, 'size': 77} for m in ('core', 'remote')]
        text = '\n'.join(f.said)
        assert 'carries no archive for: core prf-isle-backend, remote prf-isle-backend' in text and 'core must still have their files' in text
    for change in ({'url': 'https://apt.isle/runtime/other.tar'}, {'url': 'https://elsewhere.isle/runtime/%s.tar' % KEPT_SHA},
                   {'size': 0}, {'sha256': 'E' * 64}, {'note': 'one key more'}):
        with tmp() as t:
            d = kept_packet(t, kept_doc(**change)); f = Fake(t)
            assert rr.main(['check', str(d)], f) == 1 and 'has to name the file the core publishes' in ' '.join(f.said), (change, f.said)
    with tmp() as t:        # every row kept: the packet changes no runtime image, so it has no business publishing one
        m = kept_doc(); m['runtimes']['remote'][1]['image']['archive'] = dict(m['runtimes']['core'][0]['image']['archive'])
        d = kept_packet(t, m); f = Fake(t)
        assert rr.main(['check', str(d)], f) == 1 and 'names no runtime image the packet carries' in ' '.join(f.said), f.said

@test
def the_core_refuses_a_kept_runtime_image_it_does_not_publish_before_anything_is_published():
    for size in (None, 76):                      # no such file, and a file of another size
        with tmp() as t:
            d = kept_packet(t); f = Fake(t); f.update_rcs = [0]
            f.published_runtime_size = lambda sha, size=size: size
            assert rr.main(['run', str(d), '--yes'], f) == 1, f.said
            text = ' '.join(f.said)
            assert 'REFUSED: publish-runtime on core1' in text and 'prf-isle-backend (%s.tar)' % KEPT_SHA[:12] in text, f.said
            assert 'Nothing was published' in text and '--all-images' in text
            assert f.events == [] and not [a for a in f.argv if a[:1] == ['bash']], (f.events, f.argv)
            assert f.notice == {'id': 'x' * 64, 'packages': [1], 'command': 'keep me'}
    with tmp() as t:                             # the core has the file: the run goes on as any other
        d = kept_packet(t); f = Fake(t); f.update_rcs = [0]; asked = []
        f.published_runtime_size = lambda sha: asked.append(sha) or 77
        assert rr.main(['run', str(d), '--yes'], f) == 0, f.said
        assert set(asked) == {KEPT_SHA} and f.events == [e for e in PACKET_CORE_ORDER if e != 'confirm'], (asked, f.events)


@test
def full_run_executes_in_fixed_order_from_a_copy_and_signs_the_release_id():
    with tmp() as t:
        d = full_packet(t); f = Fake(t); f.update_rcs = [0]
        seen = {}
        def hook(kind):
            seen[kind] = (f.runs_dir / rr.load_packet(d).run_id / 'packet')
            seen[kind + '-mode'] = oct(seen[kind].stat().st_mode & 0o777)
        f.on_publish = hook
        assert rr.main(['run', str(d), '--yes'], f) == 0, f.said
        assert f.events == [e for e in PACKET_CORE_ORDER if e != 'confirm'], f.events
        pk = rr.load_packet(d); copy = f.runs_dir / pk.run_id / 'packet'
        argvs = [a for a in f.argv if a[:1] == ['bash']]
        assert argvs == [['bash', str(f.scripts / 'update.sh'), 'publish-runtime', str(copy / 'runtime/manifest.json')],
                         ['bash', str(f.scripts / 'apt-repo.sh'), 'publish', '--from', str(copy / 'debs')],
                         ['bash', str(f.scripts / 'store.sh'), 'publish', str(copy / 'release.json')]], argvs
        assert seen['debs-mode'] == '0o700' and not copy.exists()                       # copy removed when the core's part finished
        rid = 'ab' * 32
        assert ['python3', str(f.scripts / 'operations.py'), 'run', 'update', 'apply', rid] in f.argv
        receipt = json.loads((f.runs_dir / pk.run_id / 'receipt.json').read_text())
        assert [s['verb'] for s in receipt['steps']] == ['publish-runtime', 'publish-debs', 'release-id', 'wait-served', 'publish', 'wait-served', 'store-install', 'update-apply', 'announce']
        assert next(s for s in receipt['steps'] if s['verb'] == 'release-id')['data'] == {'releaseId': rid}
        assert json.loads(f.notice['run']['approval']['payload'])['releaseId'] == rid
        assert any('Release id assigned by publication: ' + rid in l for l in f.said)

@test
def member_resolves_published_only_from_the_signed_payload():
    with tmp() as t:
        d = full_packet(t); f = Fake(t); f.update_rcs = [0]
        assert rr.main(['run', str(d), '--yes'], f) == 0, f.said
        body = copy.deepcopy(f.notice['run']); body['releaseId'] = 'f' * 64          # an unsigned field that disagrees: ignored
        m = Fake(t, 'remote', 'member1'); m.runs_dir = Path(t) / 'mruns'; m.updates_body = body
        assert rr.main(['follow', '--yes'], m) == 0, m.said
        assert ['python3', str(m.scripts / 'operations.py'), 'run', 'update', 'apply', 'ab' * 32] in m.argv
        assert not any('f' * 64 in a for a in m.argv)
    with tmp() as t:        # null or malformed releaseId in the signed payload: refused before anything runs
        d = full_packet(t); pk = rr.load_packet(d)
        for bad in (None, 'zz', 'A' * 64, 'f' * 63):
            f = Fake(t, 'remote', 'member1'); key, crt = good_key(f)
            f.updates_body = forged(f, pk, key, crt, releaseId=bad, publications=[])
            f.updates_body['record'] = pk.record; f.updates_body['devices'] = pk.doc['devices']
            code = rr.main(['follow', '--yes'], f)
            assert code == 1 and 'no valid release id' in refused(f)[0] and not f.events and not (f.runs_dir / pk.run_id).exists(), (bad, f.said)
        f = Fake(t, 'remote', 'member1'); key, crt = good_key(f); f.updates_body = forged(f, pk, key, crt, releaseId=None, publications=[])
        f.updates_body['releaseId'] = 'f' * 64                                           # unsigned id must not rescue it
        assert rr.main(['follow', '--auto'], f) == 1 and not f.events

@test
def wait_served_for_the_release_checks_id_packages_and_runtime_assets():
    with tmp() as t:        # /updates still names the old id for 100 s, then everything appears
        d = full_packet(t); f = Fake(t); f.update_rcs = [0]; f.release_after = 100; f.assets_after = 160
        assert rr.main(['run', str(d), '--yes'], f) == 0, f.said
        waits = [l for l in f.said if l.startswith('wait-served release') and 'still missing' in l]
        assert waits and 'names release old' in waits[0] and any('answers 404' in w for w in waits), waits
        urls = [u for u, head in f.http_log if head and u.startswith('https://apt.isle/')]
        assert 'https://apt.isle/isle-mesh-cli_1.deb' in urls and RT_URL in urls
    with tmp() as t:        # a runtime asset never appears: refused, nothing installed or announced
        d = full_packet(t); f = Fake(t); f.missing_urls = {RT_URL}
        assert rr.main(['run', str(d), '--yes'], f) == 1
        assert 'wait-served release' in refused(f)[0] and RT_URL in refused(f)[0] and not {'install', 'update', 'notice', 'publish'} & set(f.events)
        assert (f.runs_dir / rr.load_packet(d).run_id / 'packet').is_dir()               # failure keeps the copy

@test
def release_id_refuses_a_notice_that_this_publication_did_not_rebuild():
    with tmp() as t:        # apt-repo.sh only WARNS when the notice cannot be written; the run must not continue on a stale id
        d = full_packet(t); f = Fake(t); f.notice_stale = True; f.notice = {'id': 'c' * 64, 'publishedAt': datetime.fromtimestamp(f.clock - 3600, timezone.utc).isoformat(), 'packages': [{'file': 'x.deb'}]}
        assert rr.main(['run', str(d), '--yes'], f) == 1 and 'release-id' in refused(f)[0] and 'not rebuilt' in refused(f)[0] and not {'install', 'update', 'notice'} & set(f.events)

@test
def publication_failure_resumes_where_it_stopped_and_reuses_the_copy():
    with tmp() as t:
        d = full_packet(t); f = Fake(t); f.apt_rc = 1
        assert rr.main(['run', str(d), '--yes'], f) == 1 and 'publish-debs' in refused(f)[0]
        assert f.events == ['publish-runtime', 'publish-debs'], f.events
        pk = rr.load_packet(d); copy = f.runs_dir / pk.run_id / 'packet'
        ino = (copy / 'debs' / 'isle-mesh-cli_1.deb').stat().st_ino
        f2 = Fake(t); f2.update_rcs = [0]; f2.runtime_published = f.runtime_published
        assert rr.main(['run', str(d), '--yes'], f2) == 0, f2.said
        assert f2.events == ['publish-debs', 'publish', 'install', 'update', 'notice'], f2.events     # runtime was not published again
        assert f2.publish_argv[0][4] == str(copy / 'debs') and not copy.exists()
        # a finished run re-run: nothing is published and no copy is made at all
        f3 = Fake(t); f3.notice = f2.notice; assert rr.main(['run', str(d), '--yes'], f3) == 0
        assert not {'publish-runtime', 'publish-debs', 'publish', 'install', 'update'} & set(f3.events) and not (f.runs_dir / pk.run_id / 'packet').exists()
    with tmp() as t:        # a runtime publication that fails stops before any package is published
        d = full_packet(t); f = Fake(t); f.runtime_rc = 1
        assert rr.main(['run', str(d), '--yes'], f) == 1 and 'publish-debs' not in f.events

# ---- a dependency set in a packet
def deps_packet(t, **kw):
    """The full packet plus a dependency set, the set listed last on purpose."""
    more, own = kw.pop('mutate', None), {k: kw.pop(k) for k in ('system', 'entry') if k in kw}
    def mutate(doc):
        add_deps(doc, Path(t) / 'packet', **own)
        if more: more(doc)
    return full_packet(t, mutate=mutate, **kw)

DEPS_ORDER = ['publish-dependencies', 'publish-runtime', 'publish-debs', 'publish', 'install', 'update', 'notice']

@test
def a_dependency_set_checks_and_the_record_holds_one_line_for_its_folder():
    with tmp() as t:
        d = deps_packet(t); f = Fake(t)
        assert rr.main(['check', str(d)], f) == 0, f.said
        text = '\n'.join(f.said)
        assert 'the dependency set of ubuntu-22.04: 2 package file(s) from %s, served under deps/jammy' % DEPS_DIR in text
        assert text.index('the dependency set of') < text.index('runtime images from') < text.index('package file(s) from debs')
        pk = rr.load_packet(d)
        assert [p['kind'] for p in pk.publications] == ['dependencies', 'runtime', 'debs', 'catalog']
        assert [k for k in pk.files if k.startswith('dependencies')] == [DEPS_DIR + '/']               # one line, not one per file
        listed = len((d / 'SHA256SUMS').read_text().splitlines())
        assert pk.checked == listed == len(pk.files) + 3 and 'Packet is valid: %d file(s) match SHA256SUMS' % listed in text
        assert pk.publications[0] == {'kind': 'dependencies', 'dir': DEPS_DIR, 'system': 'ubuntu-22.04', 'release': 'jammy',
                                      'files': {n: rr.sha256_bytes(b) for n, b in DEPS.items()}}
        assert rr.record_problem(pk.record) is None                                                    # a member reads the same record
        ids = {pk.run_id}
        for name, data in (('libfoo_1_amd64.deb', b'other'), ('CLOSURE_URIS.txt', b'other'), ('ubuntu-22.04.txt', b'other')):
            (d / DEPS_DIR / name).write_bytes(data); write_sums(d); ids.add(rr.load_packet(d).run_id)   # every byte of the folder is in the run id
        assert len(ids) == 4
        (d / DEPS_DIR / 'libfoo_1_amd64.deb').rename(d / DEPS_DIR / 'libfoo_2_amd64.deb'); write_sums(d)
        assert rr.load_packet(d).run_id not in ids                                                      # and so is every name

@test
def a_dependency_set_is_refused_when_it_does_not_say_one_known_system_or_holds_an_unlisted_file():
    marker = DEPS_DIR + '/ubuntu-22.04.txt'
    with tmp() as t:
        d = deps_packet(t, mutate=lambda doc: doc['publish'].append({'kind': 'dependencies', 'dir': DEPS_DIR})); f = Fake(t)
        assert rr.main(['check', str(d)], f) == 1 and 'at most one runtime, one debs and one dependencies entry' in ' '.join(f.said)
    for system, words in (([], 'with one file <system id>.txt (it has: none)'),
                          (['ubuntu-22.04', 'ubuntu-24.04'], '(it has: ubuntu-22.04.txt, ubuntu-24.04.txt)'),
                          ('ubuntu-18.04', 'is built for a system this CLI does not know')):
        with tmp() as t:
            d = deps_packet(t, system=system); f = Fake(t)
            assert rr.main(['check', str(d)], f) == 1 and words in ' '.join(f.said), (system, f.said)
    with tmp() as t:        # the file that names the system is not listed: the copy the core publishes from would not hold it
        d = deps_packet(t, sums_skip=(marker,)); f = Fake(t)
        assert rr.main(['check', str(d)], f) == 1 and 'not listed in SHA256SUMS' in ' '.join(f.said) and marker in ' '.join(f.said), f.said
    with tmp() as t:
        d = deps_packet(t, sums_skip=(DEPS_DIR + '/CLOSURE_URIS.txt',)); f = Fake(t)
        assert rr.main(['check', str(d)], f) == 1 and 'not listed in SHA256SUMS' in ' '.join(f.said)
    for label, spoil, words in (
            ('link', lambda d: os.symlink('libfoo_1_amd64.deb', d / DEPS_DIR / 'alias.deb'), 'not a regular file'),
            ('folder', lambda d: (d / DEPS_DIR / 'nested').mkdir(), 'not a regular file'),
            ('changed', lambda d: (d / DEPS_DIR / 'libfoo_1_amd64.deb').write_bytes(b'changed'), 'checksum mismatch'),
            ('gone', lambda d: (d / DEPS_DIR / 'ubuntu-22.04.txt').unlink(), 'it has: none')):
        with tmp() as t:
            d = deps_packet(t); spoil(d); f = Fake(t)
            assert rr.main(['check', str(d)], f) == 1 and words in ' '.join(f.said), (label, f.said)
    with tmp() as t:
        d = deps_packet(t)
        for n in DEPS: (d / DEPS_DIR / n).unlink()
        write_sums(d); f = Fake(t)
        assert rr.main(['check', str(d)], f) == 1 and 'no .deb files in ' + DEPS_DIR in ' '.join(f.said), f.said
    with tmp() as t: check_refusal(t, 'leaves the packet', mutate=lambda doc: doc['publish'].append({'kind': 'dependencies', 'dir': '../elsewhere'}))
    with tmp() as t: check_refusal(t, 'not a directory inside the packet', mutate=lambda doc: doc['publish'].append({'kind': 'dependencies', 'dir': 'absent'}))
    with tmp() as t: check_refusal(t, 'unknown key', mutate=lambda doc: doc['publish'].append({'kind': 'dependencies', 'dir': 'debs', 'release': 'jammy'}))

@test
def a_dependency_set_is_published_first_and_alone_and_waited_for():
    with tmp() as t:
        d = deps_packet(t); f = Fake(t); f.update_rcs = [0]; f.deps_after = 40
        assert rr.main(['run', str(d), '--yes'], f) == 0, f.said
        assert f.events == DEPS_ORDER, f.events
        pk = rr.load_packet(d); copy = f.runs_dir / pk.run_id / 'packet'
        assert f.publish_argv[0] == ['bash', str(f.scripts / 'apt-repo.sh'), 'publish', '--only-dependencies', '--dependencies-from', str(copy / DEPS_DIR)]
        assert f.deps_others == ['CLOSURE_URIS.txt', 'ubuntu-22.04.txt']                 # the copy held the file that names the system
        assert f.deps_published == pk.publications[0]['files'] and not copy.exists()
        receipt = json.loads((f.runs_dir / pk.run_id / 'receipt.json').read_text())
        assert [s['verb'] for s in receipt['steps']][:4] == ['publish-dependencies', 'wait-served', 'publish-runtime', 'publish-debs'], receipt['steps']
        waits = [l for l in f.said if l.startswith('wait-served dependencies') and 'still missing' in l]
        assert waits and 'https://apt.isle/deps/jammy/Packages answers 404' in waits[0], f.said
        heads = [u for u, head in f.http_log if head]
        assert 'https://apt.isle/libfoo_1_amd64.deb' in heads and 'https://apt.isle/libbar_2%253a1_all.deb' in heads, heads
        first_runtime = next(i for i, a in enumerate(f.argv) if a[:1] == ['bash'] and a[2] == 'publish-runtime')
        index_read = max(i for i, (u, head) in enumerate(f.http_log) if u.endswith('/deps/jammy/Packages'))
        assert f.http_log.index(('https://apt.isle/libfoo_1_amd64.deb', True)) > index_read
        assert f.argv.index(f.publish_argv[0]) < first_runtime
        assert json.loads(f.notice['run']['approval']['payload'])['releaseId'] == 'ab' * 32           # the release id is the packages' one, as before

@test
def a_dependency_set_that_is_not_served_stops_the_run_before_anything_else_is_published():
    cases = (('unlisted', lambda f: setattr(f, 'deps_unlisted', {'libfoo_1_amd64.deb'}), 'lists 1 of the 2 package files of the set with their checksums (first one it does not: libfoo_1_amd64.deb)'),
             ('other bytes', lambda f: setattr(f, 'deps_wrong', {'libbar_2%3a1_all.deb': 'e' * 64}), 'first one it does not: libbar_2%3a1_all.deb'),
             ('no index', lambda f: setattr(f, 'deps_after', 10 ** 9), 'https://apt.isle/deps/jammy/Packages answers 404'),
             ('no file', lambda f: setattr(f, 'missing_urls', {'https://apt.isle/libbar_2%253a1_all.deb'}), 'https://apt.isle/libbar_2%3a1_all.deb answers 404'))
    for label, spoil, words in cases:
        with tmp() as t:
            d = deps_packet(t); f = Fake(t); spoil(f)
            assert rr.main(['run', str(d), '--yes'], f) == 1, (label, f.said)
            assert 'wait-served dependencies' in refused(f)[0] and words in refused(f)[0], (label, refused(f))
            assert f.events == ['publish-dependencies'], (label, f.events)
            assert (f.runs_dir / rr.load_packet(d).run_id / 'packet').is_dir()               # failure keeps the copy
            if label == 'unlisted': assert not [u for u, head in f.http_log if head and u.endswith('.deb')]   # no file is asked for before the index lists them all

@test
def a_dependency_set_publication_resumes_where_it_stopped():
    with tmp() as t:        # the set itself failed: nothing else ran, and the second run starts with it
        d = deps_packet(t); f = Fake(t); f.deps_rc = 1
        assert rr.main(['run', str(d), '--yes'], f) == 1 and 'publish-dependencies' in refused(f)[0] and f.events == ['publish-dependencies'], f.said
        f2 = Fake(t); f2.update_rcs = [0]
        assert rr.main(['run', str(d), '--yes'], f2) == 0 and f2.events == DEPS_ORDER, f2.events
    with tmp() as t:        # the packages failed after the set was served: the set is not published a second time
        d = deps_packet(t); f = Fake(t); f.apt_rc = 1
        assert rr.main(['run', str(d), '--yes'], f) == 1 and f.events == DEPS_ORDER[:3], f.events
        f2 = Fake(t); f2.update_rcs = [0]; f2.runtime_published = f.runtime_published; f2.deps_published = f.deps_published
        assert rr.main(['run', str(d), '--yes'], f2) == 0 and f2.events == DEPS_ORDER[2:], f2.events
        assert not [u for u, head in f2.http_log if '/deps/' in u]

@test
def a_packet_may_hold_a_dependency_set_and_nothing_else_to_publish():
    with tmp() as t:
        d = make_packet(t, devices={'core1': []}, verify=[], publish=False, mutate=lambda doc: add_deps(doc, Path(t) / 'packet')); f = Fake(t)
        assert rr.main(['check', str(d)], f) == 0, f.said
        before = copy.deepcopy(f.notice)
        assert rr.main(['run', str(d), '--yes'], f) == 0, f.said
        assert f.events == ['publish-dependencies', 'notice'], f.events
        assert f.notice['id'] == before['id'] and f.notice['packages'] == before['packages']           # the release the core announces is the one it was
        assert not [a for a in f.argv if a[:1] == ['bash'] and a[2:4] == ['publish', '--from']]
    with tmp() as t:        # "published" names the release id of packages; a set has none
        d = make_packet(t, devices={'member1': [{'verb': 'update-apply', 'releaseId': 'published'}]}, publish=False, mutate=lambda doc: add_deps(doc, Path(t) / 'packet')); f = Fake(t)
        assert rr.main(['check', str(d)], f) == 1 and '"published" needs a runtime or debs publication' in ' '.join(f.said), f.said

@test
def a_set_alone_is_checked_by_the_devices_a_check_names_though_they_have_no_step():
    served = 'https://apt.isle/deps/jammy/InRelease'
    def packet(t, verify):
        return make_packet(t, devices={'core1': [], 'member1': []}, verify=verify, publish=False, mutate=lambda doc: add_deps(doc, Path(t) / 'packet'))
    with tmp() as t:
        d = packet(t, [{'url': served, 'device': 'core1'}, {'url': served, 'device': 'member1'}]); f = Fake(t)
        assert rr.main(['run', str(d), '--yes'], f) == 0, f.said
        assert [u for u, _ in f.http_log].count(served) == 1 and 'verify %s: ok' % served in f.said, f.said
        m = Fake(t, 'remote', 'member1'); m.runs_dir = Path(t) / 'mruns'; m.updates_body = copy.deepcopy(f.notice['run']); m.deps_published = f.deps_published
        assert rr.main(['follow', '--yes'], m) == 0, m.said
        assert [u for u, _ in m.http_log].count(served) == 1 and 'verify %s: ok' % served in m.said, m.said
    with tmp() as t:        # a check that names no device is asked of a device with steps, and here no device has one
        d = packet(t, [{'url': served}]); f = Fake(t)
        assert rr.main(['run', str(d), '--yes'], f) == 0, f.said
        assert served not in [u for u, _ in f.http_log], f.http_log

@test
def a_member_follows_a_run_whose_packet_held_a_dependency_set():
    with tmp() as t:
        d = deps_packet(t); f = Fake(t); f.update_rcs = [0]
        assert rr.main(['run', str(d), '--yes'], f) == 0, f.said
        m = Fake(t, 'remote', 'member1'); m.runs_dir = Path(t) / 'mruns'; m.updates_body = copy.deepcopy(f.notice['run'])
        assert rr.main(['follow', '--yes'], m) == 0, m.said
        assert m.events == ['confirm', 'update'] or m.events == ['update'], m.events
        assert 'publish-dependencies' not in m.events and not [a for a in m.argv if 'apt-repo.sh' in ' '.join(a)]

# ---- packet copy
@test
def packet_copy_refuses_symlinks_and_keeps_nothing():
    with tmp() as t:
        d = make_packet(t); (d / 'compose.yml').unlink(); os.symlink('release.json', d / 'compose.yml'); write_sums(d)
        (d / 'SHA256SUMS').write_text((d / 'SHA256SUMS').read_text() + '%s  compose.yml\n' % rr.sha256_file(d / 'release.json'))
        f = Fake(t); pk = rr.load_packet(d)        # a symlink that stays inside the packet passes the old checks...
        assert rr.main(['run', str(d), '--yes'], f) == 1
        assert 'symbolic link' in refused(f)[0] and 'publish' not in f.events and not (f.runs_dir / pk.run_id / 'packet').exists()
    with tmp() as t:        # a symlinked directory component
        import types
        d = make_packet(t); (Path(t) / 'elsewhere').mkdir(); (Path(t) / 'elsewhere' / 'compose.yml').write_text('services: {}\n')
        os.symlink(Path(t) / 'elsewhere', d / 'sub')
        (d / 'SHA256SUMS').write_text('%s  release-run.json\n%s  sub/compose.yml\n' % (rr.sha256_file(d / 'release-run.json'), rr.sha256_file(Path(t) / 'elsewhere' / 'compose.yml')))
        f = Fake(t)
        try: rr.copy_packet(f, types.SimpleNamespace(directory=d), f.root / 'dest'); assert False
        except rr.Refused as r: assert 'symbolic link' in r.why and not (f.root / 'dest').exists()

@test
def packet_copy_ignores_swaps_after_the_copy_and_catches_swaps_during_it():
    with tmp() as t:
        d = full_packet(t); f = Fake(t); f.update_rcs = [0]
        f.on_publish = lambda kind: (d / 'compose.yml').write_text('services: {evil: {}}\n') or (d / 'debs' / 'isle-mesh-cli_1.deb').write_bytes(b'evil')
        seen = []
        real = f.run
        def run(argv, stream=False, timeout=None):
            if argv[0] == 'bash' and Path(argv[-1]).name in ('debs', 'release.json'):
                root = Path(argv[-1]).parent if Path(argv[-1]).name == 'release.json' else Path(argv[-1]).parent
                seen.append({n: rr.sha256_file(root / n) for n in ('release-run.json',)} | {'path': str(argv[-1])})
            return real(argv, stream, timeout)
        f.run = run
        assert rr.main(['run', str(d), '--yes'], f) == 0, f.said
        assert all(str(f.runs_dir) in s['path'] for s in seen) and len(seen) == 2          # every publish read the copy, not the original
        assert (d / 'compose.yml').read_text() != 'services: {}\n'                       # the swap really happened on the original
    with tmp() as t:        # direct: swap between load_packet and the copy -> refused by the hash taken WHILE copying
        d = make_packet(t); pk = rr.load_packet(d); (d / 'compose.yml').write_text('swapped'); f = Fake(t)
        try: rr.copy_packet(f, pk, f.root / 'dest'); assert False
        except rr.Refused as r: assert 'checksum mismatch' in r.why and not (f.root / 'dest').exists()
    with tmp() as t:        # a good copy verifies, has the same run id, and later edits of the original do not touch it
        d = make_packet(t); pk = rr.load_packet(d); f = Fake(t); dest = f.root / 'dest'
        rr.copy_packet(f, pk, dest); (d / 'compose.yml').write_text('swapped')
        assert rr.load_packet(dest).run_id == pk.run_id and oct(dest.stat().st_mode & 0o777) == '0o700'
        assert oct((dest / 'release.json').stat().st_mode & 0o777) == '0o600'
        assert sorted(p.name for p in dest.iterdir()) == ['SHA256SUMS', 'compose.yml', 'release-run.json', 'release.json']

@test
def packet_copy_is_kept_on_failure_removed_on_success_and_refused_without_space():
    with tmp() as t:
        d = make_packet(t); f = Fake(t); f.install_rc = 1; pk = rr.load_packet(d)
        assert rr.main(['run', str(d), '--yes'], f) == 1 and (f.runs_dir / pk.run_id / 'packet').is_dir()
        f2 = Fake(t); assert rr.main(['run', str(d), '--yes'], f2) == 0 and not (f.runs_dir / pk.run_id / 'packet').exists()
    with tmp() as t:
        d = make_packet(t); f = Fake(t); f.free = 1024; pk = rr.load_packet(d)
        assert rr.main(['run', str(d), '--yes'], f) == 1 and 'not enough free space' in refused(f)[0] and 'publish' not in f.events
        assert not (f.runs_dir / pk.run_id / 'packet').exists()
    with tmp() as t:        # an unusable (stale) copy is replaced from the original
        d = make_packet(t); f = Fake(t); pk = rr.load_packet(d); copy = f.runs_dir / pk.run_id / 'packet'
        copy.mkdir(parents=True); (copy / 'junk').write_text('x'); f.install_rc = 1
        assert rr.main(['run', str(d), '--yes'], f) == 1 and sorted(p.name for p in copy.iterdir()) == ['SHA256SUMS', 'compose.yml', 'release-run.json', 'release.json']

# ---- module-add
MOD_STEP = [{'verb': 'module-add', 'instance': 'polari-x', 'module': 'mod-a'}]
def module_world(t, **kw):
    d = make_packet(t, devices={'core1': MOD_STEP}, publish=False); f = Fake(t); f.allow_module = True
    f.files[str(f.polari_dir / 'polari-x' / 'deployment.json')] = kw.get('record', {'name': 'polari-x', 'modules': 'islemesh'})
    f.texts[str(f.polari_dir / 'polari-x' / 'docker-compose.yml')] = 'POLARI_MODULES=islemesh\n'
    return d, f

@test
def module_add_runs_the_real_spelling_and_checks_the_record_compose_and_backend():
    with tmp() as t:
        d, f = module_world(t)
        assert rr.main(['run', str(d), '--yes', '--no-wait'], f) == 0, f.said
        assert ['python3', str(f.scripts / 'operations.py'), 'run', 'polari', 'module', 'add', 'mod-a', '--to', 'polari-x'] in f.argv
        assert f.files[str(f.polari_dir / 'polari-x' / 'deployment.json')]['modules'] == 'islemesh,mod-a'
        assert any(u.endswith('/api/modules/status') for u, _ in f.http_log)
    with tmp() as t:        # the backend never becomes ready
        d, f = module_world(t); f.modules_ok_after = 10 ** 6
        assert rr.main(['run', str(d), '--yes', '--no-wait'], f) == 1 and 'modules ready' in refused(f)[0]

@test
def module_add_refuses_when_nothing_would_survive_an_update():
    with tmp() as t:        # no durable record key at all: refused BEFORE anything changes
        d, f = module_world(t, record={'name': 'polari-x'})
        assert rr.main(['run', str(d), '--yes', '--no-wait'], f) == 1 and 'would not survive the next update' in refused(f)[0] and 'module-add' not in f.events
    with tmp() as t:        # the add ran but deployment.json did not learn it (CLI without the durable record change)
        d, f = module_world(t); f.module_writes = False
        assert rr.main(['run', str(d), '--yes', '--no-wait'], f) == 1 and 'not in deployment.json' in refused(f)[0] and 'e' * 32 in refused(f)[0]
    with tmp() as t:        # recorded, but the compose POLARI_MODULES lacks it
        d, f = module_world(t)
        real = f.run
        def run(argv, stream=False, timeout=None):
            out = real(argv, stream, timeout)
            if 'module' in argv: f.texts[str(f.polari_dir / 'polari-x' / 'docker-compose.yml')] = 'POLARI_MODULES=islemesh\n'
            return out
        f.run = run
        assert rr.main(['run', str(d), '--yes', '--no-wait'], f) == 1 and 'POLARI_MODULES of polari-x does not list mod-a' in refused(f)[0]
    with tmp() as t:        # an older installed CLI refuses by the journal allowlist
        d, f = module_world(t); f.allow_module = False
        assert rr.main(['run', str(d), '--yes', '--no-wait'], f) == 1 and 'no journaled' in refused(f)[0] and 'module-add' not in f.events

# ---- hotfix
HOTFIX_STEP = [{'verb': 'hotfix', 'name': 'storage-units'}]
@test
def hotfix_step_shape_is_checked_but_an_unknown_name_is_not_a_packet_error():
    with tmp() as t:
        d = make_packet(t, devices={'core1': [{'verb': 'hotfix', 'name': 'some-future-fix'}]}, publish=False)
        assert rr.main(['check', str(d)], Fake(t)) == 0
    for bad in ({'verb': 'hotfix'}, {'verb': 'hotfix', 'name': 'Bad Name'}, {'verb': 'hotfix', 'name': '../x'}, {'verb': 'hotfix', 'name': 'a', 'extra': 1},
                {'verb': 'hotfix', 'name': ''}, {'verb': 'hotfix', 'name': 'x' * 65}):
        with tmp() as t:
            d = make_packet(t, devices={'core1': [bad]}, publish=False)
            try: rr.load_packet(d); raise AssertionError('accepted %r' % bad)
            except rr.PacketError: pass

@test
def hotfix_step_runs_the_journaled_apply_and_failures_are_clean():
    with tmp() as t:
        d = make_packet(t, devices={'core1': HOTFIX_STEP}, publish=False); f = Fake(t); f.allow_module = True
        assert rr.main(['run', str(d), '--yes', '--no-wait'], f) == 0, f.said
        assert f.hotfix_argv == ['hotfix', 'apply', 'storage-units', '--yes'] and f.events.count('hotfix') == 1
        pk = rr.load_packet(d); step = json.loads((f.runs_dir / pk.run_id / 'receipt.json').read_text())['steps'][0]
        assert step['state'] == 'done' and step['operationId'] == 'd' * 32 and 'storage-units' in step['detail'], step
        f2 = Fake(t); f2.allow_module = True      # a rerun skips the finished step
        assert rr.main(['run', str(d), '--yes', '--no-wait'], f2) == 0 and 'hotfix' not in f2.events
    with tmp() as t:        # the installed CLI of the device does not know the name (hotfix.py exits 2)
        d = make_packet(t, devices={'core1': HOTFIX_STEP}, publish=False); f = Fake(t); f.allow_module = True; f.hotfix_rc = 2
        assert rr.main(['run', str(d), '--yes', '--no-wait'], f) == 1 and 'does not know hotfix storage-units' in refused(f)[0]
    with tmp() as t:        # the fix itself failed
        d = make_packet(t, devices={'core1': HOTFIX_STEP}, publish=False); f = Fake(t); f.allow_module = True; f.hotfix_rc = 1
        assert rr.main(['run', str(d), '--yes', '--no-wait'], f) == 1 and 'failed (exit 1)' in refused(f)[0] and 'd' * 32 in refused(f)[0]
    with tmp() as t:        # an older CLI without the journaled verb
        d = make_packet(t, devices={'core1': HOTFIX_STEP}, publish=False); f = Fake(t)
        assert rr.main(['run', str(d), '--yes', '--no-wait'], f) == 1 and 'no journaled' in refused(f)[0] and 'hotfix' not in f.events

BACKUP_STEP = {'verb': 'backup'}

@test
def backup_step_takes_a_device_backup_first_and_a_failed_one_stops_the_run():
    steps = [BACKUP_STEP] + HOTFIX_STEP
    did = lambda fake: [e for e in fake.events if e in ('backup', 'hotfix')]
    with tmp() as t:
        d = make_packet(t, devices={'core1': steps}, publish=False); f = Fake(t); f.allow_module = True
        assert rr.main(['run', str(d), '--yes', '--no-wait'], f) == 0, f.said
        assert f.backup_argv == ['backup', 'create', '--yes'] and did(f) == ['backup', 'hotfix'], f.events
        pk = rr.load_packet(d); got = json.loads((f.runs_dir / pk.run_id / 'receipt.json').read_text())['steps']
        assert got[0]['state'] == 'done' and got[0]['operationId'] == 'b' * 32 and 'read back' in got[0]['detail'], got[0]
        assert got[0]['key'] == 'step:0:backup' and got[1]['verb'] == 'hotfix' and got[1]['state'] == 'done', got
        assert rr.step_name(BACKUP_STEP) == 'backup'
        assert rr.describe_step(BACKUP_STEP).startswith('take a device-backup of this device')
        f2 = Fake(t); f2.allow_module = True      # a rerun takes no second device-backup
        assert rr.main(['run', str(d), '--yes', '--no-wait'], f2) == 0 and did(f2) == []
    with tmp() as t:        # not complete: nothing after it is started
        d = make_packet(t, devices={'core1': steps}, publish=False); f = Fake(t); f.allow_module = True; f.backup_rc = 1
        assert rr.main(['run', str(d), '--yes', '--no-wait'], f) == 1 and did(f) == ['backup'], f.events
        assert 'the device-backup is not complete (exit 1)' in refused(f)[0] and 'b' * 32 in refused(f)[0]
    with tmp() as t:        # an older installed CLI without the journaled verb
        d = make_packet(t, devices={'core1': steps}, publish=False); f = Fake(t)
        assert rr.main(['run', str(d), '--yes', '--no-wait'], f) == 1 and 'no journaled "backup create"' in refused(f)[0] and did(f) == []
    for bad in ({'verb': 'backup', 'slot': 'long-term'}, {'verb': 'backup', 'name': 'x'}, {'verb': 'backup', 'to': '/somewhere'}):
        with tmp() as t:    # the step takes no parameter: where it lives and what it holds is the device's own
            d = make_packet(t, devices={'core1': [bad]}, publish=False)
            try: rr.load_packet(d); raise AssertionError(bad)
            except rr.PacketError: pass

@test
def compose_modules_reads_both_compose_formats():
    assert rr.compose_modules(json.dumps({'services': {'backend': {'environment': {'POLARI_MODULES': 'a,b'}}}})) == 'a,b'
    assert rr.compose_modules(json.dumps({'services': {'backend': {'environment': ['X=1', 'POLARI_MODULES=c,d']}}})) == 'c,d'
    assert rr.compose_modules('services:\n  backend:\n    environment:\n      - POLARI_MODULES=e,f\n') == 'e,f'
    assert rr.compose_modules('nothing') == '' and rr.csv_has('a,b', 'a') and not rr.csv_has('ab,c', 'a')

@test
def nothing_is_imported_lazily_after_a_step_has_run():
    import re as _re
    src = Path(rr.__file__).read_text().splitlines()
    inner = [(i, l.strip()) for i, l in enumerate(src) if _re.match(r'\s+(import|from \S+ import) ', l)]
    allowed = {'import names', 'import operations', 'import names, operations   # noqa: F401'}
    # the only function-level imports are in Deps.device / journal_allows / preload, all reached before the first step;
    # release_intake and release_offer are imported where a verb starts (setting, a member's run, intake, offer), never
    # after a step, and release_run only by the __main__ block; release_build is the verb `build`, which runs no step at all
    assert all(l.split('#')[0].strip() in {'import names', 'import operations', 'import names, operations', 'import release_intake',
                                           'import release_offer', 'import release_run', 'import release_build'} for _, l in inner), inner
    for i, l in inner:
        if l.startswith(('import release_intake', 'import release_offer')):
            before = '\n'.join(src[max(0, i - 12):i])
            assert any(mark in before for mark in ('def cmd_setting', 'def cmd_run', "if cmd == 'intake'", "if cmd == 'offer'")), (i, l)
    assert 'def preload' in '\n'.join(src)

@test
def a_stale_notice_is_cured_by_a_rerun_that_publishes_the_packages_again():
    with tmp() as t:        # only apt-repo publish rebuilds the notice, so the rerun must not skip it
        d = full_packet(t); f = Fake(t); f.notice_stale = True
        f.notice = {'id': 'c' * 64, 'publishedAt': datetime.fromtimestamp(f.clock - 3600, timezone.utc).isoformat(), 'packages': [{'file': 'x.deb'}]}
        assert rr.main(['run', str(d), '--yes'], f) == 1 and 'publishes the packages again' in refused(f)[0]
        f2 = Fake(t); f2.update_rcs = [0]; f2.runtime_published = f.runtime_published
        assert rr.main(['run', str(d), '--yes'], f2) == 0, f2.said
        assert f2.events[0] == 'publish-debs' and 'publish-runtime' not in f2.events and 'notice' in f2.events, f2.events

@test
def an_update_apply_that_died_mid_step_is_applied_again_not_trusted():
    steps = [{'verb': 'update-apply', 'releaseId': RELID}]
    with tmp() as t:        # desired-release.json is written BEFORE the reconcile, so after a killed attempt it proves nothing
        d = make_packet(t, devices={'core1': steps}, publish=False); f = Fake(t); f.files[rr.DESIRED_RELEASE] = {'id': RELID}
        pk = rr.load_packet(d)
        rr.Receipt(f, pk.run_id, 'core1', pk.record).begin('step:0:update-apply', 'update-apply', 'died here')
        f.update_rcs = [0]
        assert rr.main(['run', str(d), '--yes'], f) == 0, f.said
        assert 'update' in f.events
        step = json.loads((f.runs_dir / pk.run_id / 'receipt.json').read_text())['steps'][0]
        assert step['attempts'] == 2 and 'applied' in step['detail'], step

@test
def the_wrong_role_is_refused_before_anyone_is_asked_for_a_password():
    with tmp() as t:        # user ruling 2026-11-17: core-only is accepted; a member must be told so, without sudo
        d = make_packet(t)
        for verb, argv in (('run', ['run', str(d)]), ('approval-key', ['approval-key'])):
            for root in (False, True):
                f = Fake(t, 'remote', 'member1'); f.root_user = root
                assert rr.main(argv, f) == 1 and len(f.said) == 1 and not f.events, f.said
                line = f.said[0]
                assert line.startswith('REFUSED') and 'works on the core only' in line and 'this device is member1 (a member)' in line and 'sudo' not in line, line
        for root in (False, True):
            f = Fake(t, 'core', 'core1'); f.root_user = root; f.updates_body = {}
            assert rr.main(['follow'], f) == 1 and len(f.said) == 1 and not f.events, f.said
            assert 'works on a member only' in f.said[0] and 'this device is core1 (the core)' in f.said[0] and 'sudo isle release run' in f.said[0]
            assert rr.main(['follow', '--auto'], f) == 0 and len(f.said) == 1          # the timer stays silent on a core
        f = Fake(t, '', 'loner'); f.root_user = False
        assert rr.main(['run', str(d)], f) == 1 and "not set up as a core or a member (agent mode is '')" in f.said[0]
        f = Fake(t, 'core', 'core1'); f.root_user = False       # the right role without root still gets the sudo line
        assert rr.main(['run', str(d)], f) == 1 and f.said == ['This changes the device. Type: sudo isle release run %s' % d]
        f = Fake(t, 'remote', 'member1'); f.root_user = False; f.updates_body = {}
        assert rr.main(['follow'], f) == 1 and f.said == ['This changes the device. Type: sudo isle release follow']

@test
def a_star_leaves_out_members_the_core_has_not_heard_from_and_says_so_before_the_prompt():
    old = lambda f, days: datetime.fromtimestamp(f.clock - days * 86400, timezone.utc).isoformat()
    with tmp() as t:
        d = make_packet(t, devices={'*': [{'verb': 'update-apply', 'releaseId': RELID}]}, publish=False); f = Fake(t)
        f.device_list += [{'name': 'old-host', 'agent_mode': 'remote', 'is_mock': False, 'last_seen': old(f, 11)},
                          {'name': 'never-seen', 'agent_mode': 'remote', 'is_mock': False, 'last_seen': None}]
        f.answer = False
        assert rr.main(['run', str(d)], f) == 1 and f.events == ['confirm'], f.said       # shown BEFORE the confirmation
        text = '\n'.join(f.said)
        assert '"every device" is, right now: member1, member2' in text, text
        assert 'left out, not heard from in 24 h: old-host (last seen %s)' % old(f, 11) in text and 'never-seen (last seen never)' in text, text
        f.answer, f.said = True, []
        assert rr.main(['run', str(d), '--yes'], f) == 0, f.said
        assert json.loads(f.notice['run']['approval']['payload'])['devices'] == ['core1', 'member1', 'member2']
        assert not any(l.startswith('  old-host') or l.startswith('  never-seen') for l in f.said), f.said   # not waited on
    with tmp() as t:        # a name written out is included whatever its last report: the reviewer chose it
        d = make_packet(t, devices={'*': [{'verb': 'update-apply', 'releaseId': RELID}], 'old-host': []}, publish=False); f = Fake(t)
        f.device_list.append({'name': 'old-host', 'agent_mode': 'remote', 'is_mock': False, 'last_seen': old(f, 11)})
        f.member_state['old-host'] = 'done'
        assert rr.main(['run', str(d), '--yes', '--no-wait'], f) == 0, f.said
        assert 'old-host' in json.loads(f.notice['run']['approval']['payload'])['devices']
    with tmp() as t:        # no "*": the device list is not read for the summary
        d = make_packet(t, devices={'member1': [{'verb': 'update-apply', 'releaseId': RELID}]}, publish=False); f = Fake(t); f.answer = False
        assert rr.main(['run', str(d)], f) == 1 and not any('every device' in l for l in f.said) and not f.http_log, f.http_log

# ---- the start setting (the user, 2026-11-16/17: core-only or any device, behind a setting; the warning follows it)
@test
def the_start_setting_defaults_to_core_only_and_is_changed_on_the_core_alone():
    with tmp() as t:
        f = Fake(t)
        assert rr.main(['setting'], f) == 0 and f.said[0] == 'Start setting of this isle: core-only (the default, never set)', f.said
        text = '\n'.join(f.said)
        assert 'any-device' in text and 'root on any paired member can release to the whole isle' in text and 'sudo isle release setting' in text, text
        assert not f.setting_file.exists() and not f.events                       # showing it writes nothing
        f = Fake(t); f.root_user = False                                           # the right device without root: the sudo line
        assert rr.main(['setting', 'core-only'], f) == 1 and f.said == ['This changes the isle. Type: sudo isle release setting core-only']
        assert not f.setting_file.exists() and not f.events
        f = Fake(t)
        assert rr.main(['setting', 'core-only'], f) == 0, f.said
        record = json.loads(f.setting_file.read_text())
        assert record == {'start': 'core-only', 'setAt': f.now(), 'setOn': 'core1'}, record
        assert f.events == ['notice-setting'] and f.notice['startSetting'] == record and f.notice['command'] == 'keep me'
        assert 'Published to the members' in f.said[-1], f.said
        f.said = []
        assert rr.main(['setting'], f) == 0 and f.said[0] == 'Start setting of this isle: core-only (set %s on core1)' % record['setAt'], f.said

@test
def any_device_cannot_be_chosen_while_the_core_cannot_read_what_it_needs_to_take_an_offer():
    with tmp() as t:
        f = Fake(t); f.root_user = False                                           # what the core reads for it is root-only: sudo first
        assert rr.main(['setting', 'any-device'], f) == 1 and f.said == ['This changes the isle. Type: sudo isle release setting any-device']
        assert not f.events and not f.setting_file.exists()
        f = Fake(t)
        assert rr.main(['setting', 'any-device'], f) == 1 and len(f.said) == 1 and not f.events, f.said
        line = f.said[0]
        assert line.startswith('REFUSED: setting on core1: any-device cannot be chosen: the core cannot take a packet from a member: '
                               'the member list of the certificate authority cannot be read on this core'), line
        assert 'stays core-only; nothing was changed' in line and not f.setting_file.exists(), line
        f = Fake(t)                                                                # the root-owned record is what the core goes by
        f.setting_file.parent.mkdir(parents=True); f.setting_file.write_text(json.dumps({'start': 'any-device'}))
        assert rr.core_start_setting(f)[0] == 'any-device'
        f.setting_file.write_text(json.dumps({'start': 'everywhere'}))             # a value nobody knows = the default
        assert rr.core_start_setting(f) == ('core-only', None)
        assert rr.main(['setting'], f) == 0 and f.said[0].startswith('Start setting of this isle: core-only'), f.said
        f = Fake(t)
        assert rr.main(['setting', 'everywhere'], f) == 2 and 'the start setting is one of: core-only, any-device' in f.said[0], f.said
        assert rr.main(['setting', 'core-only', 'extra'], f) == 2

@test
def the_setting_is_recorded_on_the_core_even_when_the_notice_cannot_be_written():
    with tmp() as t:
        f = Fake(t); f.notice_setting_fails = True
        assert rr.main(['setting', 'core-only'], f) == 0, f.said
        assert json.loads(f.setting_file.read_text())['start'] == 'core-only' and 'startSetting' not in f.notice
        assert 'the release notice could not be updated' in f.said[-1] and 'after the next publication' in f.said[-1], f.said

@test
def a_member_reads_the_start_setting_from_the_core_and_its_refusal_follows_it():
    with tmp() as t:
        d = make_packet(t)
        def member(**kw):
            f = Fake(t, 'remote', 'member1'); f.root_user = False
            for k, v in kw.items(): setattr(f, k, v)
            return f
        f = member()                                                               # a core that publishes no setting = the default
        assert rr.main(['run', str(d)], f) == 1 and len(f.said) == 1 and not f.events, f.said
        assert f.said[0] == ('REFUSED: run on member1: the start setting of this isle is core-only, so isle release run works on the '
                             'core only; this device is member1 (a member). Run it on the core device of the isle.'), f.said[0]
        f.said = []
        assert rr.main(['setting'], f) == 0 and f.said[0] == 'Start setting of this isle: core-only (the default; the core publishes no setting)', f.said
        f = member(); f.notice['startSetting'] = {'start': 'core-only', 'setAt': 'T0', 'setOn': 'core1'}
        assert rr.main(['run', str(d)], f) == 1 and 'the start setting of this isle is core-only, so isle release run works on the core only' in f.said[0]
        f.said = []
        assert rr.main(['setting'], f) == 0 and f.said[0] == 'Start setting of this isle, as the core publishes it: core-only', f.said
        f = member(updates_down=True)                                              # the core cannot be asked: still refused, and it says why
        assert rr.main(['run', str(d)], f) == 1 and len(f.said) == 1, f.said
        assert 'the start setting of this isle is not known here (the core did not answer), and until it is, isle release run works on the core only' in f.said[0], f.said[0]
        assert 'sudo' not in f.said[0]
        f.said = []
        assert rr.main(['setting'], f) == 0 and f.said[0] == 'Start setting of this isle: not known on member1 (the core did not answer). The default is core-only.', f.said
        f = member(); f.notice['startSetting'] = {'start': 'any-device'}          # any-device published: the member offers, which needs root
        assert rr.main(['run', str(d)], f) == 1 and len(f.said) == 1 and not f.events, f.said
        assert f.said[0] == 'This offers the packet to the core, which runs it across the isle. Type: sudo isle release run %s' % d, f.said[0]
        f = member(root_user=True); f.notice['startSetting'] = {'start': 'any-device'}     # ...and a pairing the core can name
        assert rr.main(['run', str(d)], f) == 1 and len(f.said) == 1 and not f.events, f.said
        assert f.said[0].startswith('REFUSED: run on member1: the pairing of this device with the core cannot be read'), f.said[0]
        for root in (False, True):                                                 # a member cannot change it, and is not asked for a password
            f = member(); f.root_user = root
            assert rr.main(['setting', 'core-only'], f) == 1 and len(f.said) == 1 and not f.events, f.said
            assert 'isle release setting works on the core only; this device is member1 (a member)' in f.said[0] and 'sudo' not in f.said[0], f.said[0]
            assert not f.setting_file.exists()
        f = Fake(t, '', 'loner')
        assert rr.main(['setting'], f) == 1 and 'not set up as a core or a member' in f.said[0], f.said

@test
def the_notice_writer_replaces_the_start_setting_and_keeps_the_run():
    import subprocess
    with tmp() as t:
        env = dict(os.environ, DATABASE_PATH=str(Path(t) / 'db.sqlite')); notice = Path(t) / 'isle-release.json'
        write = lambda key, body: subprocess.run([sys.executable, '-c', rr.NOTICE_WRITE] + key, input=body, text=True, capture_output=True, env=env)
        assert write(['startSetting'], '{"start": "core-only"}').returncode != 0 and not notice.exists()
        notice.write_text(json.dumps({'id': 'i', 'packages': [1], 'run': {'runId': 'x'}}))
        p = write(['startSetting'], '{"start": "core-only"}'); assert p.returncode == 0, p.stderr
        assert json.loads(notice.read_text()) == {'id': 'i', 'packages': [1], 'run': {'runId': 'x'}, 'startSetting': {'start': 'core-only'}}
        p = write(['run'], '{"runId": "y"}'); assert p.returncode == 0, p.stderr
        assert json.loads(notice.read_text())['startSetting'] == {'start': 'core-only'} and json.loads(notice.read_text())['run'] == {'runId': 'y'}
        before = notice.read_text()
        assert write(['packages'], '[]').returncode != 0 and notice.read_text() == before      # only the named keys may be replaced
        p = write(['intake'], '{"answers": {}}'); assert p.returncode == 0, p.stderr           # the core's answers to offered packets
        assert json.loads(notice.read_text())['intake'] == {'answers': {}} and json.loads(notice.read_text())['run'] == {'runId': 'y'}

failed = [n for n, e in RESULTS if e]
print('\n%d passed, %d failed' % (len(RESULTS) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
