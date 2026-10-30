import importlib.util,json,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('probe',Path(__file__).with_name('capabilities.py'));probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)
class Probe(unittest.TestCase):
    def facts(self, result):
        with patch.object(probe,'run',return_value=result),patch.object(probe.shutil,'which',return_value='/bin/docker'):
            return probe.collect('fixture')['capability_evidence']['facts']
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
    def test_source_and_time_on_every_fact(self):
        for fact in self.facts((0,'200','')).values():
            self.assertIn('observed_at',fact);self.assertIn('source',fact)
if __name__=='__main__':unittest.main()
