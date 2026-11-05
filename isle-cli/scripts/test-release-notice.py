import tempfile,unittest,json,copy
from pathlib import Path
from release_notice import notice
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
if __name__=='__main__':unittest.main()
