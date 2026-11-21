#!/usr/bin/env python3
"""release_offer.py - the member half of the start setting any-device: a member offers its packet, the core fetches it.

  member  sudo isle release run <packet-dir>     with the start setting any-device: cmd_submit() checks the packet, copies
                                                 it to a root-owned folder, serves that folder read-only behind this
                                                 member's own agent, and watches what the core does with it
  anyone  isle release offer                     read-only: what this member offers, and what the core answered
  member  sudo isle release offer withdraw       take the offer down and remove the copy

Nothing is sent to the core and no door of the backend is used: the core comes and fetches (release_intake.py).

How the core knows whose offer it reads: the offer is an isle app (`isle app deploy --staged`) under a name derived from
the name the certificate authority knows this member by (offer_domain). The authority issues the certificate of a name
to the member that claimed it and to no other member, and the private key never leaves this device. The core fetches
that name over verified TLS from this member's agent, so a packet it reads there was put there by root on this member.

The app runs the image this device's own agent container runs (it serves files with the same nginx), so nothing is
pulled and no image name is written down here. While a packet is offered, every device on the isle can read its files.
Every side effect goes through OfferIO so tests can substitute it.
"""
import json, os, re, secrets, shutil, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import names
import release_run as rr

PREFIX = 'release-offer-'
SERVICE, PORT = 'offer', 8080
OFFER_FILE, PACKET = 'offer.json', 'packet'
AGENT = re.compile(r'isle-(vlan|remote)-agent\Z')       # the same two names `isle app deploy` asks for
IMAGE_ID = re.compile(r'sha256:[0-9a-f]{64}\Z')
OFFER_ID = re.compile(r'[0-9a-f]{32}\Z')
SEGMENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+~-]{0,199}\Z')
MAX_FILES, MAX_FILE_BYTES, MAX_OFFER_BYTES = 4096, 1 << 40, 1 << 20
FINAL = ('done', 'failed', 'refused')
WAIT_MINUTES = 30
NOTICE_KEY = 'intake'
STATE_WORDS = {'taken': 'is fetching and checking the packet', 'running': 'is running the release',
               'done': 'done', 'failed': 'the run failed', 'refused': 'refused the packet'}
NGINX = """worker_processes 1;
pid /tmp/nginx.pid;
error_log /dev/stderr warn;
events { worker_connections 64; }
http {
    default_type application/octet-stream;
    access_log off;
    client_body_temp_path /tmp/body;
    proxy_temp_path /tmp/proxy;
    fastcgi_temp_path /tmp/fastcgi;
    uwsgi_temp_path /tmp/uwsgi;
    scgi_temp_path /tmp/scgi;
    server {
        listen %d;
        root /offer/www;
        autoindex off;
        location / { limit_except GET { deny all; } try_files $uri =404; }
    }
}
""" % PORT


class OfferFailed(Exception):
    """The offer could not be put up; nothing is offered."""


# ---------------------------------------------------------------- names and shapes (the core uses these too)

def app_name(device):
    """The app name of the offer of one member. Raises ValueError when the member's name cannot carry one."""
    name = PREFIX + str(device)
    if not names.valid_label(device) or not names.valid_label(name):
        raise ValueError('the device name %r is too long or not a name an isle address can carry' % (device,))
    return name

def offer_domain(device): return app_name(device) + '.isle'

def safe_rel(rel):
    """True for a path below the packet that the core is willing to fetch and to write."""
    parts = rel.split('/') if isinstance(rel, str) else []
    return 0 < len(parts) <= 8 and len(rel) <= 400 and all(SEGMENT.fullmatch(p) and p not in ('.', '..') for p in parts)

def offer_problem(doc, device=None):
    """None when doc is an offer (of `device`, when given), else what is wrong with it."""
    if not isinstance(doc, dict) or doc.get('schemaVersion') != 1: return 'it is not an offer this CLI release reads'
    if not isinstance(doc.get('offer'), str) or not OFFER_ID.fullmatch(doc['offer']): return 'it carries no offer id'
    if not isinstance(doc.get('runId'), str) or not rr.HEX64.fullmatch(doc['runId']): return 'it names no run id'
    if device is not None and doc.get('device') != device: return 'it names another device (%r)' % (doc.get('device'),)
    files = doc.get('files')
    if not isinstance(files, dict) or not 0 < len(files) <= MAX_FILES: return 'it does not name the files of a packet'
    for rel, size in files.items():
        if not safe_rel(rel): return 'it names a file the core does not fetch (%r)' % (rel,)
        if type(size) is not int or not 0 <= size <= MAX_FILE_BYTES: return 'it gives no size for %s' % rel
    if 'SHA256SUMS' not in files or 'release-run.json' not in files: return 'it does not name SHA256SUMS and release-run.json'
    return None

def packet_files(pk):
    """{path: size} of every file of a checked packet: what release-run.json names, what SHA256SUMS names, SHA256SUMS."""
    root = Path(pk.directory)
    sums = rr.read_sums(root)
    for rel in sorted(set(sums) - set(pk.files)): rr.hashed_file(root, sums, rel, 'SHA256SUMS')
    return {rel: os.stat(root / rel).st_size for rel in sorted(set(pk.files) | set(sums) | {'SHA256SUMS'})}

def compose(image, home):
    """The compose document of the offer app: one read-only file server, the two folders mounted read-only."""
    return {'services': {SERVICE: {
        'image': image, 'entrypoint': ['nginx', '-c', '/offer/nginx.conf', '-g', 'daemon off;'],
        'restart': 'no', 'read_only': True, 'tmpfs': ['/tmp'], 'security_opt': ['no-new-privileges:true'],
        'volumes': [{'type': 'bind', 'source': str(Path(home) / 'nginx.conf'), 'target': '/offer/nginx.conf', 'read_only': True},
                    {'type': 'bind', 'source': str(Path(home) / 'www'), 'target': '/offer/www', 'read_only': True}]}}}


# ---------------------------------------------------------------- side effects

class OfferIO:
    """Real side effects of an offer. Tests substitute a fake with the same methods."""
    home = Path('/var/lib/isle-mesh/release-offer')
    enrollment_file = Path('/etc/isle-mesh/ca/member-enrollment.json')
    poll_seconds = 10

    def time(self): return time.time()
    def sleep(self, seconds): time.sleep(seconds)
    def free_bytes(self, path): return shutil.disk_usage(path).free

    def enrolled_device(self):
        """The name the certificate authority of the core knows this member by, or None. The credential is not read out."""
        try: device = json.loads(self.enrollment_file.read_text()).get('device')
        except (OSError, ValueError, AttributeError): return None
        return device if names.valid_label(device) else None

    def _docker(self, argv, timeout=60):
        try: p = subprocess.run(['docker'] + argv, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError): return 1, ''
        return p.returncode, p.stdout

    def agent_image(self):
        """The id of the image this device's agent container runs, or None when there is not exactly one agent."""
        code, out = self._docker(['ps', '--format', '{{.Names}}'])
        agents = [n for n in out.split() if AGENT.fullmatch(n)] if code == 0 else []
        if len(agents) != 1: return None
        code, out = self._docker(['inspect', '--format', '{{.Image}}', agents[0]])
        return out.strip() if code == 0 and IMAGE_ID.fullmatch(out.strip()) else None

    def serving(self, name):
        code, out = self._docker(['ps', '--filter', 'label=com.docker.compose.project=isle-' + name, '--format', '{{.ID}}'])
        return code == 0 and bool(out.strip())

    def _script(self, argv, timeout):
        try: p = subprocess.run(['bash'] + argv, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc: return 1, str(exc)
        return p.returncode, (p.stderr.strip() or p.stdout.strip())[-400:]

    def deploy(self, name, domain, compose_file):
        """`isle app deploy --staged`: certificate by enrollment, container behind the agent, no DNS name published."""
        return self._script([str(rr.SCRIPTS / 'app-deploy.sh'), name, '--compose', str(compose_file), '--service', SERVICE,
                             '--port', str(PORT), '--domain', domain, '--health-path', '/' + OFFER_FILE, '--staged'], 900)

    def undeploy(self, name):
        return self._script([str(rr.SCRIPTS / 'app.sh'), 'undeploy', name], 300)


def io_of(deps): return getattr(deps, 'offer_io', None) or OfferIO()


# ---------------------------------------------------------------- the offer on this member

def read_state(io):
    try: state = json.loads((Path(io.home) / 'state.json').read_text())
    except (OSError, ValueError): return None
    ok = isinstance(state, dict) and isinstance(state.get('app'), str) and isinstance(state.get('offer'), str) and isinstance(state.get('runId'), str)
    return state if ok else None

def core_answer(deps, member, offer=None):
    """What the core publishes about this member's offer: (answer or None, why nothing is known or None)."""
    status, body = deps.http(rr.API + '/api/islemesh/updates')
    if status != 200: return None, 'the core did not answer'
    try: answers = (json.loads(body)['release'].get(NOTICE_KEY) or {}).get('answers') or {}
    except (ValueError, KeyError, AttributeError, TypeError): return None, 'the answer of the core could not be read'
    answer = answers.get(member) if isinstance(answers, dict) else None
    if not isinstance(answer, dict) or (offer is not None and answer.get('offer') != offer): return None, None
    return answer, None

def put(deps, io, pk, member):
    """Copy the checked packet to the offer folder and serve it. -> the state record. Raises OfferFailed."""
    home, name = Path(io.home), app_name(member)
    image = io.agent_image()
    if not image: raise OfferFailed('this device does not run exactly one isle agent container, so it cannot serve a packet to the core')
    try: files = packet_files(pk)
    except rr.PacketError as exc: raise OfferFailed(str(exc))
    bad = [rel for rel in files if not safe_rel(rel)]
    if bad or len(files) > MAX_FILES: raise OfferFailed('the packet holds a file the core does not fetch (%s)' % (bad[0] if bad else 'too many files'))
    home.parent.mkdir(parents=True, exist_ok=True)
    need, free = sum(files.values()) + deps.copy_reserve, io.free_bytes(home.parent)
    if free < need: raise OfferFailed('not enough free space on this device to offer the packet: need %d MiB, %d MiB free' % (need >> 20, free >> 20))
    shutil.rmtree(home, ignore_errors=True)
    home.mkdir(mode=0o755); os.chmod(home, 0o755)
    try:
        copy = home / 'www' / PACKET
        for rel in files:
            (copy / rel).parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            shutil.copyfile(Path(pk.directory) / rel, copy / rel)     # the content that was checked, as a plain file
            os.chmod(copy / rel, 0o644)
        for folder, _, _ in os.walk(home / 'www'): os.chmod(folder, 0o755)
        if rr.load_packet(copy).run_id != pk.run_id: raise OfferFailed('the packet changed while it was copied')
        state = {'schemaVersion': 1, 'app': name, 'domain': offer_domain(member), 'device': member, 'offer': secrets.token_hex(16),
                 'runId': pk.run_id, 'name': pk.doc['name'], 'at': deps.now(), 'from': str(pk.directory)}
        offer = {k: state[k] for k in ('schemaVersion', 'offer', 'device', 'runId', 'name', 'at')}
        offer['files'] = files
        rr.atomic(home / 'www' / OFFER_FILE, offer, 0o644)
        (home / 'nginx.conf').write_text(NGINX)
        rr.atomic(home / 'docker-compose.yml', compose(image, home), 0o644)      # JSON is YAML
        rr.atomic(home / 'state.json', state, 0o644)
    except (rr.PacketError, OSError) as exc:
        shutil.rmtree(home, ignore_errors=True)
        raise OfferFailed(str(exc))
    code, words = io.deploy(name, state['domain'], home / 'docker-compose.yml')
    if code:
        io.undeploy(name)
        shutil.rmtree(home, ignore_errors=True)
        raise OfferFailed('the offer could not be served behind this device\'s agent (isle app deploy): %s' % (words or 'no reason given'))
    return state

def withdraw(deps, io):
    """Take the offer down and remove the copy. -> None, or why the app could not be taken down (the copy then stays)."""
    state = read_state(io)
    if state is None:
        shutil.rmtree(io.home, ignore_errors=True); return None
    code, words = io.undeploy(state['app'])
    if code and io.serving(state['app']): return words or 'isle app undeploy failed'
    shutil.rmtree(io.home, ignore_errors=True)
    return None


# ---------------------------------------------------------------- the verbs

def _refuse(deps, step, why):
    deps.say('REFUSED: %s on %s: %s' % (step, deps.device(), why)); return 1

def _indent(text): return '\n'.join('    ' + line for line in str(text).splitlines()[-12:])

def cmd_submit(deps, directory, yes, expect=None, wait=None):
    """`isle release run` typed on a member while the core publishes any-device. Exit 0 the core finished its part,
    1 refused or failed, 3 offered but not finished inside the wait."""
    io, device = io_of(deps), deps.device()
    try: pk = rr.load_packet(directory)
    except rr.PacketError as exc: return _refuse(deps, 'check', exc)
    if not rr.check_expect(deps, pk, expect, 'run', device): return 1
    if not deps.is_root(): deps.say('This offers the packet to the core, which runs it across the isle. Type: sudo isle release run %s' % directory); return 1
    member = io.enrolled_device()
    if not member: return _refuse(deps, 'run', 'the pairing of this device with the core cannot be read, so the core would not know whose packet this is; pair this device again')
    try: app_name(member)
    except ValueError as exc: return _refuse(deps, 'run', '%s; start this run on the core' % exc)
    for line in rr.summary_lines(pk.record, pk.run_id, pk.publications): deps.say(line)
    deps.say('This device is a member (the core knows it as %s). The packet is copied and offered here; the core fetches it, '
             'checks this device and the packet again, and then runs it.' % member)
    if not yes and not deps.confirm('Offer this release to the core now?'): deps.say('Not confirmed; nothing was changed.'); return 1
    state = read_state(io)
    answer = core_answer(deps, member, state['offer'])[0] if state else None
    if state and state['runId'] == pk.run_id and io.serving(state['app']) and (answer or {}).get('state') not in FINAL:
        deps.say('This packet is already offered here (offer %s); watching the core.' % state['offer'][:12])
    else:
        if state:
            why = withdraw(deps, io)
            if why: return _refuse(deps, 'run', 'the earlier offer on this device could not be taken down (%s); nothing new is offered' % why)
        try: state = put(deps, io, pk, member)
        except OfferFailed as exc: return _refuse(deps, 'offer', '%s; nothing runs' % exc)
        deps.say('Offered as %s (offer %s, run id %s). The core looks about once a minute; nothing more is typed here.'
                 % (state['domain'], state['offer'][:12], pk.run_id))
    code = watch(deps, io, member, state['offer'], WAIT_MINUTES if wait is None else wait)
    if code != 3: _take_down(deps, io)
    return code

def _take_down(deps, io):
    why = withdraw(deps, io)
    if why: deps.say('The offer could not be taken down (%s). Take it down with: sudo isle release offer withdraw' % why)
    else: deps.say('The offer was taken down and the copy removed.')

def watch(deps, io, member, offer, minutes):
    """Follow one offer until the core answers finally, or for `minutes` (0 = one look)."""
    deadline, last = io.time() + minutes * 60, None
    while True:
        answer, _ = core_answer(deps, member, offer)
        state = answer.get('state') if answer else None
        if answer and state != last:
            words = answer.get('words') or ''
            deps.say('Core: %s%s' % (STATE_WORDS.get(state, state), ('\n' + _indent(words)) if words else '')); last = state
        if state == 'done':
            deps.say('The core finished its part. Members follow on their timers; this device too. Its own steps: isle release status %s' % answer.get('runId'))
            return 0
        if state in ('failed', 'refused'):
            deps.say('Nothing more happens on its own. After the cause is fixed, the same command offers the packet again.')
            return 1
        if io.time() >= deadline:
            deps.say('The core has not finished with this offer yet. It stays up and the core carries on by itself; look again with: '
                     'isle release offer   (take it down with: sudo isle release offer withdraw)')
            return 3
        io.sleep(io.poll_seconds)

def cmd_show(deps):
    io = io_of(deps)
    state = read_state(io)
    if state is None: deps.say('This device offers no release packet to the core.'); return 0
    deps.say('This device offers packet %s (run id %s) as %s, offer %s, since %s.'
             % (state.get('name') or '-', state['runId'][:12], state.get('domain'), state['offer'][:12], state.get('at')))
    if not io.serving(state['app']): deps.say('Its app is not running: the core cannot fetch it. The same `sudo isle release run` offers it again.')
    answer, why = core_answer(deps, state.get('device'), state['offer'])
    if why: deps.say('What the core made of it is not known here (%s).' % why)
    elif not answer: deps.say('The core has not answered this offer yet.')
    else:
        deps.say('Core: %s (%s)' % (STATE_WORDS.get(answer.get('state'), answer.get('state')), answer.get('at')))
        if answer.get('words'): deps.say(_indent(answer['words']))
    deps.say('Take it down with: sudo isle release offer withdraw')
    return 0

def cmd_withdraw(deps):
    io = io_of(deps)
    why = rr.wrong_role(deps, 'offer withdraw', 'remote')
    if why: deps.say(why); return 1
    if read_state(io) is None and not Path(io.home).exists(): deps.say('This device offers no release packet. Nothing was changed.'); return 0
    if not deps.is_root(): deps.say('This changes the device. Type: sudo isle release offer withdraw'); return 1
    why = withdraw(deps, io)
    if why: return _refuse(deps, 'offer withdraw', why)
    deps.say('The offer was taken down and the copy removed. A run the core already started carries on.')
    return 0

def main(deps, args):
    """`isle release offer [withdraw]`, called by release_run.main."""
    if not args: return cmd_show(deps)
    if args == ['withdraw']: return cmd_withdraw(deps)
    raise ValueError('offer takes: nothing (show), or: withdraw')
