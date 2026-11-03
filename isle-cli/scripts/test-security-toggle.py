import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
spec = importlib.util.spec_from_file_location('toggle', Path(__file__).with_name('security-toggle.py'))
toggle = importlib.util.module_from_spec(spec); spec.loader.exec_module(toggle)
class ToggleTests(unittest.TestCase):
 def test_https_origin(self):
  self.assertEqual(toggle.origin('https://polari.isle:443/isle'), 'https://polari.isle')
  for value in ['http://polari.isle','https://user@polari.isle','file:///etc/passwd','https://polari.isle?q=x']:
   with self.assertRaises(ValueError):toggle.origin(value)
 def test_symlink_and_hardlink_refused(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'config';p.write_text('{}');link=Path(d)/'link';link.symlink_to(p)
   with self.assertRaises(OSError):toggle.open_config(link,os.getuid())
   link.unlink();os.link(p,link)
   with self.assertRaises(ValueError):toggle.open_config(p,os.getuid())
 def test_directory_symlink_refused(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'real').mkdir();(root/'real/config').write_text('{}');(root/'fake').symlink_to(root/'real')
   with self.assertRaises(OSError):toggle.open_config(root/'fake/config',os.getuid())
 def test_same_inode_json_write(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'config';p.write_text('{}');inode=p.stat().st_ino
   with toggle.open_config(p,os.getuid(),True) as f:f.write('{"enabled":false}');f.truncate()
   self.assertEqual(p.stat().st_ino,inode);self.assertFalse(json.loads(p.read_text())['enabled'])
 def test_no_redirect(self):
  with self.assertRaises(ValueError):toggle.NoRedirect().redirect_request(None,None,None,None,None,None)
 def test_provider_issuer_must_match(self):
  class Response:
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def read(self,*a):return b'{"issuer":"https://other.isle"}'
  with patch.object(toggle.urllib.request,'build_opener') as opener:
   opener.return_value.open.return_value=Response()
   with self.assertRaises(ValueError):toggle.check_provider({'keycloak':{'authority':'https://auth.isle','clientId':'frontend'}})
if __name__=='__main__':unittest.main()
