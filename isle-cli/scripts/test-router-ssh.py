#!/usr/bin/env python3
"""ssh to the isle's router goes only to the router whose key is pinned on this machine, at the address this
machine's own record names.  Nothing here reaches a router: ssh, sudo and curl are stand-ins on PATH."""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import host_network
import router_forwarding
import router_ssh
import router_trust
import storage_core

GATEWAY = '10.55.0.1'
PIN_LINE = '10.55.0.1 ssh-ed25519 (ED25519)'


class Done:
    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class Io(router_ssh.Io):
    def __init__(self, pinned=True, ssh=Done(), root=True, gateway=GATEWAY):
        self.lines, self.calls = [], []
        self.pinned, self.ssh, self.root, self.gateway = pinned, ssh, root, gateway
        self.key, self.known = Path('/k/key'), Path('/k/known_hosts')

    def say(self, line=''): self.lines.append(line)
    def is_root(self): return self.root

    def address(self):
        if self.gateway is None: raise ValueError('not an address')
        return self.gateway

    def run(self, argv, **kwargs):
        self.calls.append(argv)
        if isinstance(self.ssh, Exception) and argv[0] == 'ssh': raise self.ssh
        if argv[0] == 'ssh-keygen':
            return Done(0, '# Host %s found: line 1\n%s\n' % (GATEWAY, '256 SHA256:abcdefg %s' % PIN_LINE)) if self.pinned else Done(1)
        return self.ssh


class Options(unittest.TestCase):
    def test_the_options_accept_the_pinned_key_and_nothing_else(self):
        got = router_ssh.options(GATEWAY)
        pairs = [got[i + 1] for i in range(0, len(got), 2)]
        self.assertEqual(got[0::2], ['-o'] * 4)
        self.assertEqual(pairs, ['StrictHostKeyChecking=yes', 'UserKnownHostsFile=%s' % router_ssh.KNOWN,
                                 'GlobalKnownHostsFile=/dev/null', 'HostKeyAlias=' + GATEWAY])

    def test_one_definition_of_the_key_and_the_pin_file(self):
        self.assertIs(router_forwarding.KNOWN, router_ssh.KNOWN)
        self.assertIs(router_forwarding.KEY, router_ssh.KEY)
        self.assertIs(router_trust.KNOWN, router_ssh.KNOWN)

    def test_the_address_is_the_recorded_one(self):
        with tempfile.TemporaryDirectory() as t:
            config = Path(t) / 'config'
            config.write_text(json.dumps({'managementGateway': '192.168.8.1'}))
            with patch.object(host_network, 'CONFIG', config):
                self.assertEqual(router_ssh.address(), '192.168.8.1')
                self.assertEqual(router_ssh.options()[-1], 'HostKeyAlias=192.168.8.1')
                config.write_text(json.dumps({'managementGateway': 'not an address'}))
                with self.assertRaises(ValueError): router_ssh.address()
                with self.assertRaises(ValueError): router_ssh.options()

    def test_the_lease_lookup_of_the_core_uses_them(self):
        calls = []
        with tempfile.TemporaryDirectory() as t:
            config = Path(t) / 'config'
            config.write_text(json.dumps({'managementGateway': '192.168.8.1'}))
            with patch.object(host_network, 'CONFIG', config), \
                 patch.object(storage_core, '_run', side_effect=lambda a, **k: calls.append(a) or Done(0, '')):
                storage_core.router_leases()
        text = ' '.join(calls[0])
        self.assertIn(' '.join(router_ssh.options('192.168.8.1')), text)
        self.assertIn('BatchMode=yes', text)
        self.assertNotIn('StrictHostKeyChecking=no', text)
        self.assertEqual(calls[0][-2], 'root@192.168.8.1')


class Status(unittest.TestCase):
    def test_pinned_and_shown(self):
        io = Io()
        self.assertEqual(router_ssh.status(io), 0)
        self.assertIn('SHA256:abcdefg', io.lines[0])
        self.assertIn('shows the pinned key', io.lines[-1])
        ssh = io.calls[-1]
        self.assertEqual(ssh[0], 'ssh')
        self.assertIn(' '.join(router_ssh.options(GATEWAY, io.known)), ' '.join(ssh))
        self.assertIn('BatchMode=yes', ssh)
        self.assertEqual(ssh[-2:], ['root@' + GATEWAY, 'true'])

    def test_no_pin_names_the_command_and_does_not_connect(self):
        io = Io(pinned=False)
        self.assertEqual(router_ssh.status(io), 1)
        self.assertIn('No router key is pinned', io.lines[0])
        self.assertIn('sudo isle router trust-key %s ' % GATEWAY, io.lines[1])
        self.assertEqual([c[0] for c in io.calls], ['ssh-keygen'])

    def test_another_key_is_said_as_such(self):
        for words in ('Host key verification failed.', '@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @'):
            io = Io(ssh=Done(255, '', words))
            self.assertEqual(router_ssh.status(io), 1)
            self.assertTrue(any('ANOTHER key' in line for line in io.lines), io.lines)
            self.assertIn('sudo isle router trust-key', io.lines[-1])

    def test_no_answer_is_not_called_another_key(self):
        for ssh in (Done(255, '', 'ssh: connect to host: No route to host'), subprocess.TimeoutExpired('ssh', 20), OSError('no ssh')):
            io = Io(ssh=ssh)
            self.assertEqual(router_ssh.status(io), 1)
            self.assertFalse(any('ANOTHER' in line for line in io.lines), io.lines)
            self.assertIn('not known', io.lines[-1])

    def test_not_root_and_no_address(self):
        io = Io(root=False)
        self.assertEqual(router_ssh.status(io), 1)
        self.assertIn('sudo isle router key-status', io.lines[0])
        self.assertEqual(io.calls, [])
        io = Io(gateway=None)
        self.assertEqual(router_ssh.status(io), 1)
        self.assertEqual(io.calls, [])

    def test_the_words_for_the_shell(self):
        def run(*words, **env):
            return subprocess.run([sys.executable, str(HERE / 'router_ssh.py'), *words], capture_output=True, text=True,
                                  env={**os.environ, **env})
        expected = host_network.management_gateway()
        self.assertEqual(run('address').stdout.strip(), expected)
        self.assertEqual(run('options').stdout.strip(), ' '.join(router_ssh.options(expected)))
        self.assertEqual(run().returncode, 2)
        self.assertEqual(run('trust').returncode, 2)
        if not host_network.CONFIG.exists():        # the record wins over the environment where there is one
            self.assertEqual(run('address', OPENWRT_IP='10.9.8.7').stdout.strip(), '10.9.8.7')
            bad = run('options', OPENWRT_IP='not an address')
            self.assertEqual((bad.returncode, bad.stdout), (1, ''))
            self.assertIn('cannot be read', bad.stderr)

    def test_the_verb_is_dispatched(self):
        text = (HERE / 'router.sh').read_text()
        self.assertRegex(text, r'key-status\)\n\s+exec python3 "\$\(dirname "\$\{BASH_SOURCE\[0\]\}"\)/router_ssh\.py" status ;;')


class Shell(unittest.TestCase):
    """The shell callers, with stand-ins for ssh, sudo and curl that write down how they were called."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bin = Path(self.temp.name) / 'bin'
        self.bin.mkdir()
        self.log = Path(self.temp.name) / 'calls'
        self.log.write_text('')
        for name, body in (('ssh', 'echo "ssh $*" >> "$CALLS"; cat >/dev/null 2>&1 <&0 || true; exit ${SSH_EXIT:-0}'),
                           ('sudo', '[ "$1" = -n ] && shift; exec "$@"'),
                           ('sshpass', 'echo "sshpass" >> "$CALLS"; exit 1'),
                           ('curl', 'echo "curl $*" >> "$CALLS"; exit 22')):
            path = self.bin / name
            path.write_text('#!/bin/bash\n' + body + '\n')
            path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def bash(self, script, **env):
        base = {k: v for k, v in os.environ.items() if k != 'OPENWRT_IP'}
        return subprocess.run(['bash', '-c', script], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              env={**base, 'PATH': '%s:%s' % (self.bin, os.environ['PATH']), 'CALLS': str(self.log), **env})

    def calls(self): return self.log.read_text().splitlines()

    def function(self, name, script):
        text = (HERE / script).read_text()
        found = re.search(r'^%s\(\) \{.*?^\}\n' % name, text, re.S | re.M)
        self.assertIsNotNone(found, script)
        return found.group(0)

    def expected(self, **env):
        done = subprocess.run([sys.executable, str(HERE / 'router_ssh.py'), 'address'], capture_output=True, text=True,
                              env={**{k: v for k, v in os.environ.items() if k != 'OPENWRT_IP'}, **env})
        return done.stdout.strip()

    def test_ssh_router_of_the_security_and_scan_commands(self):
        for script in ('security.sh', 'scan.sh', 'test-remote-reachability.sh'):
            body = self.function('ssh_router', script)
            if script == 'test-remote-reachability.sh': body = self.function('get_router_mgmt_ip', script) + body
            self.log.write_text('')
            done = self.bash('SCRIPT_DIR=%s\n%s\nssh_router uptime' % (HERE, body), OPENWRT_IP='10.9.8.7')
            self.assertEqual(done.returncode, 0, script + done.stderr)
            address = self.expected(OPENWRT_IP='10.9.8.7')
            call = self.calls()[-1]
            self.assertIn(' '.join(router_ssh.options(address)), call, script)
            self.assertTrue(call.endswith('root@%s uptime' % address), (script, call))
            self.assertNotIn('StrictHostKeyChecking=no', call)

    def test_ssh_router_refuses_without_an_address(self):
        if host_network.CONFIG.exists(): self.skipTest('this machine has a record; the environment cannot break it')
        for script in ('security.sh', 'scan.sh'):
            self.log.write_text('')
            done = self.bash('SCRIPT_DIR=%s\n%s\nssh_router uptime; echo "status $?"' % (HERE, self.function('ssh_router', script)),
                             OPENWRT_IP='not an address')
            self.assertIn('status 1', done.stdout, script)
            self.assertEqual(self.calls(), [], script)

    def test_ssh_router_refuses_when_the_options_cannot_be_read(self):
        real = subprocess.run(['bash', '-c', 'command -v python3'], capture_output=True, text=True).stdout.strip()
        path = self.bin / 'python3'
        path.write_text('#!/bin/bash\n[ "$2" = options ] && exit 1\nexec %s "$@"\n' % real)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        for script in ('security.sh', 'scan.sh', 'test-remote-reachability.sh'):
            body = self.function('ssh_router', script)
            if script == 'test-remote-reachability.sh': body = self.function('get_router_mgmt_ip', script) + body
            self.log.write_text('')
            done = self.bash('SCRIPT_DIR=%s\n%s\nssh_router uptime; echo "status $?"' % (HERE, body))
            self.assertIn('status 1', done.stdout, script)
            self.assertEqual(self.calls(), [], script)

    def test_the_dns_command_names_the_pin_when_the_router_refuses(self):
        text = (HERE / 'dns.sh').read_text()
        head = text[text.index('router_pinned() {'):text.index('PROJECT_ROOT=')]
        script = 'SCRIPT_DIR=%s\nROUTER_IP=10.10.0.1\n%s\nrouter_run <<< "uci show"; echo "status $?"' % (HERE, head)
        done = self.bash(script, SSH_EXIT='255')
        self.assertIn('status 255', done.stdout)
        self.assertIn('sudo isle router key-status', done.stderr)
        call = self.calls()[-1]
        self.assertIn(' '.join(router_ssh.options(self.expected())), call)       # named by the management address
        self.assertTrue(call.endswith('root@10.10.0.1'), call)                    # reached by the address it was found at
        done = self.bash(script)
        self.assertIn('status 0', done.stdout)
        self.assertEqual(done.stderr, '')
        self.assertEqual(text.count("router_run << 'EOFSSH'"), 5)
        self.assertNotIn('root@${ROUTER_IP} <<', text)
        # when the options cannot be read, what is used instead still refuses every router
        if not host_network.CONFIG.exists():
            self.log.write_text('')
            self.bash(script, OPENWRT_IP='not an address')
            self.assertIn('StrictHostKeyChecking=yes -o UserKnownHostsFile=/dev/null -o GlobalKnownHostsFile=/dev/null', self.calls()[-1])

    def test_dns_register_reaches_only_the_pinned_router(self):
        run = 'bash %s list' % (HERE / 'dns-register.sh')
        done = self.bash(run, ISLE_DNS_ALLOW_NONROOT='1', SSH_EXIT='255', OPENWRT_IP='10.9.8.7')
        self.assertEqual(done.returncode, 1)
        self.assertIn('sudo isle router key-status', done.stderr)
        address = self.expected(OPENWRT_IP='10.9.8.7')
        ssh = [c for c in self.calls() if c.startswith('ssh ')]
        self.assertTrue(ssh)
        for call in ssh:
            self.assertIn(' '.join(router_ssh.options(address)), call)
            self.assertIn('root@' + address, call)
        if not host_network.CONFIG.exists():
            self.log.write_text('')
            done = self.bash(run, ISLE_DNS_ALLOW_NONROOT='1', OPENWRT_IP='not an address')
            self.assertEqual(done.returncode, 1)
            self.assertEqual(self.calls(), [])

    def test_dns_reconcile_does_nothing_without_an_address(self):
        if host_network.CONFIG.exists(): self.skipTest('this machine has a record; the environment cannot break it')
        done = self.bash('bash %s' % (HERE / 'dns-reconcile.sh'), OPENWRT_IP='not an address')
        self.assertEqual(done.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_dns_reconcile_asks_the_pinned_router(self):
        # the catalogue is not served here (the stand-in curl fails), so the run ends before the router is asked;
        # the line that would ask it is read from the file
        done = self.bash('bash %s' % (HERE / 'dns-reconcile.sh'), OPENWRT_IP='10.9.8.7')
        self.assertEqual(done.returncode, 0)
        self.assertFalse([c for c in self.calls() if c.startswith('ssh ')])
        text = (HERE / 'dns-reconcile.sh').read_text()
        self.assertIn('ROUTER=$(python3 "$HERE/router_ssh.py" address 2>/dev/null) || unread', text)
        self.assertIn('$(python3 "$HERE/router_ssh.py" options 2>/dev/null)" || unread', text)
        self.assertIn('sudo -n ssh $SSH_OPTS "root@$ROUTER"', text)

    def test_dns_reconcile_says_when_the_address_of_the_router_cannot_be_read(self):
        real = subprocess.run(['bash', '-c', 'command -v python3'], capture_output=True, text=True).stdout.strip()
        for word in ('address', 'options'):
            path = self.bin / 'python3'
            path.write_text('#!/bin/bash\n[ "$2" = %s ] && exit 1\nexec %s "$@"\n' % (word, real))
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
            self.log.write_text('')
            done = self.bash('bash %s' % (HERE / 'dns-reconcile.sh'))
            self.assertEqual(done.returncode, 0, word)
            self.assertEqual(self.calls(), [], word)                # nothing was asked of anything
            self.assertIn('the address of the router cannot be read from the record of this machine; no name was reconciled', done.stderr)
            self.assertIn('sudo isle router key-status', done.stderr)


    def test_dns_reconcile_says_when_the_router_does_not_show_the_pinned_key(self):
        path = self.bin / 'curl'
        path.write_text('#!/bin/bash\necho "curl $*" >> "$CALLS"; echo "{}"\n')
        done = self.bash('bash %s' % (HERE / 'dns-reconcile.sh'), SSH_EXIT='255')
        self.assertEqual(done.returncode, 0)                 # the pusher that starts it goes on
        asked = [c for c in self.calls() if c.startswith('ssh ')]
        self.assertEqual(len(asked), 1)
        self.assertIn('StrictHostKeyChecking=yes', asked[0])
        self.assertIn('root@%s cat /tmp/dhcp.leases' % self.expected(), asked[0])
        self.assertIn('did not answer with its pinned key; no name was reconciled (see: sudo isle router key-status)', done.stderr)


if __name__ == '__main__':
    unittest.main()
