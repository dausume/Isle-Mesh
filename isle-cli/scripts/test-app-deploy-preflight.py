#!/usr/bin/env python3
"""A missing member certificate must stop before workload mutation."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class AppPreflightTest(unittest.TestCase):
    def test_missing_member_certificate_stops_before_compose(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            calls = root / 'calls'
            compose = root / 'compose.yml'
            compose.write_text('services:\n  board:\n    image: fixture:1\n    expose: [9830]\n')
            for command, body in {
                'docker': 'echo "docker $*" >> "$CALLS"\n[ "$1" = ps ] && echo isle-remote-agent\n',
                'sudo': 'echo "sudo $*" >> "$CALLS"\ncase "$*" in *member_preflight.py*) exit 0;; esac\nexit 1\n',
            }.items():
                binary = root / command
                binary.write_text('#!/bin/sh\n' + body)
                binary.chmod(0o755)
            result = subprocess.run(['bash', str(Path(__file__).with_name('app-deploy.sh')),
                                     'board', '--compose', str(compose), '--health-path', '/capability'],
                                    env={**os.environ, 'PATH': temp + ':' + os.environ['PATH'], 'CALLS': str(calls)},
                                    capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('certificate not ready', result.stdout)
            log = calls.read_text()
            self.assertIn('certs.sh ensure board.isle', log)
            self.assertNotIn('docker compose', log)
            self.assertNotIn('mkdir', log)

    def test_invalid_health_path_is_rejected_before_any_host_action(self):
        for path in ('relative', '/bad path', '/path#fragment'):
            result=subprocess.run(['bash',str(Path(__file__).with_name('app-deploy.sh')),
                                   'fixture','--compose','/not-used','--health-path',path],
                                  capture_output=True,text=True,timeout=5)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('invalid health path',result.stdout)


if __name__ == '__main__':
    unittest.main()
