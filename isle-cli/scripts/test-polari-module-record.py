#!/usr/bin/env python3
"""Module changes must reach deployment.json, which reconcile replays; no Docker, sudo or network."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).parent
SCRIPT = HERE / 'polari.sh'
spec = importlib.util.spec_from_file_location('config', HERE / 'polari-component-config.py')
config = importlib.util.module_from_spec(spec); spec.loader.exec_module(config)
spec = importlib.util.spec_from_file_location('reconcile', HERE / 'provider_reconcile.py')
reconcile = importlib.util.module_from_spec(spec); spec.loader.exec_module(reconcile)

MOCK = r'''
sudo(){ case "$*" in *'docker compose '*) printf 'sudo %s\n' "$*" >> "$CALLS" ;; *) "$@" ;; esac; }
require_member(){ :; }
self_report(){ :; }
BASE="$TEST_BASE"
'''


def instance(base, compose_modules, record_modules, role='backend', name='polari-pol'):
    d = base / name; d.mkdir()
    (d / 'docker-compose.yml').write_text(json.dumps({'services': {'backend': {'environment': {'POLARI_MODULES': compose_modules}}}}))
    meta = {'name': name, 'role': role, 'modules': record_modules,
            'backend_url': 'https://api.polari-pol.isle', 'frontend_origin': 'https://electronics.isle'}
    (d / 'deployment.json').write_text(json.dumps(meta, indent=2) + '\n'); os.chmod(d / 'deployment.json', 0o640)
    return d


class ModuleRecord(unittest.TestCase):
    def run_sh(self, base, action):
        calls = base / 'calls'
        r = subprocess.run(['bash', '-c', 'source "$1" help >/dev/null\n' + MOCK + '\n' + action, 'test', str(SCRIPT)],
                           capture_output=True, text=True, env={**os.environ, 'CALLS': str(calls), 'TEST_BASE': str(base)})
        return r, calls.read_text() if calls.exists() else ''

    def test_module_add_records_deployment_and_keeps_other_fields(self):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t); d = instance(base, 'islemesh,board', 'islemesh,board')
            r, calls = self.run_sh(base, 'add_module hal --to polari-pol')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            meta = json.loads((d / 'deployment.json').read_text())
            self.assertEqual(meta['modules'], 'islemesh,board,hal')
            self.assertEqual(meta['frontend_origin'], 'https://electronics.isle')
            self.assertEqual(oct((d / 'deployment.json').stat().st_mode & 0o777), oct(0o640))
            self.assertIn('islemesh,board,hal', (d / 'docker-compose.yml').read_text())
            self.assertIn('docker compose', calls)

    def test_already_present_heals_a_stale_record_without_recreate(self):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t); d = instance(base, 'islemesh,board,hal', 'islemesh,board')
            r, calls = self.run_sh(base, 'add_module hal --to polari-pol')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(json.loads((d / 'deployment.json').read_text())['modules'], 'islemesh,board,hal')
            self.assertNotIn('docker compose', calls)

    def test_reconcile_replays_the_recorded_modules(self):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t); instance(base, 'islemesh,board', 'islemesh,board')
            self.run_sh(base, 'add_module hal --to polari-pol')
            with mock.patch.object(reconcile, 'BASE', base), mock.patch.object(reconcile.subprocess, 'run') as run:
                reconcile.reconcile()
            argv = run.call_args[0][0]
            self.assertEqual(argv[argv.index('--modules') + 1], 'islemesh,board,hal')

    def test_record_refuses_bad_input(self):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t)
            d = instance(base, 'islemesh', 'islemesh')
            with self.assertRaises(ValueError): config.record_modules(d, 'islemesh;rm -rf')
            f = instance(base, '', '', role='frontend', name='front')
            with self.assertRaises(ValueError): config.record_modules(f, 'islemesh')
            (d / 'deployment.json').write_text(json.dumps({'name': 'other', 'role': 'backend', 'modules': 'islemesh'}))
            with self.assertRaises(ValueError): config.record_modules(d, 'islemesh,hal')
            self.assertFalse((d / '.deployment.json.tmp').exists())


if __name__ == '__main__':
    unittest.main()
