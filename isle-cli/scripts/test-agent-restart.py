#!/usr/bin/env python3
import copy, json, os, subprocess, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import agent_restart as ar
import operations as op

class Restart(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mode = Path(self.tmp.name) / 'mode'
        self.mode.write_text('remote')
        self.before = {'Id': 'same-container', 'Image': 'same-image',
            'Config': {'Env': ['DEVICE_NAME=econ-core'], 'Healthcheck': {'Test': ['CMD', 'check']}},
            'Mounts': [{'Source': '/certs', 'Destination': '/certs'}],
            'NetworkSettings': {'Networks': {'mesh': {'NetworkID': 'net', 'MacAddress': '02:00:00:00:00:01', 'IPAddress': 'old'}}},
            'State': {'Running': True, 'Health': {'Status': 'healthy'}}}
        self.calls = []
        for p in [patch.object(ar, 'MODE', self.mode), patch('os.geteuid', return_value=0),
                  patch.dict(os.environ, ISLE_OPERATION_ID='a'*32), patch.object(ar, 'stage'),
                  patch.object(ar.subprocess, 'run', side_effect=self.command)]:
            p.start(); self.addCleanup(p.stop)
    def command(self, argv, **kwargs):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps([self.before]))
    def test_core_and_remote_preserve_existing_container(self):
        for mode, name in [('remote', 'isle-remote-agent'), ('core', 'isle-vlan-agent')]:
            self.mode.write_text(mode); self.calls.clear(); ar.restart()
            self.assertIn(['docker', 'restart', '--time', '20', name], self.calls)
            self.assertTrue(all(c[:2] in (['docker','inspect'], ['docker','restart'],
                ['systemctl','restart'], ['systemctl','is-active']) or c == ['bash', str(Path(ar.__file__).with_name('hosts-reconcile.sh'))] for c in self.calls))
            self.assertEqual(self.calls[-1], ['bash', str(Path(ar.__file__).with_name('hosts-reconcile.sh'))])
    def test_host_ingress_failure_is_not_success(self):
        real = self.command
        def fail(argv, **kwargs):
            if argv[0] == 'bash':
                raise subprocess.CalledProcessError(1, argv)
            return real(argv, **kwargs)
        with patch.object(ar.subprocess, 'run', side_effect=fail), self.assertRaises(subprocess.CalledProcessError):
            ar.restart()
        self.assertNotIn(('a'*32, 'agent-ready'), [c.args for c in ar.stage.call_args_list])

    def test_dynamic_lease_allowed_but_network_change_rejected(self):
        after = copy.deepcopy(self.before)
        after['NetworkSettings']['Networks']['mesh']['IPAddress'] = 'new'
        with patch.object(ar, 'inspect', side_effect=[self.before, after]): ar.restart()
        after['NetworkSettings']['Networks']['mesh']['NetworkID'] = 'different'
        with patch.object(ar, 'inspect', side_effect=[self.before, after]), self.assertRaisesRegex(RuntimeError, 'membership changed'):
            ar.restart()
    def test_missing_container_never_creates_or_joins(self):
        with patch.object(ar, 'inspect', side_effect=subprocess.CalledProcessError(1, 'inspect')), self.assertRaisesRegex(RuntimeError, 'missing'):
            ar.restart()
        self.assertEqual(self.calls, [])
    def test_missing_healthcheck_refuses_before_restart(self):
        self.before['Config'].pop('Healthcheck')
        with self.assertRaisesRegex(RuntimeError, 'no health check'): ar.restart()
        self.assertFalse(any(c[:2] == ['docker', 'restart'] for c in self.calls))
    def test_unhealthy_is_failure(self):
        self.before['State']['Health']['Status'] = 'unhealthy'
        with patch.object(ar.time, 'monotonic', side_effect=[0, 181]), self.assertRaisesRegex(RuntimeError, 'did not converge'):
            ar.restart()
    def test_operation_failure_retry_and_success_noop(self):
        root = Path(self.tmp.name)
        (root/'agent.sh').write_text('test "$1 $2" = "restart device" || exit 8\n'
            'if [ ! -e "$ISLE_OPERATION_DIR/once" ]; then touch "$ISLE_OPERATION_DIR/once"; exit 7; fi\n')
        with patch.object(op, 'ROOT', root/'ops'), patch.object(op, 'SCRIPTS', root):
            self.assertEqual(op.request(['agent','restart','device']), ['agent','restart','device'])
            # Use real subprocess boundary for the journal worker.
            with patch.object(op.subprocess, 'run', REAL_RUN):
                self.assertEqual(op.execute(['agent','restart','device']), 7)
                status = op.latest('device-update')
                self.assertEqual(op.execute(ident=status['id']), 0)
                self.assertEqual(op.read(status['id'])['attempt'], 2)
                self.assertEqual(op.execute(ident=status['id']), 0)
                self.assertEqual(op.read(status['id'])['attempt'], 2)

    def test_real_cli_dispatch_and_context(self):
        root = Path(self.tmp.name)
        (root/'agent.sh').write_text(Path(__file__).with_name('agent.sh').read_text())
        (root/'agent_restart.py').write_text('print("restart-worker")\n')
        (root/'operations.py').write_text('import sys; print(" ".join(sys.argv[1:]))\n')
        env = dict(os.environ)
        env.pop('ISLE_OPERATION_ID', None)
        result = REAL_RUN(['bash', str(root/'agent.sh'), 'restart'], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), 'run agent restart device')
        env['ISLE_OPERATION_ID'] = 'a'*32
        for args, expected in [(['restart','device'], 0), (['restart'], 1), (['restart','wrong'], 1)]:
            result = REAL_RUN(['bash', str(root/'agent.sh'), *args], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, expected)
            if expected == 0: self.assertEqual(result.stdout.strip(), 'restart-worker')

REAL_RUN = subprocess.run
if __name__ == '__main__': unittest.main()
