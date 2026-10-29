#!/usr/bin/env python3
"""Exercise profile decisions and failures without touching host networking."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name('remote-lease.sh')
MOCK = r'''nmcli(){
  printf '%s\n' "$*" >> "$CALLS"
  case "$*" in
    '-g GENERAL.CON-UUID device show eth-test')
      if [[ "$CASE" == bound || "$CASE" == ambiguous || "$CASE" == unbound ]]; then echo --; else echo active-uuid; fi ;;
    '-g UUID connection show') printf 'bound-one\nbound-two\n' ;;
    '-g connection.interface-name connection show uuid bound-one') [[ "$CASE" == unbound ]] || echo eth-test; return 0 ;;
    '-g connection.interface-name connection show uuid bound-two')
      if [[ "$CASE" == ambiguous ]]; then echo eth-test; else echo other-port; fi ;;
    '-g connection.type connection show uuid '*) echo 802-3-ethernet ;;
    '-g ipv4.method connection show uuid '*)
      if [[ "$CASE" == static ]]; then echo manual; else echo auto; fi ;;
    'connection modify uuid '*) return 0 ;;
    '--wait 30 connection up uuid '*) [[ "$CASE" != activation_failure ]] ;;
    *) echo "Unexpected nmcli command: $*" >&2; return 99 ;;
  esac
}
ip(){ [[ "$CASE" == no_lease ]] || echo '2: eth-test inet 10.10.0.123/24 scope global eth-test'; }
'''

class RemoteLeaseTest(unittest.TestCase):
    def run_case(self, case, action='configure_nm eth-test'):
        with tempfile.TemporaryDirectory() as temp:
            calls = Path(temp)/'calls'
            result = subprocess.run(['bash', '-c', 'source "$1"\n'+MOCK+'\n'+action,
                                     'test', str(SCRIPT)],
                                    env={**os.environ, 'CASE': case, 'CALLS': str(calls)},
                                    capture_output=True, text=True)
            return result, calls.read_text() if calls.exists() else ''

    def test_reuses_active_profile_without_duplicate(self):
        result, calls = self.run_case('active')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('connection up uuid active-uuid ifname eth-test', calls)
        self.assertNotIn('connection add', calls)

    def test_reuses_bound_profile(self):
        result, calls = self.run_case('bound')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('connection up uuid bound-one', calls)

    def test_ambiguous_profiles_refuse_before_mutation(self):
        result, calls = self.run_case('ambiguous')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('connection modify', calls)

    def test_unbound_profile_requires_selection(self):
        result, calls = self.run_case('unbound')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('connection add', calls)
        self.assertNotIn('connection modify', calls)

    def test_static_profile_refuses_before_mutation(self):
        result, calls = self.run_case('static')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('connection modify', calls)

    def test_failed_activation_is_failure(self):
        result, _ = self.run_case('activation_failure')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('Ethernet address:', result.stdout)

    def test_missing_lease_is_failure(self):
        result, _ = self.run_case('no_lease')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No IPv4 lease', result.stderr)

    def test_missing_candidate_is_failure(self):
        result, calls = self.run_case('active', 'is_candidate(){ return 1; }; pick_iface')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, '')

    def test_multiple_candidates_is_failure(self):
        result, calls = self.run_case('active', 'is_candidate(){ return 0; }; pick_iface')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, '')

if __name__ == '__main__': unittest.main()
