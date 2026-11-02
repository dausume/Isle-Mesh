import tempfile,unittest
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
   self.assertEqual(a['id'],notice(d)['id']);self.assertIn('--no-remove',a['command']);self.assertNotIn(' upgrade',a['command'])
 def test_incomplete_set_not_published(self):
  with tempfile.TemporaryDirectory() as d:
   (Path(d)/'Packages').write_text('')
   with self.assertRaises(ValueError):notice(d)
if __name__=='__main__':unittest.main()
