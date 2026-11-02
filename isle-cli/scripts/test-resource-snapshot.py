import unittest
from unittest.mock import patch
from types import SimpleNamespace
from resource_snapshot import cpu_set,vm_allocation,activity,storage_inventory

class Resources(unittest.TestCase):
    def test_cpu_sets_and_pinning_are_not_exclusive(self):
        self.assertEqual(cpu_set('0-4,^2,7'),[0,1,3,4,7])
        v=vm_allocation('vm','<domain><vcpu current="2" cpuset="0-3">4</vcpu><cputune><vcpupin vcpu="0" cpuset="2"/></cputune></domain>')
        self.assertEqual(v['vcpus'],2);self.assertFalse(v['exclusive']);self.assertEqual(v['pins'][0]['cpus'],[2])
    def test_activity_delta(self):
        self.assertEqual(activity((100,40),(200,90)),50)
        self.assertIsNone(activity((100,40),(100,40)))
        self.assertIsNone(activity((100,40),(90,30)))
    @patch('resource_snapshot.shutil.disk_usage',return_value=SimpleNamespace(total=100*1048576,free=40*1048576,used=60*1048576))
    def test_storage_inheritance_dedup_and_no_fake_speed(self,_):
        child=dict(name='dm0',type='lvm',mountpoints=['/'],**{'maj:min':'253:0'})
        s=storage_inventory({'blockdevices':[dict(name='sda',type='disk',rota=True,tran='sata',size=200*1048576,children=[child]),dict(name='nvme0n1',type='disk',rota=False,tran='nvme',size=200*1048576,children=[child]),dict(name='loop0',type='loop',mountpoints=['/snap'])]})
        self.assertEqual(len(s['volumes']),1);self.assertEqual(s['volumes'][0]['tier'],'mixed');self.assertEqual(s['volumes'][0]['availableMiB'],40)
        self.assertEqual(len(s['drives']),2);self.assertNotIn('readMBps',s['drives'][0])
if __name__=='__main__':unittest.main()
