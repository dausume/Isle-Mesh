#!/usr/bin/env python3
"""Exercise legacy setup entrypoints without touching host networking.

A successful return must not inspect/copy credentials or invoke a network,
service, profile, DHCP or resolver operation, in any installation context.
All old absolute /etc references are redirected into the fixture as a second
safeguard, while stubs stop external tools before they could change anything.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ('setup-wifi-access.sh', 'switch-to-systemd-networkd.sh')
FORBIDDEN = ('cat', 'iw', 'nmcli', 'wpa_passphrase', 'wpa_supplicant', 'ip',
             'systemctl', 'nsenter', 'sudo', 'dhclient', 'udhcpc', 'dhcpcd',
             'resolvectl', 'networkctl', 'netplan', 'tee', 'mkdir', 'truncate',
             'unlink', 'rm', 'ln', 'cp', 'chmod', 'chown')


class UplinkOwnerPreservation(unittest.TestCase):
    def test_direct_entrypoints_preserve_owner_in_every_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture=Path(temporary)
            fakebin=fixture/'bin';fakebin.mkdir()
            etc=fixture/'etc';(etc/'polari').mkdir(parents=True)
            calls=fixture/'calls'
            for command in FORBIDDEN:
                stub=fakebin/command
                stub.write_text('''#!/usr/bin/python3
import json,os,pathlib,sys
with open(os.environ['CALLS'],'a') as output:
    output.write(json.dumps([pathlib.Path(sys.argv[0]).name]+sys.argv[1:])+'\\n')
sys.exit(97)
''')
                stub.chmod(0o755)
            for context in ('online','offline','unconfigured'):
                mode=etc/'polari/install-mode'
                if context=='unconfigured':mode.unlink(missing_ok=True)
                else:mode.write_text(context+'\n')
                for script in SCRIPTS:
                    with self.subTest(context=context,entrypoint=script):
                        calls.unlink(missing_ok=True)
                        isolated=fixture/script
                        isolated.write_text((ROOT/'mdns/scripts'/script).read_text().replace('/etc/',str(etc)+'/'))
                        before={str(p.relative_to(etc)):p.read_bytes() for p in etc.rglob('*') if p.is_file()}
                        environment=dict(os.environ,PATH=str(fakebin)+':'+os.environ['PATH'],CALLS=str(calls),POLARI_INSTALL_MODE=context)
                        result=subprocess.run(['/bin/bash',str(isolated)],env=environment,cwd=fixture,capture_output=True,text=True,timeout=5)
                        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                        self.assertFalse(calls.exists(),calls.read_text() if calls.exists() else '')
                        after={str(p.relative_to(etc)):p.read_bytes() for p in etc.rglob('*') if p.is_file()}
                        self.assertEqual(before,after)
                        self.assertIn('existing OS network owner',result.stdout)


if __name__=='__main__':unittest.main()
