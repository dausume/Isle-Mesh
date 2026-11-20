import json,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from storage_preflight import inspect
class Storage(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.mount=dict(target=str(self.root),uuid='disk-id',fstype='ext4',options='rw,relatime')
 def check(self,**kwargs):
  return inspect(str(self.root/'repo'), 'disk-id', 100, run=lambda *a,**k:SimpleNamespace(stdout=json.dumps({'filesystems':[self.mount]})),statvfs=lambda p:SimpleNamespace(f_flag=0,f_bavail=10,f_frsize=100),**kwargs)
 def test_valid_does_not_create_target(self):
  result=self.check();self.assertFalse((self.root/'repo').exists());self.assertEqual(result['availableBytes'],1000);self.assertFalse(result['bootDependencyVerified'])
 def test_wrong_disk(self):
  self.mount['uuid']='other';self.assertRaisesRegex(ValueError,'UUID',self.check)
 def test_absent_disk_root_fallback(self):
  self.mount['target']='/';self.assertRaisesRegex(ValueError,'fallback',self.check)
 def test_readonly(self):
  self.mount['options']='ro';self.assertRaisesRegex(ValueError,'read-write',self.check)
 def test_symlink(self):
  (self.root/'repo').symlink_to('/tmp');self.assertRaisesRegex(ValueError,'symlink',self.check)
 def test_insufficient_capacity(self):
  with self.assertRaisesRegex(ValueError,'Insufficient'):
   inspect(str(self.root/'repo'),'disk-id',2000,run=lambda *a,**k:SimpleNamespace(stdout=json.dumps({'filesystems':[self.mount]})),statvfs=lambda p:SimpleNamespace(f_flag=0,f_bavail=10,f_frsize=100))
 def test_invalid_plan(self):
  for path,uuid,size in [('/tmp/../bad','x',0),('/tmp','',0),('/tmp','x',True),('relative','x',0)]:
   with self.assertRaises(ValueError):inspect(path,uuid,size)
if __name__=='__main__':unittest.main()
