#!/usr/bin/env python3
"""stm-5 ownership surfaces: dns-register.sh fence (C), dns-reconcile / hosts-reconcile / apt-repo / registry push (D),
certificate authority (E).  Everything runs against temp directories with fake ssh / sudo / dig / curl in PATH."""
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import storage_placement as sp

PLAN = dict(id='a' * 32, revision=2, digest='b' * 64)
DOMAIN, DATASET = 'apt.isle', 'apt-public-repo'
TARGET, CORE = '10.10.0.116', '10.10.0.20'
COUNT = [0]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake(directory, name, body):
    path = Path(directory) / name
    path.write_text('#!/bin/bash\n' + body + '\n')
    path.chmod(0o755)
    return path


def entry_at(path, state='migrated', pending=None, dataset=DATASET, domain=DOMAIN):
    """An entry walked to `state`; `pending` ({action,...}) is written with the intent protocol."""
    if Path(path).exists():
        Path(path).unlink()
    sp.authorize(dataset, 'pol-core', domain, '/etc/isle-mesh/apt/repo', PLAN, path=path, operation='setup')
    walk = {'authorized': [], 'migrated': ['migrated'], 'cutover': ['migrated', 'cutover'], 'hosting': ['migrated', 'cutover', 'hosting'],
            'recovery-needed': ['migrated', 'recovery-needed']}[state]
    prev = 'authorized'
    for nxt in walk:
        sp.transition(dataset, prev, nxt, by='t', operation='setup', path=path, bump=True)
        prev = nxt
    if pending:
        sp.write_pending(dataset, pending, expect_generation=sp.hosting_for(dataset, path)['generation'], path=path, by='t')
    return sp.hosting_for(dataset, path)


def pending(op='op1', address=TARGET, action='cutover'):
    return {'action': action, 'digest': PLAN['digest'], 'from': 'migrated', 'to': 'cutover', 'address': address, 'operation': op}


# what a script copied to a folder of its own asks for the router's address and its pinned key
ROUTER_FILES = ('router_ssh.py', 'host_network.py', 'operations.py')


class DnsRegisterFence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.scripts = self.root / 'scripts'
        self.scripts.mkdir()
        for name in ('storage_placement.py', 'storage_snapshot.py', 'repository_lock.py') + ROUTER_FILES:
            shutil.copy(HERE / name, self.scripts / name)
        self.placement = self.root / 'placement.json'
        text = (HERE / 'dns-register.sh').read_text()
        self.assertIn('PLACEMENT="${ISLE_PLACEMENT:-/etc/isle-mesh/storage/placement.json}"', text)
        self.script = self.scripts / 'dns-register.sh'
        self.script.write_text(text)
        self.sshlog = self.root / 'ssh.log'
        self.locklog = self.root / 'lock.log'
        fake(self.bin, 'ssh', '''last="${@: -1}"
if [ "$last" = true ]; then exit 0; fi
if flock -n "%s.lock" true 2>/dev/null; then echo free >> "%s"; else echo held >> "%s"; fi
cat >> "%s"; echo OK''' % (self.placement, self.locklog, self.locklog, self.sshlog))
        self.env = dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH'], ISLE_SSH_KEY=str(self.root / 'nokey'))
        for var in ('ISLE_STORAGE_INTENT', 'ISLE_OPERATION_ID'):
            self.env.pop(var, None)
        self.env['ISLE_PLACEMENT'] = str(self.placement)
        self.env['ISLE_DNS_ALLOW_NONROOT'] = '1'

    def run_script(self, *args, intent=None, op=None):
        env = dict(self.env)
        if intent is not None:
            env['ISLE_STORAGE_INTENT'] = str(intent)
        if op is not None:
            env['ISLE_OPERATION_ID'] = op
        return subprocess.run(['bash', str(self.script), *args], env=env, capture_output=True, text=True)

    def intent_file(self, entry, **changes):
        data = dict(domain=DOMAIN, address=TARGET, operation='op1', pending=entry['pending'])
        data.update(changes)
        path = self.root / 'intent.json'
        path.write_text(json.dumps(data))
        return path

    def wrote(self):
        return self.sshlog.exists() and 'uci set' in self.sshlog.read_text()

    def test_unmanaged_without_placement_file_unchanged(self):
        done = self.run_script('register', 'other', '10.0.0.9')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(self.wrote())
        self.assertIn("name='other.isle'", self.sshlog.read_text())

    def test_unmanaged_name_with_placement_present_takes_no_lock(self):
        entry_at(self.placement, 'migrated', pending())
        done = self.run_script('register', 'other.isle', '10.0.0.9')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(self.wrote())
        self.assertEqual(self.locklog.read_text().split(), ['free'])

    def test_managed_refused_without_intent(self):
        entry_at(self.placement, 'migrated', pending())
        done = self.run_script('register', DOMAIN, TARGET)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('managed name: refused outside a storage intent', done.stderr)
        self.assertFalse(self.wrote())

    def test_managed_refused_when_entry_has_no_pending(self):
        entry = entry_at(self.placement, 'migrated')
        path = self.root / 'i.json'
        path.write_text(json.dumps(dict(domain=DOMAIN, address=TARGET, operation='op1', pending=pending())))
        done = self.run_script('register', DOMAIN, TARGET, intent=path, op='op1')
        self.assertNotEqual(done.returncode, 0)
        self.assertFalse(self.wrote())

    def test_managed_allowed_with_matching_intent_and_lock_held(self):
        entry = entry_at(self.placement, 'migrated', pending())
        done = self.run_script('register', DOMAIN, TARGET, intent=self.intent_file(entry), op='op1')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(self.wrote())
        self.assertIn("ip='%s'" % TARGET, self.sshlog.read_text())
        self.assertEqual(self.locklog.read_text().split(), ['held'], 'placement.lock must be held across the router mutation')

    def test_managed_refused_on_address_operation_or_pending_mismatch(self):
        entry = entry_at(self.placement, 'migrated', pending())
        cases = [('other address', dict(address='10.9.9.9'), 'op1', TARGET),
                 ('other operation env', {}, 'op2', TARGET),
                 ('other address on the command line', {}, 'op1', '10.9.9.9'),
                 ('other domain in the intent', dict(domain='x.isle'), 'op1', TARGET)]
        for label, changes, op, address in cases:
            done = self.run_script('register', DOMAIN, address, intent=self.intent_file(entry, **changes), op=op)
            self.assertNotEqual(done.returncode, 0, label)
        stored = dict(entry['pending'], digest='9' * 64)
        done = self.run_script('register', DOMAIN, TARGET, intent=self.intent_file(entry, pending=stored), op='op1')
        self.assertNotEqual(done.returncode, 0, 'intent pending differs from the stored pending')
        self.assertFalse(self.wrote())

    def test_managed_refused_in_recovery_needed_and_after_clear(self):
        entry = entry_at(self.placement, 'recovery-needed')
        path = self.root / 'i.json'
        path.write_text(json.dumps(dict(domain=DOMAIN, address=TARGET, operation='op1', pending=pending())))
        self.assertNotEqual(self.run_script('register', DOMAIN, TARGET, intent=path, op='op1').returncode, 0)
        self.assertFalse(self.wrote())

    def test_unregister_managed_refused_unmanaged_allowed(self):
        entry = entry_at(self.placement, 'migrated', pending())
        done = self.run_script('unregister', DOMAIN, intent=self.intent_file(entry), op='op1')
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('managed name: refused outside a storage intent', done.stderr)
        done = self.run_script('unregister', 'other')
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_unreadable_placement_fails_closed_for_everything_managed(self):
        self.placement.write_text('{not json')
        done = self.run_script('register', DOMAIN, TARGET)
        self.assertNotEqual(done.returncode, 0)
        self.assertFalse(self.wrote())

    def test_domain_entry_verb_is_read_only(self):
        entry_at(self.placement, 'migrated', pending())
        before = self.placement.read_bytes()
        done = subprocess.run([sys.executable, str(HERE / 'storage_placement.py'), 'domain-entry', '--domain', DOMAIN, '--placement', str(self.placement)],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)['domain'], DOMAIN)
        self.assertEqual(self.placement.read_bytes(), before)
        self.assertEqual(subprocess.run(['bash', '-n', str(HERE / 'dns-register.sh')]).returncode, 0)


class HostsReconcile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        r = self.root = Path(self.tmp.name)
        (r / 'router').mkdir()
        (r / 'agent.mode').write_text('core\n')
        self.hosts = r / 'hosts'
        self.hosts.write_text('127.0.0.1 localhost\n192.0.2.8 example.test\n')
        self.reg = r / 'registry.json'
        self.reg.write_text(json.dumps({'apps': {'a': {'domain': 'whoami.isle'}, 'apt': {'domain': DOMAIN}}}))
        self.placement = r / 'placement.json'
        text = (HERE / 'hosts-reconcile.sh').read_text()
        for old, new in (('/etc/isle-mesh/agent/agent.mode', r / 'agent.mode'), ('/etc/isle-mesh/router', r / 'router'),
                         ('/etc/isle-mesh/agent/registry.json', self.reg), ('/etc/isle-mesh/storage/placement.json', self.placement),
                         ('/etc/hosts', self.hosts)):
            text = text.replace(old, str(new))
        self.script = r / 'reconcile.sh'
        self.script.write_text(text)
        for name, body in {'curl': 'exit 1', 'hostname': 'echo test-core', 'sudo': '[[ "$1" != -n ]] || shift; exec "$@"'}.items():
            fake(r, name, body)
        self.env = dict(os.environ, PATH=str(r) + ':' + os.environ['PATH'])

    def run_script(self, *args):
        return subprocess.run(['bash', str(self.script), *args], env=self.env, capture_output=True, text=True)

    def block(self):
        return self.hosts.read_text()

    def test_authorized_and_migrated_keep_the_core_pin(self):
        for state in ('authorized', 'migrated'):
            self.hosts.write_text('127.0.0.1 localhost\n')
            entry_at(self.placement, state)
            self.assertEqual(self.run_script().returncode, 0)
            self.assertRegex(self.block(), r'127\.0\.0\.1 [^\n]*apt\.isle', state)

    def test_no_placement_unchanged_behaviour(self):
        self.assertEqual(self.run_script().returncode, 0)
        self.assertRegex(self.block(), r'127\.0\.0\.1 api\.polari\.isle apt\.isle polari\.isle whoami\.isle')

    def test_cutover_and_hosting_leave_no_core_pin(self):
        for state in ('cutover', 'hosting'):
            self.hosts.write_text('127.0.0.1 localhost\n')
            entry_at(self.placement, state)
            self.assertEqual(self.run_script().returncode, 0)
            self.assertNotIn('apt.isle', self.block(), state)
            self.assertIn('whoami.isle', self.block())

    def test_pending_and_recovery_needed_write_nothing(self):
        self.run_script()
        before = self.block()
        entry_at(self.placement, 'migrated', pending())
        done = self.run_script()
        self.assertEqual(done.returncode, 0)
        self.assertEqual(self.block(), before)
        self.assertIn('pending', done.stdout)
        entry_at(self.placement, 'recovery-needed')
        self.run_script()
        self.assertEqual(self.block(), before)

    def test_unreadable_placement_preserves_hosts(self):
        self.run_script()
        before = self.block()
        self.placement.write_text('{broken')
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertEqual(self.block(), before)

    def test_pin_and_pin_of(self):
        self.run_script()
        self.assertEqual(self.run_script('pin-of', DOMAIN).stdout.strip(), '127.0.0.1')
        self.assertEqual(self.run_script('pin', DOMAIN, 'none').returncode, 0)
        self.assertNotIn('apt.isle', self.block())
        self.assertIn('whoami.isle', self.block())
        self.assertIn('192.0.2.8 example.test', self.block())
        self.assertEqual(self.run_script('pin-of', DOMAIN).stdout.strip(), '')
        self.assertEqual(self.run_script('pin', DOMAIN, '127.0.0.1').returncode, 0)
        self.assertEqual(self.run_script('pin-of', DOMAIN).stdout.strip(), '127.0.0.1')
        self.assertEqual(self.run_script('pin', DOMAIN, '10.1.2.3').returncode, 0)
        self.assertEqual(self.run_script('pin-of', DOMAIN).stdout.strip(), '10.1.2.3')
        self.assertIn('127.0.0.1 localhost', self.block())

    def test_pin_rejects_bad_input(self):
        before = self.block()
        for args in (('pin', 'bad name', 'none'), ('pin', DOMAIN, 'x;rm'), ('pin', 'evil.test', 'none'), ('pin',)):
            self.assertNotEqual(self.run_script(*args).returncode, 0, args)
        self.assertEqual(self.block(), before)


class AptRepoAndPush(unittest.TestCase):
    def test_apt_repo_has_no_unmanaged_hosts_line(self):
        text = (HERE / 'apt-repo.sh').read_text()
        self.assertNotIn('tee -a /etc/hosts', text)
        self.assertNotIn('echo "127.0.0.1 apt.isle"', text)
        self.assertIn('hosts-reconcile.sh', text)
        self.assertEqual(subprocess.run(['bash', '-n', str(HERE / 'apt-repo.sh')]).returncode, 0)

    def push_body(self):
        text = (HERE.parent.parent / 'polari-isle' / 'push-to-polari.sh').read_text()
        marker = '# Storage contract section 5'
        part = text[text.index(marker):]
        body = part.split('<< "PYEOF"', 1)[1].split('\n', 1)[1].split('\nPYEOF', 1)[0]
        return body

    def run_push(self, tmp, placement_text, registry):
        reg = Path(tmp) / 'registry.json'
        reg.write_text(json.dumps(registry))
        body = self.push_body().replace('/etc/isle-mesh/agent/registry.json', str(reg))
        argv = [sys.executable, '-c', body, 'isle-core', str(Path(tmp) / 'placement.json')]
        if placement_text is not None:
            (Path(tmp) / 'placement.json').write_text(placement_text)
        before = reg.read_bytes()
        done = subprocess.run(argv, capture_output=True, text=True)
        self.assertEqual(reg.read_bytes(), before, 'the local registry.json must stay untouched')
        return done

    REGISTRY = {'apps': {'apt': {'domain': DOMAIN, 'services': []}, 'who': {'domain': 'whoami.isle'}}, 'domains': {}}

    def test_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'placement.json'
            for state, omitted in (('authorized', False), ('migrated', False), ('cutover', True), ('hosting', True)):
                entry_at(path, state)
                done = self.run_push(tmp, path.read_text(), self.REGISTRY)
                self.assertEqual(done.returncode, 0, done.stderr)
                apps = json.loads(done.stdout)['registry']['apps']
                self.assertEqual('apt' not in apps, omitted, state)
                self.assertIn('who', apps)
            path.unlink()
            done = self.run_push(tmp, None, self.REGISTRY)
            self.assertEqual(set(json.loads(done.stdout)['registry']['apps']), {'apt', 'who'})
            done = self.run_push(tmp, '{broken', self.REGISTRY)
            self.assertNotEqual(done.returncode, 0)
            self.assertEqual(done.stdout, '')

    def test_push_script_syntax_and_wiring(self):
        script = HERE.parent.parent / 'polari-isle' / 'push-to-polari.sh'
        self.assertEqual(subprocess.run(['bash', '-n', str(script)]).returncode, 0)
        self.assertIn('/etc/isle-mesh/storage/placement.json', script.read_text())


class DnsReconcile(unittest.TestCase):
    CATALOG = {'entries': [{'instances': [{'device': 'aaa-remote', 'domain': DOMAIN}, {'device': 'zzz-remote', 'domain': DOMAIN},
                                           {'device': 'remote-one', 'domain': 'remote.isle'}]}]}
    LEASES = ('9999999999 02:00:00:00:0a:0e 10.10.0.116 pol-core *\n'
              '9999999999 02:00:00:00:0a:0f 10.10.0.150 remote-one *\n'
              '9999999999 02:00:00:00:0a:10 10.10.0.151 aaa-remote *\n')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        r = self.root = Path(self.tmp.name)
        self.placement = r / 'placement.json'
        self.log = r / 'isle.log'
        text = (HERE / 'dns-reconcile.sh').read_text()
        text = text.replace('/usr/local/bin/isle', str(r / 'isle'))
        self.script = r / 'dns-reconcile.sh'
        self.script.write_text(text)
        for name in ROUTER_FILES: shutil.copy(HERE / name, r / name)
        shutil.copytree(HERE / 'lib', r / 'lib')
        (r / 'agent-address.sh').write_text('isle_agent_address(){ echo %s; }\n' % CORE)
        fake(r, 'isle', 'echo "$@" >> "%s"' % self.log)
        fake(r, 'curl', 'echo \'%s\'' % json.dumps(self.CATALOG))
        fake(r, 'sudo', '[[ "$1" != -n ]] || shift; exec "$@"')
        fake(r, 'ssh', 'printf %%s "%s"' % self.LEASES.replace('\n', '\\n'))
        fake(r, 'hostname', 'echo isle-core')
        fake(r, 'dig', 'echo "$DIG_ANSWER"')

    def run_script(self, answer):
        env = dict(os.environ, PATH=str(self.root) + ':' + os.environ['PATH'], PLACEMENT_FILE=str(self.placement), DIG_ANSWER=answer)
        return subprocess.run(['bash', str(self.script)], env=env, capture_output=True, text=True)

    def isle_calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_cutover_drift_goes_through_reconcile_address_never_register(self):
        entry = entry_at(self.placement, 'cutover')
        done = self.run_script(CORE)       # the router still answers the core
        self.assertEqual(done.returncode, 0, done.stderr)
        calls = self.isle_calls()
        self.assertEqual(calls, ['storage reconcile-address --dataset %s --generation %d' % (DATASET, entry['generation'])])
        self.assertFalse(any('dns register' in c for c in calls))

    def test_no_drift_no_call(self):
        entry_at(self.placement, 'hosting')
        self.assertEqual(self.run_script(TARGET).returncode, 0)
        self.assertEqual(self.isle_calls(), [])

    def test_pending_and_recovery_needed_write_nothing(self):
        entry_at(self.placement, 'cutover', pending(address=TARGET))
        self.run_script(CORE)
        entry_at(self.placement, 'recovery-needed')
        self.run_script(CORE)
        self.assertEqual(self.isle_calls(), [])

    def test_migrated_drift_is_reported_not_repaired(self):
        entry_at(self.placement, 'migrated')
        done = self.run_script(TARGET)
        self.assertEqual(self.isle_calls(), [])
        self.assertIn('not repaired automatically', done.stdout)

    def test_first_sorted_instance_rule_skips_managed_but_not_others(self):
        entry_at(self.placement, 'cutover')
        env = dict(os.environ, CAT=json.dumps(self.CATALOG), LEASES=self.LEASES, HOSTN='isle-core', PLACEMENT_FILE=str(self.placement))
        text = (HERE / 'dns-reconcile.sh').read_text()
        body = text.split("python3 - <<'PYEOF'\n", 1)[1].split("\nPYEOF", 1)[0]
        out = subprocess.run([sys.executable, '-c', body], env=env, capture_output=True, text=True, check=True).stdout.split('\n')
        self.assertNotIn('apt.isle 10.10.0.151', out)
        self.assertIn('remote.isle 10.10.0.150', out)
        os.unlink(self.placement)
        out = subprocess.run([sys.executable, '-c', body], env=env, capture_output=True, text=True, check=True).stdout.split('\n')
        self.assertIn('apt.isle 10.10.0.151', out)     # unmanaged: today's rule unchanged

    def test_static_shape(self):
        text = (HERE / 'dns-reconcile.sh').read_text()
        self.assertIn('dig +short', text)
        self.assertIn('reconcile-address', text)
        self.assertNotRegex(text.split('PYMANAGED\n)')[1].split('[ -n "$PAIRS" ]')[0], r'dns register')
        self.assertEqual(subprocess.run(['bash', '-n', str(HERE / 'dns-reconcile.sh')]).returncode, 0)


base = load('fixture_ownership', 'test-certs-enroll.py')
authority = load('authority_ownership', 'cert-authority.py')


class AuthorityPlacement(unittest.TestCase):
    setUpClass = classmethod(base.EnrollmentTest.setUpClass.__func__)
    tearDownClass = classmethod(base.EnrollmentTest.tearDownClass.__func__)

    def setUp(self):
        base.EnrollmentTest.setUp(self)
        self.authority = authority.Authority(self.core.state)
        self.tokens = {}
        for name in ('pol-core', 'other'):
            out = self.work / ('cred-' + name)
            self.authority.authorize(name, out)
            self.tokens[name] = json.loads(out.read_text())['token']
        self.member.request(DOMAIN, self.csr)
        self.storage = self.core.state / 'storage'
        self.storage.mkdir(parents=True, exist_ok=True)

    def place(self, state, device='pol-core', domain=DOMAIN):
        (self.storage / 'placement-claims.json').write_text(json.dumps({domain: dict(device=device, dataset=DATASET, state=state)}))

    def issue(self, device='pol-core', domain=DOMAIN):
        return self.authority.issue(device, self.tokens[device], domain, self.csr.read_text())

    def test_member_refused_without_placement(self):
        with self.assertRaisesRegex(PermissionError, 'belongs to the core'):
            self.issue()

    def test_allowed_for_the_named_device_in_each_issuing_state(self):
        for state in ('authorized', 'migrated', 'cutover', 'hosting'):
            self.place(state)
            self.assertIn('CERTIFICATE', self.issue(), state)
        claims = self.authority.read(self.authority.claims)
        self.assertNotIn(DOMAIN, claims, 'claims.json stays untouched by a placement grant (the core keeps renewing its own cert)')

    def test_refused_for_another_device(self):
        self.place('migrated')
        with self.assertRaisesRegex(PermissionError, 'placed on another device'):
            self.issue('other')

    def test_recovery_needed_issues_nothing(self):
        self.place('recovery-needed')
        for device in ('pol-core', 'other'):
            with self.assertRaises(PermissionError):
                self.issue(device)

    def test_other_core_names_still_refused_with_an_entry_for_a_different_domain(self):
        self.place('migrated')
        with self.assertRaisesRegex(PermissionError, 'belongs to the core'):
            self.authority.issue('pol-core', self.tokens['pol-core'], 'auth.polari.isle', self.csr.read_text())

    def test_core_registry_domain_can_be_granted_and_core_cert_stays(self):
        self.core.certs.mkdir(parents=True, exist_ok=True)
        (self.core.certs / (DOMAIN + '.crt')).write_text('core cert')
        with self.assertRaises(PermissionError):
            self.issue()
        self.place('cutover')
        self.assertIn('CERTIFICATE', self.issue())
        self.assertEqual((self.core.certs / (DOMAIN + '.crt')).read_text(), 'core cert')

    def test_unreadable_claims_file_grants_nothing(self):
        (self.storage / 'placement-claims.json').write_text('{bad')
        with self.assertRaises(PermissionError):
            self.issue()

    def test_core_certs_sh_claim_check_unchanged_for_the_core(self):
        """The core's own issue path (certs.sh PYCLAIM) refuses only a name claims.json gives to a member."""
        text = (HERE / 'certs.sh').read_text()
        body = text.split("<<'PYCLAIM' || return 1\n", 1)[1].split('\nPYCLAIM', 1)[0]
        claims = self.authority.directory / 'claims.json'
        body = body.replace('/etc/isle-mesh/ca/enrollment/claims.json', str(claims))
        self.place('hosting')
        self.issue()                                  # placement grant to the member
        done = subprocess.run([sys.executable, '-c', body, DOMAIN], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        claims.write_text(json.dumps({'board.isle': 'pol-core'}))
        done = subprocess.run([sys.executable, '-c', body, 'board.isle'], capture_output=True, text=True)
        self.assertNotEqual(done.returncode, 0)


if __name__ == '__main__':
    result = unittest.main(exit=False, verbosity=1).result
    print('%d tests, %d failure(s), %d error(s)' % (result.testsRun, len(result.failures), len(result.errors)))
    sys.exit(0 if result.wasSuccessful() else 1)
