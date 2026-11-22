#!/usr/bin/env python3
import hashlib,importlib.util,io,json,os,subprocess,tarfile,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
import catalog_artifacts as artifacts
from image_identity import verify_archive,admit_installed
from catalog_publish import publish

def archive(path,ref):
 config=b'{"architecture":"amd64","os":"linux","rootfs":{"type":"layers","diff_ids":[]}}';layer=b'layer-data'
 digest=lambda b:'sha256:'+hashlib.sha256(b).hexdigest()
 cd,ld=digest(config),digest(layer)
 document=json.dumps(dict(schemaVersion=2,config=dict(digest=cd),layers=[dict(digest=ld)])).encode();md=digest(document)
 blobs={'index.json':json.dumps({'schemaVersion':2,'manifests':[{'digest':md}]}).encode(),'blobs/sha256/'+cd[7:]:config,'blobs/sha256/'+ld[7:]:layer,'blobs/sha256/'+md[7:]:document}
 with tarfile.open(path,'w') as t:
  for name,raw in blobs.items():
   m=tarfile.TarInfo(name);m.size=len(raw);t.addfile(m,io.BytesIO(raw))
 return dict(ref=ref,configDigest=cd,manifestDigest=md,archive=dict(source=str(path)))

class Artifacts(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
 def fixture(self,name):
  image=archive(self.root/(name+'.tar'),'example/'+name+':v1')
  compose=self.root/(name+'.yml');compose.write_text('services: {}')
  m=dict(schemaVersion=1,name=name,version='v1',compose=dict(source=str(compose)),images=[image],deploy=dict(service='web',port=80))
  path=self.root/(name+'.json');path.write_text(json.dumps(m));return path,m
 def test_typed_manifest_config_and_unrelated_identity(self):
  path,m=self.fixture('alpha');image=m['images'][0]
  identities=verify_archive(self.root/'alpha.tar',image)
  self.assertEqual(admit_installed(image['configDigest'],identities),image['configDigest'])
  self.assertEqual(admit_installed(image['manifestDigest'],identities),image['manifestDigest'])
  with self.assertRaises(ValueError):admit_installed('sha256:'+'f'*64,identities)
  image['configDigest']='sha256:'+'f'*64
  with self.assertRaises(ValueError):verify_archive(self.root/'alpha.tar',image)
 def test_two_catalog_apps_resolve_without_cwd_or_checkout(self):
  repo=self.root/'repo';cache=self.root/'cache';calls=[]
  def run(argv,**kw):
   calls.append(argv)
   if argv[0]=='curl':
    self.assertNotIn('-k',argv);self.assertNotIn('--insecure',argv)
    file=repo/argv[-1].split('https://apt.isle/',1)[1]
    Path(argv[argv.index('--output')+1]).write_bytes(file.read_bytes());return subprocess.CompletedProcess(argv,0)
   if argv[:2]==['docker','compose']:
    current=json.loads((Path(argv[argv.index('-f')+1]).parent/'manifest.json').read_text())
    self.assertEqual(argv[argv.index('--project-name')+1],'isle-'+current['name'])
    return subprocess.CompletedProcess(argv,0,json.dumps({'services':{'web':{'image':current['images'][0]['ref']}}}))
   if argv[:2]==['docker','load']:return subprocess.CompletedProcess(argv,0)
   if argv[:3]==['docker','image','inspect']:
    name=argv[-1].split('/')[1].split(':')[0]
    return subprocess.CompletedProcess(argv,0,images[name]['manifestDigest']+'\n')
   raise AssertionError(argv)
  images={};published={}
  for name in ['alpha','bravo']:
   path,m=self.fixture(name);images[name]=m['images'][0]
   _,published[name]=publish(path,repo)
  empty=self.root/'empty';empty.mkdir();old=Path.cwd();os.chdir(empty)
  try:
   with patch.object(artifacts,'CACHE',cache),patch.object(artifacts.subprocess,'run',side_effect=run):
    for name in ['alpha','bravo']:
     manifest,compose=artifacts.prepare(published[name],name)
     config=json.loads(compose.read_text());self.assertEqual(config['services']['web']['image'],images[name]['manifestDigest'])
     self.assertEqual(manifest['name'],name);self.assertEqual(list(empty.iterdir()),[])
  finally:os.chdir(old)
  self.assertEqual(sum(c[:2]==['docker','load'] for c in calls),2)
 def test_readiness_failure_is_saved_and_not_reported_success(self):
  loader=Mock();module=Mock();module.status.return_value={'deployed':False,'state':'runtime-started','error':'Trusted HTTPS failed'}
  with patch.object(artifacts,'stage'),patch.object(artifacts.importlib.util,'spec_from_file_location',return_value=Mock(loader=loader)),patch.object(artifacts.importlib.util,'module_from_spec',return_value=module),patch.object(artifacts.time,'monotonic',side_effect=[0,61]):
   with self.assertRaisesRegex(ValueError,'Trusted HTTPS failed'):artifacts.wait_ready('alpha','a'*32,self.root)
  self.assertFalse(json.loads((self.root/'readiness.json').read_text())['deployed'])
 def test_invalid_ingress_fails_before_docker(self):
  path,m=self.fixture('alpha');m['deploy']['port']=70000;path.write_text(json.dumps(m));_,release=publish(path,self.root/'repo')
  with patch.object(artifacts,'CACHE',self.root/'cache'),patch.object(artifacts,'fetch',return_value=next((self.root/'repo/catalog/alpha/v1').glob('*.json'))),patch.object(artifacts.subprocess,'run') as run:
   with self.assertRaisesRegex(ValueError,'service/port'):artifacts.prepare(release,'alpha')
   run.assert_not_called()
 def test_invalid_probe_manifest_rejected_before_assets_or_docker(self):
  source,definition=self.fixture('alpha');repo=self.root/'repo'
  _,release=publish(source,repo)
  published=next((repo/'catalog/alpha/v1').glob('*.json'))
  valid=json.loads(published.read_text());bad=self.root/'bad-manifest.json'
  for path in ('/health#fragment','/health#','relative','/bad path','/'+('x'*512)):
   with self.subTest(path=path):
    invalid={**valid,'health':{'path':path}};bad.write_text(json.dumps(invalid))
    with self.assertRaisesRegex(ValueError,'health path'):artifacts.validate_manifest(invalid)
    with patch.object(artifacts,'CACHE',self.root/'cache'),patch.object(artifacts,'fetch',return_value=bad) as fetch,patch.object(artifacts.subprocess,'run') as run:
     with self.assertRaisesRegex(ValueError,'health path'):artifacts.prepare(release,'alpha')
     fetch.assert_called_once() # only the manifest, no compose or image fetch
     run.assert_not_called() # no Docker config/load or other process
    source.write_text(json.dumps({**definition,'health':{'path':path}}))
    with patch('catalog_publish.publish_asset') as asset,patch('catalog_publish.verify_archive') as verify:
     with self.assertRaisesRegex(ValueError,'health path'):publish(source,repo)
     asset.assert_not_called();verify.assert_not_called()
 def test_descriptor_and_bad_checksum_fail_before_docker(self):
  for url in ['http://apt.isle/x','https://evil.example/x','https://user:pass@apt.isle/x']:
   with self.assertRaises(ValueError):artifacts.descriptor(dict(url=url,sha256='a'*64,size=1))
  target=self.root/'target';calls=[]
  def fake(argv,**kw):calls.append(argv);Path(argv[argv.index('--output')+1]).write_bytes(b'bad')
  with patch.object(artifacts.subprocess,'run',side_effect=fake):
   with self.assertRaises(ValueError):artifacts.fetch(dict(url='https://apt.isle/x',sha256='a'*64,size=3),target)
  self.assertFalse(target.exists());self.assertTrue(all(c[0]=='curl' for c in calls))
 def test_declared_domain_rule(self):
  ok={'board-engines':{'domain':'board-engines.isle'}}
  rule=artifacts.declared_domain
  self.assertEqual(rule('twin-board-engines','twin.board-engines.isle','board-engines',ok),'twin.board-engines.isle')
  self.assertEqual(rule('twin-board-engines','twin-board-engines.isle','board-engines',ok),'twin-board-engines.isle')
  self.assertEqual(rule('twin-board-engines','twin.board-engines.isle','board-engines',{**ok,'twin-board-engines':{'domain':'twin.board-engines.isle'}}),'twin.board-engines.isle')
  for dom in ('twin.other.isle','a.b.board-engines.isle','*.board-engines.isle','Twin.board-engines.isle','board-engines.isle','.board-engines.isle','twin.board-engines.isle.isle'):
   with self.subTest(domain=dom),self.assertRaises(ValueError):rule('twin-board-engines',dom,'board-engines',ok)
  with self.assertRaisesRegex(ValueError,'board-engines'):rule('twin-board-engines','twin.board-engines.isle','board-engines',{})
  with self.assertRaisesRegex(ValueError,'board-engines'):rule('twin-board-engines','twin.board-engines.isle','board-engines',{'board-engines':{'domain':'be.isle'}})
  with self.assertRaisesRegex(ValueError,'rival'):rule('twin-board-engines','twin.board-engines.isle','board-engines',{**ok,'rival':{'domain':'twin.board-engines.isle'}})
 def test_declared_domain_manifest_fields(self):
  m=dict(deploy=dict(service='web',port=80),images=[])
  artifacts.validate_manifest(m)
  m['deploy'].update(domain='twin.alpha.isle',parent='alpha');artifacts.validate_manifest(m)
  for extra in (dict(domain='twin.alpha.isle'),dict(parent='alpha')):
   m=dict(deploy=dict(service='web',port=80),images=[]);m['deploy'].update(extra)
   with self.assertRaisesRegex(ValueError,'together'):artifacts.validate_manifest(m)
  for extra in (dict(domain='x',parent='alpha'),dict(domain='twin.alpha.isle',parent='Bad'),dict(domain=5,parent='alpha')):
   m=dict(deploy=dict(service='web',port=80),images=[]);m['deploy'].update(extra)
   with self.assertRaises(ValueError):artifacts.validate_manifest(m)
 def install_domain(self,deploy,registry,instance,served=None,name='twin-board-engines',health=None,staged=False,slots=None,declare=None):
  m=dict(name=name,version='v1',deploy=dict(service='web',port=80,**deploy),**({'health':health} if health else {}),**({'storageSlots':slots} if slots is not None else {}));calls=self.calls=[]
  reg=self.root/'registry.json';reg.write_text(json.dumps({'apps':registry}))
  real=artifacts.Path
  class P(type(real())):
   def __new__(cls,*a):return real(str(reg)) if a==('/etc/isle-mesh/agent/registry.json',) else real(*a)
  catalog=self.root/'catalog.json';catalog.write_text(json.dumps({'entry':{'name':name,'install_plan':{'release':{'sha256':'a'*64}}}}))
  def run(argv,**kw):calls.append(argv);return subprocess.CompletedProcess(argv,0)
  def slots_of(*a):calls.append(('slots',)+a);return (declare or (lambda *a:[]))(*a)   # never the records of this machine
  with patch.object(artifacts,'declare_slots',side_effect=slots_of),patch.object(artifacts,'Path',P),patch.object(artifacts.os,'geteuid',return_value=0),patch.dict(os.environ,ISLE_OPERATION_ID='a'*32,ISLE_OPERATION_DIR=str(self.root)),patch.object(artifacts,'stage'),patch.object(artifacts,'atomic'),patch.object(artifacts,'wait_ready',return_value=0),patch.object(artifacts,'prepare',return_value=(m,self.root/'c.yml')),patch.object(artifacts.subprocess,'run',side_effect=run):
   code=artifacts.install(catalog,instance,served=served,staged=staged)
  return code,calls
 def test_install_domain_precedence(self):
  parent={'board-engines':{'domain':'board-engines.isle'}}
  decl=dict(domain='twin.board-engines.isle',parent='board-engines')
  def used(calls):return calls[-1][calls[-1].index('--domain')+1]
  _,c=self.install_domain(decl,parent,'twin-board-engines');self.assertEqual(used(c),'twin.board-engines.isle')
  _,c=self.install_domain(decl,parent,'twin-board-engines',served='apt.isle');self.assertEqual(used(c),'apt.isle')
  _,c=self.install_domain(decl,{},'twin-board-engines',served='apt.isle');self.assertEqual(used(c),'apt.isle')  # served skips the parent rule
  with self.assertRaisesRegex(ValueError,'board-engines'):self.install_domain(decl,{},'twin-board-engines')
  with self.assertRaisesRegex(ValueError,'renamed instance'):self.install_domain(decl,parent,'twin-board-engines-2')
  # manifest without the fields behaves as before: registered domain, else <instance>.isle
  _,c=self.install_domain({},{'twin-board-engines':{'domain':'old.isle'}},'twin-board-engines');self.assertEqual(used(c),'old.isle')
  _,c=self.install_domain({},{},'twin-board-engines');self.assertEqual(used(c),'twin-board-engines.isle')
  _,c=self.install_domain({},{},'twin-board-engines-2');self.assertEqual(used(c),'twin-board-engines-2.isle')
 SLOT=dict(name='projects',role='assets',target='/home/app/projects',writable=True)
 def test_install_keeps_the_storage_slots_before_it_deploys(self):
  code,calls=self.install_domain({},{},'alpha',name='alpha',slots=[self.SLOT])
  self.assertEqual(code,0);self.assertEqual(calls[0],('slots','alpha',[self.SLOT]))
  self.assertEqual(calls[1][:4],['bash',str(artifacts.SCRIPTS/'app.sh'),'deploy','alpha']);self.assertEqual(len(calls),2)
  _,calls=self.install_domain({},{},'alpha-2',name='alpha',slots=[self.SLOT]);self.assertEqual(calls[0],('slots','alpha-2',[self.SLOT]))   # the slots are the instance's
  _,calls=self.install_domain({},{},'alpha',name='alpha');self.assertEqual(calls[0],('slots','alpha',None))   # a release without the list: said as it is
 def test_install_stops_before_any_deploy_when_a_slot_cannot_be_given(self):
  def refuse(*a):raise ValueError('the storage slot projects of alpha cannot be used: not bound')
  with self.assertRaisesRegex(ValueError,'cannot be used: not bound'):self.install_domain({},{},'alpha',name='alpha',slots=[self.SLOT],declare=refuse)
  self.assertEqual(self.calls,[('slots','alpha',[self.SLOT])])   # no app.sh deploy, nothing started
 def test_storage_slots_of_a_release_are_read_when_it_is_validated_and_published(self):
  m=dict(deploy=dict(service='web',port=80),images=[],storageSlots=[self.SLOT]);artifacts.validate_manifest(m)
  artifacts.validate_manifest(dict(m,storageSlots=[]))
  artifacts.validate_manifest(dict(deploy=dict(service='web',port=80),images=[],storage=dict(bindParameter='ISLE_DATASET_PATH',mount='/data',readOnly=True)))   # the dataset bind of a hosting app is another key
  for bad in ([dict(self.SLOT,role='attic')],[dict(self.SLOT,target='relative')],[self.SLOT,self.SLOT],dict(self.SLOT),None,'projects'):
   with self.subTest(bad=bad):
    with self.assertRaises(ValueError):artifacts.validate_manifest(dict(m,storageSlots=bad))
    source,definition=self.fixture('alpha');source.write_text(json.dumps(dict(definition,storageSlots=bad)))
    with patch('catalog_publish.publish_asset') as asset,patch('catalog_publish.verify_archive') as verify:
     with self.assertRaises(ValueError):publish(source,self.root/'repo')
     asset.assert_not_called();verify.assert_not_called()
  source,definition=self.fixture('bravo');source.write_text(json.dumps(dict(definition,storageSlots=[self.SLOT])))
  _,release=publish(source,self.root/'repo')
  self.assertEqual(json.loads(next((self.root/'repo/catalog/bravo/v1').glob('*.json')).read_text())['storageSlots'],[self.SLOT])   # published as declared
 def test_a_slot_for_a_service_the_compose_file_lacks_is_refused_before_any_image(self):
  repo=self.root/'repo';loads=[]
  def run(argv,**kw):
   if argv[0]=='curl':
    Path(argv[argv.index('--output')+1]).write_bytes((repo/argv[-1].split('https://apt.isle/',1)[1]).read_bytes());return subprocess.CompletedProcess(argv,0)
   if argv[:2]==['docker','compose']:return subprocess.CompletedProcess(argv,0,json.dumps({'services':{'web':{'image':'example/alpha:v1'}}}))
   loads.append(argv);return subprocess.CompletedProcess(argv,0,images['manifestDigest']+'\n')
  for slot,words in ((dict(self.SLOT,service='editor'),'Storage slot projects names a service missing from Compose'),(dict(self.SLOT,service='web'),None),(self.SLOT,None)):
   with self.subTest(slot=slot):
    source,definition=self.fixture('alpha');images=definition['images'][0];source.write_text(json.dumps(dict(definition,storageSlots=[slot])))
    _,release=publish(source,repo);loads.clear()
    with patch.object(artifacts,'CACHE',self.root/'cache'),patch.object(artifacts.subprocess,'run',side_effect=run):
     if words:
      with self.assertRaisesRegex(ValueError,words):artifacts.prepare(release,'alpha')
      self.assertEqual(loads,[])
     else:self.assertEqual(artifacts.prepare(release,'alpha')[0]['storageSlots'],[slot])
 def test_staged_install_probes_the_release_health_path(self):
  # Storage fix-list item 5: the staged probe (app-deploy.sh, by --resolve) asks for the path the release declares.
  where='/.well-known/isle-storage/current'
  def path(calls):return calls[-1][calls[-1].index('--health-path')+1]
  _,c=self.install_domain({},{},'isle-content-host',served='apt.isle',name='isle-content-host',health={'path':where},staged=True)
  self.assertEqual(path(c),where);self.assertIn('--staged',c[-1])
  _,c=self.install_domain({},{},'isle-content-host',served='apt.isle',name='isle-content-host',staged=True)
  self.assertEqual(path(c),'/')   # a release that declares none is probed at /
  release=json.loads((Path(__file__).resolve().parent.parent/'releases'/'isle-content-host'/'release.json').read_text())
  self.assertEqual(release['health'],{'path':where})
  deploy=(Path(__file__).resolve().parent/'app-deploy.sh').read_text()
  staged=deploy[deploy.index('RESOLVE_ARGS=(--resolve'):]
  self.assertIn('"https://$DOMAIN$HEALTH_PATH"',staged[:staged.index('READY=1')])
if __name__=='__main__':unittest.main()
