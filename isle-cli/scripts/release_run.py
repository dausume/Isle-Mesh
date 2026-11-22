#!/usr/bin/env python3
"""One-command release, slices 1-2: check / run (core) / follow [--auto] (member) / status / approval-key / setting.

Slice 2: the core signs an approval (payload + signature + certificate) with a leaf issued DIRECTLY by the
isle root; every member verifies it against the root certificate alone before anything executes (manual and
automatic follow alike). Members report status back through the capability report (report_fact below); that
status is advisory only. Importing this module does no work (capabilities.py imports report_fact).

Every side effect (commands, HTTP, the core's release notice, clock, device
identity, state directory) goes through one Deps object so tests can fake it.
Steps are existing journaled operations; nothing here talks to a member
directly and nothing here skips TLS verification.
"""
import base64, fcntl, hashlib, json, os, posixpath, re, secrets, shutil, stat, subprocess, sys, tempfile, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from operations import NAME, atomic   # the app-name pattern and the journal's atomic writer
from supported_os import MARKER as SET_MARKER, load as supported_systems, system_of

SCRIPTS = Path(__file__).resolve().parent
API = 'https://api.polari.isle'
VERSION = re.compile(r'[0-9A-Za-z.+:~_-]{1,100}\Z')
HEX64 = re.compile(r'[0-9a-f]{64}\Z')
IMAGE_ID = re.compile(r'sha256:[0-9a-f]{64}\Z')
RUN_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}\Z')
HOST = re.compile(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+isle\Z')
DESIRED_RELEASE = '/var/lib/isle-mesh/desired-release.json'
APPS_DIR = '/etc/isle-mesh/apps'
ARTIFACTS = '/var/lib/isle-mesh/artifacts'   # catalog_artifacts.CACHE: <manifest sha>/{manifest,admitted}.json
MODE_FILE = '/etc/isle-mesh/agent/agent.mode'
PUBLISHED_RUNTIME_DIR = '/etc/isle-mesh/apt/repo/runtime'     # catalog_publish.REPO / 'runtime'
NOTICE_READ = ("import os,sys; from pathlib import Path; "
               "p=Path(os.environ['DATABASE_PATH']).parent/'isle-release.json'; "
               "sys.stdout.write(p.read_text() if p.exists() else '{}')")
# Read the existing notice, replace ONLY one key (`run`, `startSetting`, `identityProvider`, or `intake`), write atomically (same way release_notice.py writes).
# It refuses when no runtime release notice exists yet: the backend serves this file to every member and
# needs its `packages`; a file holding only `run` would turn /api/islemesh/updates into a 503 for the isle.
NOTICE_WRITE = """import json, os, sys, tempfile
from pathlib import Path
p = Path(os.environ['DATABASE_PATH']).parent / 'isle-release.json'
if not p.exists(): sys.exit('no runtime release notice exists on the core yet (publish a runtime release first)')
d = json.loads(p.read_text())
if not isinstance(d, dict) or 'id' not in d or 'packages' not in d: sys.exit('the release notice on the core is not a release')
key = sys.argv[1] if len(sys.argv) > 1 else 'run'
if key not in ('run', 'startSetting', 'identityProvider', 'intake'): sys.exit('not a key this writer may replace')
d[key] = json.load(sys.stdin)
f = tempfile.NamedTemporaryFile(mode='w', dir=p.parent, delete=False)
json.dump(d, f); f.flush(); os.fsync(f.fileno()); f.close(); os.replace(f.name, p)
"""


PUBLISHED = 'published'   # update-apply releaseId meaning: the release id this run's publication produced

# The start setting: where a release run may be started. The core keeps it (START_SETTING_FILE, root-owned) and
# enforces it; members read the core's copy in the release notice (key `startSetting`) for their wording only.
#   core-only    the default: `isle release run` works on the core only.
#   any-device   a member offers its packet (release_offer.py); the core fetches it, checks the member and the packet
#                again, and runs it (release_intake.py). The backend takes no part.
START_SETTING_FILE = '/etc/isle-mesh/release/start-setting.json'   # release_notice.py reads the same file
START_SETTINGS = ('core-only', 'any-device')
START_DEFAULT = 'core-only'
START_WORDS = {'core-only': 'a release run can be started on the core only',
               'any-device': 'a release run can be started on any device of the isle; the core checks the packet again and runs it'}
ANY_DEVICE_MISSING = 'the core cannot take a packet from a member'

class PacketError(ValueError):
    """The packet is not acceptable; nothing was changed."""


class Refused(Exception):
    def __init__(self, step, why, operation=None, state='refused'):
        super().__init__(why)
        self.step, self.why, self.operation, self.state = step, why, operation, state


# ---------------------------------------------------------------- side effects

class Deps:
    """Real side effects. Tests substitute a fake with the same methods."""
    wait_limit = 2400        # the apt.isle replica pulls on a 10-minute timer, then has to transfer; a rerun resumes the wait
    poll_interval = 15
    modules_limit = 300
    runs_dir = Path('/var/lib/isle-mesh/release-runs')
    ca_dir = Path('/etc/isle-mesh/ca')
    approval_dir = Path('/etc/isle-mesh/ca/release-approval')
    wait_minutes = 30
    polari_dir = Path('/etc/isle-mesh/polari')
    setting_file = Path(START_SETTING_FILE)
    copy_reserve = 256 * 1024 * 1024
    scripts = SCRIPTS

    def say(self, line=''): print(line, flush=True)
    def time(self): return time.time()
    def sleep(self, seconds): time.sleep(seconds)
    def now(self): return datetime.now(timezone.utc).isoformat()
    def is_root(self): return os.geteuid() == 0

    def preload(self):
        """Import every sibling module this file needs BEFORE the first step: `update apply` replaces the package
        files under a running follower, and a module first imported afterwards would be a mix of old and new code."""
        import names, operations   # noqa: F401

    def free_bytes(self, path): return shutil.disk_usage(path).free

    def published_runtime_size(self, sha):
        """The size of the runtime image file this core publishes under that sha256, or None when it has none."""
        try: return os.stat(Path(PUBLISHED_RUNTIME_DIR) / (sha + '.tar')).st_size
        except OSError: return None

    def read_text(self, path):
        try: return Path(path).read_text()
        except OSError: return None

    def mode(self):
        try: return Path(MODE_FILE).read_text().strip()
        except OSError: return ''

    def device(self):
        import names
        return names.current_device()

    def confirm(self, prompt):
        if not sys.stdin.isatty(): return False
        return input(prompt + ' [y/N] ').strip().lower() == 'y'

    def read_json(self, path):
        try: return json.loads(Path(path).read_text())
        except (OSError, ValueError): return None

    def installed_instances(self, app):
        """{instance name: its release.json} for every local instance of this catalog app."""
        found = {}
        for path in sorted(Path(APPS_DIR).glob('*/release.json')):
            record = self.read_json(path)
            if isinstance(record, dict) and record.get('name') == app: found[path.parent.name] = record
        return found

    def run(self, argv, stream=False, timeout=None):
        """argv list only, never a shell string. Returns (returncode, stdout)."""
        if not stream:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            return p.returncode, p.stdout + (p.stderr if p.returncode else '')
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        lines = []
        for line in proc.stdout:
            print(line, end='', flush=True)
            lines.append(line)
        return proc.wait(), ''.join(lines)

    def http(self, url, head=False):
        """Verified TLS only. Returns (status, body bytes); status 0 means no answer (body = reason)."""
        argv = ['curl', '--http1.1', '--fail', '--silent', '--show-error', '--max-time', '20',
                '--write-out', '\n%{http_code}']
        if head: argv.append('--head')
        argv.append(url)
        p = subprocess.run(argv, capture_output=True)
        body, _, code = p.stdout.rpartition(b'\n')
        status = int(code) if code.isdigit() else 0
        if status == 0: return 0, p.stderr.strip() or b'no answer'
        return status, b'' if head else body

    def read_notice(self):
        code, out = self.run(['docker', 'exec', 'prf-isle-backend', 'python3', '-c', NOTICE_READ], timeout=20)
        if code: raise RuntimeError('cannot read the release notice: ' + out.strip())
        return json.loads(out)

    def write_notice_run(self, record):
        p = subprocess.run(['docker', 'exec', '-i', 'prf-isle-backend', 'python3', '-c', NOTICE_WRITE],
                           input=json.dumps(record), text=True, capture_output=True, timeout=20)
        if p.returncode: raise RuntimeError('cannot write the release notice: ' + p.stderr.strip())

    def write_notice_setting(self, record):
        p = subprocess.run(['docker', 'exec', '-i', 'prf-isle-backend', 'python3', '-c', NOTICE_WRITE, 'startSetting'],
                           input=json.dumps(record), text=True, capture_output=True, timeout=20)
        if p.returncode: raise RuntimeError('cannot write the release notice: ' + p.stderr.strip())

    def write_notice_intake(self, record):
        """What the core answered to the packets its members offer (release_intake.py); members read it from the notice."""
        p = subprocess.run(['docker', 'exec', '-i', 'prf-isle-backend', 'python3', '-c', NOTICE_WRITE, 'intake'],
                           input=json.dumps(record), text=True, capture_output=True, timeout=20)
        if p.returncode: raise RuntimeError('cannot write the release notice: ' + p.stderr.strip())

    def read_setting(self):
        try: return json.loads(Path(self.setting_file).read_text())
        except (OSError, ValueError): return None

    def write_setting(self, record):
        path = Path(self.setting_file)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        atomic(path, record)

    def try_lock(self, path):
        """Exclusive non-blocking flock on path; returns the open file (close it to release) or None when held."""
        Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        f = open(path, 'a+')
        try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close(); return None
        return f

    def journal_allows(self, args):
        import operations
        try: operations.request(list(args))
        except ValueError: return False
        return True


# ---------------------------------------------------------------- packet

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))

def sha256_bytes(raw): return hashlib.sha256(raw).hexdigest()

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()

def _no_duplicates(pairs):
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys): raise PacketError('duplicate key in release-run.json')
    return dict(pairs)

def _closed(obj, allowed, required, where):
    if not isinstance(obj, dict): raise PacketError(where + ' must be an object')
    extra = set(obj) - set(allowed)
    if extra: raise PacketError('%s has unknown key(s): %s' % (where, ', '.join(sorted(extra))))
    missing = set(required) - set(obj)
    if missing: raise PacketError('%s is missing: %s' % (where, ', '.join(sorted(missing))))

def _match(value, pattern, where):
    if not isinstance(value, str) or not pattern.fullmatch(value): raise PacketError('invalid ' + where)
    return value

def check_url(value, where):
    if not isinstance(value, str): raise PacketError('invalid ' + where)
    u = urlsplit(value)
    if u.scheme != 'https' or u.username or u.password or u.fragment or not u.hostname \
            or not HOST.fullmatch(u.hostname):
        raise PacketError('%s must be an https:// URL on a .isle host: %r' % (where, value))
    return value

def safe_relpath(rel, where):
    if not isinstance(rel, str) or not rel or '\0' in rel or '\\' in rel: raise PacketError('invalid path in ' + where)
    parts = Path(rel).parts
    if Path(rel).is_absolute() or '..' in parts or not parts: raise PacketError('path leaves the packet: %r' % rel)
    return rel

STEP_KEYS = {
    'update-apply': ({'verb', 'releaseId'}, {'verb', 'releaseId'}),
    'store-install': ({'verb', 'app', 'version', 'manifestSha256', 'expectImageDigest'},
                      {'verb', 'app', 'version', 'manifestSha256'}),
    'module-add': ({'verb', 'instance', 'module'}, {'verb', 'instance', 'module'}),
    'hotfix': ({'verb', 'name'}, {'verb', 'name'}),
    'backup': ({'verb'}, {'verb'}),
}

def validate_step(step, where):
    if not isinstance(step, dict) or step.get('verb') not in STEP_KEYS:
        raise PacketError('%s: unknown step verb %r' % (where, step.get('verb') if isinstance(step, dict) else step))
    allowed, required = STEP_KEYS[step['verb']]
    _closed(step, allowed, required, where)
    verb = step['verb']
    if verb == 'update-apply':
        if step['releaseId'] != PUBLISHED: _match(step['releaseId'], HEX64, where + ' releaseId')
    elif verb == 'store-install':
        _match(step['app'], NAME, where + ' app'); _match(step['version'], VERSION, where + ' version')
        _match(step['manifestSha256'], HEX64, where + ' manifestSha256')
        if 'expectImageDigest' in step: _match(step['expectImageDigest'], IMAGE_ID, where + ' expectImageDigest')
    elif verb == 'hotfix':
        # Only the SHAPE is checked here: whether the name is a registered fix is decided by the installed CLI of the
        # device that runs the step (an older CLI may not know it yet), so an unknown name is not a packet error.
        _match(step['name'], NAME, where + ' name')
    elif verb == 'backup':
        pass        # no parameters: what a device-backup holds and where it lives is decided on the device that takes it
    else:
        _match(step['instance'], NAME, where + ' instance'); _match(step['module'], NAME, where + ' module')

def validate_doc(doc):
    """Closed schema for release-run.json. Raises PacketError on the first problem."""
    _closed(doc, {'schemaVersion', 'name', 'publish', 'devices', 'verify'}, {'schemaVersion', 'name', 'devices'}, 'release-run.json')
    if doc['schemaVersion'] != 1 or isinstance(doc['schemaVersion'], bool): raise PacketError('unsupported schemaVersion')
    _match(doc['name'], RUN_NAME, 'name')
    publish = doc.get('publish', [])
    if not isinstance(publish, list): raise PacketError('publish must be a list')
    for i, item in enumerate(publish):
        where = 'publish[%d]' % i
        if not isinstance(item, dict) or item.get('kind') not in ('catalog', 'runtime', 'debs', 'dependencies'):
            raise PacketError('%s: unknown kind' % where)
        if item['kind'] == 'catalog':
            _closed(item, {'kind', 'release'}, {'kind', 'release'}, where)
            safe_relpath(item['release'], where)
        elif item['kind'] == 'runtime':
            _closed(item, {'kind', 'manifest'}, {'kind', 'manifest'}, where)
            safe_relpath(item['manifest'], where)
        else:
            _closed(item, {'kind', 'dir'}, {'kind', 'dir'}, where)
            safe_relpath(item['dir'], where)
    kinds = [i['kind'] for i in publish]
    if any(kinds.count(kind) > 1 for kind in ('runtime', 'debs', 'dependencies')):
        raise PacketError('publish may hold at most one runtime, one debs and one dependencies entry')
    if 'runtime' in kinds and 'debs' not in kinds:
        # apt-repo.sh is the only thing that rebuilds the release notice (and its id), and `apt-repo publish` refuses an empty --from.
        raise PacketError('a runtime publication needs the debs entry too (only apt-repo publish rebuilds the release notice that members read)')
    devices = doc['devices']
    if not isinstance(devices, dict) or not devices: raise PacketError('devices must name at least one device')
    for device, steps in devices.items():
        if device != '*' and not NAME.fullmatch(device): raise PacketError('invalid device name %r' % device)
        if not isinstance(steps, list): raise PacketError('steps for %s must be a list' % device)
        for i, step in enumerate(steps):
            validate_step(step, 'devices.%s[%d]' % (device, i))
            if step.get('releaseId') == PUBLISHED and not ({'runtime', 'debs'} & set(kinds)):
                raise PacketError('devices.%s[%d]: "releaseId": "published" needs a runtime or debs publication in this packet' % (device, i))
    verify = doc.get('verify', [])
    if not isinstance(verify, list): raise PacketError('verify must be a list')
    for i, check in enumerate(verify):
        where = 'verify[%d]' % i
        _closed(check, {'url', 'device', 'expectStatus', 'expectJsonContains'}, {'url'}, where)
        if 'device' in check and (not isinstance(check['device'], str) or not NAME.fullmatch(check['device']) or check['device'] not in devices):
            raise PacketError(where + ': device must be one of the devices this run names')
        check_url(check['url'], where + ' url')
        status = check.get('expectStatus', 200)
        if type(status) is not int or not 100 <= status <= 599: raise PacketError(where + ': invalid expectStatus')
        if 'expectJsonContains' in check and not isinstance(check['expectJsonContains'], dict):
            raise PacketError(where + ': expectJsonContains must be an object')

def read_sums(directory):
    path = directory / 'SHA256SUMS'
    if not path.is_file(): raise PacketError('SHA256SUMS is missing from the packet')
    return parse_sums(path.read_text())

def parse_sums(text):
    sums = {}
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip(): continue
        m = re.fullmatch(r'([0-9a-f]{64}) [ *](.+)', line)
        if not m: raise PacketError('SHA256SUMS line %d is not "<sha256>  <file>"' % n)
        rel = m.group(2)
        rel = rel[2:] if rel.startswith('./') else rel
        if rel in sums: raise PacketError('SHA256SUMS lists %s twice' % rel)
        sums[rel] = m.group(1)
    return sums

def hashed_file(directory, sums, rel, where):
    """A file the packet names: inside the packet, regular, listed in SHA256SUMS, matching. Returns its sha256."""
    safe_relpath(rel, where)
    root = Path(os.path.realpath(directory))
    full = Path(os.path.realpath(directory / rel))
    if root != full and root not in full.parents: raise PacketError('%s leaves the packet: %s' % (where, rel))
    if not full.is_file(): raise PacketError('%s names a file that is not there: %s' % (where, rel))
    if rel not in sums: raise PacketError('%s is not listed in SHA256SUMS: %s' % (where, rel))
    actual = sha256_file(full)
    if actual != sums[rel]: raise PacketError('checksum mismatch for %s' % rel)
    return actual

PUBLISHED_RUNTIME = 'https://apt.isle/runtime/'      # where the core serves a runtime image: <sha256>.tar

def runtime_archives(manifest_rel, manifest):
    """(packet-relative paths of every archive file a runtime manifest names, the rows it keeps as published).
    runtime_publish.py reads the archives from there.  A row the release does not change carries no archive: it names
    the file the core publishes already (url, sha256, size), and the core must have that file."""
    runtimes = manifest.get('runtimes') if isinstance(manifest, dict) else None
    if not isinstance(runtimes, dict) or set(runtimes) - {'core', 'remote'} or not runtimes:
        raise PacketError('%s must hold {"runtimes": {"core": [...], "remote": [...]}}' % manifest_rel)
    found, kept, base = [], [], posixpath.dirname(manifest_rel)
    for mode, rows in sorted(runtimes.items()):
        if not isinstance(rows, list): raise PacketError('%s: runtimes.%s must be a list' % (manifest_rel, mode))
        for item in rows:
            try: archive = item['image']['archive']; container = item['container']; source = archive.get('source')
            except (KeyError, TypeError, AttributeError): raise PacketError('%s: every runtime row needs container and image.archive' % manifest_rel)
            if source is None and isinstance(container, str):
                sha, size = archive.get('sha256'), archive.get('size')
                if not isinstance(sha, str) or not HEX64.fullmatch(sha) or type(size) is not int or size <= 0 \
                        or archive.get('url') != PUBLISHED_RUNTIME + sha + '.tar' or set(archive) != {'url', 'sha256', 'size'}:
                    raise PacketError('%s: the row of %s carries no archive, so it has to name the file the core publishes: '
                                      'url %s<sha256>.tar, sha256 and size, and nothing else' % (manifest_rel, container, PUBLISHED_RUNTIME))
                kept.append({'mode': mode, 'container': container, 'sha256': sha, 'size': size})
                continue
            if not isinstance(source, str) or not isinstance(container, str) or posixpath.isabs(source):
                raise PacketError('%s: archive sources must be relative to the manifest' % manifest_rel)
            rel = safe_relpath(posixpath.normpath(posixpath.join(base, source)), manifest_rel + ' archive')
            if rel not in found: found.append(rel)
    if not found:
        raise PacketError('%s names no runtime image the packet carries; a packet that changes no runtime image has no runtime publication' % manifest_rel)
    return found, kept

def folder_files(directory, rel_dir, where):
    """The names in a packet directory, in order; the directory is inside the packet and holds regular files only."""
    full = directory / rel_dir
    root = Path(os.path.realpath(directory))
    if not full.is_dir() or full.is_symlink() or (root != Path(os.path.realpath(full)) and root not in Path(os.path.realpath(full)).parents):
        raise PacketError('%s: not a directory inside the packet: %s' % (where, rel_dir))
    names = sorted(os.listdir(full))
    for n in names:
        if not stat.S_ISREG(os.lstat(full / n).st_mode): raise PacketError('%s: %s/%s is not a regular file' % (where, rel_dir, n))
    return names

def deb_files(directory, sums, rel_dir, where):
    """Names of the .deb files in a packet directory; every entry must be a regular .deb listed in SHA256SUMS and matching."""
    names = folder_files(directory, rel_dir, where)
    for n in names:
        if not n.endswith('.deb'): raise PacketError('%s: %s/%s is not a .deb (the debs directory holds only packages)' % (where, rel_dir, n))
        hashed_file(directory, sums, rel_dir.rstrip('/') + '/' + n, where)
    if not names: raise PacketError('%s: no .deb files in %s' % (where, rel_dir))
    return names

def dependency_set(directory, sums, rel_dir, where):
    """A dependency set a packet carries: the operating system packages of ONE system of the record
    (apt/supported-os.json), which the folder names with the file <system id>.txt, as an offline medium does.
    Every file of the folder has to be listed in SHA256SUMS and match: the core publishes from a copy that holds
    only listed files, and a set that lost its marker on the way would be filed under the core's own release.
    -> (system id, its release name, {package file: sha256}, one sha256 over every file of the folder)"""
    base, debs, marks, listing = rel_dir.rstrip('/'), {}, [], []
    for n in folder_files(directory, rel_dir, where):
        sha = hashed_file(directory, sums, base + '/' + n, where)
        listing.append('%s  %s\n' % (sha, n))
        if n.endswith('.deb'): debs[n] = sha
        elif SET_MARKER.match(n): marks.append(n[:-len('.txt')])
    if not debs: raise PacketError('%s: no .deb files in %s' % (where, rel_dir))
    if len(marks) != 1:
        raise PacketError('%s: %s has to say the one system it is built for, with one file <system id>.txt (it has: %s)'
                          % (where, rel_dir, ', '.join(m + '.txt' for m in marks) or 'none'))
    try: entry = system_of(supported_systems(), marks[0])
    except ValueError as exc: raise PacketError('%s: %s is built for a system this CLI does not know. %s' % (where, rel_dir, exc))
    return marks[0], entry['codename'], debs, sha256_bytes(''.join(listing).encode())

def release_sources(release):
    """Relative source files a catalog release.json asks the publisher to copy."""
    found = []
    if isinstance(release.get('compose'), dict) and 'source' in release['compose']: found.append(release['compose']['source'])
    for image in release.get('images', []):
        if isinstance(image, dict) and isinstance(image.get('archive'), dict) and 'source' in image['archive']:
            found.append(image['archive']['source'])
    for item in release.get('files', []):
        if isinstance(item, dict) and 'source' in item: found.append(item['source'])
    return found

class Packet:
    def __init__(self, directory, doc, files, publications, checked=None):
        self.directory, self.doc, self.files, self.publications = directory, doc, files, publications
        self.checked = len(files) if checked is None else checked     # how many files were compared with SHA256SUMS
        self.record = {'packet': doc, 'files': files}
        self.run_id = sha256_bytes(canonical(self.record).encode())

def load_packet(path):
    directory = Path(path)
    if not directory.is_dir(): raise PacketError('not a directory: %s' % path)
    directory = Path(os.path.abspath(directory))
    sums = read_sums(directory)
    files = {}
    files['release-run.json'] = hashed_file(directory, sums, 'release-run.json', 'packet')
    try: doc = json.loads((directory / 'release-run.json').read_text(), object_pairs_hook=_no_duplicates)
    except ValueError as exc:
        raise PacketError('release-run.json is not valid JSON: %s' % exc)
    validate_doc(doc)
    publications, catalogs, folded = [], [], 0
    for i, item in enumerate(doc.get('publish', [])):
        where = 'publish[%d]' % i
        if item['kind'] == 'dependencies':
            system, release, debs, digest = dependency_set(directory, sums, item['dir'], where)
            # One line of the record stands for the whole folder (its name ends in "/"): a set is hundreds of
            # files, and the record is read by every member with the core's release notice.
            files[item['dir'].rstrip('/') + '/'] = digest
            folded += len(os.listdir(directory / item['dir'])) - 1
            publications.append({'kind': 'dependencies', 'dir': item['dir'], 'system': system, 'release': release, 'files': debs})
            continue
        if item['kind'] == 'runtime':
            rel = item['manifest']
            files[rel] = hashed_file(directory, sums, rel, where)
            try: manifest = json.loads((directory / rel).read_text())
            except ValueError: raise PacketError('%s is not valid JSON' % rel)
            archives, kept = runtime_archives(rel, manifest)
            for a in archives: files[a] = hashed_file(directory, sums, a, rel + ' archive')
            publications.append({'kind': 'runtime', 'manifest': rel, 'archives': archives, 'kept': kept,
                                 'runtimes': {m: [r['container'] for r in rows] for m, rows in sorted(manifest['runtimes'].items())}})
            continue
        if item['kind'] == 'debs':
            names = deb_files(directory, sums, item['dir'], where)
            for n in names: files[item['dir'].rstrip('/') + '/' + n] = sums[item['dir'].rstrip('/') + '/' + n]
            publications.append({'kind': 'debs', 'dir': item['dir'], 'files': names})
            continue
        rel = item['release']
        files[rel] = hashed_file(directory, sums, rel, where)
        try: release = json.loads((directory / rel).read_text())
        except ValueError: raise PacketError('%s is not valid JSON' % rel)
        if not isinstance(release, dict) or not isinstance(release.get('name'), str) or not NAME.fullmatch(release['name']) \
                or not isinstance(release.get('version'), str) or not re.fullmatch(r'[a-zA-Z0-9.+_-]{1,80}', release['version']):
            raise PacketError('%s has an invalid name/version' % rel)
        for source in release_sources(release):
            if not isinstance(source, str): raise PacketError('%s has an invalid source' % rel)
            files[source] = hashed_file(directory, sums, source, rel + ' source')
        catalogs.append({'kind': 'catalog', 'app': release['name'], 'version': release['version'], 'path': rel})
    # Fixed execution order whatever the packet order: a dependency set, runtime images, packages, then catalog apps.
    # The set goes first: adding it rebuilds the core's release notice from what is published, and between the
    # runtime images and the packages that notice would name a release that is neither the old one nor the new.
    publications = sorted(publications, key=lambda p: ('dependencies', 'runtime', 'debs').index(p['kind'])) + catalogs
    return Packet(directory, doc, dict(sorted(files.items())), publications, len(files) + folded)

def record_problem(record):
    """Validate a record received from the core (same closed schema, no files on disk). Returns None or a reason."""
    try:
        _closed(record, {'packet', 'files'}, {'packet', 'files'}, 'record')
        validate_doc(record['packet'])
        if not isinstance(record['files'], dict) or any(not HEX64.fullmatch(str(v)) for v in record['files'].values()):
            raise PacketError('record files are invalid')
    except PacketError as exc:
        return str(exc)
    return None

def run_id_of(record): return sha256_bytes(canonical(record).encode())


# ---------------------------------------------------------------- summaries

def steps_for(doc, device):
    return list(doc['devices'].get('*', [])) + ([] if device == '*' else list(doc['devices'].get(device, [])))

def verify_for(doc, device):
    """A check naming a device runs there, after that device's steps. An unscoped check runs on every device that has steps."""
    has_steps = bool(steps_for(doc, device))
    return [c for c in doc.get('verify', []) if c.get('device') == device or ('device' not in c and has_steps)]

def describe_step(step):
    verb = step['verb']
    if verb == 'update-apply':
        if step['releaseId'] == PUBLISHED: return 'update this device to the release this run publishes (its id is assigned at publication)'
        return 'update this device to runtime release %s' % step['releaseId'][:12]
    if verb == 'store-install':
        text = 'install app %s %s (manifest %s)' % (step['app'], step['version'], step['manifestSha256'][:12])
        return text + (' and check it runs image %s' % step['expectImageDigest'][:19] if 'expectImageDigest' in step else '')
    if verb == 'hotfix':
        return 'apply hotfix %s (isle hotfix apply; idempotent, backs up what it replaces)' % step['name']
    if verb == 'backup':
        return 'take a device-backup of this device (isle backup create; the run stops here if it is not complete)'
    return 'add module %s to instance %s (isle polari module add, then check it is recorded and the backend is ready)' % (step['module'], step['instance'])

def summary_lines(record, run_id, publications=None):
    doc = record['packet']
    lines = ['Release run "%s"' % doc['name'], 'Run id: ' + run_id]
    pubs = publications if publications is not None else []
    if pubs:
        lines.append('Core publishes, in this order:')
        for p in pubs:
            if p['kind'] == 'dependencies':
                lines.append('  - the dependency set of %s: %d package file(s) from %s, served under deps/%s (isle apt-repo publish '
                             '--only-dependencies); the release the core announces stays the one it is'
                             % (p['system'], len(p['files']), p['dir'], p['release']))
            elif p['kind'] == 'runtime':
                lines.append('  - runtime images from %s: %s (isle update publish-runtime)' % (p['manifest'], '; '.join(
                    '%s %s' % (m, ', '.join(c)) for m, c in p['runtimes'].items())))
                if p.get('kept'):
                    lines.append('    of these the packet carries no archive for: %s. They stay the images the core publishes now, and the '
                                 'core must still have their files' % ', '.join('%s %s' % (k['mode'], k['container']) for k in p['kept']))
            elif p['kind'] == 'debs':
                lines.append('  - %d package file(s) from %s: %s (isle apt-repo publish --from); this assigns the release id members update to'
                             % (len(p['files']), p['dir'], ', '.join(p['files'])))
            else: lines.append('  - catalog release %s %s from %s' % (p['app'], p['version'], p['path']))
        if any(p['kind'] in ('runtime', 'debs') for p in pubs):
            lines.append('The release id does not exist yet: it is derived when the packages are published, shown then, and bound into the signed approval.')
    for device, steps in sorted(doc['devices'].items()):
        lines.append('Device %s:' % ('every device' if device == '*' else device))
        lines += ['  %d. %s' % (i, describe_step(s)) for i, s in enumerate(steps, 1)] or ['  (nothing)']
    for check in doc.get('verify', []):
        extra = ' containing ' + json.dumps(check['expectJsonContains']) if 'expectJsonContains' in check else ''
        lines.append('Verify (on %s, after its steps): %s answers %s%s' % (check.get('device', 'each device with steps'), check['url'], check.get('expectStatus', 200), extra))
    return lines


# ---------------------------------------------------------------- receipt

class Receipt:
    """<runs_dir>/<run-id>/run.json + receipt.json for THIS device. Steps already done are skipped on resume."""
    def __init__(self, deps, run_id, device, record, created_at=None, serial=None):
        self.deps, self.dir = deps, Path(deps.runs_dir) / run_id
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o755)
        if not (self.dir / 'run.json').exists():
            atomic(self.dir / 'run.json', {'runId': run_id, 'createdAt': created_at or deps.now(), 'record': record}, 0o644)
        old = deps.read_json(self.dir / 'receipt.json')
        if old and old.get('device') == device and old.get('runId') == run_id: self.data = old
        else: self.data = {'runId': run_id, 'device': device, 'startedAt': deps.now(), 'finishedAt': None,
                           'state': 'running', 'steps': [], 'verify': []}
        self.data['state'], self.data['finishedAt'] = 'running', None
        if serial is not None: self.data['serial'] = serial
        self.save()

    def save(self):
        self.data['updatedAt'] = self.deps.now()
        atomic(self.dir / 'receipt.json', self.data, 0o644)

    def entry(self, key):
        return next((s for s in self.data['steps'] if s['key'] == key), None)

    def begin(self, key, verb, summary):
        """Returns the step entry, or None when it is already done (resume)."""
        e = self.entry(key)
        if e and e['state'] == 'done': return None
        if not e:
            e = {'key': key, 'verb': verb, 'summary': summary, 'state': 'running', 'operationId': None,
                 'startedAt': None, 'finishedAt': None, 'detail': '', 'data': {}}
            self.data['steps'].append(e)
        e.update(state='running', startedAt=self.deps.now(), finishedAt=None, detail='', attempts=e.get('attempts', 0) + 1)
        self.save()
        return e

    def end(self, e, state, detail='', operation=None, data=None):
        e.update(state=state, detail=detail, finishedAt=self.deps.now())
        if operation: e['operationId'] = operation
        if data: e['data'] = data
        self.save()

    def finish(self, state):
        self.data.update(state=state, finishedAt=self.deps.now())
        self.save()


# ---------------------------------------------------------------- waiting and probes

def poll(deps, label, check, limit):
    """Call check() -> list of what is still missing, until empty or the limit; one progress line per poll."""
    start = deps.time()
    while True:
        missing = check()
        if not missing: return
        waited = int(deps.time() - start)
        if waited >= limit:
            raise Refused(label, 'gave up after %ds; never appeared: %s' % (waited, '; '.join(missing)))
        deps.say('%s (%ds of %ds): still missing: %s' % (label, waited, limit, '; '.join(missing)))
        deps.sleep(deps.poll_interval)

def descriptor_release(deps, app):
    status, body = deps.http('%s/api/islemesh/catalog/%s' % (API, app))
    if status != 200: return None, 'catalog answered %s' % status
    try: release = json.loads(body)['entry']['install_plan']['release']
    except (ValueError, KeyError, TypeError): return None, 'catalog entry has no release'
    return release, None

def served_missing(deps, app, manifest_sha, manifest_url=None, assets=True):
    """What is still not in place: descriptor naming this manifest, manifest and asset URLs answering 200."""
    release, why = descriptor_release(deps, app)
    if release is None: return [why]
    if release.get('sha256') != manifest_sha:
        return ['catalog descriptor for %s names %s, not %s' % (app, str(release.get('sha256'))[:12], manifest_sha[:12])]
    url = manifest_url or release.get('url')
    try: check_url(url, 'manifest url')
    except PacketError as exc: return [str(exc)]
    if not assets:
        status, _ = deps.http(url, head=True)
        return [] if status == 200 else ['%s answers %s' % (url, status)]
    status, body = deps.http(url)
    if status != 200: return ['%s answers %s' % (url, status)]
    if sha256_bytes(body) != manifest_sha: return ['%s does not match manifest sha %s' % (url, manifest_sha[:12])]
    try:
        manifest = json.loads(body)
        urls = [manifest['compose']['url']] + [i['archive']['url'] for i in manifest['images']] \
            + [f['url'] for f in manifest.get('files', [])]
    except (ValueError, KeyError, TypeError): return ['manifest %s is not a catalog manifest' % url]
    missing = []
    for asset in urls:
        try: check_url(asset, 'asset url')
        except PacketError as exc: missing.append(str(exc)); continue
        status, _ = deps.http(asset, head=True)
        if status != 200: missing.append('%s answers %s' % (asset, status))
    return missing

def json_contains(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and json_contains(actual[k], v) for k, v in expected.items())
    return actual == expected

def run_verify(deps, receipt, checks):
    receipt.data['verify'] = []
    ok = True
    for check in checks:
        want = check.get('expectStatus', 200)
        status, body = deps.http(check['url'])
        problem = ''
        if status != want: problem = 'answered %s, wanted %s' % (status, want)
        elif 'expectJsonContains' in check:
            try:
                if not json_contains(json.loads(body), check['expectJsonContains']): problem = 'body lacks %s' % json.dumps(check['expectJsonContains'])
            except ValueError: problem = 'body is not JSON'
        ok &= not problem
        deps.say('verify %s: %s' % (check['url'], problem or 'ok'))
        receipt.data['verify'].append({'url': check['url'], 'state': 'failed' if problem else 'done', 'status': status, 'detail': problem})
        receipt.save()
    return ok


# ---------------------------------------------------------------- journaled steps

OP_LINE = re.compile(r'^Operation: ([a-f0-9]{32})\s*$', re.M)

def journaled(deps, args, stream=True, action='run'):
    """Run ONE operation through the journal entry point; returns (exit code, operation id or None)."""
    code, out = deps.run(['python3', str(deps.scripts / 'operations.py'), action] + args, stream=stream)
    m = OP_LINE.search(out)
    return code, (m.group(1) if m else None)

def operation_status(deps, op):
    code, out = deps.run(['python3', str(deps.scripts / 'operations.py'), 'status', op])
    try: return json.loads(out)
    except ValueError: return {}

def installed_release_ok(deps, step, op):
    """After store install: a local instance of the app carries exactly this release and runs its admitted image."""
    name, sha = step_name(step), step['manifestSha256']
    instances = deps.installed_instances(step['app'])
    match = [i for i, r in instances.items() if r.get('version') == step['version'] and r.get('manifestSha256') == sha]
    if not match:
        have = ', '.join('%s is %s (manifest %s)' % (i, r.get('version'), str(r.get('manifestSha256'))[:12]) for i, r in sorted(instances.items()))
        raise Refused(name, 'no local instance of %s is on %s (manifest %s); %s' % (
            step['app'], step['version'], sha[:12], have or 'no installed release record'), op, 'failed')
    admitted = deps.read_json('%s/%s/admitted.json' % (ARTIFACTS, sha)) or {}
    accepted = set((admitted.get('images') or {}).values()) if admitted.get('manifestSha256') == sha else set()
    want = step.get('expectImageDigest')
    if want:
        manifest = deps.read_json('%s/%s/manifest.json' % (ARTIFACTS, sha)) or {}
        named = {i.get(k) for i in manifest.get('images', []) if isinstance(i, dict) for k in ('manifestDigest', 'configDigest')}
        if manifest and want not in named:
            raise Refused(name, 'the packet expects image %s but the published manifest does not name it' % want[:19], op, 'failed')
        if not accepted: accepted = {want}
    if not accepted: return
    for instance in match:
        code, out = deps.run(['docker', 'ps', '--filter', 'label=com.docker.compose.project=isle-' + instance, '--format', '{{.ID}}'])
        ids = out.split() if code == 0 else []
        images = []
        if ids:
            code, out = deps.run(['docker', 'inspect', '--format', '{{.Image}}'] + ids)
            images = out.split() if code == 0 else []
        if not images or any(i not in accepted for i in images):
            raise Refused(name, 'instance %s does not run the admitted image of this release (running: %s)' % (
                instance, ', '.join(i[:19] for i in images) or 'nothing'), op, 'failed')

def step_name(step):
    return (step['verb'] + ' ' + (step.get('app') or step.get('releaseId', '')[:12] or step.get('module', '') or step.get('name', ''))).rstrip()

def exec_store_install(deps, step, e):
    name = step_name(step)
    poll(deps, 'gate ' + step['app'], lambda: served_missing(deps, step['app'], step['manifestSha256']), deps.wait_limit)
    code, op = journaled(deps, ['store', 'install', step['app'], '--yes'])
    if code: raise Refused(name, 'store install failed (exit %d)' % code, op, 'failed')
    installed_release_ok(deps, step, op)
    return op, 'installed %s %s' % (step['app'], step['version'])

def modules_ready(deps):
    status, body = deps.http(API + '/api/modules/status')
    try: ok = status == 200 and json.loads(body).get('ok') is True
    except ValueError: ok = False
    return [] if ok else ['backend modules/status is not ok yet (answered %s)' % status]

def retry_once(deps, name, op, why):
    deps.say('%s; waiting for the backend, then retrying operation %s once' % (why, op[:8]))
    poll(deps, 'retry ' + op[:8], lambda: modules_ready(deps), deps.modules_limit)
    code, _ = journaled(deps, [op], action='retry')
    if code: raise Refused(name, 'update apply failed again on the one retry (exit %d)' % code, op, 'failed')

def exec_update_apply(deps, step, e):
    name, rid = step_name(step), step['releaseId']
    prior = e.get('operationId')
    if prior:
        # A halted run is resumed. desired-release.json is written BEFORE reconcile, so it cannot say the
        # earlier attempt finished; the journal can.
        status = operation_status(deps, prior)
        if status.get('state') == 'succeeded': return prior, 'applied %s (operation had succeeded)' % rid[:12]
        if not status.get('retryable'): raise Refused(name, 'earlier update apply failed and is not retryable', prior, 'failed')
        retry_once(deps, name, prior, 'resuming the earlier failed update apply')
        return prior, 'applied %s after resuming' % rid[:12]
    desired = deps.read_json(DESIRED_RELEASE)
    if isinstance(desired, dict) and desired.get('id') == rid and e.get('attempts', 1) <= 1:
        # Only on the first attempt: an attempt that died mid-apply (reboot, kill) leaves this file written but the
        # reconcile unfinished, and no operation id here. Applying the same id again is how that is completed.
        return None, 'already on ' + rid
    code, op = journaled(deps, ['update', 'apply', rid])
    if code == 0: return op, 'applied ' + rid[:12]
    if not op: raise Refused(name, 'update apply failed (exit %d) and gave no operation id' % code)
    e['operationId'] = op
    if not operation_status(deps, op).get('retryable'): raise Refused(name, 'update apply failed (exit %d), not retryable' % code, op, 'failed')
    retry_once(deps, name, op, 'update apply failed (exit %d)' % code)
    return op, 'applied %s after one retry' % rid[:12]

def compose_modules(text):
    """POLARI_MODULES of an instance compose file; same two formats polari.sh _modules_of_compose reads."""
    try:
        env = json.loads(text)['services']['backend']['environment']
        if isinstance(env, dict): return str(env.get('POLARI_MODULES', ''))
        return next((e.split('=', 1)[1] for e in env if e.startswith('POLARI_MODULES=')), '')
    except (ValueError, KeyError, TypeError):
        m = re.search(r'POLARI_MODULES=([^\s]+)', text)
        return m.group(1) if m else ''

def csv_has(csv, module): return module in str(csv).split(',')

def exec_module_add(deps, step, e):
    name, module, instance = step_name(step), step['module'], step['instance']
    args = ['polari', 'module', 'add', module, '--to', instance]
    if not deps.journal_allows(args):
        raise Refused(name, 'this CLI has no journaled "polari module add" operation (an older installed CLI); update the CLI first')
    base = Path(deps.polari_dir) / instance
    record = deps.read_json(base / 'deployment.json')
    if not isinstance(record, dict): raise Refused(name, 'instance %s has no deployment.json under %s' % (instance, deps.polari_dir))
    if 'modules' not in record:
        raise Refused(name, 'deployment.json of %s has no "modules" record, so the module would not survive the next update '
                            '(this CLI lacks the durable module record); nothing was changed' % instance)
    code, op = journaled(deps, args)
    if code: raise Refused(name, 'module add failed (exit %d)' % code, op, 'failed')
    record = deps.read_json(base / 'deployment.json')
    if not isinstance(record, dict) or not csv_has(record.get('modules', ''), module):
        raise Refused(name, 'module %s is not in deployment.json of %s after the add; it would not survive the next update' % (module, instance), op, 'failed')
    compose = deps.read_text(base / 'docker-compose.yml')
    if compose is None or not csv_has(compose_modules(compose), module):
        raise Refused(name, 'POLARI_MODULES of %s does not list %s after the add' % (instance, module), op, 'failed')
    poll(deps, 'modules ready', lambda: modules_ready(deps), deps.modules_limit)
    return op, 'added module %s to %s (recorded in deployment.json and the compose file; backend ready)' % (module, instance)

def exec_hotfix(deps, step, e):
    """isle hotfix apply <name> --yes through the journal. Exit 0 covers both "applied" and "nothing to do"."""
    name, fix = step_name(step), step['name']
    args = ['hotfix', 'apply', fix, '--yes']
    if not deps.journal_allows(args):
        raise Refused(name, 'this CLI has no journaled "hotfix apply" operation (an older installed CLI); update the CLI first')
    code, op = journaled(deps, args)
    if code == 2: raise Refused(name, 'the installed CLI of this device does not know hotfix %s (isle hotfix list); nothing was changed' % fix, op, 'failed')
    if code: raise Refused(name, 'hotfix %s failed (exit %d); see isle hotfix status' % (fix, code), op, 'failed')
    return op, 'hotfix %s applied or already in place' % fix

def exec_backup(deps, step, e):
    """isle backup create --yes through the journal. Exit 0 only when every file was re-read and matched."""
    name, args = step_name(step), ['backup', 'create', '--yes']
    if not deps.journal_allows(args):
        raise Refused(name, 'this CLI has no journaled "backup create" operation (an older installed CLI); update the CLI first')
    code, op = journaled(deps, args)
    if code: raise Refused(name, 'the device-backup is not complete (exit %d), so nothing after it was started; see: sudo isle backup list' % code, op, 'failed')
    return op, 'device-backup taken and read back (sudo isle backup list)'

EXECUTORS = {'update-apply': exec_update_apply, 'store-install': exec_store_install, 'module-add': exec_module_add,
             'hotfix': exec_hotfix, 'backup': exec_backup}

def run_device_steps(deps, receipt, steps, publications, release_id=None):
    """release_id: the id "published" means. On the core it is what the publication produced; on a member it
    is ONLY the signed payload's releaseId (the caller checks it before anything runs)."""
    for i, step in enumerate(steps):
        if step['verb'] == 'update-apply' and step['releaseId'] == PUBLISHED:
            if not (isinstance(release_id, str) and HEX64.fullmatch(release_id)):
                e = receipt.begin('step:%d:%s' % (i, step['verb']), step['verb'], describe_step(step))
                err = Refused('update-apply published', 'no valid published release id is available for this step')
                if e is not None: receipt.end(e, err.state, err.why)
                raise err
            step = dict(step, releaseId=release_id)
        e = receipt.begin('step:%d:%s' % (i, step['verb']), step['verb'], describe_step(step))
        if e is None:
            deps.say('skip (already done): ' + describe_step(step)); continue
        deps.say('step %d/%d: %s' % (i + 1, len(steps), describe_step(step)))
        pub = next((p for p in publications if step['verb'] == 'store-install' and p.get('app') == step['app']), None)
        try:
            if pub and pub['manifestSha256'] != step['manifestSha256']:
                raise Refused(step_name(step), 'the run announces manifest %s for %s but this step wants %s' % (
                    pub['manifestSha256'][:12], step['app'], step['manifestSha256'][:12]))
            op, detail = EXECUTORS[step['verb']](deps, step, e)
        except Refused as r:
            receipt.end(e, r.state, r.why, r.operation); raise
        receipt.end(e, 'done', detail, op)


# ---------------------------------------------------------------- approval (slice 2)

APPROVAL_CN = 'isle-release-approval'
APPROVAL_DAYS = 730
RENEW_WITHIN = 30 * 86400
APPROVAL_LIFE = 30 * 86400
TIME_FMT = '%Y-%m-%dT%H:%M:%SZ'
PEM_CERT = re.compile(r'-----BEGIN CERTIFICATE-----[A-Za-z0-9+/=\s]+?-----END CERTIFICATE-----')
NEEDS = 'sudo isle release follow'
AUTO_ATTEMPTS = 3

def stamp(t): return datetime.fromtimestamp(t, timezone.utc).strftime(TIME_FMT)
def unstamp(text): return datetime.strptime(text, TIME_FMT).replace(tzinfo=timezone.utc).timestamp()
def openssl(deps, *args):
    return deps.run(['openssl'] + [str(a) for a in args], timeout=30)

def cert_facts(deps, cert):
    """subject/issuer/validity/fingerprint and the extensions of ONE certificate file, via the openssl CLI."""
    code, out = openssl(deps, 'x509', '-in', cert, '-noout', '-subject', '-issuer', '-startdate', '-enddate',
                        '-fingerprint', '-sha256', '-nameopt', 'RFC2253', '-ext', 'basicConstraints,keyUsage,extendedKeyUsage')
    if code: return None
    info, ext, cur = {}, {}, None
    for line in out.splitlines():
        for key, name in (('subject=', 'subject'), ('issuer=', 'issuer'), ('notBefore=', 'notBefore'), ('notAfter=', 'notAfter'),
                          ('sha256 Fingerprint=', 'fingerprint')):
            if line.startswith(key): info[name] = line[len(key):].strip(); cur = None; break
        else:
            if line.startswith('X509v3 '):
                cur = line[7:].split(':')[0].strip(); ext[cur] = [line.partition(':')[2].strip()]
            elif cur and line.startswith(' '): ext[cur].extend(x.strip() for x in line.split(','))
    info['ext'] = {k: [x for x in v if x] for k, v in ext.items()}
    try: info['expires'] = datetime.strptime(info['notAfter'], '%b %d %H:%M:%S %Y GMT').replace(tzinfo=timezone.utc).timestamp()
    except (KeyError, ValueError): info['expires'] = 0
    return info

def cert_problem(deps, cert, now):
    """None when cert is a good approval certificate: issued DIRECTLY by the isle root (no -untrusted, no system store)."""
    root = Path(deps.ca_dir) / 'isle-root.crt'
    if not root.is_file(): return 'this device has no isle root certificate at %s' % root
    code, out = openssl(deps, 'verify', '-no-CApath', '-no-CAstore', '-CAfile', root, '-attime', int(now), cert)
    if code: return 'the approval certificate does not chain directly to the isle root (%s)' % (out.strip().splitlines() or ['openssl failed'])[-1][:120]
    info, rinfo = cert_facts(deps, cert), cert_facts(deps, root)
    if not info or not rinfo: return 'cannot read the approval certificate'
    if info.get('issuer') != rinfo.get('subject'): return 'the approval certificate was not issued by the isle root itself'
    if info.get('subject') != 'CN=' + APPROVAL_CN: return 'the approval certificate subject is %r, not CN=%s' % (info.get('subject'), APPROVAL_CN)
    eku = set(info['ext'].get('Extended Key Usage', []))
    if eku != {'Code Signing'}: return 'the approval certificate key usage is %s; it must be Code Signing only' % (sorted(eku) or 'absent')
    if 'CA:FALSE' not in info['ext'].get('Basic Constraints', []): return 'the approval certificate is not CA:FALSE'
    if 'Digital Signature' not in info['ext'].get('Key Usage', []): return 'the approval certificate may not sign'
    return None

def ensure_approval_key(deps):
    """Mint (or keep) /etc/isle-mesh/ca/release-approval/{key,cert}.pem. Core only. Never prints key material."""
    ad = Path(deps.approval_dir)
    key, cert = ad / 'key.pem', ad / 'cert.pem'
    if key.is_file() and cert.is_file():
        info = cert_facts(deps, cert)
        c1, pub = openssl(deps, 'pkey', '-in', key, '-pubout')
        c2, cpub = openssl(deps, 'x509', '-in', cert, '-noout', '-pubkey')
        if info and not c1 and not c2 and pub == cpub and not cert_problem(deps, cert, deps.time()) \
                and info['expires'] - deps.time() > RENEW_WITHIN:
            return info
    ca = Path(deps.ca_dir)
    if not (ca / 'root_ca_key').is_file() or not (ca / 'isle-root.crt').is_file():
        raise Refused('approval-key', 'the isle root key is not on this device; only the core can mint the approval key')
    ad.mkdir(parents=True, exist_ok=True, mode=0o700); os.chmod(ad, 0o700)
    with tempfile.TemporaryDirectory(dir=ad) as tmp:
        tmp = Path(tmp)
        nkey, csr, ext, ncert = tmp / 'key.pem', tmp / 'req.pem', tmp / 'ext', tmp / 'cert.pem'
        os.close(os.open(nkey, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        ext.write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=codeSigning\n')
        steps = [('ecparam', '-name', 'prime256v1', '-genkey', '-noout', '-out', nkey),
                 ('req', '-new', '-key', nkey, '-subj', '/CN=' + APPROVAL_CN, '-out', csr),
                 ('x509', '-req', '-in', csr, '-CA', ca / 'isle-root.crt', '-CAkey', ca / 'root_ca_key',
                  '-set_serial', '0x' + secrets.token_hex(16), '-days', APPROVAL_DAYS, '-sha256', '-extfile', ext, '-out', ncert)]
        for step in steps:
            code, out = openssl(deps, *step)
            if code: raise Refused('approval-key', 'openssl %s failed: %s' % (step[0], out.strip()[-200:]))
        problem = cert_problem(deps, ncert, deps.time())
        if problem: raise Refused('approval-key', 'the new approval certificate is not acceptable: ' + problem)
        os.chmod(ncert, 0o644)
        os.replace(nkey, key); os.replace(ncert, cert)
    return cert_facts(deps, cert)

def sign_text(deps, text):
    with tempfile.TemporaryDirectory() as tmp:
        payload, sig = Path(tmp) / 'payload', Path(tmp) / 'sig'
        payload.write_bytes(text.encode())
        code, out = openssl(deps, 'dgst', '-sha256', '-sign', Path(deps.approval_dir) / 'key.pem', '-out', sig, payload)
        if code: raise Refused('approval', 'signing failed: ' + out.strip()[-200:])
        return base64.b64encode(sig.read_bytes()).decode()

def list_devices(deps):
    """The core's device list from the backend, or None when it cannot be read."""
    status, body = deps.http(API + '/api/islemesh/devices')
    try: listing = json.loads(body)['devices'] if status == 200 else None
    except (ValueError, KeyError, TypeError): return None
    return [d for d in listing if isinstance(d, dict) and isinstance(d.get('name'), str)] if isinstance(listing, list) else None

STAR_SEEN_WITHIN = 24 * 3600     # "*" means the members the core has heard from in this long; a name can still be written out

def star_devices(deps):
    """(members "*" covers now, [(name, last seen)] left out as not heard from), or None when the list cannot be read.
    A device that never reports again would otherwise be signed in and waited on until the limit."""
    listing = list_devices(deps)
    if listing is None: return None
    fresh, stale = [], []
    for d in listing:
        if d.get('is_mock') or d.get('agent_mode') != 'remote': continue
        seen = d.get('last_seen')
        try: age = deps.time() - datetime.fromisoformat(seen).timestamp()
        except (TypeError, ValueError): age = None
        if age is not None and age <= STAR_SEEN_WITHIN: fresh.append(d['name'])
        else: stale.append((d['name'], seen if isinstance(seen, str) else 'never'))
    return sorted(fresh), sorted(stale)

def resolve_devices(deps, doc, core):
    """Names the approval covers, fixed at signing time: explicit names, for "*" every remote non-mock device heard from
    within STAR_SEEN_WITHIN, and the core itself when it has steps. A device that joins later is in none of these."""
    names = {n for n in doc['devices'] if n != '*'}
    if '*' in doc['devices']:
        star = star_devices(deps)
        if star is None:
            raise Refused('approval', 'cannot read the device list from %s/api/islemesh/devices, so "*" cannot be resolved; '
                                      'not signing an open-ended approval' % API)
        names |= set(star[0])
    if steps_for(doc, core): names.add(core)
    return sorted(names)

def star_lines(deps, doc):
    """What "every device" means right now, for the summary shown before the confirmation."""
    if '*' not in doc['devices']: return []
    star = star_devices(deps)
    if star is None: return ['"every device" cannot be resolved yet: the device list cannot be read (the run will refuse to sign).']
    lines = ['"every device" is, right now: %s' % (', '.join(star[0]) or '(no member)')]
    lines += ['  left out, not heard from in 24 h: %s (last seen %s); name it in the packet to include it' % s for s in star[1]]
    return lines

def next_serial(deps):
    Path(deps.runs_dir).mkdir(parents=True, exist_ok=True, mode=0o755)
    path = Path(deps.runs_dir) / 'serial'
    last = deps.read_json(path)
    last = last if type(last) is int else 0
    serial = max(last + 1, int(deps.time()))
    atomic(path, serial, 0o600)
    return serial

def make_approval(deps, run_id, publications, devices, stored=None, release_id=None):
    """Sign the approval for this run. A resumed run (stored approval, same facts, not expiring) is reused as is."""
    if isinstance(stored, dict):
        try:
            old = json.loads(stored['payload'])
            if old['runId'] == run_id and old['publications'] == publications and old['devices'] == devices and old.get('releaseId') == release_id \
                    and unstamp(old['expiresAt']) - deps.time() > 86400:
                return stored, old['serial']
        except (KeyError, ValueError, TypeError): pass
    ensure_approval_key(deps)
    serial = next_serial(deps)
    now = deps.time()
    payload = canonical({'type': 'isle-release-approval', 'version': 1, 'runId': run_id, 'serial': serial,
                         'approvedAt': stamp(now), 'expiresAt': stamp(now + APPROVAL_LIFE), 'publications': publications,
                         'devices': devices, 'releaseId': release_id})
    cert = (Path(deps.approval_dir) / 'cert.pem').read_text()
    return {'payload': payload, 'signature': sign_text(deps, payload), 'certificate': cert}, serial

def verify_approval(deps, run, run_id, device):
    """Member side. Returns the SIGNED payload dict or raises Refused('approval', why). Nothing has executed yet."""
    ap = run.get('approval')
    if not isinstance(ap, dict) or not all(isinstance(ap.get(k), str) for k in ('payload', 'signature', 'certificate')):
        raise Refused('approval', 'the run carries no signed approval')
    if len(ap['payload']) > 262144 or len(ap['certificate']) > 16384 or len(ap['signature']) > 4096:
        raise Refused('approval', 'the approval is too large')
    certs = PEM_CERT.findall(ap['certificate'])
    if len(certs) != 1 or ap['certificate'].count('BEGIN') != 1:
        raise Refused('approval', 'the approval must carry exactly one certificate (no chain)')
    try: sig = base64.b64decode(ap['signature'], validate=True)
    except ValueError: raise Refused('approval', 'the approval signature is not base64')
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        cert, pub, sigf, payload = tmp / 'cert.pem', tmp / 'pub.pem', tmp / 'sig', tmp / 'payload'
        cert.write_text(certs[0] + '\n'); sigf.write_bytes(sig); payload.write_bytes(ap['payload'].encode())
        problem = cert_problem(deps, cert, deps.time())
        if problem: raise Refused('approval', problem)
        code, out = openssl(deps, 'x509', '-in', cert, '-noout', '-pubkey')
        if code: raise Refused('approval', 'cannot read the approval public key')
        pub.write_text(out)
        code, out = openssl(deps, 'dgst', '-sha256', '-verify', pub, '-signature', sigf, payload)
        if code: raise Refused('approval', 'the approval signature does not verify')
    try: data = json.loads(ap['payload'])
    except ValueError: raise Refused('approval', 'the approval payload is not JSON')
    try:
        if data['type'] != 'isle-release-approval' or data['version'] != 1: raise Refused('approval', 'unknown approval type or version')
        if data['runId'] != run_id or run.get('runId') != run_id:
            raise Refused('approval', 'the approval is for run %s, not %s' % (str(data['runId'])[:12], run_id[:12]))
        if unstamp(data['expiresAt']) <= deps.time(): raise Refused('approval', 'the approval expired at ' + data['expiresAt'])
        serial = data['serial']
        if type(serial) is not int or serial < 1: raise Refused('approval', 'invalid approval serial')
        if not isinstance(data['publications'], list) or not isinstance(data['devices'], list) \
                or any(not isinstance(n, str) or not NAME.fullmatch(n) for n in data['devices']):
            raise Refused('approval', 'invalid approval payload')
    except (KeyError, TypeError, ValueError):
        raise Refused('approval', 'invalid approval payload')
    path = Path(deps.runs_dir) / 'accepted-serial.json'
    seen = deps.read_json(path)
    if isinstance(seen, dict) and type(seen.get('serial')) is int:
        if serial < seen['serial']: raise Refused('approval', 'approval serial %d is lower than the %d this device already accepted' % (serial, seen['serial']))
        if serial == seen['serial'] and seen.get('runId') != run_id:
            raise Refused('approval', 'approval serial %d was already accepted for a different run' % serial)
    if not (isinstance(seen, dict) and seen.get('serial') == serial and seen.get('runId') == run_id):
        Path(deps.runs_dir).mkdir(parents=True, exist_ok=True, mode=0o755)
        atomic(path, {'serial': serial, 'runId': run_id}, 0o600)
    return data


# ---------------------------------------------------------------- status reported back through the capability report

def report_fact(runs_dir='/var/lib/isle-mesh/release-runs'):
    """JSON string describing this device's newest receipt, or None. Reads files only; never raises."""
    try:
        best = None
        for d in Path(runs_dir).iterdir():
            try:
                f = d / 'receipt.json'
                data, mtime = json.loads(f.read_text()), f.stat().st_mtime_ns
            except (OSError, ValueError): continue
            if isinstance(data, dict) and (best is None or (mtime, d.name) > best[0]): best = ((mtime, d.name), data, mtime)
        if best is None: return None
        _, data, mtime = best
        state = data.get('state')
        state = state if state in ('running', 'done') else 'failed'
        steps = [s for s in data.get('steps') or [] if isinstance(s, dict)]
        cur = next((s for s in steps if s.get('state') == 'running'), None) or next((s for s in steps if s.get('state') not in ('done', None)), None) \
            or (steps[-1] if steps else {})
        step = str(cur.get('summary') or '')
        if state == 'failed' and cur.get('detail'): step += ': ' + str(cur['detail'])
        serial = data.get('serial')
        return json.dumps({'runId': str(data.get('runId')), 'serial': serial if type(serial) is int else None, 'state': state,
                           'step': step[:200], 'updatedAt': str(data.get('updatedAt') or datetime.fromtimestamp(mtime / 1e9, timezone.utc).isoformat())},
                          sort_keys=True, separators=(',', ':'))
    except Exception:
        return None


# ---------------------------------------------------------------- core waits on member reports (ADVISORY ONLY)
# The reports come over the unauthenticated capability channel. They only drive what the core PRINTS and its
# exit code; nothing is ever executed, skipped or marked done because of them. Authority is the signed approval.

def member_states(deps, run_id, names):
    """{name: (state, step)} for names from one read of the device list, or None when the list cannot be read."""
    listing = list_devices(deps)
    if listing is None: return None
    by_name = {d['name']: d for d in listing}
    out = {}
    for n in names:
        out[n] = ('pending', '')
        try:
            fact = by_name[n]['capability_evidence']['facts']['isle.release_run']
            if fact.get('status', 'observed') != 'observed': continue
            value = fact['value']
            value = json.loads(value) if isinstance(value, str) else value
            if isinstance(value, dict) and value.get('runId') == run_id and value.get('state') in ('running', 'done', 'failed'):
                out[n] = (value['state'], str(value.get('step') or '')[:200])
        except (KeyError, TypeError, ValueError, AttributeError): pass
    return out

def member_table(deps, states):
    for n, (state, step) in sorted(states.items()): deps.say('  %-20s %-8s %s' % (n, state, step))

def approved_members(approval, core):
    try: return sorted(n for n in json.loads(approval['payload'])['devices'] if n != core)
    except (KeyError, TypeError, ValueError): return []

def wait_for_members(deps, run_id, names, minutes):
    """Poll until every name is done/failed or the limit; one line per change; final table. Exit 0 / 1 / 3."""
    if not names: deps.say('No other devices to wait for.'); return 0
    start, last, limit = deps.time(), {}, minutes * 60
    deps.say('Waiting up to %g min for %s to report (they follow on their own timers).' % (minutes, ', '.join(names)))
    while True:
        states = member_states(deps, run_id, names)
        if states is None:
            if last.get('?') is None: deps.say('cannot read the device list yet; will keep trying'); last['?'] = 1
            states = {n: ('pending', '') for n in names}
        for n, v in sorted(states.items()):
            if last.get(n) != v: deps.say('%s: %s%s' % (n, v[0], ' - ' + v[1] if v[1] else '')); last[n] = v
        if all(v[0] in ('done', 'failed') for v in states.values()) or deps.time() - start >= limit: break
        deps.sleep(deps.poll_interval)
    deps.say('Member results for run %s:' % run_id[:12]); member_table(deps, states)
    failed = sorted(n for n, v in states.items() if v[0] == 'failed')
    pending = sorted(n for n, v in states.items() if v[0] not in ('done', 'failed'))
    if pending: deps.say('Still pending when the wait ended: %s. They will still catch up on their timers; check: isle release status' % ', '.join(pending))
    if failed: deps.say('Failed: %s' % ', '.join(failed)); return 1
    return 3 if pending else 0


# ---------------------------------------------------------------- commands

def fail(deps, r, device, receipt=None):
    deps.say('REFUSED: %s on %s: %s%s' % (r.step, device, r.why, ' (operation %s)' % r.operation if r.operation else ''))
    if receipt: receipt.finish(r.state)
    return 1

def check_expect(deps, pk, expect, step, device):
    if expect and pk.run_id != expect:
        deps.say('REFUSED: %s on %s: expected run id %s but the packet is %s' % (step, device, expect, pk.run_id)); return False
    return True

def cmd_check(deps, directory, expect=None):
    try: pk = load_packet(directory)
    except PacketError as exc:
        deps.say('REFUSED: check on %s: %s' % (deps.device(), exc)); return 1
    if not check_expect(deps, pk, expect, 'check', deps.device()): return 1
    for line in summary_lines(pk.record, pk.run_id, pk.publications): deps.say(line)
    deps.say('Packet is valid: %d file(s) match SHA256SUMS. Nothing was changed.' % pk.checked)
    return 0

ROLE_WORDS = {'core': 'the core', 'remote': 'a member'}

def wrong_role(deps, verb, need, basis=None):
    """None when this device has the role verb needs, else the refusal line. Read before any root check: nobody
    should be asked for a password by a command that would then refuse on this device."""
    mode = deps.mode()
    if mode == need: return None
    here = ROLE_WORDS.get(mode, 'not set up as a core or a member (agent mode is %r)' % mode)
    if need == 'core':
        because = (start_basis(deps) if basis is None else basis) if verb == 'run' and mode == 'remote' else ''
        return ('REFUSED: %s on %s: %sisle release %s works on the core only; this device is %s (%s). '
                'Run it on the core device of the isle.' % (verb, deps.device(), because, verb, deps.device(), here))
    return ('REFUSED: %s on %s: isle release %s works on a member only; this device is %s (%s). '
            'On the core a release is started with: sudo isle release run <packet-dir>' % (verb, deps.device(), verb, deps.device(), here))

# ---- the start setting

def core_start_setting(deps):
    """-> (value in force, the core's record or None). Anything but a value that can be chosen means the default."""
    record = deps.read_setting()
    value = record.get('start') if isinstance(record, dict) else None
    return (value, record) if value in START_SETTINGS else (START_DEFAULT, None)

def published_start_setting(deps, told=None):
    """What the core publishes to members: (value, None), or (None, why it is not known). No record = the default;
    `told` (a list) then stays empty, and receives the value when the core does publish one."""
    status, body = deps.http(API + '/api/islemesh/updates')
    if status != 200: return None, 'the core did not answer'
    try: record = json.loads(body)['release'].get('startSetting')
    except (ValueError, KeyError, AttributeError, TypeError): return None, 'the answer of the core could not be read'
    value = record.get('start') if isinstance(record, dict) else None
    if value not in START_SETTINGS: return START_DEFAULT, None
    if told is not None: told.append(value)
    return value, None

def start_basis(deps, known=None):
    """The opening words of a member's refusal to start a run: they follow the start setting the core publishes.
    With any-device a member does not refuse: cmd_run offers the packet to the core (release_offer.cmd_submit)."""
    value, why = known or published_start_setting(deps)
    if value is None: return 'the start setting of this isle is not known here (%s), and until it is, ' % why
    return 'the start setting of this isle is %s, so ' % value

def cmd_setting(deps, value=None):
    import release_intake          # the any-device half; no step has run when a verb starts
    device, mode = deps.device(), deps.mode()
    if value is None:
        if mode == 'core':
            now, record = core_start_setting(deps)
            deps.say('Start setting of this isle: %s (%s)' % (now, 'set %s on %s' % (record.get('setAt'), record.get('setOn')) if record else 'the default, never set'))
        elif mode == 'remote':
            told = []
            now, why = published_start_setting(deps, told)
            if now is None: deps.say('Start setting of this isle: not known on %s (%s). The default is %s.' % (device, why, START_DEFAULT))
            elif told: deps.say('Start setting of this isle, as the core publishes it: %s' % now)
            else: deps.say('Start setting of this isle: %s (the default; the core publishes no setting)' % now)
        else:
            deps.say('This device is not set up as a core or a member (agent mode is %r); it has no start setting.' % mode); return 1
        for name in START_SETTINGS: deps.say('  %-11s %s' % (name, START_WORDS[name]))
        deps.say('  ' + release_intake.COST)
        deps.say('It is changed on the core only: sudo isle release setting <%s>' % '|'.join(START_SETTINGS))
        return 0
    why = wrong_role(deps, 'setting', 'core')
    if why: deps.say(why); return 1
    if not deps.is_root(): deps.say('This changes the isle. Type: sudo isle release setting %s' % value); return 1
    if value == 'any-device':                        # what the core reads to take an offer is root-only, so it is asked as root
        missing = release_intake.setting_problem(deps)
        if missing:
            deps.say('REFUSED: setting on %s: %s cannot be chosen: %s: %s. The start setting stays %s; nothing was changed.'
                     % (device, value, ANY_DEVICE_MISSING, missing, core_start_setting(deps)[0])); return 1
    record = {'start': value, 'setAt': deps.now(), 'setOn': device}
    deps.write_setting(record)
    deps.say('Start setting of this isle: %s (%s).' % (value, START_WORDS[value]))
    release_intake.apply_setting(deps, value)
    try: deps.write_notice_setting(record); deps.say('Published to the members in the release notice of the core.')
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        deps.say('Recorded on the core, but the release notice could not be updated (%s). Members read the new value after the next publication; until then they are told the default, %s.' % (exc, START_DEFAULT))
    return 0

def cmd_approval_key(deps):
    why = wrong_role(deps, 'approval-key', 'core')
    if why: deps.say(why); return 1
    if not deps.is_root(): deps.say('Type: sudo isle release approval-key'); return 1
    try: info = ensure_approval_key(deps)
    except Refused as r: return fail(deps, r, deps.device())
    for label, k in (('Subject', 'subject'), ('Issuer', 'issuer'), ('Valid from', 'notBefore'), ('Valid until', 'notAfter'), ('SHA-256', 'fingerprint')):
        deps.say('%-11s %s' % (label + ':', info.get(k)))
    return 0

# ---- the root-owned packet copy

def copy_packet(deps, pk, dest):
    """Copy exactly the files SHA256SUMS names (plus SHA256SUMS and release-run.json) into dest (0700), hashing while
    copying and refusing symlinks, so nothing can be swapped between the hash check and the publish."""
    root = Path(pk.directory)
    def nofollow_open(rel):
        p = root
        parts = Path(rel).parts
        for i, part in enumerate(parts):
            p = p / part
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode): raise PacketError('%s is a symbolic link' % rel)
            if i < len(parts) - 1 and not stat.S_ISDIR(st.st_mode): raise PacketError('%s: a path component is not a directory' % rel)
            if i == len(parts) - 1 and not stat.S_ISREG(st.st_mode): raise PacketError('%s is not a regular file' % rel)
        return os.open(p, os.O_RDONLY | os.O_NOFOLLOW), st.st_size
    try:
        fd, _ = nofollow_open('SHA256SUMS')
        with os.fdopen(fd, 'rb') as f: raw = f.read()
        sums = parse_sums(raw.decode())
        names = sorted(set(sums) | {'release-run.json'})
        total = 0
        for rel in names:
            safe_relpath(rel, 'SHA256SUMS')
            fd, size = nofollow_open(rel); os.close(fd); total += size
    except OSError as exc: raise Refused('copy', 'cannot read the packet: %s' % exc)
    except (PacketError, UnicodeDecodeError) as exc: raise Refused('copy', str(exc))
    need, free = total + deps.copy_reserve, deps.free_bytes(dest.parent)
    if free < need:
        raise Refused('copy', 'not enough free space for the packet copy: need %d MiB, %d MiB free in %s' % (need >> 20, free >> 20, dest.parent))
    dest.mkdir(mode=0o700, parents=True); os.chmod(dest, 0o700)
    try:
        for rel in names:
            fd, _ = nofollow_open(rel)
            target = dest / rel
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            h = hashlib.sha256()
            with os.fdopen(fd, 'rb') as src, os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as out:
                for block in iter(lambda: src.read(1024 * 1024), b''): h.update(block); out.write(block)
                out.flush(); os.fsync(out.fileno())
            if rel in sums and h.hexdigest() != sums[rel]: raise PacketError('checksum mismatch for %s while copying' % rel)
        with open(os.open(dest / 'SHA256SUMS', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as out: out.write(raw)
    except (OSError, PacketError) as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise Refused('copy', str(exc))

def prepared_copy(deps, pk, receipt):
    """The packet copy for this run: a still-valid one from an earlier attempt, else a fresh one. Returns its Packet."""
    dest = receipt.dir / 'packet'
    if dest.is_dir():
        try:
            old = load_packet(dest)
            if old.run_id == pk.run_id: return old
        except PacketError: pass
        shutil.rmtree(dest, ignore_errors=True)
    copy_packet(deps, pk, dest)
    try: fresh = load_packet(dest)
    except PacketError as exc: raise Refused('copy', 'the copy does not verify: %s' % exc)
    if fresh.run_id != pk.run_id: raise Refused('copy', 'the copy has run id %s, not %s' % (fresh.run_id[:12], pk.run_id[:12]))
    return fresh

def publication_key(p):
    return {'runtime': 'publish-runtime', 'debs': 'publish-debs', 'dependencies': 'publish-dependencies'}.get(p['kind']) or 'publish:' + p['app']

def dependencies_missing(deps, p):
    """What a device of that release could not yet fetch of a dependency set: deps/<release>/Packages on apt.isle
    must list every package file of the set with its checksum, and every one of those files must answer 200."""
    index = 'https://apt.isle/deps/%s/Packages' % p['release']
    status, body = deps.http(index)
    if status != 200: return ['%s answers %s' % (index, status)]
    served = {}
    for block in body.decode('utf-8', 'replace').split('\n\n'):
        fields = dict(line.split(': ', 1) for line in block.splitlines() if ': ' in line and not line.startswith(' '))
        if 'Filename' in fields: served[fields['Filename'].rsplit('/', 1)[-1]] = fields.get('SHA256')
    absent = sorted(n for n, sha in p['files'].items() if served.get(n) != sha)
    if absent:
        return ['%s lists %d of the %d package files of the set with their checksums (first one it does not: %s)'
                % (index, len(p['files']) - len(absent), len(p['files']), absent[0])]
    missing = []
    for n in sorted(p['files']):
        status, _ = deps.http('https://apt.isle/' + quote(n), head=True)
        if status != 200: missing.append('https://apt.isle/%s answers %s' % (n, status))
        if len(missing) == 5: missing.append('(and it was not asked for the rest)'); break
    return missing

def release_missing(deps, release_id):
    """What a member could not yet fetch for this release: /updates must name the id, and every package file and every
    runtime asset URL update.py / runtime_reconcile.py would use must answer 200 on apt.isle."""
    status, body = deps.http(API + '/api/islemesh/updates')
    if status != 200: return ['/api/islemesh/updates answers %s' % status]
    try:
        release = json.loads(body)['release']
        served = release['id']
        urls = ['https://apt.isle/' + p['file'] for p in list(release['packages']) + list((release.get('bundles') or {}).values())]
        for rows in (release.get('runtimes') or {}).values():
            urls += [item['image']['archive']['url'] for item in rows]
    except (ValueError, KeyError, TypeError, AttributeError): return ['/api/islemesh/updates does not serve a well-formed release']
    if served != release_id: return ['/api/islemesh/updates names release %s, not %s' % (str(served)[:12], release_id[:12])]
    missing = []
    for u in urls:
        try: check_url(u, 'release url')
        except PacketError as exc: missing.append(str(exc)); continue
        status, _ = deps.http(u, head=True)
        if status != 200: missing.append('%s answers %s' % (u, status))
    return missing

def notice_problem(notice, started, runtime_rows):
    """The notice must have been rebuilt BY this publication (apt-repo.sh only warns when it cannot write it)."""
    rid = notice.get('id') if isinstance(notice, dict) else None
    if not isinstance(rid, str) or not HEX64.fullmatch(rid): return 'the core release notice has no valid release id'
    try: published = datetime.fromisoformat(notice['publishedAt']).timestamp()
    except (KeyError, ValueError, TypeError): return 'the core release notice has no publishedAt'
    if published < started - 5:
        return 'the core release notice was not rebuilt by this publication (apt-repo publish may have warned that it could not write it); rerun once the backend is ready'
    if runtime_rows is not None:
        have = {m: sorted(r.get('container') for r in rows if isinstance(r, dict)) for m, rows in (notice.get('runtimes') or {}).items()}
        if have != {m: sorted(c) for m, c in runtime_rows.items()}: return 'the core release notice lists different runtime containers than this packet'
    return None

def kept_runtime_here(deps, pk, receipt):
    """A packet may leave out the archive of a runtime image the release does not change.  The core then has to
    publish that file already; one that is missing stops the run here, before a dependency set or anything else is
    published.  (publish-runtime checks the checksum of each of them again.)"""
    if (receipt.entry('publish-runtime') or {}).get('state') == 'done': return
    for p in pk.publications:
        missing = sorted({'%s (%s.tar)' % (k['container'], k['sha256'][:12]) for k in p.get('kept') or []
                          if deps.published_runtime_size(k['sha256']) != k['size']})
        if missing:
            raise Refused('publish-runtime', 'the packet carries no archive for %s and says the core publishes %s already, but this core '
                          'does not have %s. Nothing was published; build the packet with every image in it (isle release build --all-images)'
                          % (', '.join(missing), 'it' if len(missing) == 1 else 'them', 'that file' if len(missing) == 1 else 'those files'))

def run_publish(deps, receipt, e, argv, name, data=None):
    code, _ = deps.run(argv, stream=True)
    if code:
        receipt.end(e, 'failed', '%s failed (exit %d)' % (name, code))
        raise Refused(name, '%s failed (exit %d); a rerun resumes here' % (name, code), state='failed')
    receipt.end(e, 'done', 'published', data=data)

def cmd_run(deps, directory, yes, expect=None, wait=None, submitted_by=None, held=False):
    """wait: minutes to wait for members; 0 = do not wait; None = default. submitted_by: the member whose packet the
    intake runner is running (release_intake.cmd_take), which then already holds the run lock (held)."""
    deps.preload()
    device = deps.device()
    basis = None
    if deps.mode() == 'remote':
        known = published_start_setting(deps)
        if known[0] == 'any-device':
            import release_offer
            return release_offer.cmd_submit(deps, directory, yes, expect, wait)
        basis = start_basis(deps, known)
    why = wrong_role(deps, 'run', 'core', basis)
    if why: deps.say(why); return 1
    if not deps.is_root(): deps.say('This changes the device. Type: sudo isle release run %s' % directory); return 1
    try: pk = load_packet(directory)
    except PacketError as exc: deps.say('REFUSED: check on %s: %s' % (device, exc)); return 1
    if not check_expect(deps, pk, expect, 'run', device): return 1
    for line in summary_lines(pk.record, pk.run_id, pk.publications): deps.say(line)
    for line in star_lines(deps, pk.doc): deps.say(line)
    if not yes and not deps.confirm('Run this release now?'): deps.say('Not confirmed; nothing was changed.'); return 1
    # One run at a time on the core: a run typed here and the intake runner (a member's packet) never overlap.
    lock = None if held else deps.try_lock(Path(deps.runs_dir) / 'run.lock')
    if lock is None and not held:
        deps.say('REFUSED: run on %s: another release run is at work on this core; try again when it has finished (isle release status)' % device); return 1
    try: names = run_core(deps, pk, device, submitted_by)
    finally:
        if lock: lock.close()
    if not isinstance(names, list): return names
    if wait == 0: return 0
    return wait_for_members(deps, pk.run_id, names, deps.wait_minutes if wait is None else wait)

def run_core(deps, pk, device, submitted_by=None):
    """The core's part of one run, under the run lock. -> the approved members (a list), or the exit code of a refusal."""
    receipt = Receipt(deps, pk.run_id, device, pk.record)
    if submitted_by and receipt.data.get('submittedBy') != submitted_by:
        receipt.data['submittedBy'] = submitted_by; receipt.save()
    created = deps.read_json(receipt.dir / 'run.json')['createdAt']
    published, release_id = [], None
    try:
        ensure_approval_key(deps)        # before anything is published: a run that cannot be approved must not start
        resolve_devices(deps, pk.doc, device)
        kept_runtime_here(deps, pk, receipt)             # before anything is published
        src = pk
        if any((receipt.entry(publication_key(p)) or {}).get('state') != 'done' for p in pk.publications):
            src = prepared_copy(deps, pk, receipt)       # publish from a root-owned copy, never from the user's directory
        for p in pk.publications:
            if p['kind'] == 'dependencies':
                e = receipt.begin('publish-dependencies', 'publish-dependencies', 'publish the dependency set of %s (%d package files) from %s'
                                  % (p['system'], len(p['files']), p['dir']))
                if e is not None:
                    run_publish(deps, receipt, e, ['bash', str(deps.scripts / 'apt-repo.sh'), 'publish', '--only-dependencies',
                                                   '--dependencies-from', str(src.directory / p['dir'])], 'publish-dependencies')
                e = receipt.begin('wait-served:dependencies', 'wait-served', 'wait until the dependency set of %s is served on apt.isle' % p['system'])
                if e is not None:
                    try: poll(deps, 'wait-served dependencies', lambda p=p: dependencies_missing(deps, p), deps.wait_limit)
                    except Refused as r:
                        receipt.end(e, r.state, r.why); raise
                    receipt.end(e, 'done', 'deps/%s lists every package file of the set, and every one answers' % p['release'])
            elif p['kind'] == 'runtime':
                e = receipt.begin('publish-runtime', 'publish-runtime', 'publish runtime images from ' + p['manifest'])
                if e is not None:
                    run_publish(deps, receipt, e, ['bash', str(deps.scripts / 'update.sh'), 'publish-runtime', str(src.directory / p['manifest'])], 'publish-runtime')
            elif p['kind'] == 'debs':
                e = receipt.begin('publish-debs', 'publish-debs', 'publish %d package file(s) from %s' % (len(p['files']), p['dir']))
                if e is not None:
                    started = deps.time()
                    run_publish(deps, receipt, e, ['bash', str(deps.scripts / 'apt-repo.sh'), 'publish', '--from', str(src.directory / p['dir'])],
                                'publish-debs', {'startedEpoch': started})
                started = receipt.entry('publish-debs')['data'].get('startedEpoch', 0)
                runtime = next((q for q in pk.publications if q['kind'] == 'runtime'), None)
                e = receipt.begin('release-id', 'release-id', 'read the release id this publication produced')
                if e is None: release_id = receipt.entry('release-id')['data']['releaseId']
                else:
                    try: why, notice = None, deps.read_notice()
                    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc: why = str(exc)
                    why = why or notice_problem(notice, started, runtime['runtimes'] if runtime else None)
                    if why:
                        # Only apt-repo publish rebuilds the notice, so a rerun must publish again (the same files
                        # again are harmless: nothing is copied twice and the release id stays the same).
                        receipt.entry('publish-debs')['state'] = 'failed'
                        receipt.end(e, 'failed', why); raise Refused('release-id', why + '; a rerun publishes the packages again', state='failed')
                    release_id = notice['id']
                    receipt.end(e, 'done', 'release id ' + release_id, data={'releaseId': release_id})
                    deps.say('Release id assigned by publication: ' + release_id)
                e = receipt.begin('wait-served:release', 'wait-served', 'wait until release %s is served on apt.isle' % release_id[:12])
                if e is not None:
                    try: poll(deps, 'wait-served release', lambda: release_missing(deps, release_id), deps.wait_limit)
                    except Refused as r:
                        receipt.end(e, r.state, r.why); raise
                    receipt.end(e, 'done', 'updates names the id; every package file and runtime asset answers')
            else:
                e = receipt.begin('publish:' + p['app'], 'publish', 'publish catalog release %s %s' % (p['app'], p['version']))
                if e is None:
                    published.append(receipt.entry('publish:' + p['app'])['data']); continue
                code, out = deps.run(['bash', str(deps.scripts / 'store.sh'), 'publish', str(src.directory / p['path'])], stream=True)
                try:
                    result = json.loads(next(l for l in reversed(out.splitlines()) if l.startswith('{')))
                    data = {'app': result['name'], 'version': p['version'], 'manifestSha256': result['release']['sha256'], 'url': result['release']['url']}
                    if code or result['name'] != p['app']: raise ValueError
                except (StopIteration, ValueError, KeyError):
                    receipt.end(e, 'failed', 'store publish failed (exit %d)' % code); raise Refused('publish ' + p['app'], 'store publish failed (exit %d)' % code, state='failed')
                receipt.end(e, 'done', 'published manifest ' + data['manifestSha256'][:12], data=data)
                published.append(data)
        for data in published:
            e = receipt.begin('wait-served:' + data['app'], 'wait-served', 'wait until %s is served on apt.isle' % data['app'])
            if e is None: continue
            try: poll(deps, 'wait-served ' + data['app'], lambda d=data: served_missing(deps, d['app'], d['manifestSha256'], d['url']), deps.wait_limit)
            except Refused as r:
                receipt.end(e, r.state, r.why); raise
            receipt.end(e, 'done', 'descriptor and every URL answer')
        run_device_steps(deps, receipt, steps_for(pk.doc, device), published, release_id)
        e = receipt.begin('announce', 'announce', 'sign the approval and write the run record into the core release notice')
        run_json = deps.read_json(receipt.dir / 'run.json') or {}
        if e is None and run_json.get('approval'): e = None
        elif e is None: e = receipt.entry('announce'); e.update(state='running', finishedAt=None)
        if e is not None:
            pubs = [{k: d[k] for k in ('app', 'version', 'manifestSha256', 'url')} for d in published]
            try:
                approval, serial = make_approval(deps, pk.run_id, pubs, resolve_devices(deps, pk.doc, device), run_json.get('approval'), release_id)
            except Refused as r:
                receipt.end(e, 'failed', r.why); raise Refused('approval', r.why, state='failed')
            run_json['approval'] = approval
            atomic(receipt.dir / 'run.json', run_json, 0o644)
            receipt.data['serial'] = serial
            announce = {'runId': pk.run_id, 'name': pk.doc['name'], 'createdAt': created, 'devices': pk.doc['devices'],
                        'verify': pk.doc.get('verify', []), 'publications': pubs, 'record': pk.record, 'approval': approval}
            def written():
                try: deps.write_notice_run(announce)
                except (RuntimeError, OSError, subprocess.SubprocessError) as exc: return [str(exc)]
                return []
            try: poll(deps, 'announce', written, deps.modules_limit)
            except Refused as r:
                receipt.end(e, 'failed', r.why); raise Refused('announce', r.why, state='failed')
            receipt.end(e, 'done', 'approval signed (serial %d); run key written; other notice keys untouched' % serial)
        if not run_verify(deps, receipt, verify_for(pk.doc, device)):
            raise Refused('verify', 'a verify check failed (see receipt)', state='failed')
    except Refused as r:
        return fail(deps, r, device, receipt)      # the packet copy stays: a rerun resumes from it
    receipt.finish('done')
    shutil.rmtree(receipt.dir / 'packet', ignore_errors=True)    # the core's part is finished
    deps.say('Core %s is done (receipt %s).' % (device, receipt.dir / 'receipt.json'))
    names = approved_members(deps.read_json(receipt.dir / 'run.json')['approval'], device)
    for d in names: deps.say('%s follows on its own timer; to do it now type there: sudo isle release follow' % d)
    return names

def trigger_report(deps):
    try: deps.run(['systemctl', 'start', '--no-block', 'isle-resource-report.service'], timeout=30)
    except Exception: pass

def refuse_quietly_once(deps, device, auto, run_id, step, why):
    """A verification-class refusal: nothing ran and no receipt exists. Auto mode says each distinct refusal once."""
    if auto:
        path = Path(deps.runs_dir) / 'refused-run.json'
        mark = {'runId': run_id, 'why': why}
        if deps.read_json(path) == mark: return 0
        Path(deps.runs_dir).mkdir(parents=True, exist_ok=True, mode=0o755)
        atomic(path, mark, 0o600)
    deps.say('REFUSED: %s on %s: %s' % (step, device, why))
    return 1

def cmd_follow(deps, yes, auto=False):
    device = deps.device()
    why = wrong_role(deps, 'follow', 'remote')
    if why:
        if auto: return 0
        deps.say(why); return 1
    if not deps.is_root(): deps.say('This changes the device. Type: sudo isle release follow'); return 1
    lock = deps.try_lock(Path(deps.runs_dir) / 'follow.lock')
    if lock is None:
        if auto: return 0
        deps.say('REFUSED: follow on %s: another release follow is already running on this device; try again when it has finished' % device); return 1
    try: return follow_locked(deps, device, yes, auto)
    finally: lock.close()

def follow_locked(deps, device, yes, auto):
    deps.preload()
    status, body = deps.http(API + '/api/islemesh/updates')
    try: run = json.loads(body)['release'].get('run') if status == 200 else None
    except (ValueError, KeyError, AttributeError, TypeError): run = None
    if not isinstance(run, dict):
        if auto: return 0
        deps.say('REFUSED: follow on %s: the core announces no release run (answered %s)' % (device, status)); return 1
    record, announced_id = run.get('record'), str(run.get('runId'))
    why = record_problem(record) if isinstance(record, dict) else 'run record is missing'
    if why: return refuse_quietly_once(deps, device, auto, announced_id, 'follow', why)
    devices = record['packet']['devices']
    if device not in devices and '*' not in devices:
        if auto: return 0
        deps.say('REFUSED: follow on %s: this release run does not name this device' % device); return 1
    run_id = run_id_of(record)
    if run.get('runId') != run_id:
        return refuse_quietly_once(deps, device, auto, announced_id, 'follow', 'announced run id %s does not match the record (%s)' % (announced_id[:12], run_id[:12]))
    if run.get('devices') != devices or run.get('verify', []) != record['packet'].get('verify', []):
        return refuse_quietly_once(deps, device, auto, run_id, 'follow', 'announced devices/verify differ from the hashed record')
    old = deps.read_json(Path(deps.runs_dir) / run_id / 'receipt.json')
    old = old if old and old.get('device') == device and old.get('runId') == run_id else None
    if old and old.get('state') == 'done':
        if not auto: deps.say('Run %s is already completed on %s; nothing to do.' % (run_id[:12], device))
        return 0
    if auto and old and old.get('halted'): return 0
    try: signed = verify_approval(deps, run, run_id, device)
    except Refused as r: return refuse_quietly_once(deps, device, auto, run_id, r.step, r.why)
    try: Path(deps.runs_dir, 'refused-run.json').unlink()
    except OSError: pass
    if device not in signed['devices']:
        if auto: return 0
        deps.say('REFUSED: follow on %s: the signed approval does not cover this device (it covers: %s)' % (device, ', '.join(signed['devices']) or 'nobody')); return 1
    steps = steps_for(record['packet'], device)
    signed_release = signed.get('releaseId')
    if any(st['verb'] == 'update-apply' and st['releaseId'] == PUBLISHED for st in steps) \
            and not (isinstance(signed_release, str) and HEX64.fullmatch(signed_release)):
        # "published" is resolved ONLY from the signed payload; nothing unsigned may supply the id.
        return refuse_quietly_once(deps, device, auto, run_id, 'approval', 'a step updates to the published release but the signed approval carries no valid release id')
    pubs = [{'kind': 'catalog', 'app': p['app'], 'version': p['version'], 'path': '(core)', 'manifestSha256': p['manifestSha256']}
            for p in signed['publications'] if isinstance(p, dict) and isinstance(p.get('app'), str) and isinstance(p.get('version'), str)
            and HEX64.fullmatch(str(p.get('manifestSha256')))]
    if auto: deps.say('follow: running release run "%s" (%s) on %s' % (record['packet']['name'], run_id[:12], device))
    else:
        for line in summary_lines({'packet': dict(record['packet'], devices={device: steps})}, run_id): deps.say(line)
        if not yes and not deps.confirm('Run these steps on this device now?'): deps.say('Not confirmed; nothing was changed.'); return 1
    attempts = (old or {}).get('autoAttempts', 0)
    receipt = Receipt(deps, run_id, device, record, run.get('createdAt'), signed['serial'])
    def halt():
        receipt.data.update(state='failed', halted=True, needs=NEEDS, finishedAt=deps.now()); receipt.save()
        deps.say('HALTED: run %s on %s after %d automatic attempts; needs: %s' % (run_id[:12], device, AUTO_ATTEMPTS, NEEDS))
    if auto:
        if attempts >= AUTO_ATTEMPTS: halt(); trigger_report(deps); return 1      # earlier attempts died without finishing
        receipt.data['autoAttempts'] = attempts + 1
    else:
        receipt.data['autoAttempts'] = 0; receipt.data.pop('halted', None); receipt.data.pop('needs', None)
    receipt.save()
    try:
        run_device_steps(deps, receipt, steps, pubs, signed_release)
        if not run_verify(deps, receipt, verify_for(record['packet'], device)):
            raise Refused('verify', 'a verify check failed (see receipt)', state='failed')
    except Refused as r:
        code = fail(deps, r, device, receipt)
        if auto and receipt.data['autoAttempts'] >= AUTO_ATTEMPTS: halt()
        trigger_report(deps)
        return code
    receipt.finish('done')
    deps.say('Done on %s (receipt %s).' % (device, receipt.dir / 'receipt.json'))
    trigger_report(deps)
    return 0

def cmd_status(deps, run_id=None):
    root = Path(deps.runs_dir)
    ids = [run_id] if run_id else sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    if run_id and not HEX64.fullmatch(run_id): deps.say('REFUSED: status: invalid run id'); return 1
    if not ids: deps.say('No release runs recorded on this device.')
    for rid in ids:
        r = deps.read_json(root / rid / 'receipt.json')
        if not r: deps.say('Run %s: no receipt on this device' % rid[:12]); continue
        deps.say('Run %s on %s: %s%s%s' % (rid[:12], r['device'], r['state'], ' (HALTED, needs: %s)' % r.get('needs') if r.get('halted') else '',
                                       ', packet offered by %s' % r['submittedBy'] if r.get('submittedBy') else ''))
        for s in r['steps']:
            deps.say('  %-8s %s%s%s' % (s['state'], s['summary'], ' [op %s]' % s['operationId'] if s.get('operationId') else '', ' - ' + s['detail'] if s.get('detail') else ''))
        for v in r.get('verify', []): deps.say('  verify %-7s %s %s' % (v['state'], v['url'], v.get('detail', '')))
    if deps.mode() == 'core':
        try: announced = deps.read_notice().get('run')
        except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc: announced = None; deps.say('Core notice unreadable: %s' % exc)
        if announced:
            deps.say('Announced by the core: "%s" run %s' % (announced.get('name'), str(announced.get('runId'))[:12]))
            for d, steps in sorted(announced.get('devices', {}).items()): deps.say('  %s: %d step(s)' % (d, len(steps)))
            names = approved_members(announced.get('approval'), deps.device())
            states = member_states(deps, str(announced.get('runId')), names) if names else {}
            if states is None: deps.say('Member reports unavailable (cannot read the device list).')
            elif states:
                deps.say('Member reports (advisory, as last reported):'); member_table(deps, states)
        else: deps.say('The core notice carries no release run.')
    return 0

HELP = """isle release - one reviewed release across the isle (slice 2)
  isle release build <out-dir> --previous <dir>          no sudo: build the packet of the next CLI release from one
        commit of the isle repository (`isle release build help` for the rest); nothing is published or installed.
        With --agents the packet also carries the two agent images, with the agent scripts of that commit;
        with --runtime-image <container>=<image>, an image built elsewhere (Polari's backend or frontend);
        with --module-add <device>:<instance>:<module>, a module that device adds to its Polari instance
  isle release build <out-dir> --dependencies <set dir>  no sudo: a packet that holds one dependency set and nothing else
  isle release check <packet-dir> [--expect <run-id>]    read-only: validate the packet, say what would happen
  sudo isle release run <packet-dir> [--yes] [--expect <run-id>] [--wait <minutes> | --no-wait]
        core: copy the packet to a root-owned directory, publish (fixed order: a dependency set, runtime images, packages, catalog apps),
        wait until served, run own steps, sign the approval, announce, then wait (default 30 min) for the approved
        members to report. Exit 0 all done, 1 any failed, 3 some still pending.
  sudo isle release follow [--yes] [--auto]   member: verify the signed approval, then run this device's steps;
        --auto is what isle-release-follow.timer runs (no prompt, quiet when there is nothing to do, halts after 3 failures)
  isle release status [run-id]                per step state and operation id; on the core also the member table
  sudo isle release approval-key              core: mint the approval key if needed, print its certificate details
  isle release setting                        where a run may be started: core-only (the default) or any-device
  sudo isle release setting <core-only|any-device>   core: choose it; members read it from the core.
        core-only: `isle release run` typed on a member refuses and says so.
        any-device: `sudo isle release run <packet-dir>` typed on a member offers the packet from that member; the core
        fetches it, checks the member and the packet again, and runs it. Its cost: root on any paired member can then
        release to the whole isle, and while a packet is offered every device on the isle can read its files.
  isle release offer                          member: the packet this device offers to the core, and the core's answer
  sudo isle release offer withdraw            member: take the offer down and remove its copy of the packet
  isle release intake                         whether the core takes packets from members, and what it answered
  sudo isle release intake run [--auto]       core: look at the members and take one offered packet now; --auto is what
        isle-release-intake.timer runs (on while the setting is any-device; quiet when there is nothing to do)
A packet is SHA256SUMS + release-run.json (docs/release-run.example.json) and may publish:
  {"kind":"runtime","manifest":"<file>"}   backend runtime images (isle update publish-runtime); every archive the manifest
                                           names must be in the packet and in SHA256SUMS; needs a debs entry too
  {"kind":"debs","dir":"<dir>"}            CLI/shell packages (isle apt-repo publish --from); every file in the dir must be a .deb
                                           listed in SHA256SUMS; publishing assigns the release id
  {"kind":"catalog","release":"<file>"}    a store app (isle store publish)
  {"kind":"dependencies","dir":"<dir>"}    one dependency set: operating system packages and one file <system id>.txt that says
                                           the system they are for (isle apt-repo publish --only-dependencies); every file in the
                                           dir must be listed in SHA256SUMS; added first and waited for, and the release the core
                                           announces stays the one it is; needs no debs entry
Device steps: update-apply (releaseId may be "published" = the id this run's publication assigned, taken from the signed
approval on members), store-install, module-add, hotfix (name: an `isle hotfix` fix the device's own CLI must know). At most one runtime, one debs and one dependencies entry."""

def parse_args(args, flags, valued):
    """-> (positionals, {option: value}); raises ValueError on an unknown option or missing value."""
    pos, opts, i = [], {}, 0
    while i < len(args):
        a = args[i]
        if a in flags: opts[a] = True
        elif a in valued:
            i += 1
            if i >= len(args): raise ValueError(a + ' needs a value')
            opts[a] = args[i]
        elif a.startswith('--'): raise ValueError('unknown option ' + a)
        else: pos.append(a)
        i += 1
    return pos, opts

def main(argv, deps=None):
    deps = deps or Deps()
    try:
        cmd = argv[0] if argv else 'help'
        rest = argv[1:]
        if cmd in ('help', '-h', '--help'): deps.say(HELP); return 0
        if cmd == 'build':
            import release_build
            return release_build.main(rest)
        spec = {'check': ((), ('--expect',)), 'run': (('--yes', '--no-wait'), ('--expect', '--wait')),
                'follow': (('--yes', '--auto'), ()), 'status': ((), ()), 'approval-key': ((), ()), 'setting': ((), ()),
                'intake': (('--auto',), ()), 'offer': ((), ())}
        if cmd in spec:
            pos, o = parse_args(rest, *spec[cmd])
            expect = o.get('--expect')
            if expect is not None and not HEX64.fullmatch(expect): deps.say('REFUSED: %s: --expect needs a 64-hex run id' % cmd); return 2
            if cmd == 'check' and len(pos) == 1: return cmd_check(deps, pos[0], expect)
            if cmd == 'run' and len(pos) == 1:
                if '--wait' in o and '--no-wait' in o: raise ValueError('use --wait or --no-wait, not both')
                wait = 0 if '--no-wait' in o else None
                if '--wait' in o:
                    try: wait = float(o['--wait'])
                    except ValueError: wait = -1
                    if not 0 < wait <= 1440: raise ValueError('--wait needs minutes between 0 and 1440')
                return cmd_run(deps, pos[0], '--yes' in o, expect, wait)
            if cmd == 'follow' and not pos: return cmd_follow(deps, '--yes' in o, '--auto' in o)
            if cmd == 'status' and len(pos) <= 1: return cmd_status(deps, pos[0] if pos else None)
            if cmd == 'approval-key' and not pos: return cmd_approval_key(deps)
            if cmd == 'intake':
                import release_intake
                return release_intake.main(deps, pos + (['--auto'] if '--auto' in o else []))
            if cmd == 'offer':
                import release_offer
                return release_offer.main(deps, pos)
            if cmd == 'setting' and not pos: return cmd_setting(deps)
            if cmd == 'setting' and len(pos) == 1:
                if pos[0] not in START_SETTINGS: raise ValueError('the start setting is one of: ' + ', '.join(START_SETTINGS))
                return cmd_setting(deps, pos[0])
    except ValueError as exc:
        deps.say('release: %s' % exc); deps.say(HELP); return 2
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        deps.say('release: ' + str(exc)); return 1
    deps.say(HELP); return 2

if __name__ == '__main__':
    import release_run          # one copy of this module for release_intake and release_offer too (not __main__ beside release_run)
    sys.exit(release_run.main(sys.argv[1:]))
