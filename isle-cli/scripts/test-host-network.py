#!/usr/bin/env python3
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import host_network as h
class Network(unittest.TestCase):
 def invoke(self,conflict=False,wifi=False):
  def run(a):
   if a[:5]==['ip','-j','-4','route','get']:return json.dumps([{'dev':'wlan0' if wifi else 'br-custom','prefsrc':'192.168.8.254'}])
   if a[:2]==['docker','inspect']:return json.dumps([{'NetworkSettings':{'Networks':{'vlan':{'IPAddress':'10.55.0.2','IPPrefixLen':24},'bridge':{}}}}])
   if a[:3]==['docker','network','inspect']:return json.dumps([{'Driver':'macvlan' if a[-1]=='vlan' else 'bridge'}])
   if a[:5]==['ip','-j','-4','route','show']:return json.dumps([{'gateway':'192.168.8.2','dev':'foreign'}] if conflict else [])
   raise AssertionError(a)
  with tempfile.TemporaryDirectory() as t:
   c=Path(t)/'config';c.write_text(json.dumps({'managementGateway':'192.168.8.1'}))
   with patch.object(h,'CONFIG',c),patch.object(h,'run',side_effect=run):return h.plan()
 def test_custom_network_is_derived_not_member_hardcoded(self):
  self.assertEqual(self.invoke(),{'gateway':'192.168.8.1','interface':'br-custom','source':'192.168.8.254','subnets':['10.55.0.0/24']})
 def test_the_router_address_is_the_recorded_one_for_the_lease_lookup_too(self):
  import storage_core as s
  class Done:returncode=0;stdout='1 02:00:00:00:0a:01 10.55.0.7 Member-One *\n2 aa:bb:cc:dd:ee:ff 10.55.0.9 laptop *\n'
  with tempfile.TemporaryDirectory() as t:
   c=Path(t)/'config';c.write_text(json.dumps({'managementGateway':'192.168.8.1'}));calls=[]
   with patch.object(h,'CONFIG',c),patch.object(s,'_run',side_effect=lambda a,**k:calls.append(a) or Done()):
    self.assertEqual(s.router_leases(),[('member-one','10.55.0.7')])
   self.assertIn('root@192.168.8.1',calls[0]);self.assertEqual(len(calls),1)
   c.write_text(json.dumps({'managementGateway':'not an address'}));calls.clear()
   with patch.object(h,'CONFIG',c),patch.object(s,'_run',side_effect=lambda a,**k:calls.append(a) or Done()):
    self.assertIsNone(s.router_leases())
   self.assertEqual(calls,[])
 def test_conflicting_route_and_wifi_are_refused(self):
  with self.assertRaises(ValueError):self.invoke(conflict=True)
  with self.assertRaises(ValueError):self.invoke(wifi=True)
if __name__=='__main__':unittest.main()
