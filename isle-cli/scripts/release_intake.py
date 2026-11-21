#!/usr/bin/env python3
"""release_intake.py - the core half of the start setting any-device: the core fetches a packet a member offers, and runs it.

  anyone  isle release intake                     read-only: does the core take packets from members, and what it answered
  core    sudo isle release intake run [--auto]   cmd_take(): look at every paired member, fetch the first offer that has
                                                  no final answer, check it, run it. --auto is what
                                                  isle-release-intake.timer calls (quiet when there is nothing to do)

The member half is release_offer.py: a member serves its packet as an isle app behind its own agent. The core comes and
fetches; the backend takes no part (no door, no route, no stored file).

What the core checks before a member's packet runs (cmd_take), in this order:
  1. the core's own root-owned start setting is any-device (the published copy is never trusted for this);
  2. the member is in the certificate authority's root-owned member list, and the offer name derived from that member's
     name is recorded there as claimed by that same member (claims.json): the authority issues the certificate of a
     name to its claimant and to no other member, and the private key stays on the member;
  3. offer.json and every file are fetched from that name over TLS verified against the isle root alone, from the
     address the router leased to that member's agent; the offer must name that member;
  4. the files, in a root-owned folder, are plain files and exactly the offered ones at their sizes;
  5. the packet passes the full `isle release check` there (every checksum), and its run id is the offered one.
Then the run is `isle release run --yes --no-wait` on that copy: same publication, same signed approval, same follow
by the members. The receipt records which member offered it.

The cost of any-device, stated plainly (COST below is said when the setting is chosen): root on ANY paired member can
then release to the whole isle. The person who reviews the packet is the one typing the command on that member; the
core checks who and what, not whether.

The core's answers are kept in a root-owned file and published to the members in the release notice (key `intake`).
Every side effect goes through IntakeIO so tests can substitute it.
"""
import ipaddress, json, os, shutil, stat, subprocess, sys, tempfile, time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_run as rr
import release_offer as offer_of

TIMER = 'isle-release-intake.timer'
ATTEMPTS = 3                     # an interrupted take is tried again this often, then the offer is answered `failed`
FETCH_RATE = 256 * 1024          # a file may take this long: its size at this many bytes a second, at least a minute
COST = ('With any-device, root on any paired member can release to the whole isle: the core checks which member offers a '
        'packet and that the packet is intact; the review is done by the person typing the command on that member. '
        'While a packet is offered, every device on the isle can read its files from that member.')


class IntakeIO:
    """Real side effects of the intake. Tests substitute a fake with the same methods."""
    dir = Path('/var/lib/isle-mesh/release-intake')
    members_file = Path('/etc/isle-mesh/ca/enrollment/members.json')
    claims_file = Path('/etc/isle-mesh/ca/enrollment/claims.json')
    ca_file = '/etc/isle-mesh/ca/isle-root.crt'
    poll_seconds = 10

    def time(self): return time.time()
    def sleep(self, seconds): time.sleep(seconds)

    def _names(self, path, missing=None):
        try: record = json.loads(Path(path).read_text())
        except FileNotFoundError: return missing
        except (OSError, ValueError): return None
        return {d: v for d, v in record.items() if isinstance(d, str) and isinstance(v, str)} if isinstance(record, dict) else None

    def members(self):
        """The paired members as the certificate authority lists them ({device: ...}), or None when unreadable."""
        return self._names(self.members_file)

    def claims(self):
        """{service name: the member it was issued to} of the certificate authority, or None when unreadable."""
        return self._names(self.claims_file, missing={})

    def leases(self):
        """[(agent host name, isle address)] as the router leased them, or None when the router did not answer."""
        import storage_core
        return storage_core.router_leases()

    def lease_of(self, leases, device):
        import storage_core
        return storage_core.lease_of(leases, device)

    def fetch(self, domain, address, path, dest, limit, max_time=60):
        """Fetch https://<domain>/<path> from `address` into dest, at most `limit` bytes. -> None, or why not.
        Trust is the isle root alone; no redirect is followed; nothing but HTTPS is spoken."""
        try: ipaddress.IPv4Address(address)
        except ValueError: return 'the router gave no usable address'
        argv = ['curl', '--http1.1', '--silent', '--show-error', '--fail', '--proto', '=https', '--max-redirs', '0',
                '--connect-timeout', '8', '--max-time', str(int(max_time)), '--max-filesize', str(int(limit)),
                '--cacert', self.ca_file, '--resolve', '%s:443:%s' % (domain, address), '--output', str(dest),
                'https://%s/%s' % (domain, quote(path, safe='/'))]
        try: p = subprocess.run(argv, capture_output=True, text=True, timeout=max_time + 30)
        except (OSError, subprocess.SubprocessError) as exc: return str(exc)
        return None if p.returncode == 0 else (p.stderr.strip()[-200:] or 'curl exit %d' % p.returncode)

    def timer(self, on):
        argv = ['systemctl', 'enable' if on else 'disable', '--now', TIMER]
        try: return subprocess.run(argv, capture_output=True, text=True, timeout=60).returncode == 0
        except (OSError, subprocess.SubprocessError): return False


def io_of(deps): return getattr(deps, 'intake_io', None) or IntakeIO()


# ---------------------------------------------------------------- the setting (called by release_run.cmd_setting)

def setting_problem(deps):
    """None when this core can take a member's offer, else why any-device cannot be chosen. Asked as root."""
    io = io_of(deps)
    if io.members() is None: return 'the member list of the certificate authority cannot be read on this core'
    if io.claims() is None: return 'the list of claimed service names of the certificate authority cannot be read on this core'
    if io.leases() is None: return 'the router did not tell which addresses it leased, so the core could not reach a member\'s offer'
    return None

def apply_setting(deps, value):
    """After the core recorded its start setting: switch the timer. Says what it did."""
    io = io_of(deps)
    if io.timer(value == 'any-device'):
        deps.say('%s is %s.' % (TIMER, 'on: the core looks at its members about once a minute' if value == 'any-device' else 'off'))
    elif value == 'any-device':
        deps.say('%s could not be switched on; until it is, an offered packet waits for: sudo isle release intake run' % TIMER)
    if value == 'any-device': deps.say(COST)


# ---------------------------------------------------------------- the core's answers

def read_answers(io):
    try: answers = json.loads((Path(io.dir) / 'answers.json').read_text())
    except (OSError, ValueError): return {}
    return {d: a for d, a in answers.items() if isinstance(a, dict)} if isinstance(answers, dict) else {}

def _publish(deps, io, answers):
    """Put the answers into the release notice for the members to read. -> True when written."""
    flag = Path(io.dir) / 'unpublished'
    try: deps.write_notice_intake({'schemaVersion': 1, 'at': deps.now(), 'answers': answers})
    except (RuntimeError, OSError, subprocess.SubprocessError):
        flag.touch(); return False
    flag.unlink(missing_ok=True)
    return True

def _answer(deps, io, answers, member, doc, state, words):
    """Record the core's answer to one offer and publish it. A final answer is tried for a minute: an update the run just
    applied may be restarting the backend. If it still fails, the next pass of the runner publishes it."""
    answers[member] = {'offer': doc['offer'], 'runId': doc['runId'], 'name': str(doc.get('name') or '')[:80], 'state': state,
                       'words': str(words)[-1500:], 'at': deps.now()}
    rr.atomic(Path(io.dir) / 'answers.json', answers, 0o644)
    for last in [False] * (6 if state in offer_of.FINAL else 0) + [True]:
        if _publish(deps, io, answers): return
        if last: deps.say('The answer %r is recorded on the core but could not be published to the members yet.' % state)
        else: io.sleep(io.poll_seconds)


# ---------------------------------------------------------------- the core looks at its members

def look(io, members, claims, answers, told=None):
    """The offers of the paired members that have no final answer yet: [(member, offer, address)], members in name
    order. `told` (a list) receives a line for every member whose offer could not be read for a reason worth saying."""
    def tell(line):
        if told is not None: told.append(line)
    leases, found = None, []
    Path(io.dir).mkdir(parents=True, exist_ok=True)
    for member in sorted(members):
        try: domain = offer_of.offer_domain(member)
        except ValueError: continue
        owner = claims.get(domain)
        if owner is None: continue                            # this member never put an offer up
        if owner != member: tell('%s: the name %s was issued to %s, so nothing is fetched from it.' % (member, domain, owner)); continue
        if leases is None: leases = io.leases()               # the router is asked only when a member holds an offer name
        if leases is None: tell('The router did not tell which addresses it leased; no member could be asked.'); return found
        address = io.lease_of(leases, member)
        if not address: tell('%s: the router lists no address for its agent.' % member); continue
        with tempfile.NamedTemporaryFile(dir=io.dir, prefix='offer-') as sink:
            if io.fetch(domain, address, offer_of.OFFER_FILE, sink.name, offer_of.MAX_OFFER_BYTES, 20): continue     # no offer is up
            try: doc = json.loads(Path(sink.name).read_bytes()[:offer_of.MAX_OFFER_BYTES + 1])
            except ValueError: doc = None
        why = offer_of.offer_problem(doc, member)
        if why: tell('%s: what it serves at %s is not read as an offer: %s.' % (member, domain, why)); continue
        last = answers.get(member) or {}
        if last.get('offer') == doc['offer'] and last.get('state') in offer_of.FINAL: continue
        found.append((member, doc, address))
    return found

def _local_problem(root, sizes):
    """None when root holds exactly the offered plain files at their sizes."""
    found = {}
    for base, dirs, names in os.walk(root):
        for name in dirs + names:
            st = os.lstat(os.path.join(base, name))
            rel = os.path.relpath(os.path.join(base, name), root)
            if stat.S_ISDIR(st.st_mode) and name in dirs: continue
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1: return '%s is not a plain file' % rel
            found[rel] = st.st_size
    if found != sizes:
        odd = sorted(set(found) ^ set(sizes)) or sorted(r for r in sizes if found[r] != sizes[r])
        return 'the fetched files are not the offered ones (%s)' % ', '.join(odd[:3])
    return None

def cmd_take(deps, auto=False):
    """Exit 0: nothing to do, or the packet ran and the core's part is done. 1: refused or failed."""
    io = io_of(deps)
    why = rr.wrong_role(deps, 'intake run', 'core')
    if why:
        if auto: return 0
        deps.say(why); return 1
    if not deps.is_root(): deps.say('This can start a release run. Type: sudo isle release intake run'); return 1
    if rr.core_start_setting(deps)[0] != 'any-device':
        if not auto: deps.say('The start setting of this isle is core-only: the core takes no packet from a member. Nothing was changed.')
        return 0
    lock = deps.try_lock(Path(io.dir) / 'intake.lock')
    if lock is None:
        if auto: return 0
        return _refuse(deps, 'intake run', 'the intake is already at work on this core; see: isle release intake')
    try: return _take_locked(deps, io, auto)
    finally: lock.close()

def _refuse(deps, step, why):
    deps.say('REFUSED: %s on %s: %s' % (step, deps.device(), why)); return 1

def _take_locked(deps, io, auto):
    members, claims = io.members(), io.claims()
    if members is None or claims is None:
        return _refuse(deps, 'intake run', 'the member list or the claimed names of the certificate authority cannot be read; no packet is taken')
    answers = read_answers(io)
    if (Path(io.dir) / 'unpublished').exists(): _publish(deps, io, answers)      # an answer an earlier pass could not publish
    told = []
    offers = look(io, members, claims, answers, told)
    if not auto:
        for line in told: deps.say(line)
    if not offers:
        if not auto: deps.say('No paired member offers a packet that is still to be taken. Nothing was changed.')
        return 0
    busy = deps.try_lock(Path(deps.runs_dir) / 'run.lock')
    if busy is None:
        if not auto: deps.say('A release run is at work on this core; the offer waits until it has finished.')
        return 0
    try: return _take_one(deps, io, answers, *offers[0])
    finally: busy.close()

def _take_one(deps, io, answers, member, doc, address):
    """Under the intake lock and the run lock: fetch one offered packet, check it and run it."""
    run, sizes, domain = doc['runId'], doc['files'], offer_of.offer_domain(member)
    home = Path(io.dir) / run
    home.mkdir(mode=0o700, parents=True, exist_ok=True); os.chmod(home, 0o700)
    try: note = json.loads((home / 'intake.json').read_text())
    except (OSError, ValueError): note = {}
    last = answers.get(member) or {}
    again = last.get('offer') == doc['offer'] and note.get('offer') == doc['offer']       # this offer was being taken before
    attempts = (note.get('attempts') if type(note.get('attempts')) is int else 0) + 1 if again else 1
    rr.atomic(home / 'intake.json', {'runId': run, 'device': member, 'offer': doc['offer'], 'attempts': attempts, 'at': deps.now()}, 0o600)
    def stop(state, words):
        shutil.rmtree(home, ignore_errors=True)
        _answer(deps, io, answers, member, doc, state, words)
        deps.say('%s: packet %s offered by %s: %s' % (state.upper(), run[:12], member, words)); return 1
    if attempts > ATTEMPTS: return stop('failed', 'the core was interrupted %d times while taking this packet; offer it again' % ATTEMPTS)
    need, free = sum(sizes.values()) * 2 + deps.copy_reserve, deps.free_bytes(io.dir)
    if free < need: return stop('failed', 'not enough free space on the core for this packet: need %d MiB, %d MiB free' % (need >> 20, free >> 20))
    _answer(deps, io, answers, member, doc, 'taken', 'the core is fetching the packet from %s and checking it' % member)
    packet = home / 'packet'
    shutil.rmtree(packet, ignore_errors=True)
    packet.mkdir(mode=0o700); os.chmod(packet, 0o700)
    for rel in sorted(sizes):
        (packet / rel).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        why = io.fetch(domain, address, offer_of.PACKET + '/' + rel, packet / rel, sizes[rel], max(60, sizes[rel] // FETCH_RATE))
        if why: return stop('failed', '%s could not be fetched from %s: %s' % (rel, member, why))
    why = _local_problem(str(packet), sizes)
    if why: return stop('refused', why)
    try: pk = rr.load_packet(packet)
    except rr.PacketError as exc: return stop('refused', 'the packet does not pass the check on the core: %s' % exc)
    if pk.run_id != run: return stop('refused', 'the packet on the core has run id %s, not the offered %s' % (pk.run_id[:12], run[:12]))
    _answer(deps, io, answers, member, doc, 'running', 'the core is running the release')
    deps.say('Taking packet %s (%s) offered by %s.' % (run[:12], pk.doc['name'], member))
    said, real, own = [], deps.say, vars(deps).get('say')
    def heard(line=''): said.append(str(line)); real(line)
    deps.say = heard                                          # what the run says is also the answer the member reads
    try: code = rr.cmd_run(deps, str(packet), True, run, 0, submitted_by=member, held=True)
    finally:
        if own is None: del deps.say
        else: deps.say = own
    shutil.rmtree(home, ignore_errors=True)                   # the run keeps its own copy with its receipt until it is done
    if code == 0:
        _answer(deps, io, answers, member, doc, 'done', '\n'.join(said[-6:]))
        return 0
    lines = [l for l in said if l.startswith('REFUSED')] or said[-3:]
    _answer(deps, io, answers, member, doc, 'failed', '\n'.join(lines[-6:]))
    return 1


# ---------------------------------------------------------------- show

def _indent(text): return '\n'.join('    ' + line for line in str(text).splitlines()[-12:])

def cmd_show(deps):
    io, mode = io_of(deps), deps.mode()
    if mode == 'core':
        setting, answers = rr.core_start_setting(deps)[0], read_answers(io)
    else:
        setting, why = rr.published_start_setting(deps)
        if setting is None: deps.say('What the core takes is not known here (%s).' % why); return 1
        status, body = deps.http(rr.API + '/api/islemesh/updates')
        try: answers = (json.loads(body)['release'].get(offer_of.NOTICE_KEY) or {}).get('answers') or {}
        except (ValueError, KeyError, AttributeError, TypeError): answers = {}
    deps.say('The core takes release packets its members offer: %s' % ('yes (start setting any-device)' if setting == 'any-device' else 'no (start setting core-only)'))
    rows = sorted((a.get('at') or '', d, a) for d, a in answers.items() if isinstance(a, dict)) if isinstance(answers, dict) else []
    if not rows: deps.say('It has answered no offer.')
    for _, device, a in rows:
        deps.say('  %s  %-20s from %-16s %s (%s)' % (str(a.get('runId'))[:12], a.get('name') or '-', device,
                                                   offer_of.STATE_WORDS.get(a.get('state'), a.get('state')), a.get('at')))
        if a.get('words'): deps.say(_indent(a['words']))
    return 0


def main(deps, args):
    """`isle release intake [run [--auto]]`, called by release_run.main."""
    if not args: return cmd_show(deps)
    if args[0] == 'run' and args[1:] in ([], ['--auto']): return cmd_take(deps, '--auto' in args)
    raise ValueError('intake takes: nothing (show), or: run [--auto]')
