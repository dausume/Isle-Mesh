#!/usr/bin/env python3
"""Exercise real lifecycle/status functions with isolated command fixtures."""
import os, subprocess, unittest
from pathlib import Path

MANAGER=Path(__file__).resolve().parents[2]/'isle-agent/scripts/agent-manager.sh'
class ModeStatus(unittest.TestCase):
    def run_case(self, mode, command, health='healthy', host='inactive'):
        script = '''source "$MANAGER"
get_agent_mode() { echo "$MODE"; }
is_host_running() { test "$HOST" = active; }
is_vlan_running() { return 0; }
docker() {
  case "$*" in
    *'.State.Health'*) echo "$HEALTH";;
    *'range $name, $net'*)
      [[ "$*" == *'printf'* ]] || return 7
      printf '  Network: bridge | MAC: aa:bb | Docker IP: 172.20.0.2\n  Network: macvlan | MAC: cc:dd | Docker IP: 10.10.0.2\n';;
    *'/run/isle-lease'*) echo 'eth1 10.10.0.147 9999999999';;
    *'nginx -t'*) return 0;;
    *) return 0;;
  esac
}
eval "$ACTION"
'''
        # Source function definitions without dispatch; no host services touched.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'manager.sh'
            source=MANAGER.read_text()
            source=source.replace('SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"', 'SCRIPT_DIR="'+str(MANAGER.parent)+'"')
            source=source.replace('main "$@"', ': # test: dispatch invoked explicitly')
            p.write_text(source)
            return subprocess.run(['bash','-c',script],env=dict(os.environ,MANAGER=str(p),MODE=mode,HOST=host,HEALTH=health,ACTION=command),capture_output=True,text=True)
    def test_remote_does_not_require_host(self):
        r=self.run_case('remote','is_running && verify_setup')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('host service is not required',r.stdout)
    def test_remote_unhealthy_fails_verification(self):
        r=self.run_case('remote','verify_setup',health='unhealthy')
        self.assertNotEqual(r.returncode,0)
        self.assertIn('unhealthy',r.stdout+r.stderr)
    def test_core_still_requires_host(self):
        self.assertNotEqual(self.run_case('core','is_running').returncode,0)
        self.assertEqual(self.run_case('core','is_running',host='active').returncode,0)
    def test_networks_are_separate_and_lease_is_not_assumed_eth0(self):
        r=self.run_case('remote','show_network_addresses isle-remote-agent')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('Network: bridge | MAC: aa:bb',r.stdout)
        self.assertIn('Network: macvlan | MAC: cc:dd',r.stdout)
        self.assertIn('eth1 10.10.0.147',r.stdout)

if __name__=='__main__':unittest.main()
