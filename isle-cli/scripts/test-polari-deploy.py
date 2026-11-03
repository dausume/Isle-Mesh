#!/usr/bin/env python3
"""Deployment outcomes with mocked commands; no Docker, sudo or network access."""
import os
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('polari.sh')
MOCK = r'''
sudo(){
  printf 'sudo %s\n' "$*" >> "$CALLS"
  case "$*" in
    *'certs.sh ensure '*) [[ "$CASE" != certificate_missing ]] ;;
    *'register --name demo-api '*) [[ "$CASE" != backend_registration ]] ;;
    *'register --name demo '*) [[ "$CASE" != frontend_registration ]] ;;
    *'docker compose '*) [[ "$CASE" != compose_failure ]] ;;
    *'dns register '*) [[ "$CASE" != dns_failure ]] ;;
    *) return 0 ;;
  esac
}
curl(){
  printf 'curl %s\n' "$*" >> "$CALLS"
  case "$CASE" in
    tls_failure|dns_failure) printf 000; return 60 ;;
    backend_unhealthy) [[ "$*" == *'/api/health' ]] && { printf 503; return; } ;;
    frontend_unhealthy) [[ "$*" != *'/api/health' ]] && { printf 503; return; } ;;
  esac
  printf 200
}
sleep(){ :; }
require_member(){ :; }
ensure_images(){ :; }
write_configs(){ mkdir -p "$BASE/$1"; }
agent_ip(){ echo 10.10.0.147; }
self_report(){ :; }
BASE="$TEST_BASE"
'''


class PolariDeployTest(unittest.TestCase):
    def run_case(self, case, action='deploy --name demo'):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            if not action.startswith("deploy "):
                (base / "demo").mkdir()
            calls = base / 'calls'
            result = subprocess.run(
                ['bash', '-c', 'source "$1" help >/dev/null\n' + MOCK + '\n' + action,
                 'test', str(SCRIPT)], capture_output=True, text=True,
                env={**os.environ, 'CASE': case, 'CALLS': str(calls), 'TEST_BASE': temp})
            return result, calls.read_text() if calls.exists() else ''

    def test_registration_failures_stop_deployment(self):
        for case in ('frontend_registration', 'backend_registration'):
            with self.subTest(case=case):
                result, calls = self.run_case(case)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn('registration failed', result.stdout)
                self.assertNotIn('curl ', calls)
                self.assertNotIn('deployed and verified', result.stdout)

    def test_compose_failure_stops_before_registration(self):
        result, calls = self.run_case('compose_failure')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('register --name', calls)

    def test_missing_certificate_stops_before_workload_changes(self):
        result, calls = self.run_case('certificate_missing')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('certificate not ready', result.stdout)
        self.assertNotIn('docker compose', calls)
        self.assertNotIn('register --name', calls)

    def test_unready_endpoints_never_report_success(self):
        for case in ('tls_failure', 'dns_failure', 'backend_unhealthy', 'frontend_unhealthy'):
            with self.subTest(case=case):
                result, calls = self.run_case(case)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn('verification incomplete', result.stdout)
                self.assertIn('configuration retained', result.stdout)
                self.assertNotIn('deployed and verified', result.stdout)
                self.assertEqual(calls.count('curl '), 12)

    def test_success_requires_both_trusted_endpoints(self):
        result, calls = self.run_case('healthy')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Polari both deployment 'demo' verified", result.stdout)
        self.assertIn('https://demo.isle/', calls)
        self.assertIn('https://api.demo.isle/api/health', calls)
        self.assertNotIn('--resolve', calls)
        self.assertNotIn('-sk', calls)
        self.assertNotIn('--insecure', calls)

    def test_verify_retry_does_not_mutate(self):
        result, calls = self.run_case('healthy', 'verify_instance demo')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('sudo ', calls)
        self.assertEqual(calls.count('curl '), 2)

    def test_ensure_stops_after_failed_step(self):
        action = r'''
api_get(){ echo '{"ok":true,"complete":false,"plan":[{"action":"add-module","module":"board","target":"demo","cmd":"unused display string"},{"action":"add-module","module":"scope","target":"demo","cmd":"unused display string"}]}'; }
sudo(){ echo "$*" >> "$CALLS"; return 1; }
app_ensure electronics --yes
'''
        result, calls = self.run_case('healthy', action)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('isle polari module add board --to demo', calls)
        self.assertNotIn('isle polari module add scope --to demo', calls)
        self.assertNotIn('ensured electronics', result.stdout)

    def test_ensure_rechecks_placement_after_steps(self):
        for complete in ('true', 'false'):
            with self.subTest(complete=complete):
                action = r'''
api_get(){
  if [ -f "$TEST_BASE/planned" ]; then
    echo '{"ok":true,"complete":COMPLETE}'
  else
    touch "$TEST_BASE/planned"
    echo '{"ok":true,"complete":false,"plan":[{"action":"add-module","module":"board","target":"demo","cmd":"unused display string"}]}'
  fi
}
app_ensure electronics --yes
'''.replace('COMPLETE', complete)
                result, calls = self.run_case('healthy', action)
                self.assertIn('isle polari module add board --to demo', calls)
                self.assertEqual(result.returncode == 0, complete == 'true', result.stdout)
                self.assertEqual('ensured electronics' in result.stdout, complete == 'true')

    def test_ensure_validates_whole_plan_before_first_mutation(self):
        for invalid in ({'action': 'run-shell', 'module': 'board'},
                        {'action': 'add-module', 'module': 'board; touch marker', 'target': 'demo'},
                        {'action': 'add-module', 'module': 'board', 'target': '../other'}):
            with self.subTest(invalid=invalid):
                response = json.dumps({'ok': True, 'complete': False, 'plan': [
                    {'action': 'add-module', 'module': 'board', 'target': 'demo'}, invalid]})
                action = "api_get(){ cat <<'JSON'\n" + response + '\nJSON\n}\napp_ensure electronics --yes'
                result, calls = self.run_case('healthy', action)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, '')

    def test_server_command_text_is_not_executed(self):
        action = r'''
api_get(){
  if [ -f "$TEST_BASE/planned" ]; then
    echo '{"ok":true,"complete":true}'
  else
    touch "$TEST_BASE/planned"
    echo '{"ok":true,"complete":false,"plan":[{"action":"deploy-instance","module":"board","cmd":"touch /should-never-execute"}]}'
  fi
}
app_ensure electronics --yes
'''
        result, calls = self.run_case('healthy', action)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('sudo isle polari instance deploy --modules board', calls)
        self.assertNotIn('should-never-execute', calls)

    def test_new_modules_share_one_instance_deployment(self):
        action = r'''
api_get(){
  if [ -f "$TEST_BASE/planned" ]; then
    echo '{"ok":true,"complete":true}'
  else
    touch "$TEST_BASE/planned"
    echo '{"ok":true,"complete":false,"plan":[{"action":"deploy-instance","module":"board"},{"action":"deploy-instance","module":"cmod"}]}'
  fi
}
app_ensure electronics --yes
'''
        result, calls = self.run_case('healthy', action)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls.count('instance deploy'), 1)
        self.assertIn('--modules board,cmod', calls)

    def test_remote_target_refuses_before_any_local_step(self):
        action = r'''
api_get(){ echo '{"ok":true,"complete":false,"plan":[{"action":"add-module","module":"board","target":"demo"},{"action":"add-module","module":"cmod","target":"remote-node"}]}'; }
app_ensure electronics --yes
'''
        result, calls = self.run_case('healthy', action)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not configured on this device', result.stdout)
        self.assertEqual(calls, '')


if __name__ == '__main__':
    unittest.main()
