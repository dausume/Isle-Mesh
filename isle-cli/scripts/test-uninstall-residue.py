import importlib.util,tempfile,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('residue',Path(__file__).with_name('uninstall-residue.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Residue(unittest.TestCase):
 def test_generated_deployment_only(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);(p/'docker-compose.yml').write_text('container_name: prf-isle-backend')
   self.assertTrue(m.generated_deployment(p));(p/'.git').mkdir();self.assertFalse(m.generated_deployment(p))
 def test_unrelated_compose_spared(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);(p/'docker-compose.yml').write_text('container_name: unrelated')
   self.assertFalse(m.generated_deployment(p))
 def test_only_isle_shell_processes(self):
  self.assertTrue(m.shell_process(['/usr/bin/java','--config','/usr/share/isle-app-store/polari-shell.json']))
  self.assertFalse(m.shell_process(['/usr/bin/java','--config','/home/user/other.json']))
  self.assertFalse(m.shell_process(['/bin/bash','-c','java --config /usr/share/isle-app-store/polari-shell.json']))
  self.assertTrue(m.shell_process(['/opt/polari-shell/lib/jcef-natives/jcef_helper','--user-data-dir=/home/user/.local/share/polari-shell/profiles/isle-store']))
  self.assertFalse(m.shell_process(['/opt/polari-shell/lib/jcef-natives/jcef_helper','--user-data-dir=/home/user/.local/share/polari-shell/profiles/other']))
if __name__=='__main__':unittest.main()
