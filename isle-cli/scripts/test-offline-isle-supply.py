#!/usr/bin/env python3
"""Pinned Isle registry acquisition with no real Docker/network access."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('offline-runtime.sh')
PIN = 'registry.isle:5000/engines/board@sha256:' + 'a' * 64
MOCK = r'''
isle_offline_image(){ echo "medium $*" >> "$CALLS"; [[ "$CASE" == bundled ]]; }
curl(){ echo "curl $*" >> "$CALLS"; [[ "$CASE" != wrong_ca ]]; }
docker(){
    echo "docker $*" >> "$CALLS"
    if [ "$1" = pull ]; then
        [[ "$CASE" != pull_failed ]] || return 1
        touch "$PULLED"; return 0
    fi
    [[ "$CASE" == cached || -f "$PULLED" ]] || return 1
    if [[ "$CASE" == wrong_digest ]]; then echo '[]'; else printf '["%s"]\n' "$REF"; fi
}
'''


class OfflineIsleSupplyTest(unittest.TestCase):
    def run_case(self, case, ref=PIN):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / 'calls'
            result = subprocess.run(['bash', '-c', 'source "$1"\n' + MOCK + '\nisle_prepare_offline_app_image "$REF"',
                                     'test', str(SCRIPT)], capture_output=True, text=True,
                                    env={**os.environ, 'CASE': case, 'REF': ref, 'CALLS': str(log),
                                         'PULLED': str(Path(temp) / 'pulled')})
            return result, log.read_text() if log.exists() else ''

    def test_bundled_image_keeps_existing_medium_path(self):
        result, calls = self.run_case('bundled', 'fixture:1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, 'medium fixture:1\n')

    def test_cached_exact_digest_needs_no_network(self):
        result, calls = self.run_case('cached')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('curl', calls)
        self.assertNotIn('docker pull', calls)

    def test_missing_digest_uses_only_trusted_isle_registry(self):
        result, calls = self.run_case('missing')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--cacert /etc/isle-mesh/ca/isle-root.crt', calls)
        self.assertIn('https://registry.isle:5000/v2/', calls)
        self.assertIn('docker pull ' + PIN, calls)
        self.assertNotIn('medium ', calls)

    def test_wrong_ca_refuses_before_pull(self):
        result, calls = self.run_case('wrong_ca')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('docker pull', calls)

    def test_failed_pull_or_wrong_digest_is_failure(self):
        for case in ('pull_failed', 'wrong_digest'):
            with self.subTest(case=case):
                result, _ = self.run_case(case)
                self.assertNotEqual(result.returncode, 0)

    def test_external_mutable_or_malformed_references_never_pull(self):
        for ref in ('registry.isle:5000/board:latest', PIN.replace('registry.isle', 'registry.isle.evil'),
                    PIN.replace('registry.isle', 'ghcr.io'), PIN.replace(':5000', ':65536'),
                    PIN.replace('/engines/', '/../'), PIN + ';touch marker'):
            with self.subTest(ref=ref):
                result, calls = self.run_case('missing', ref)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('docker pull', calls)
                self.assertNotIn('curl', calls)


if __name__ == '__main__':
    unittest.main()
