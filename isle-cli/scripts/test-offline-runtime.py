#!/usr/bin/env python3
"""Offline runtime decisions with isolated files and fake system commands.

Never starts services, creates VMs, installs packages, or accesses the network.
The complete-medium verifier has its own fixtures; this isolates consumers.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parent
LIBS = SCRIPTS.parents[1] / 'openwrt-router/scripts/router-setup/router-init-lib'


class OfflineRuntime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'; self.bin.mkdir()
        self.etc = self.root / 'etc/polari'; self.etc.mkdir(parents=True)
        self.medium = self.root / 'medium'
        for part in ('scripts', 'images', 'router/packages'):
            (self.medium / part).mkdir(parents=True)
        (self.etc / 'install-mode').write_text('offline\n')
        (self.etc / 'offline-source').write_text(str(self.medium)+'\n')
        (self.medium / 'ISLE_OFFLINE_BUNDLE').write_text('fixture')
        (self.medium / 'scripts/verify-offline.sh').write_text('#!/bin/bash\nexit "${VERIFY_FAIL:-0}"\n')
        (self.medium / 'router/base.qcow2').write_bytes(b'pristine bundled router fixture')
        (self.medium / 'router/packages/avahi.ipk').write_bytes(b'fixture package')
        self.image_id = 'sha256:'+'a'*64
        (self.medium / 'images/images.json').write_text(json.dumps([
            {'name': 'isle-vlan-agent', 'tag': 'latest', 'id': self.image_id}]))
        self.manifest = {'router': {'file': 'base.qcow2', 'packages': ['packages/avahi.ipk']},
                         'requiredPackages': ['dependency (>= 2) | compatible-dependency (>= 3)']}
        (self.medium / 'manifest.json').write_text(json.dumps(self.manifest))
        self.state = self.root / 'state.json'
        self.state.write_text(json.dumps({'installed': {'dependency': 'installed\t2'}}))
        self.log = self.root / 'calls'
        self.image_dir = self.root / 'staged-images'
        self.copies = []
        for original in (SCRIPTS/'offline-runtime.sh', LIBS/'05-offline.sh', LIBS/'40-image.sh',
                         LIBS/'70-download-packages.sh', LIBS/'85-install-packages.sh'):
            copy = self.root / original.name
            copy.write_text(original.read_text().replace('/etc/polari', str(self.etc)))
            self.copies.append(copy)
        stub = '''#!/usr/bin/python3
import json,os,pathlib,subprocess,sys
name=pathlib.Path(sys.argv[0]).name;a=sys.argv[1:]
with open(os.environ['CALLS'],'a') as f:f.write(json.dumps([name]+a)+'\\n')
if name in ('curl','wget','apt','apt-get','opkg','pip','pip3'):
 print('FORBIDDEN network/package command: '+name,file=sys.stderr);sys.exit(90)
elif name=='docker':
 if a[:2]!=['image','inspect']:sys.exit(90)
 if os.environ.get('IMAGE_MISSING'):sys.exit(1)
 print(os.environ.get('IMAGE_ID',''))
elif name=='dpkg-query':
 state=json.load(open(os.environ['STATE']))['installed'].get(a[-1])
 if not state:sys.exit(1)
 print(state,end='')
elif name=='dpkg':sys.exit(subprocess.run(['/usr/bin/dpkg']+a).returncode)
elif name=='router_ssh_test':pass
elif name=='router_ssh':
 if 'opkg' in a[0] and ' install ' in a[0] and os.environ.get('FAIL_OPKG'):sys.exit(1)
 print('running' if 'status' in a[0] else '3')
elif name=='cp':
 # The tested image paths are entirely inside the fixture directory.
 for p in a[-2:]:
  if not pathlib.Path(p).resolve().is_relative_to(pathlib.Path(os.environ['FIXTURE'])):sys.exit(99)
 sys.exit(subprocess.run(['/usr/bin/cp']+a).returncode)
else:sys.exit(99)
'''
        for name in ('curl','wget','apt','apt-get','opkg','pip','pip3','docker','dpkg-query','dpkg',
                     'router_ssh','router_ssh_test','cp'):
            p=self.bin/name;p.write_text(stub);p.chmod(0o755)
        self.env=dict(os.environ,PATH=str(self.bin)+':'+os.environ['PATH'],CALLS=str(self.log),
                      STATE=str(self.state),IMAGE_ID=self.image_id,FIXTURE=str(self.root))

    def run_shell(self, action, **flags):
        setup = '''set -euo pipefail
log_step(){ :; }; log_info(){ :; }; log_success(){ :; }
log_error(){ echo "$*" >&2; }; log_warning(){ echo "$*" >&2; }
for library in "$@"; do source "$library"; done
_download_package(){ curl "https://forbidden.invalid/package"; }
IMAGE_DIR="$FIXTURE/staged-images"
'''
        return subprocess.run(['bash','-c',setup+'\n'+action,'test',*[str(p) for p in self.copies]],
                              env=dict(self.env,**flags),capture_output=True,text=True)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def assert_no_fetch(self):
        forbidden={'curl','wget','apt','apt-get','opkg','pip','pip3'}
        self.assertFalse(any(call[0] in forbidden or call[:2]==['docker','pull'] for call in self.calls()),self.calls())

    def test_preflight_accepts_matching_images_and_installed_version(self):
        r=self.run_shell('isle_offline_preflight')
        self.assertEqual(r.returncode,0,r.stderr);self.assert_no_fetch()

    def test_preflight_refuses_missing_or_wrong_image_content(self):
        for flags in ({'IMAGE_MISSING':'1'},{'IMAGE_ID':'sha256:'+'b'*64}):
            with self.subTest(flags=flags):
                r=self.run_shell('isle_offline_preflight',**flags)
                self.assertNotEqual(r.returncode,0);self.assert_no_fetch()

    def test_image_lookup_requires_release_membership(self):
        r=self.run_shell('isle_offline_image arbitrary:latest')
        self.assertNotEqual(r.returncode,0);self.assert_no_fetch()
        r=self.run_shell('isle_offline_image isle-vlan-agent:latest')
        self.assertEqual(r.returncode,0,r.stderr)

    def test_preflight_refuses_removed_or_old_dependency(self):
        for installed in ({},{'dependency':'config-files\t9'},{'dependency':'installed\t1'}):
            with self.subTest(installed=installed):
                self.state.write_text(json.dumps({'installed':installed}))
                r=self.run_shell('isle_offline_preflight')
                self.assertNotEqual(r.returncode,0,r.stdout+r.stderr);self.assert_no_fetch()

    def test_preflight_accepts_compatible_installed_alternative(self):
        self.state.write_text(json.dumps({'installed':{'compatible-dependency':'installed\t4'}}))
        r=self.run_shell('isle_offline_preflight')
        self.assertEqual(r.returncode,0,r.stderr);self.assert_no_fetch()

    def test_bad_medium_refused_before_docker_or_copy(self):
        r=self.run_shell('isle_offline_preflight',VERIFY_FAIL='1')
        self.assertNotEqual(r.returncode,0)
        self.assertFalse(any(call[0] in ('docker','cp') for call in self.calls()));self.assert_no_fetch()

    def test_router_image_staged_and_identical_copy_reused(self):
        r=self.run_shell('download_image')
        self.assertEqual(r.returncode,0,r.stderr)
        target=self.image_dir/'openwrt-isle-router.qcow2'
        self.assertEqual(target.read_bytes(),(self.medium/'router/base.qcow2').read_bytes())
        before=sum(call[0]=='cp' for call in self.calls())
        r=self.run_shell('download_image')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(sum(call[0]=='cp' for call in self.calls()),before)
        self.assert_no_fetch()

    def test_router_missing_image_never_falls_back_to_download(self):
        (self.medium/'router/base.qcow2').unlink()
        r=self.run_shell('download_image')
        self.assertNotEqual(r.returncode,0);self.assert_no_fetch()
        self.assertFalse(any(call[0]=='cp' for call in self.calls()))

    def test_router_missing_ipks_refuses_without_fetch(self):
        (self.medium/'router/packages/avahi.ipk').unlink()
        r=self.run_shell('download_openwrt_packages')
        self.assertNotEqual(r.returncode,0);self.assert_no_fetch()

    def test_router_bundled_ipks_need_no_download(self):
        r=self.run_shell('download_openwrt_packages')
        self.assertEqual(r.returncode,0,r.stderr);self.assert_no_fetch()

    def test_opkg_failure_stops_router_setup_without_feed_fallback(self):
        r=self.run_shell('install_and_configure_packages',FAIL_OPKG='1')
        self.assertNotEqual(r.returncode,0)
        ssh=[call[1] for call in self.calls() if call[0]=='router_ssh']
        self.assertEqual(len(ssh),1,ssh)
        self.assertIn('--conf /tmp/isle-offline-opkg.conf install',ssh[0])
        self.assertNotIn('opkg update',ssh[0]);self.assert_no_fetch()

    def test_opkg_success_uses_isolated_configuration(self):
        r=self.run_shell('install_and_configure_packages')
        self.assertEqual(r.returncode,0,r.stderr)
        install=next(call[1] for call in self.calls() if call[0]=='router_ssh' and 'opkg' in call[1] and ' install ' in call[1])
        self.assertIn('--conf /tmp/isle-offline-opkg.conf',install)
        self.assertNotIn('http:',install);self.assertNotIn('https:',install);self.assert_no_fetch()


if __name__=='__main__':unittest.main()
