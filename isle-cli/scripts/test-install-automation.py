#!/usr/bin/env python3
import importlib.util,json,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import member_preflight as pre
import host_network as network
import provider_images as images
import provider_readiness as readiness
import engine_sync as engines
import operations
ROOT=Path(__file__).parent
spec=importlib.util.spec_from_file_location('components',ROOT/'polari-component-config.py');config=importlib.util.module_from_spec(spec);spec.loader.exec_module(config)

class Automation(unittest.TestCase):
 def setUp(self):
  guard=patch('os.geteuid',return_value=0);guard.start();self.addCleanup(guard.stop)
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  for d in ['agent/remote','ca']:(self.root/d).mkdir(parents=True)
  (self.root/'agent/agent.mode').write_text('remote')
  (self.root/'agent/remote/hostname').write_text('member-one')
  (self.root/'ca/isle-root.crt').write_text('fixture')
 def test_firefox_policy_preserves_other_roots_and_removes_only_isle(self):
  from firefox_trust import policy
  directory=self.root/'policy';directory.mkdir()
  p=directory/'policies.json';p.write_text(json.dumps({'policies':{'DisableTelemetry':True,'Certificates':{'Install':['/other/root.crt']}}}))
  policy(directory,self.root/'ca/isle-root.crt')
  policy(directory,self.root/'ca/isle-root.crt')
  d=json.loads(p.read_text());self.assertEqual(len(d['policies']['Certificates']['Install']),2)
  policy(directory,remove=True)
  self.assertEqual(json.loads(p.read_text()),{'policies':{'DisableTelemetry':True,'Certificates':{'Install':['/other/root.crt']}}})
  self.assertFalse((directory/'isle-root.crt').exists())
 def test_identity_migrates_only_saved_registration_and_conflicts_fail(self):
  self.assertEqual(pre.identity(self.root),'member-one');self.assertFalse((self.root/'device-name').exists())
  pre.identity(self.root,True);self.assertEqual((self.root/'device-name').read_text(),'member-one\n')
  (self.root/'agent/remote/hostname').write_text('another-device')
  with self.assertRaisesRegex(ValueError,'disagree'):pre.identity(self.root,True)
  self.assertEqual((self.root/'device-name').read_text(),'member-one\n')
 def test_missing_identity_trust_credentials_before_host_actions(self):
  with patch.object(pre.subprocess,'run') as run:
   with self.assertRaisesRegex(ValueError,'isle certs pair'):pre.check(self.root,hosting=True)
   c=self.root/'ca/member-enrollment.json';c.write_text(json.dumps({'device':'member-one','endpoint':'https://certs.isle/enroll','token':'a'*64}));c.chmod(0o600)
   self.assertTrue(pre.check(self.root,hosting=True)['hostingChecked'])
   c.chmod(0o644)
   with self.assertRaisesRegex(ValueError,'root-only'):pre.check(self.root,hosting=True)
   (self.root/'ca/isle-root.crt').unlink()
   with self.assertRaisesRegex(ValueError,'Missing Isle trust'):pre.check(self.root)
   (self.root/'agent/remote/hostname').unlink()
   with self.assertRaisesRegex(ValueError,'identity missing'):pre.check(self.root)
 def test_member_dns_uses_saved_interface_and_refuses_wrong_route(self):
  (self.root/'agent/remote/interface.conf').write_text('enp2s0')
  (self.root/'agent/remote/discovery.json').write_text(json.dumps({'router_ip':'10.23.0.1'}))
  with patch.object(network,'run',return_value='[{"dev":"enp2s0"}]'):
   self.assertEqual(network.member_plan(self.root),{'interface':'enp2s0','gateway':'10.23.0.1'})
  with patch.object(network,'run',return_value='[{"dev":"wlan0"}]'),self.assertRaisesRegex(ValueError,'joined interface'):
   network.member_plan(self.root)
 def test_generated_trust_dns_and_immutable_images_are_persistent(self):
  pins={'backend':'sha256:'+'a'*64,'frontend':'sha256:'+'b'*64}
  compose,_,_=config.generate('compute','islemesh,board','both','/state/compute',images=pins,dns='10.23.0.1')
  b=compose['services']['backend'];self.assertEqual(b['image'],pins['backend'])
  self.assertEqual(b['dns'],['10.23.0.1']);self.assertIn('/etc/ssl/certs:/etc/isle-trust:ro',b['volumes'])
  self.assertEqual(b['environment']['SSL_CERT_FILE'],'/etc/isle-trust/ca-certificates.crt')
  self.assertIn('backend-data:/data',b['volumes'])
  with self.assertRaisesRegex(ValueError,'immutable'):config.generate('compute','islemesh','backend','/state',images={'backend':'prf-backend:staging'})
 def test_partial_provider_directory_can_only_resume_original_operation(self):
  path=self.root/'provider';path.mkdir();ident='a'*32
  with self.assertRaisesRegex(ValueError,'another operation'):config.write_configuration(path,{}, {},{},ident)
  (path/'.operation-id').write_text(ident)
  config.write_configuration(path,{}, {},{'name':'provider'},ident)
  self.assertTrue((path/'deployment.json').exists())
  inode=(path/'runtime-config.json').stat().st_ino
  config.write_configuration(path,{}, {'new':True},{'name':'provider'},'b'*32)
  self.assertEqual((path/'runtime-config.json').stat().st_ino,inode)
 def test_delayed_declared_health_and_failure(self):
  replies=[subprocess.CompletedProcess([],7,'',''),subprocess.CompletedProcess([],0,'{"ok":true,"phase":"starting"}',''),subprocess.CompletedProcess([],0,'{"ok":true,"phase":"online"}','')]
  with patch.object(readiness.subprocess,'run',side_effect=replies),patch.object(readiness.time,'sleep'):
   readiness.wait('https://api.compute.isle/api/health',10)
  with patch.object(readiness.subprocess,'run',return_value=replies[0]),patch.object(readiness.time,'monotonic',side_effect=[0,11]),self.assertRaisesRegex(RuntimeError,'retry'):
   readiness.wait('https://api.compute.isle/api/health',10)
 def test_failed_engine_admission_propagates_and_untrusted_urls_refused(self):
  with patch.object(engines.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'{"ok":false}')),self.assertRaisesRegex(ValueError,'refused'):
   engines.request('https://api.compute.isle/api/islemesh/ingest/engine',{})
  for url in ['http://worker.isle','https://user:secret@worker.isle','https://outside.example']:
   with self.assertRaises(ValueError):engines.payload({'url':url,'device':'member-one','app':'worker','provides':'arbitrary'})
 def test_named_provider_operation_parameters_and_retry_boundary(self):
  args=['polari','instance','deploy','--name','alpha','--role','backend','--modules','islemesh']
  self.assertEqual(operations.request(args),args)
  with self.assertRaises(ValueError):operations.request(args+['--shell','bad'])
  with self.assertRaises(ValueError):operations.request(['polari','instance','deploy','--role','backend'])

if __name__=='__main__':unittest.main()
