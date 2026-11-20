import importlib.util,sys,types,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('probe',Path(__file__).with_name('capabilities.py'));probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)
class Fact(unittest.TestCase):
    def facts(self,module):
        with patch.dict(sys.modules,{'release_run':module}),patch.object(probe,'run',return_value=(0,'200','')),patch.object(probe.shutil,'which',return_value='/bin/tool'):
            return probe.collect('fixture')['capability_evidence']['facts']
    def fake(self,fn):
        m=types.ModuleType('release_run')
        if fn:m.report_fact=fn
        return m
    def test_fact_sent_as_string(self):
        f=self.facts(self.fake(lambda:'{"runId":"r","state":"done"}'))['isle.release_run']
        self.assertEqual((f['status'],f['value']),('observed','{"runId":"r","state":"done"}'))
    def test_none_means_no_fact(self):self.assertNotIn('isle.release_run',self.facts(self.fake(lambda:None)))
    def test_missing_function_means_no_fact(self):self.assertNotIn('isle.release_run',self.facts(self.fake(None)))
    def test_exception_means_no_fact(self):
        def boom():raise RuntimeError('x')
        self.assertNotIn('isle.release_run',self.facts(self.fake(boom)))
    def test_non_string_means_no_fact(self):self.assertNotIn('isle.release_run',self.facts(self.fake(lambda:{'a':1})))
    def test_import_error_means_no_fact(self):
        with patch.dict(sys.modules,{'release_run':None}),patch.object(probe,'run',return_value=(0,'200','')),patch.object(probe.shutil,'which',return_value='/bin/tool'):
            self.assertNotIn('isle.release_run',probe.collect('fixture')['capability_evidence']['facts'])
if __name__=='__main__':unittest.main()
