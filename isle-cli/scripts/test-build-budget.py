import importlib.util
from pathlib import Path
import unittest
s=importlib.util.spec_from_file_location('budget',Path(__file__).with_name('build-budget.py'))
b=importlib.util.module_from_spec(s);s.loader.exec_module(b)
class Budget(unittest.TestCase):
    def sample(self,available=5000,pressure=0):
        return dict(total_mib=8000,available_mib=available,disk_free_mib=10000,pressure_full_avg10=pressure)
    def test_reserve_and_heap_overhead(self):
        p=b.plan(self.sample())
        self.assertTrue(p['ready']);self.assertEqual(p['workers'],1)
        self.assertLessEqual(p['memory_max_mib']+p['reserve_mib'],5000)
        self.assertGreaterEqual(p['memory_max_mib']-p['node_heap_mib'],1024)
    def test_refuses_tight_host(self):self.assertFalse(b.plan(self.sample(3000))['ready'])
    def test_refuses_existing_pressure(self):self.assertFalse(b.plan(self.sample(6000,6))['ready'])
    def test_caps_even_large_hosts(self):self.assertLessEqual(b.plan(self.sample(30000))['memory_max_mib'],4096)
if __name__=='__main__':unittest.main()
