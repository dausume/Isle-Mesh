#!/usr/bin/env python3
"""Registry setup preflight and reporting without host mutations."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('isle-registry-setup.sh')
MOCK = r'''
ip(){
  if [[ "$*" == *'dev br-mgmt'* ]]; then
    case "$CASE" in
      absent) return 1 ;;
      ambiguous) printf '3: br-mgmt inet 192.168.1.254/24 scope global br-mgmt\n3: br-mgmt inet 192.168.1.253/24 scope global br-mgmt\n'; return ;;
    esac
  fi
  echo '3: br-mgmt inet 192.168.1.254/24 scope global br-mgmt'
}
docker(){ echo "docker $*" >> "$CALLS"; [[ "$CASE" != missing_image ]]; }
sudo(){ echo "sudo $*" >> "$CALLS"; [[ "$CASE" != dns_failure ]]; }
curl(){
  echo "curl $*" >> "$CALLS"
  [[ "$CASE" != tls_failure ]] || return 60
  if [[ "$CASE" == unhealthy ]]; then printf 503; else printf 200; fi
}
sleep(){ :; }
isle_offline(){ [[ "$CASE" == offline* ]]; }
isle_offline_image(){ echo "offline-check $*" >> "$CALLS"; [[ "$CASE" == offline_present ]]; }
'''


class RegistrySetupTest(unittest.TestCase):
    def run_case(self, case, action):
        with tempfile.TemporaryDirectory() as temp:
            calls = Path(temp) / 'calls'
            result = subprocess.run(
                ['bash', '-c', 'source "$1"\n' + MOCK + '\n' + action, 'test', str(SCRIPT)],
                env={**os.environ, 'CASE': case, 'CALLS': str(calls)},
                capture_output=True, text=True, timeout=5)
            return result, calls.read_text() if calls.exists() else ''

    def test_auto_address_uses_real_bridge(self):
        result, calls = self.run_case('healthy', 'registry_configure; echo "$ISLE_IP:$PORT"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('192.168.1.254:5000', result.stdout)
        self.assertEqual(calls, '')

    def test_no_invented_fallback_or_ambiguous_choice(self):
        for case in ('absent', 'ambiguous'):
            with self.subTest(case=case):
                result, calls = self.run_case(case, 'registry_configure || exit 1')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, '')

    def test_rejects_invalid_or_nonlocal_endpoint(self):
        for args in ('--address 127.0.0.1', '--address 999.1.1.1', '--address 10.10.0.147',
                     '--port 0', '--port 65536', '--port abc', '--port', '--unknown'):
            with self.subTest(args=args):
                result, calls = self.run_case('healthy', 'registry_configure ' + args + ' || exit 1')
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(calls, '')

    def test_explicit_local_endpoint(self):
        result, _ = self.run_case('absent', 'registry_configure --port 5443 --address 192.168.1.254; echo "$ISLE_IP:$PORT"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('192.168.1.254:5443', result.stdout)

    def test_offline_checks_exact_image_without_pull(self):
        for case in ('offline_present', 'offline_missing'):
            with self.subTest(case=case):
                result, calls = self.run_case(case, 'registry_ensure_image')
                self.assertEqual(result.returncode == 0, case == 'offline_present')
                self.assertIn('offline-check registry:2', calls)
                self.assertNotIn('docker ', calls)

    def test_online_reuses_present_image(self):
        result, calls = self.run_case('healthy', 'registry_ensure_image')
        self.assertEqual(result.returncode, 0)
        self.assertNotIn('docker pull', calls)

    def test_dns_tls_or_health_failure_is_incomplete(self):
        for case in ('dns_failure', 'tls_failure', 'unhealthy'):
            with self.subTest(case=case):
                result, calls = self.run_case(case, 'ISLE_IP=192.168.1.254; PORT=5000; registry_publish_dns')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('setup incomplete', result.stderr)
                self.assertNotIn('serves the registry API', result.stdout)
                if case == 'dns_failure':
                    self.assertNotIn('curl ', calls)

    def test_success_uses_normal_dns_and_trusted_api(self):
        result, calls = self.run_case('healthy', 'ISLE_IP=192.168.1.254; PORT=5000; registry_publish_dns')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--cacert /etc/isle-mesh/ca/isle-root.crt', calls)
        self.assertIn('https://registry.isle:5000/v2/', calls)
        self.assertNotIn('--resolve', calls)
        self.assertNotIn('-sk', calls)

    def test_verify_from_member_needs_no_local_core_or_mutation(self):
        result, calls = self.run_case('absent', 'registry_verify 5443')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('https://registry.isle:5443/v2/', calls)
        self.assertNotIn('sudo ', calls)
        self.assertNotIn('docker ', calls)


if __name__ == '__main__':
    unittest.main()
