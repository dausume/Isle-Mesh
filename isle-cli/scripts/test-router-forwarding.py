#!/usr/bin/env python3
import unittest
from router_forwarding import changes
NET={'interface':[{'interface':'management','ipv4-address':[{'address':'192.168.8.1','mask':24}]},{'interface':'apps','ipv4-address':[{'address':'10.55.0.1','mask':24}]}]}
FW="""firewall.@zone[0]=zone
firewall.@zone[0].name='mgmt'
firewall.@zone[0].network='management'
firewall.@zone[1]=zone
firewall.@zone[1].name='apps'
firewall.@zone[1].network='apps'
"""
class Forwarding(unittest.TestCase):
 def test_scoped_dynamic_forwarding_and_idempotence(self):
  commands=changes(NET,FW,'192.168.8.1',['10.55.0.0/24'],'192.168.8.254')
  self.assertIn('uci set firewall.isle_core_host_to_apps.src=mgmt',commands)
  self.assertIn('uci set firewall.isle_core_host_to_apps.dest=apps',commands)
  self.assertFalse(any('defaults' in c or 'wan' in c for c in commands))
  configured=FW+"firewall.isle_core_host_to_apps=forwarding\nfirewall.isle_core_host_to_apps.src='mgmt'\nfirewall.isle_core_host_to_apps.dest='apps'\n"
  for command in commands:
   if command.startswith('uci set '): configured+=command[len('uci set '):]+'\n'
  self.assertIn('src_ip=192.168.8.254/32',' '.join(commands))
  self.assertIn('dest_ip=10.55.0.0/24',' '.join(commands))
  self.assertEqual(changes(NET,configured,'192.168.8.1',['10.55.0.0/24'],'192.168.8.254'),[])
 def test_missing_evidence_refuses(self):
  with self.assertRaises(ValueError):changes(NET,FW,'192.168.9.1',['10.55.0.0/24'],'192.168.8.254')
if __name__=='__main__':unittest.main()
