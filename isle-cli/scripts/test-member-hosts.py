#!/usr/bin/env python3
"""Generic member ingress: no real Docker, hosts edits, sudo or network access."""
import json, os, subprocess, tempfile, unittest
from pathlib import Path
SOURCE=Path(__file__).with_name('hosts-reconcile.sh').read_text()

class MemberHosts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.hosts=self.root/'hosts';self.registry=self.root/'registry.json'
        self.original='127.0.0.1 localhost\n192.0.2.8 unmanaged.test\n'
        self.hosts.write_text(self.original);(self.root/'mode').write_text('remote\n')
        self.set_apps('alpha.isle','bravo.isle')
        source=SOURCE.replace('/etc/isle-mesh/agent/agent.mode',str(self.root/'mode')).replace('/etc/isle-mesh/router',str(self.root/'router')).replace('/etc/isle-mesh/agent/registry.json',str(self.registry)).replace('/etc/hosts',str(self.hosts))
        self.script=self.root/'reconcile';self.script.write_text(source)
        stubs={'sudo':'[ "$1" != -n ] || shift; if [ "$1" = cp ] && [ "${FAIL_COPY:-}" = 1 ]; then exit 1; fi; exec "$@"',
               'docker':'''case "$1 $2" in
"inspect isle-remote-agent") echo "$BRIDGE_IP";;
"network inspect") [ "$3" = isle-agent-net ] || exit 88; echo "$DRIVER";;
*) exit 99;; esac''',
               'curl':'echo unexpected-curl >&2; exit 89',
               'hostname':'echo unrelated-os-hostname'}
        for name,body in stubs.items():
            p=self.root/name;p.write_text('#!/bin/sh\n'+body+'\n');p.chmod(0o755)
    def set_apps(self,*domains):
        self.registry.write_text(json.dumps({'apps':{str(i):{'domain':d} for i,d in enumerate(domains)}}))
    def run_probe(self,**overrides):
        env=dict(os.environ,PATH=str(self.root)+':'+os.environ['PATH'],BRIDGE_IP='172.20.0.2',DRIVER='bridge');env.update(overrides)
        return subprocess.run(['bash',str(self.script)],env=env,capture_output=True,text=True,timeout=5)
    def test_arbitrary_apps_and_dynamic_bridge(self):
        for ip in ['172.17.0.2','172.20.0.2','192.168.64.2']:
            r=self.run_probe(BRIDGE_IP=ip);self.assertEqual(r.returncode,0,r.stderr)
            contents=self.hosts.read_text();self.assertIn(ip+' alpha.isle bravo.isle',contents)
            self.assertTrue(contents.startswith(self.original));self.assertNotIn('polari.isle',contents)
            self.assertNotIn('unexpected-curl',r.stderr)
            self.assertEqual(self.run_probe(BRIDGE_IP=ip).returncode,0)
            self.assertEqual(self.hosts.read_text(),contents)
    def test_remove_and_rename_apps(self):
        self.assertEqual(self.run_probe().returncode,0)
        self.set_apps('new.isle');self.assertEqual(self.run_probe().returncode,0)
        contents=self.hosts.read_text();self.assertIn('new.isle',contents);self.assertNotIn('alpha.isle',contents)
        self.set_apps();self.assertEqual(self.run_probe(BRIDGE_IP='').returncode,0)
        self.assertEqual(self.hosts.read_text(),self.original)
    def test_invalid_address_or_network_preserves_existing(self):
        self.assertEqual(self.run_probe().returncode,0);before=self.hosts.read_text()
        for ip in ['', 'not-ip', '127.0.0.1', '0.0.0.0', '224.0.0.1', '169.254.1.2', '::1', '172.20.0.2\n10.10.0.2']:
            self.assertNotEqual(self.run_probe(BRIDGE_IP=ip).returncode,0,ip)
            self.assertEqual(self.hosts.read_text(),before)
        self.assertNotEqual(self.run_probe(DRIVER='macvlan').returncode,0)
        self.assertEqual(self.hosts.read_text(),before)
    def test_registry_error_preserves_existing(self):
        self.assertEqual(self.run_probe().returncode,0);before=self.hosts.read_text()
        self.registry.write_text('{broken')
        self.assertNotEqual(self.run_probe().returncode,0);self.assertEqual(self.hosts.read_text(),before)
        self.registry.unlink()
        self.assertNotEqual(self.run_probe().returncode,0);self.assertEqual(self.hosts.read_text(),before)
    def test_untrusted_domain_and_write_failure(self):
        self.set_apps('Alpha.isle','bad.isle\n1.2.3.4 injected.test','external.example')
        self.assertEqual(self.run_probe().returncode,0);before=self.hosts.read_text()
        self.assertIn('alpha.isle',before);self.assertNotIn('injected',before);self.assertNotIn('external.example',before)
        self.set_apps('changed.isle');self.assertNotEqual(self.run_probe(FAIL_COPY='1').returncode,0)
        self.assertEqual(self.hosts.read_text(),before)

if __name__=='__main__':unittest.main()
