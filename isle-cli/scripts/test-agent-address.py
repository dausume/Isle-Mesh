#!/usr/bin/env python3
"""Check agent address selection with fake Docker/interfaces, no host networking."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).with_name('agent-address.sh')

class AgentAddress(unittest.TestCase):
    def run_case(self, interfaces, mac='02:00:00:00:01:77', core='', expected=None):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); net=root/'net'; net.mkdir(); binary=root/'bin'; binary.mkdir()
            for name, (address, ips) in interfaces.items():
                device=net/name;device.mkdir();(device/'address').write_text(address+'\n')
                (root/(name+'.ips')).write_text(ips)
            helper=root/'helper.sh';helper.write_text(SOURCE.read_text().replace('/sys/class/net/',str(net)+'/'))
            scripts={
                'sudo':'#!/bin/sh\nexec "$@"\n',
                'docker':'''#!/bin/sh
if [ "$1" = inspect ]; then [ -n "$CORE" ] || exit 1; printf '%s\\n' "$CORE"; exit; fi
[ "$1" = exec ] && [ "$2" = isle-remote-agent ] || exit 98
shift 2
exec "$@"
''',
                'ip':'''#!/bin/sh
[ "$1 $2 $3 $4 $5" = '-4 -o addr show dev' ] || exit 99
cat "$FIXTURE/$6.ips"
'''}
            for name,content in scripts.items():
                p=binary/name;p.write_text(content);p.chmod(0o755)
            env=dict(os.environ,PATH=str(binary)+':'+os.environ['PATH'],VIRTUAL_MAC=mac,CORE=core,FIXTURE=str(root))
            result=subprocess.run(['bash','-c','source "$1"; isle_agent_address','test',str(helper)],env=env,capture_output=True,text=True,timeout=5)
            if expected is None:self.assertNotEqual(result.returncode,0,result.stdout)
            else:self.assertEqual((result.returncode,result.stdout.strip()),(0,expected),result.stderr)

    def test_dynamic_bridge_pools_and_interface_order(self):
        for bridge in ('172.17.0.2','172.18.0.2','172.20.0.2','192.168.64.2'):
            for first in (True,False):
                with self.subTest(bridge=bridge,first=first):
                    pairs=[('eth0',('02:42:11:00:00:02',f'2: eth0 inet {bridge}/16 scope global eth0\n')),('eth1',('02:00:00:00:01:77','3: eth1 inet 10.10.1.9/24 scope global eth1\n'))]
                    self.run_case(dict(pairs if first else pairs[::-1]),expected='10.10.1.9')

    def test_missing_or_wrong_mac_fails_closed(self):
        for mac in ('','02:00:00:00:01:99'):
            with self.subTest(mac=mac):self.run_case({'eth0':('02:42:11:00:00:02','2: eth0 inet 172.17.0.2/16 scope global eth0\n')},mac=mac)

    def test_no_dhcp_address_fails_closed(self):
        for ips in ('','2: eth0 inet 172.17.0.2/16 scope global eth0\n'):
            with self.subTest(ips=ips):self.run_case({'eth0':('02:00:00:00:01:77',ips)})

    def test_core_uses_isle_network(self):
        self.run_case({},core='10.10.0.2',expected='10.10.0.2')

if __name__=='__main__':unittest.main()
