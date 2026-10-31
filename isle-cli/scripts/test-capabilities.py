import importlib.util,json,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('probe',Path(__file__).with_name('capabilities.py'));probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)
class Probe(unittest.TestCase):
    def facts(self, result):
        with patch.object(probe,'run',return_value=result),patch.object(probe.shutil,'which',return_value='/bin/docker'):
            return probe.collect('fixture')['capability_evidence']['facts']
    def test_running_openwrt_proves_kvm_without_user_device_access(self):
        def respond(args, timeout=8):
            if 'dumpxml' in args:return (0,"<domain type='kvm'><name>openwrt-isle-router</name></domain>",'')
            if args[0]=='virsh':return (0,'openwrt-isle-router','')
            return (0,'200','')
        with patch.object(probe,'run',side_effect=respond),patch.object(probe.shutil,'which',return_value='/bin/tool'),patch.object(probe.os,'access',return_value=False):
            f=probe.collect('isle-core')['capability_evidence']['facts']
        self.assertTrue(f['hardware.openwrt.running']['value'])
        self.assertTrue(f['runtime.kvm.usable']['value'])
    def test_access_requires_200(self):
        self.assertTrue(self.facts((0,'200',''))['access.whoami.https']['value'])
        self.assertFalse(self.facts((0,'404',''))['access.whoami.https']['value'])
    def test_tls_failure_is_probe_error(self):
        self.assertEqual(self.facts((60,'000','TLS'))['access.whoami.https']['status'],'error')
    def test_docker_permission_failure_not_absence(self):
        self.assertEqual(self.facts((1,'','denied'))['runtime.docker.usable']['status'],'error')
    def test_digest_required(self):
        with self.assertRaises(Exception):probe.image_arg('worker=latest')
        self.assertEqual(probe.image_arg('worker=sha256:'+'a'*64)[0],'worker')
    def test_partial_probe_does_not_clear_uplinks_or_agent_on_error(self):
        with patch.object(probe,'run',return_value=(1,'','denied')),patch.object(probe.shutil,'which',return_value='/bin/tool'):
            report=probe.collect('fixture')
        self.assertNotIn('uplinks',report)
        self.assertNotIn('agent_present',report['facts'])
    def test_physical_network_and_agent_report(self):
        links=[{'ifname':'eth-test','operstate':'UP','addr_info':[{'family':'inet','scope':'global'}]},
               {'ifname':'wifi-test','operstate':'UP','addr_info':[{'family':'inet','scope':'global'}]},
               {'ifname':'veth-test','operstate':'UP','addr_info':[]}]
        def respond(args, timeout=8):
            if args[0]=='ip': return (0,json.dumps(links),'')
            if args[:2]==['docker','ps']:return (0,'isle-remote-agent','')
            return (0,'200','')
        def exists(path):return str(path) in ['/sys/class/net/eth-test/device','/sys/class/net/wifi-test/device','/sys/class/net/wifi-test/wireless']
        with patch.object(probe,'run',side_effect=respond),patch.object(probe.shutil,'which',return_value='/bin/tool'),patch.object(probe.Path,'exists',exists):
            report=probe.collect('econ-core')
        self.assertEqual([p['kind'] for p in report['uplinks']],['ethernet','wifi'])
        self.assertTrue(report['facts']['agent_present'])
        self.assertEqual(report['facts']['connectivity_mode'],'dual-home')
    def test_source_and_time_on_every_fact(self):
        for fact in self.facts((0,'200','')).values():
            self.assertIn('observed_at',fact);self.assertIn('source',fact)
if __name__=='__main__':unittest.main()
