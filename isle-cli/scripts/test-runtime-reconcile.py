#!/usr/bin/env python3
import json,os,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import runtime_reconcile as runtime
class Runtime(unittest.TestCase):
 def test_preserves_existing_project_and_identity_without_build_or_rejoin(self):
  self.exercise_runtime('agent')
 def test_core_backend_gets_explicit_registry_origin_and_identity(self):
  self.exercise_runtime('prf-isle-backend')
 def exercise_runtime(self,name):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);compose=root/'compose.yml';compose.write_text('services:\n  agent:\n    hostname: ${ISLE_HOSTNAME}\n    environment: [VIRTUAL_MAC=${VIRTUAL_MAC}]\n')
   canonical=root/'device-name';canonical.write_text('member-core')
   config='sha256:'+'c'*64;manifest='sha256:'+'d'*64;calls=[];changed=False
   labels={'com.docker.compose.project':'existing','com.docker.compose.service':'agent','com.docker.compose.project.working_dir':str(root),'com.docker.compose.project.config_files':str(compose),'isle.role':'remote-proxy'}
   def inspect(container):
    return {'Image':config if changed else 'old-image','Config':{'Image':manifest if changed else 'old-tag','Labels':labels,'Env':['ISLE_HOSTNAME=old-hostname','VIRTUAL_MAC=02:00:00:00:00:09','SECRET=not-exported']},'State':{'Running':True,'Health':{'Status':'healthy'}}}
   def run(argv,**kwargs):
    nonlocal changed
    calls.append((argv,kwargs))
    if argv[:2]==['docker','compose']:changed=True
    return subprocess.CompletedProcess(argv,0,manifest+'\n')
   row={'container':name,'image':{'ref':'example/agent:v1','configDigest':config,'manifestDigest':manifest,'archive':{'sha256':'a'*64,'url':'https://apt.isle/image.tar','size':123}}}
   with patch.object(runtime,'OVERLAYS',root/'overlays'),patch.object(runtime,'CACHE',root/'cache'),patch.object(runtime,'CANONICAL',canonical),patch('dns_publication.resolver',return_value='10.10.0.1'),patch.object(runtime,'inspect',side_effect=inspect),patch.object(runtime,'fetch',return_value=root/'archive'),patch.object(runtime,'verify_archive',return_value={config,manifest}),patch.object(runtime,'run',side_effect=run),patch.object(runtime.subprocess,'run',side_effect=run):
    runtime.reconcile({'runtimes':{'remote':[row]}},'remote')
   deployment=next((a,k) for a,k in calls if a[:2]==['docker','compose'])
   self.assertIn('--no-build',deployment[0]);self.assertIn('--no-deps',deployment[0]);self.assertNotIn('down',deployment[0])
   self.assertEqual(deployment[1]['env']['ISLE_HOSTNAME'],'member-core');self.assertEqual(deployment[1]['env']['VIRTUAL_MAC'],'02:00:00:00:00:09');self.assertNotIn('SECRET',deployment[1]['env'])
   self.assertEqual(json.loads((root/'overlays/existing.json').read_text())['services']['agent']['image'],manifest)
   self.assertIn('${ISLE_HOSTNAME}',compose.read_text())
   if name=='prf-isle-backend':
    env=json.loads((root/'overlays/existing.json').read_text())['services']['agent']['environment']
    self.assertEqual(env['ISLE_CORE_API'],'https://api.polari.isle');self.assertEqual(env['POLARI_MACHINE_NAME'],'member-core');self.assertEqual(env['POLARI_INSTANCE_NAME'],'polari')
 def test_rejects_incomplete_or_unsafe_targets_before_execution(self):
  valid={'container':'agent','image':{'ref':'example/agent:v1','configDigest':'sha256:'+'a'*64,'archive':{'url':'https://apt.isle/image.tar','size':123,'sha256':'b'*64}}}
  for mutation in ('digest','http','duplicate','health'):
   row=json.loads(json.dumps(valid));rows=[row]
   if mutation=='digest':row['image']['configDigest']='archive-hash'
   if mutation=='http':row['image']['archive']['url']='http://apt.isle/image.tar'
   if mutation=='duplicate':rows.append(row)
   if mutation=='health':row['healthUrl']='https://external.example/health'
   with self.subTest(mutation=mutation),self.assertRaises(ValueError):runtime.targets({'runtimes':{'core':rows}},'core')
 def test_slow_startup_remains_retryable_until_declared_deadline(self):
  error=subprocess.CalledProcessError(22,['curl'])
  with patch.object(runtime,'run',side_effect=[error,error,subprocess.CompletedProcess([],0)]) as run,patch.object(runtime.time,'monotonic',side_effect=[0,25,100]),patch.object(runtime.time,'sleep'):
   runtime.wait_route('https://api.polari.isle/api/health',180)
   self.assertEqual(run.call_count,3)
  with patch.object(runtime,'run',side_effect=error),patch.object(runtime.time,'monotonic',side_effect=[0,181]),patch.object(runtime.time,'sleep'):
   with self.assertRaisesRegex(RuntimeError,'180 seconds'):runtime.wait_route('https://api.polari.isle/api/health',180)
 def test_unknown_role_is_rejected(self):
  with self.assertRaises(ValueError):runtime.targets({'runtimes':{'foreign':[]}},'core')
if __name__=='__main__':unittest.main()
