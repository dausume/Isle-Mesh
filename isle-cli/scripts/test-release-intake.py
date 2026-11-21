#!/usr/bin/env python3
"""Unprivileged tests for the start setting any-device: a member offers a packet (release_offer.py), the core fetches
it and runs it (release_intake.py).

The world is the one of test-release-run.py (its part before the first test: fake Deps, packets, a throw-away CA).
Between the two halves stands a fixture isle (Net): what the certificate authority recorded, what the router leased,
and what each member serves. Both halves are tested against each other; no network, docker, systemd or sudo.
"""
import json, os, shutil, sys, time
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
world = {'__file__': str(HERE / 'test-release-run.py'), '__name__': 'release_test_world'}
exec(compile((HERE / 'test-release-run.py').read_text().split('\n@test\n', 1)[0], 'test-release-run.py', 'exec'), world)
rr, ri, ro, Fake, make_packet, write_sums = (world[n] for n in ('rr', 'ri', 'ro', 'Fake', 'make_packet', 'write_sums'))
test, tmp, refused, RESULTS = (world[n] for n in ('test', 'tmp', 'refused', 'RESULTS'))

IMAGE = 'sha256:' + 'c' * 64
NAME1 = 'release-offer-member1.isle'


class Net:
    """One isle between the halves: the authority's member list and claimed names, the router's leases, what is served."""
    def __init__(self):
        self.members = {'member1': 'fingerprint-1', 'member2': 'fingerprint-2'}
        self.claims, self.served, self.fetched = {}, {}, []
        self.leases = [('member1', '10.10.0.11'), ('member2', '10.10.0.12')]

class CoreIO(ri.IntakeIO):
    def __init__(self, root, net):
        self.dir, self.net, self.timers, self.timer_ok, self.clock, self.slept, self.on_fetch = Path(root) / 'core-intake', net, [], True, time.time(), 0, None
        self.asked_router = 0
    def time(self): return self.clock
    def sleep(self, seconds): self.clock += seconds; self.slept += 1
    def members(self): return None if self.net.members is None else dict(self.net.members)
    def claims(self): return None if self.net.claims is None else dict(self.net.claims)
    def leases(self): self.asked_router += 1; return None if self.net.leases is None else list(self.net.leases)
    def lease_of(self, leases, device): return next((a for n, a in leases if n == device), None)
    def timer(self, on): self.timers.append(on); return self.timer_ok
    def fetch(self, domain, address, path, dest, limit, max_time=60):
        self.net.fetched.append((domain, path))
        if self.on_fetch: self.on_fetch(domain, path)
        www, at = self.net.served.get(domain, (None, None))
        if www is None or at != address: return 'could not connect'
        source = Path(www) / path
        if not source.is_file(): return 'The requested URL returned error: 404'
        if source.stat().st_size > limit: return 'Maximum file size exceeded'
        shutil.copyfile(source, dest)
        return None

class MemberIO(ro.OfferIO):
    def __init__(self, root, net, name, address):
        self.home, self.net, self.name, self.address, self.image, self.free = Path(root) / 'offer', net, name, address, IMAGE, 10 ** 12
        self.deploys, self.undeploys, self.deploy_rc, self.undeploy_rc, self.on_sleep, self.clock = [], [], 0, 0, None, time.time()
        self.on_free = None
    def time(self): return self.clock
    def sleep(self, seconds):
        self.clock += seconds
        if self.on_sleep: hook, self.on_sleep = self.on_sleep, None; hook()
    def free_bytes(self, path):
        if self.on_free: self.on_free()
        return self.free
    def enrolled_device(self): return self.name
    def agent_image(self): return self.image
    def serving(self, name): return name + '.isle' in self.net.served
    def deploy(self, name, domain, compose_file):
        self.deploys.append((name, domain, json.loads(Path(compose_file).read_text())))
        if self.deploy_rc: return self.deploy_rc, 'the agent did not answer'
        if self.net.claims.setdefault(domain, self.name) != self.name: return 1, 'service name belongs to another member'
        self.net.served[domain] = (self.home / 'www', self.address)
        return 0, ''
    def undeploy(self, name):
        self.undeploys.append(name)
        if self.undeploy_rc: return self.undeploy_rc, 'the removal check did not answer'
        self.net.served.pop(name + '.isle', None)
        return 0, ''


def isle(t, setting='any-device'):
    """One isle: a core and member1 with their own folders. -> (core, member, net, packet dir on the member)."""
    net = Net()
    core, member = Fake(Path(t) / 'core'), Fake(Path(t) / 'member', 'remote', 'member1')
    member.notice = core.notice                               # members read what the core publishes
    core.intake_io, member.offer_io = CoreIO(Path(t) / 'core', net), MemberIO(Path(t) / 'member', net, 'member1', '10.10.0.11')
    if setting: assert rr.main(['setting', setting], core) == 0, core.said
    core.said, core.events = [], []
    return core, member, net, make_packet(Path(t) / 'member')

def offer(member, d, code=3):
    """The member offers the packet and does not wait: exit 3, the offer stays up."""
    member.said = []
    assert rr.main(['run', str(d), '--yes', '--no-wait'], member) == code, member.said
    return ro.read_state(member.offer_io)

def take(core, *flags):
    core.said = []
    return rr.main(['intake', 'run'] + list(flags), core)

def text(deps): return '\n'.join(str(l) for l in deps.said)
def answer(core, member='member1'): return core.notice.get('intake', {}).get('answers', {}).get(member)
def www(member): return member.offer_io.home / 'www'
def reoffer(member, change):
    """Rewrite the served offer.json as a member with root could."""
    doc = json.loads((www(member) / 'offer.json').read_text()); change(doc)
    (www(member) / 'offer.json').write_text(json.dumps(doc))


@test
def a_member_offers_its_packet_and_the_core_fetches_and_runs_it():
    with tmp() as t:
        core, member, net, d = isle(t)
        run, taken = rr.load_packet(d).run_id, []
        member.offer_io.on_sleep = lambda: taken.append(take(core, '--auto'))     # the core's timer fires while the member watches
        assert rr.main(['run', str(d)], member) == 0, member.said
        assert taken == [0] and member.events == ['confirm'] and not member.argv          # the member changed nothing else on itself
        assert core.events == ['publish', 'install', 'notice'], core.events               # nobody confirms on the core
        receipt = json.loads((core.runs_dir / run / 'receipt.json').read_text())
        assert receipt['state'] == 'done' and receipt['submittedBy'] == 'member1' and core.notice['run']['runId'] == run
        said = text(member)
        assert 'Offered as %s' % NAME1 in said and 'Core: done' in said and 'The core finished its part.' in said, said
        assert 'The offer was taken down and the copy removed.' in said and not member.offer_io.home.exists() and not net.served
        assert net.claims == {NAME1: 'member1'} and answer(core)['state'] == 'done' and answer(core)['runId'] == run
        name, domain, compose = member.offer_io.deploys[0]
        service = compose['services']['offer']
        assert (name, domain) == ('release-offer-member1', NAME1) and service['image'] == IMAGE and service['read_only'] is True
        assert service['restart'] == 'no' and service['entrypoint'][0] == 'nginx' and all(v['read_only'] for v in service['volumes'])
        assert net.fetched[0] == (NAME1, 'offer.json') and {dom for dom, _ in net.fetched} == {NAME1}
        assert {p for _, p in net.fetched[1:]} == {'packet/' + n for n in ('SHA256SUMS', 'release-run.json', 'release.json', 'compose.yml')}
        assert not (core.intake_io.dir / run).exists()                                    # the core keeps no fetched copy
        assert any('Taking packet %s (test run) offered by member1.' % run[:12] in l for l in core.said), core.said
        core.said = []
        assert rr.main(['status', run], core) == 0 and 'packet offered by member1' in text(core), core.said
        for who in (member, core):
            who.said = []
            assert rr.main(['intake'], who) == 0 and 'yes (start setting any-device)' in who.said[0], who.said
            assert any(run[:12] in l and 'from member1' in l and 'done' in l for l in who.said), who.said
        assert take(core) == 0 and core.said == ['No paired member offers a packet that is still to be taken. Nothing was changed.']
        assert core.events == ['publish', 'install', 'notice']

@test
def an_offer_stays_up_is_reused_shown_and_withdrawn():
    with tmp() as t:
        core, member, net, d = isle(t)
        state = offer(member, d)
        assert NAME1 in net.served and 'The core has not finished with this offer yet' in text(member), member.said
        doc = json.loads((www(member) / 'offer.json').read_text())
        assert ro.offer_problem(doc, 'member1') is None and doc['offer'] == state['offer'] and 'from' not in doc, doc
        assert set(doc['files']) == {'SHA256SUMS', 'release-run.json', 'release.json', 'compose.yml'}
        assert offer(member, d)['offer'] == state['offer'] and len(member.offer_io.deploys) == 1          # typed again: the same offer
        assert 'This packet is already offered here' in text(member), member.said
        member.said = []
        assert rr.main(['offer'], member) == 0 and 'The core has not answered this offer yet.' in member.said, member.said
        other = make_packet(Path(t) / 'second', mutate=lambda doc: doc.update(name='another run'))
        newer = offer(member, other)                                                       # another packet replaces the offer
        assert newer['runId'] != state['runId'] and member.offer_io.undeploys == ['release-offer-member1'] and len(member.offer_io.deploys) == 2
        member.root_user, member.said = False, []
        assert rr.main(['offer', 'withdraw'], member) == 1 and member.said == ['This changes the device. Type: sudo isle release offer withdraw']
        member.root_user, member.said = True, []
        assert rr.main(['offer', 'withdraw'], member) == 0 and not net.served and not member.offer_io.home.exists(), member.said
        member.said = []
        assert rr.main(['offer'], member) == 0 and member.said == ['This device offers no release packet to the core.']
        assert rr.main(['offer', 'withdraw'], member) == 0 and 'Nothing was changed.' in member.said[-1]
        assert rr.main(['offer', 'withdraw'], core) == 1 and rr.main(['offer', 'more', 'words'], member) == 2

@test
def a_member_that_cannot_offer_is_refused_and_nothing_stays_behind():
    with tmp() as t:
        def fresh(**kw):
            core, member, net, d = isle(t)
            for k, v in kw.items(): setattr(member.offer_io, k, v)
            return member, net, d
        member, net, d = fresh(name=None)
        offer(member, d, 1); assert 'the pairing of this device with the core cannot be read' in refused(member)[0], member.said
        member, net, d = fresh(name='m' * 55)                                              # no isle name can carry its offer
        offer(member, d, 1); assert 'too long' in refused(member)[0] and 'start this run on the core' in refused(member)[0], member.said
        member, net, d = fresh(image=None)
        offer(member, d, 1); assert 'does not run exactly one isle agent container' in refused(member)[0], member.said
        member, net, d = fresh(free=10)
        offer(member, d, 1); assert 'not enough free space on this device' in refused(member)[0] and not member.offer_io.deploys, member.said
        member, net, d = fresh()                                                           # the packet changes between its check and its copy
        member.offer_io.on_free = lambda: (d / 'compose.yml').write_text((d / 'compose.yml').read_text().upper())
        offer(member, d, 1)
        assert refused(member)[0].startswith('REFUSED: offer on member1') and not member.offer_io.deploys and not member.offer_io.home.exists(), member.said
        member, net, d = fresh(deploy_rc=1)
        offer(member, d, 1)
        assert 'could not be served behind this device\'s agent' in refused(member)[0] and 'the agent did not answer' in refused(member)[0]
        assert member.offer_io.undeploys == ['release-offer-member1'] and not member.offer_io.home.exists() and not net.served
        member, net, d = fresh(); net.claims[NAME1] = 'member2'                            # the authority issued that name to someone else
        offer(member, d, 1); assert 'service name belongs to another member' in refused(member)[0] and not net.served, member.said
        member, net, d = fresh(); member.root_user = False
        offer(member, d, 1); assert member.said[0].startswith('This offers the packet to the core') and not member.offer_io.deploys
        member, net, d = fresh(); (d / 'compose.yml').write_text('changed after the sums were written')
        offer(member, d, 1); assert refused(member)[0].startswith('REFUSED: check on member1') and not member.offer_io.deploys, member.said

@test
def the_core_fetches_only_from_a_name_the_authority_issued_to_that_member():
    with tmp() as t:
        core, member, net, d = isle(t)
        asked = core.intake_io.asked_router                                                # choosing the setting asked once
        assert take(core, '--auto') == 0 and core.intake_io.asked_router == asked          # no offer name is held: the router is left alone
        offer(member, d)
        net.claims[NAME1] = 'member2'                                                      # served by member1, issued to member2
        assert take(core) == 0 and not net.fetched and not core.events and answer(core) is None, core.said
        assert core.intake_io.asked_router == asked
        assert 'member1: the name %s was issued to member2, so nothing is fetched from it.' % NAME1 in core.said, core.said
        net.claims[NAME1] = 'member1'; del net.members['member1']                          # a claim of a device that is no member
        assert take(core) == 0 and not net.fetched and not core.events
        net.members['member1'] = 'fingerprint-1'; net.leases = [('member2', '10.10.0.12')]
        assert take(core) == 0 and not net.fetched and 'member1: the router lists no address for its agent.' in core.said, core.said
        net.leases = None
        assert take(core) == 0 and not net.fetched and 'The router did not tell which addresses it leased; no member could be asked.' in core.said
        net.leases = [('member1', '10.10.0.99')]                                           # another address answers nothing
        assert take(core) == 0 and net.fetched == [(NAME1, 'offer.json')] and not core.events and answer(core) is None

@test
def what_a_member_serves_must_be_an_offer_of_that_member():
    with tmp() as t:
        core, member, net, d = isle(t)
        offer(member, d)
        good = (www(member) / 'offer.json').read_text()
        for change, needle in ((lambda doc: doc.update(device='member2'), 'it names another device'),
                               (lambda doc: doc.update(schemaVersion=2), 'not an offer this CLI release reads'),
                               (lambda doc: doc.update(offer='short'), 'it carries no offer id'),
                               (lambda doc: doc['files'].update({'../../etc/passwd': 3}), 'it names a file the core does not fetch'),
                               (lambda doc: doc['files'].update({'a/./b': 3}), 'it names a file the core does not fetch'),
                               (lambda doc: doc['files'].update({'compose.yml': -1}), 'it gives no size for compose.yml'),
                               (lambda doc: doc['files'].pop('SHA256SUMS'), 'it does not name SHA256SUMS and release-run.json')):
            (www(member) / 'offer.json').write_text(good); reoffer(member, change); net.fetched.clear()
            assert take(core) == 0 and net.fetched == [(NAME1, 'offer.json')] and not core.events and answer(core) is None, core.said
            assert any('is not read as an offer' in l and needle in l for l in core.said), (needle, core.said)
        (www(member) / 'offer.json').write_text('not json')
        assert take(core) == 0 and not core.events and any('is not read as an offer' in l for l in core.said), core.said

@test
def a_packet_that_differs_from_its_offer_is_not_run():
    for change, state, needle in (
            (lambda p: p.write_text(p.read_text() + 'more'), 'failed', 'compose.yml could not be fetched from member1: Maximum file size exceeded'),
            (lambda p: p.write_text(p.read_text()[:-2]), 'refused', 'the fetched files are not the offered ones (compose.yml)'),
            (lambda p: p.write_text(p.read_text().upper()), 'refused', 'the packet does not pass the check on the core'),
            (lambda p: p.unlink(), 'failed', 'compose.yml could not be fetched from member1')):
        with tmp() as t:
            core, member, net, d = isle(t)
            first = offer(member, d)
            change(www(member) / 'packet' / 'compose.yml')
            assert take(core) == 1 and not core.events and answer(core)['state'] == state and needle in answer(core)['words'], (core.said, answer(core))
            assert not (core.intake_io.dir / first['runId']).exists()
            net.fetched.clear()
            assert take(core) == 0 and net.fetched == [(NAME1, 'offer.json')] and not core.events       # a final answer is not taken again
            member.said = []
            assert rr.main(['offer'], member) == 0 and needle.split(':')[0] in text(member), member.said
            assert offer(member, d)['offer'] != first['offer'] and take(core) == 0 and core.events == ['publish', 'install', 'notice']
    with tmp() as t:                                                                       # the offer names another run than the files are
        core, member, net, d = isle(t)
        offer(member, d); reoffer(member, lambda doc: doc.update(runId='f' * 64))
        assert take(core) == 1 and not core.events and answer(core)['state'] == 'refused' and 'not the offered ffffffffffff' in answer(core)['words']

@test
def fetched_files_must_be_plain_files():
    with tmp() as t:
        root = Path(t) / 'p'; (root / 'sub').mkdir(parents=True)
        (root / 'a').write_text('1'); (root / 'sub' / 'b').write_text('22')
        assert ri._local_problem(str(root), {'a': 1, 'sub/b': 2}) is None
        assert 'not the offered ones (sub/b)' in ri._local_problem(str(root), {'a': 1})
        os.link(root / 'a', root / 'twin')
        assert ri._local_problem(str(root), {'a': 1, 'twin': 1, 'sub/b': 2}).endswith('is not a plain file')
        (root / 'twin').unlink(); os.symlink('/etc/hostname', root / 'link')
        assert ri._local_problem(str(root), {'a': 1, 'link': 1, 'sub/b': 2}) == 'link is not a plain file'

@test
def an_interrupted_take_is_tried_again_and_then_given_up():
    class Crash(Exception): pass
    with tmp() as t:
        core, member, net, d = isle(t)
        offer(member, d)
        def crash(domain, path):
            if path == 'packet/release.json': raise Crash()
        core.intake_io.on_fetch = crash
        for attempt in (1, 2, 3):
            try: take(core, '--auto'); assert False, 'the take was not interrupted'
            except Crash: pass
            assert answer(core)['state'] == 'taken' and not core.events
        assert take(core, '--auto') == 1 and answer(core)['state'] == 'failed' and 'interrupted 3 times' in answer(core)['words'], answer(core)
        core.intake_io.on_fetch = None
        assert take(core) == 0 and not core.events                                         # given up means given up, until it is offered again
        assert offer(member, d) and take(core) == 0 and core.events == ['publish', 'install', 'notice']

@test
def the_core_needs_room_and_takes_one_packet_at_a_time():
    with tmp() as t:
        core, member, net, d = isle(t)
        offer(member, d)
        busy = core.try_lock(core.runs_dir / 'run.lock')                                   # a run typed on the core is at work
        assert take(core) == 0 and core.said[-1] == 'A release run is at work on this core; the offer waits until it has finished.'
        assert take(core, '--auto') == 0 and not core.said and answer(core) is None and not core.events
        busy.close()
        hold = core.try_lock(core.intake_io.dir / 'intake.lock')                           # another intake pass is at work
        assert take(core) == 1 and 'the intake is already at work on this core' in core.said[0] and take(core, '--auto') == 0
        hold.close()
        core.free = 10
        assert take(core) == 1 and answer(core)['state'] == 'failed' and 'not enough free space on the core' in answer(core)['words'], core.said
        assert net.fetched and all(p == 'offer.json' for _, p in net.fetched) and not core.events

@test
def two_members_are_taken_in_name_order_one_per_pass():
    with tmp() as t:
        core, member, net, d = isle(t)
        second = Fake(Path(t) / 'member2', 'remote', 'member2'); second.notice = core.notice
        second.offer_io = MemberIO(Path(t) / 'member2', net, 'member2', '10.10.0.12')
        offer(second, d); offer(member, d)
        assert take(core) == 0 and answer(core)['state'] == 'done' and answer(core, 'member2') is None, core.said
        assert take(core) == 0 and answer(core, 'member2')['state'] == 'done', core.said   # the same run again: nothing is done twice
        assert core.events.count('publish') == 1 and core.events.count('install') == 1, core.events

@test
def a_failed_run_is_answered_and_the_member_takes_its_offer_down():
    with tmp() as t:
        core, member, net, d = isle(t)
        core.install_rc = 1
        member.offer_io.on_sleep = lambda: take(core, '--auto')
        assert rr.main(['run', str(d), '--yes'], member) == 1, member.said
        assert answer(core)['state'] == 'failed' and 'REFUSED' in answer(core)['words'], answer(core)
        said = text(member)
        assert 'Core: the run failed' in said and 'REFUSED' in said and 'the same command offers the packet again' in said, said
        assert not net.served and not member.offer_io.home.exists()
        member.offer_io.undeploy_rc = 1; offer(member, d)                                   # an offer that cannot be taken down says how
        member.said = []
        assert rr.main(['offer', 'withdraw'], member) == 1 and 'the removal check did not answer' in refused(member)[0], member.said
        assert member.offer_io.home.exists()

@test
def an_answer_that_cannot_be_published_is_published_by_a_later_pass():
    with tmp() as t:
        core, member, net, d = isle(t)
        offer(member, d)
        core.notice_intake_fails = 10 ** 6
        assert take(core) == 0 and core.events == ['publish', 'install', 'notice'] and answer(core) is None, core.said
        assert core.intake_io.slept == 6 and (core.intake_io.dir / 'unpublished').exists()   # only the final answer is waited for
        assert "The answer 'done' is recorded on the core but could not be published to the members yet." in core.said
        assert ri.read_answers(core.intake_io)['member1']['state'] == 'done'
        member.said = []
        assert rr.main(['offer'], member) == 0 and 'The core has not answered this offer yet.' in member.said
        core.notice_intake_fails = 0
        assert take(core, '--auto') == 0 and answer(core)['state'] == 'done' and not (core.intake_io.dir / 'unpublished').exists()
        assert core.events == ['publish', 'install', 'notice']

@test
def the_core_takes_nothing_unless_its_own_setting_says_any_device():
    with tmp() as t:
        core, member, net, d = isle(t)
        offer(member, d)
        assert rr.main(['setting', 'core-only'], core) == 0 and core.intake_io.timers == [True, False], core.said
        core.events = []
        assert take(core) == 0 and 'core-only: the core takes no packet from a member' in core.said[0] and not net.fetched
        core.notice['startSetting'] = {'start': 'any-device'}                              # the published copy is not what the core goes by
        assert take(core, '--auto') == 0 and not core.said and not net.fetched and not core.events
        member.said = []
        assert rr.main(['intake', 'run'], member) == 1 and 'works on the core only' in member.said[0] and not net.fetched, member.said
        assert rr.main(['intake', 'run', '--auto'], member) == 0 and rr.main(['intake', 'run', 'extra'], core) == 2
        core.setting_file.write_text(json.dumps({'start': 'any-device'})); core.root_user = False
        assert take(core) == 1 and core.said == ['This can start a release run. Type: sudo isle release intake run'] and not net.fetched

@test
def choosing_any_device_switches_the_timer_and_states_its_cost():
    with tmp() as t:
        core, member, net, d = isle(t, setting=None)
        assert rr.main(['setting', 'any-device'], core) == 0, core.said
        said = text(core)
        assert core.intake_io.timers == [True] and 'isle-release-intake.timer is on' in said and ri.COST in said, said
        assert core.events == ['notice-setting'] and json.loads(core.setting_file.read_text())['start'] == 'any-device'
    for broken, needle in (('members', 'the member list of the certificate authority cannot be read'),
                           ('claims', 'the list of claimed service names'), ('leases', 'the router did not tell which addresses it leased')):
        with tmp() as t:
            core, member, net, d = isle(t, setting=None); setattr(net, broken, None)
            assert rr.main(['setting', 'any-device'], core) == 1 and needle in refused(core)[0] and not core.intake_io.timers, core.said
            assert not core.setting_file.exists() and not core.events
    with tmp() as t:
        core, member, net, d = isle(t, setting=None); core.intake_io.timer_ok = False
        assert rr.main(['setting', 'any-device'], core) == 0 and 'could not be switched on' in text(core) and 'sudo isle release intake run' in text(core)

@test
def names_and_paths_have_one_rule_for_both_halves():
    assert ro.app_name('member1') == 'release-offer-member1' and ro.offer_domain('pol-core') == 'release-offer-pol-core.isle'
    for bad in ('', 'Upper', 'a.b', '-a', 'a' * 50, None, 'a/b'):
        try: ro.app_name(bad); assert False, bad
        except ValueError: pass
    assert all(ro.safe_rel(r) for r in ('SHA256SUMS', 'debs/isle-mesh-cli_0.1.210+release90_all.deb', 'a/b/c.tar', 'x~1'))
    assert not any(ro.safe_rel(r) for r in ('', '/abs', '../up', 'a//b', 'a/../b', '.hidden', 'a b', 'a\\b', 'a/' * 9 + 'b', 'x' * 401, 7, None, 'a\n'))

@test
def the_real_io_reads_no_credential_and_asks_only_over_verified_tls():
    with tmp() as t:
        io_ = ro.OfferIO(); io_.enrollment_file = Path(t) / 'enrollment.json'
        assert io_.enrolled_device() is None
        io_.enrollment_file.write_text(json.dumps({'device': 'member1', 'token': 'a-credential', 'endpoint': 'https://ca.isle'}))
        assert io_.enrolled_device() == 'member1'
        io_.enrollment_file.write_text(json.dumps({'device': 'Not A Name', 'token': 'a-credential'}))
        assert io_.enrolled_device() is None
        calls = []
        def docker(outputs):
            def run(argv, **kw):
                calls.append(argv); return SimpleNamespace(returncode=0, stdout=outputs[argv[1]], stderr='')
            return run
        real = ro.subprocess.run
        try:
            ro.subprocess.run = docker({'ps': 'isle-remote-agent\nprf-app\n', 'inspect': IMAGE + '\n'}); assert io_.agent_image() == IMAGE
            ro.subprocess.run = docker({'ps': 'isle-remote-agent\nisle-vlan-agent\n', 'inspect': IMAGE}); assert io_.agent_image() is None
            ro.subprocess.run = docker({'ps': 'my-isle-remote-agent\n', 'inspect': IMAGE}); assert io_.agent_image() is None
            ro.subprocess.run = docker({'ps': 'isle-vlan-agent\n', 'inspect': 'nginx:alpine\n'}); assert io_.agent_image() is None
            calls.clear()
            ro.subprocess.run = lambda argv, **kw: calls.append(argv) or SimpleNamespace(returncode=0, stdout='', stderr='')
            assert io_.deploy('release-offer-member1', NAME1, '/x/docker-compose.yml') == (0, '')
            argv = calls[0]
            assert argv[0] == 'bash' and argv[1].endswith('/app-deploy.sh') and argv[2] == 'release-offer-member1' and '--staged' in argv, argv
            assert argv[argv.index('--health-path') + 1] == '/offer.json' and argv[argv.index('--domain') + 1] == NAME1 and '--bind' not in argv
            calls.clear(); core_io = ri.IntakeIO()
            assert core_io.fetch(NAME1, '10.10.0.11', 'packet/debs/a b.deb', '/x/out', 5, 60) is None
            argv = calls[0]
            assert argv[0] == 'curl' and argv[-1] == 'https://%s/packet/debs/a%%20b.deb' % NAME1 and argv[argv.index('--resolve') + 1] == NAME1 + ':443:10.10.0.11'
            assert argv[argv.index('--cacert') + 1] == '/etc/isle-mesh/ca/isle-root.crt' and argv[argv.index('--proto') + 1] == '=https'
            assert argv[argv.index('--max-redirs') + 1] == '0' and argv[argv.index('--max-filesize') + 1] == '5'
            assert not {'-k', '--insecure', '-L', '--location'} & set(argv)
            calls.clear()
            assert core_io.fetch(NAME1, 'core.isle', 'offer.json', '/x/out', 5) == 'the router gave no usable address' and not calls
        finally: ro.subprocess.run = real


failed = [n for n, e in RESULTS if e]
print('\n%d passed, %d failed' % (len(RESULTS) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
