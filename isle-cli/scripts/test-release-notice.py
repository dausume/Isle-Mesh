import tempfile,unittest,json,copy,os,subprocess,sys
from pathlib import Path
from release_notice import notice,WRITE
class Notice(unittest.TestCase):
 def test_exact_latest_packages(self):
  with tempfile.TemporaryDirectory() as d:
   rows=[]
   for name in ['isle-mesh-cli','isle-app-store','polari-shell-core']:
    for v in ['0.1.2','0.1.10']:
     rows.append(f'Package: {name}\nVersion: {v}\nFilename: ./{name}_{v}_all.deb\nSHA256: '+ 'a'*64)
   (Path(d)/'Packages').write_text('\n\n'.join(rows))
   a=notice(d);self.assertTrue(all(p['version']=='0.1.10' for p in a['packages']))
   self.assertEqual(a['id'],notice(d)['id']);self.assertEqual(a['command'],'sudo isle update apply '+a['id']);self.assertNotIn(' upgrade',a['command'])
 def test_combined_packages_and_runtime_images_share_one_reviewed_id(self):
  from update import validate
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)
   names=['isle-mesh-cli','isle-app-store','polari-shell-core','polari-complete-offline']
   (root/'Packages').write_text('\n\n'.join(f'Package: {name}\nVersion: 0.1.1\nFilename: ./{name}_0.1.1_all.deb\nSHA256: '+'a'*64 for name in names))
   row={'container':'agent','image':{'ref':'example/agent:v1','configDigest':'sha256:'+'b'*64,'archive':{'url':'https://apt.isle/image.tar','size':123,'sha256':'c'*64}}}
   (root/'runtime-release.json').write_text(json.dumps({'runtimes':{'remote':[row]}}))
   release=notice(d);validate(release)
   self.assertIn('polari-complete-offline',release['bundles'])
   altered=copy.deepcopy(release);altered['runtimes']['remote'][0]['image']['configDigest']='sha256:'+'d'*64
   with self.assertRaisesRegex(ValueError,'Release ID'):validate(altered)
 def test_incomplete_set_not_published(self):
  with tempfile.TemporaryDirectory() as d:
   (Path(d)/'Packages').write_text('')
   with self.assertRaises(ValueError):notice(d)
class StartSetting(unittest.TestCase):
 def test_the_cores_record_rides_the_notice_and_a_bad_or_missing_one_does_not(self):
  from release_notice import start_setting
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'start-setting.json'
   self.assertIsNone(start_setting(p))
   p.write_text('{"start": "core-only", "setAt": "t", "setOn": "core1"}')
   self.assertEqual(start_setting(p),{'start':'core-only','setAt':'t','setOn':'core1'})
   for bad in ('not json','[]','{"start": "everywhere"}'):
    p.write_text(bad);self.assertIsNone(start_setting(p))
 def test_the_setting_is_not_part_of_the_release_id(self):
  with tempfile.TemporaryDirectory() as d:
   (Path(d)/'Packages').write_text('\n\n'.join(f'Package: {name}\nVersion: 1\nFilename: ./{name}_1_all.deb\nSHA256: '+'a'*64 for name in ['isle-mesh-cli','isle-app-store','polari-shell-core']))
   from update import validate
   release=notice(d);release['startSetting']={'start':'core-only'};validate(release)
class Write(unittest.TestCase):
 def put(self,d,payload,old=None):
  p=Path(d)/'isle-release.json'
  if old is not None:p.write_text(old)
  r=subprocess.run([sys.executable,'-c',WRITE],input=json.dumps(payload),text=True,capture_output=True,env=dict(os.environ,DATABASE_PATH=str(Path(d)/'db.sqlite')))
  self.assertEqual(r.returncode,0,r.stderr);return json.loads(p.read_text()),p
 def test_run_carried_over(self):
  with tempfile.TemporaryDirectory() as d:
   got,_=self.put(d,{'id':'new'},json.dumps({'id':'old','extra':1,'run':{'runId':'r'}}))
   self.assertEqual(got,{'id':'new','run':{'runId':'r'}})
 def test_intake_answers_carried_over(self):
  with tempfile.TemporaryDirectory() as d:
   got,_=self.put(d,{'id':'new'},json.dumps({'id':'old','intake':{'answers':{'m':{'state':'done'}}}}))
   self.assertEqual(got,{'id':'new','intake':{'answers':{'m':{'state':'done'}}}})
 def test_new_run_wins(self):
  with tempfile.TemporaryDirectory() as d:
   got,_=self.put(d,{'id':'n','run':{'runId':'b'}},json.dumps({'id':'o','run':{'runId':'a'}}))
   self.assertEqual(got['run'],{'runId':'b'})
 def test_no_previous_file(self):
  with tempfile.TemporaryDirectory() as d:self.assertEqual(self.put(d,{'id':'n'})[0],{'id':'n'})
 def test_previous_corrupt(self):
  with tempfile.TemporaryDirectory() as d:self.assertEqual(self.put(d,{'id':'n'},'{not json')[0],{'id':'n'})
 def test_previous_without_run(self):
  with tempfile.TemporaryDirectory() as d:
   got,p=self.put(d,{'id':'n'},json.dumps({'id':'o','extra':1}))
   self.assertEqual(got,{'id':'n'});self.assertEqual(os.listdir(d),['isle-release.json'])
if __name__=='__main__':unittest.main()
