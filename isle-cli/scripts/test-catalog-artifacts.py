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
if __name__=='__main__':unittest.main()
