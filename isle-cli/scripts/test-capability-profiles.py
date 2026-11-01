import unittest
from types import SimpleNamespace
from capability_profiles import plan
class Profiles(unittest.TestCase):
 def test_existing_docker_and_compose_are_retained(self):
  p=plan('mesh-app',which=lambda x:'/usr/bin/'+x,run=lambda *a,**k:SimpleNamespace(returncode=0))
  self.assertEqual(p['packages'],[])
 def test_docker_ce_gets_its_compose_provider(self):
  def run(args,**kw):return SimpleNamespace(returncode=1 if args[0]=='docker' else 0,stdout='install ok installed')
  self.assertEqual(plan('mesh-app',which=lambda x:x,run=run)['packages'],['docker-compose-plugin'])
 def test_missing_docker_installs_both(self):
  self.assertEqual(plan('mesh-app',which=lambda x:None)['packages'],['docker.io','docker-compose-v2'])
 def test_qemu_architecture_and_existing_libvirt(self):
  p=plan('hardware-vm',arch='aarch64',which=lambda x:x if x in ['virsh','libvirtd'] else None)
  self.assertEqual(p['packages'],['qemu-system-arm'])
 def test_unsupported_arch_is_not_guessed(self):
  self.assertFalse(plan('hardware-vm',arch='mystery',which=lambda x:None)['supported'])
 def test_unknown_profile_is_rejected(self):
  with self.assertRaises(ValueError):plan('other-device')
if __name__=='__main__':unittest.main()
