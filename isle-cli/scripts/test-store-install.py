#!/usr/bin/env python3
"""Actual store dispatch with isolated catalog/runtime commands."""
import json, os, subprocess, tempfile, unittest
from pathlib import Path
SCRIPT = Path(__file__).with_name('store.sh')
class StoreInstall(unittest.TestCase):
    def invoke(self, steps):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mocks = {'curl': '#!/bin/sh\ncat "$CATALOG"\n',
                     'docker': '#!/bin/sh\necho isle-remote-agent\n',
                     'dpkg-query': '#!/bin/sh\nexit 1\n'}
            for name, code in mocks.items():
                path = root/name; path.write_text(code); path.chmod(0o755)
            catalog = root/'catalog.json'
            catalog.write_text(json.dumps({'ok': True, 'entry': {
                'kind': 'polari-app', 'install_plan': {'steps': steps}}}))
            return subprocess.run(['bash', str(SCRIPT), 'install', 'fixture', '--yes'],
                env={**os.environ, 'PATH': str(root)+':'+os.environ['PATH'],
                     'CATALOG': str(catalog)}, text=True, capture_output=True)
    def test_success(self):
        r = self.invoke(['true']); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('installed fixture', r.stdout)
    def test_failure_stops_following_steps(self):
        r = self.invoke(['true', 'false', 'echo SHOULD_NOT_RUN'])
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn('SHOULD_NOT_RUN\n', r.stdout.split('==> true')[-1])
        self.assertNotIn('installed fixture', r.stdout)
        self.assertIn('installation incomplete', r.stderr)
    def test_invalid_plan_cannot_report_success(self):
        r = self.invoke(None); self.assertNotEqual(r.returncode, 0)
        self.assertNotIn('installed fixture', r.stdout)
if __name__ == '__main__': unittest.main()
