import importlib.util
import unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('security',Path(__file__).with_name('polari-security.py'))
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
class SecurityTests(unittest.TestCase):
 def config(self):return {'keycloak':{'authority':'https://auth.example/realms/Polari','clientId':'frontend'}}
 def test_disable_preserves_configuration(self):
  c=self.config();s.update(c,'disable');self.assertFalse(c['keycloak']['enabled']);s.update(c,'enable');self.assertTrue(c['keycloak']['enabled']);self.assertEqual(c['keycloak']['clientId'],'frontend')
 def test_required_cannot_disable(self):
  c=self.config();s.update(c,'require','private-files')
  with self.assertRaisesRegex(ValueError,'private-files'):s.update(c,'disable')
  s.update(c,'release','private-files');s.update(c,'disable');self.assertFalse(c['keycloak']['enabled'])
 def test_missing_configuration_cannot_enable(self):
  with self.assertRaises(ValueError):s.update({},'enable')
 def test_no_backend_change(self):
  c=self.config();c['backend']={'authorization':'required'};s.update(c,'disable');self.assertEqual(c['backend'],{'authorization':'required'})
if __name__=='__main__':unittest.main()
