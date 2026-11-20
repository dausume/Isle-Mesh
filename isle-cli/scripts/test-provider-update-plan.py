#!/usr/bin/env python3
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from provider_reconcile import planned_targets
class ProviderPlan(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.image={'ref':'example/provider:v2','configDigest':'sha256:'+'a'*64,'archive':{'url':'https://apt.isle/image.tar','size':123,'sha256':'b'*64}}
  self.release={'runtimes':{'core':[{'container':'prf-isle-'+c,'image':self.image} for c in ('backend','frontend')]}}
 def write(self,name,role):
  path=self.root/name;path.mkdir();(path/'deployment.json').write_text(json.dumps(dict(name=name,role=role,modules='islemesh',backend_url='https://api.'+name+'.isle')));return path
 def test_two_unrelated_providers_use_typed_core_images_without_runtime_mutation(self):
  self.write('remote-compute','backend');self.write('workbench','frontend')
  with patch('subprocess.run',side_effect=AssertionError('preview must not execute')):
   rows=planned_targets(self.release,self.root)
  self.assertEqual(len(rows),2)
  self.assertEqual(rows[0]['dataVolume'],'prf-remote-compute_backend-data')
  self.assertEqual(rows[0]['image']['configDigest'],self.image['configDigest'])
  self.assertIn('no automatic database snapshot',rows[0]['dataPolicy'])
  self.assertIsNone(rows[1]['dataVolume'])
 def test_missing_release_provider_fails_before_claiming_plan(self):
  self.write('remote-compute','backend')
  with self.assertRaises(ValueError):planned_targets({'runtimes':{'core':[]}},self.root)
 def test_identity_and_role_mismatch_fail_closed(self):
  p=self.write('remote-compute','backend')/'deployment.json';d=json.loads(p.read_text())
  for field,value in [('name','other'),('role','invalid')]:
   invalid={**d,field:value};p.write_text(json.dumps(invalid))
   with self.assertRaises(ValueError):planned_targets(self.release,self.root)
if __name__=='__main__':unittest.main()
