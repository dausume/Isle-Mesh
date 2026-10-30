#!/usr/bin/env python3
"""Guided joining regression tests: no network or privileged host changes."""
import importlib.util
import os
from pathlib import Path
import socket
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('discovery', BASE/'join-discovery.py')
discovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(discovery)
BEACON = b'ISLE_MESH_DISCOVERY|isle=Home Isle|vlan=10|router=10.10.0.1|dhcp=10.10.0.0/24'

class Discovery(unittest.TestCase):
    def test_valid_network(self):
        self.assertEqual(discovery.parse_beacon(BEACON)['isle_name'], 'Home Isle')
    def test_invalid_packets(self):
        for data in (b'garbage', BEACON.replace(b'vlan=10',b'vlan=9999'),
                     BEACON.replace(b'10.10.0.1|',b'192.168.0.1|'),
                     BEACON.replace(b'Home Isle', b'bad\x1bname'), b'\xff'):
            with self.subTest(data=data), self.assertRaises((ValueError, KeyError, UnicodeError)):
                discovery.parse_beacon(data)
    def test_collects_all_and_deduplicates(self):
        from unittest.mock import MagicMock
        sock = MagicMock()
        sock.__enter__.return_value = sock
        second = BEACON.replace(b'Home Isle',b'Other Isle').replace(b'10.10.',b'10.11.')
        sock.recvfrom.side_effect = [(b'noise',None),(BEACON,None),(BEACON,None),(second,None),socket.timeout()]
        with patch.object(discovery.socket,'socket',return_value=sock):
            found = discovery.collect('eth-test',35)
        self.assertEqual(len(found),2)
        sock.setsockopt.assert_any_call(socket.SOL_SOCKET,socket.SO_BINDTODEVICE,b'eth-test\0')

class GuidedJoin(unittest.TestCase):
    def shell(self, code, stdin=''):
        return subprocess.run(['bash','-c','source "$1"\n'+code,'test',str(BASE/'join.sh')],
                              input=stdin,text=True,capture_output=True)
    def test_invalid_device_names(self):
        for name in ('-bad','bad-','localhost','unknown','with space'):
            r=self.shell('DEVICE_NAME="$2"; declare_device_name'.replace('"$2"',repr(name)))
            self.assertNotEqual(r.returncode,0,name)
    def test_no_silent_name_in_noninteractive_mode(self):
        r=self.shell('declare_device_name')
        self.assertNotEqual(r.returncode,0)
        self.assertIn('--device-name',r.stdout)
    def test_explicit_name(self):
        r=self.shell('DEVICE_NAME=eccentric-panda; declare_device_name; echo "$DEVICE_NAME"')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('eccentric-panda',r.stdout)
    def test_name_collision_and_dns_failure(self):
        for answer,code in [(';; status: NOERROR,\neccentric-panda.isle. 0 IN A 10.10.0.9',0),
                            (';; status: SERVFAIL,',0),('',1)]:
            body='dig(){ printf "%s\\n" '+shlex.quote(answer)+'; return '+str(code)+'; }; DEVICE_NAME=eccentric-panda; ROUTER_IP=10.10.0.1; check_device_name'
            self.assertNotEqual(self.shell(body).returncode,0)
    def test_free_name(self):
        r=self.shell("dig(){ echo ';; status: NXDOMAIN,'; }; DEVICE_NAME=panda; check_device_name")
        self.assertEqual(r.returncode,0,r.stderr)
    def test_pipeline_never_completes_after_health_or_trust_failure(self):
        functions=['check_prerequisites','check_mode_guard','declare_device_name','detect_interface',
                   'discover_isle','check_device_name','generate_mac','init_agent_dirs',
                   'setup_macvlan','start_remote_agent','wait_for_health','finish_join','show_completion']
        for failure in ('wait_for_health','finish_join',None):
            code='\n'.join(f'{name}() {{ echo {name}; return {1 if name==failure else 0}; }}' for name in functions)
            r=self.shell(code+'\nmain --device-name eccentric-panda')
            self.assertEqual(r.returncode,0 if failure is None else 1,r.stdout+r.stderr)
            self.assertEqual('show_completion' in r.stdout,failure is None)
            if failure=='wait_for_health': self.assertNotIn('\nfinish_join\n',r.stdout)
    def test_certificate_failure_stops_before_https(self):
        r=self.shell('setup_remote_dns(){ :; }; bash(){ return 1; }; curl(){ echo UNEXPECTED; }; finish_join')
        self.assertNotEqual(r.returncode,0)
        self.assertNotIn('UNEXPECTED',r.stdout)
        self.assertIn('--finish',r.stdout)
    def test_https_failure_not_success(self):
        r=self.shell('setup_remote_dns(){ :; }; bash(){ return 0; }; curl(){ return 60; }; finish_join')
        self.assertNotEqual(r.returncode,0)
        self.assertNotIn('HTTPS verified',r.stdout)
    def test_selects_requested_isle_and_refuses_implicit_selection(self):
        import json
        candidates=[discovery.parse_beacon(BEACON),discovery.parse_beacon(BEACON.replace(b'10.10.',b'10.11.'))]
        with tempfile.TemporaryDirectory() as temp:
            setup=(f'REMOTE_DIR={shlex.quote(temp)}; DISCOVERY_FILE="$REMOTE_DIR/discovery.json"; INTERFACE=eth-test; '
                   'timeout(){ return 0; }; sleep(){ :; }; validate_no_subnet_conflict(){ :; }; '
                   'python3(){ printf "%s\\n" '+shlex.quote(json.dumps(candidates))+'; }; ')
            r=self.shell(setup+'SELECT_ISLE=10.11.0.1; discover_isle; cat "$DISCOVERY_FILE"')
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            self.assertEqual(json.loads(Path(temp,'discovery.json').read_text())['router_ip'],'10.11.0.1')
            Path(temp,'discovery.json').unlink()
            r=self.shell(setup+'discover_isle')
            self.assertNotEqual(r.returncode,0)
            self.assertFalse(Path(temp,'discovery.json').exists())
    def test_finish_reuses_saved_membership_without_rejoining(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp,'mode').write_text('remote')
            Path(temp,'discovery.json').write_text('{"router_ip":"10.10.0.1"}')
            setup=f'MODE_FILE={shlex.quote(temp)}/mode; DISCOVERY_FILE={shlex.quote(temp)}/discovery.json; '
            setup+='check_prerequisites(){ :; }; check_mode_guard(){ echo UNEXPECTED; return 1; }; wait_for_health(){ :; }; finish_join(){ echo "FINISH:$ROUTER_IP"; }; '
            r=self.shell(setup+'main --finish')
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            self.assertIn('FINISH:10.10.0.1',r.stdout)
            self.assertNotIn('UNEXPECTED',r.stdout)
    def test_setup_door_uses_installed_join(self):
        setup=BASE.parent/'shells/tools/store-setup.sh'
        r=subprocess.run(['bash',str(setup),'join'],env={**os.environ,'DRY':'1'},capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn(' join',r.stdout)
        self.assertNotIn('curl',r.stdout)

if __name__=='__main__': unittest.main()
