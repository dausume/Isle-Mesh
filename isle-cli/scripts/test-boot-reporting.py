#!/usr/bin/env python3
"""Isolated regressions: no host networking, reporting, or systemd mutation."""
import contextlib,io,json,os,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import capabilities,member_link,legacy_units
class Periodic(unittest.TestCase):
 def report(self,code,http='000',periodic=True,body=''):
  result=subprocess.CompletedProcess([],code,body+'\n'+http,'transport failure' if code else '')
  args=['capabilities.py','status','--device','member-a','--publish','https://api.polari.isle']+(['--periodic'] if periodic else [])
  with patch('sys.argv',args),patch.object(capabilities,'collect',return_value={}),patch.object(capabilities.subprocess,'run',return_value=result) as run,contextlib.redirect_stderr(io.StringIO()),contextlib.redirect_stdout(io.StringIO()):
   status=capabilities.main()
  self.assertIn('--http1.1',run.call_args.args[0]);self.assertNotIn('-k',run.call_args.args[0])
  return status
 def test_periodic_transient_but_never_false_success_for_explicit_action(self):
  for code,http in [(7,'000'),(16,'000'),(28,'000'),(22,'503')]:
   self.assertEqual(self.report(code,http),0);self.assertEqual(self.report(code,http,False),1)
 def test_trust_admission_and_malformed_response_remain_failures(self):
  for code,http,body in [(60,'000',''),(22,'403',''),(0,'200','not-json'),(0,'200','{"ok":false}')]:self.assertEqual(self.report(code,http,body=body),1)
  self.assertEqual(self.report(0,'200',body='{"ok":true}'),0)
class Member(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.root=Path(self.t.name);self.remote=self.root/'agent/remote';self.remote.mkdir(parents=True);(self.remote/'interface.conf').write_text('eth9')
  self.uuid='12345678-1234-1234-1234-123456789012'
  self.values={'GENERAL.CON-UUID':self.uuid,'connection.type':'802-3-ethernet','ipv4.method':'auto','connection.interface-name':'eth9','connection.autoconnect':'yes','connection.autoconnect-retries':'-1','ipv4.dhcp-timeout':'0'}
 def output(self,args):return self.values[args[2]]
 def test_only_saved_profile_no_link_cycle_and_idempotent(self):
  with patch.object(member_link,'output',side_effect=self.output),patch.object(member_link.subprocess,'run') as run:
   member_link.reconcile(self.root);argv=run.call_args.args[0]
   self.assertEqual(argv[:5],['nmcli','connection','modify','uuid',self.uuid]);self.assertNotIn('down',argv);self.assertNotIn('up',argv)
   self.assertIn('2147483647',argv);self.assertEqual((self.remote/'nm-profile.uuid').read_text().strip(),self.uuid)
   self.values.update({'GENERAL.CON-UUID':'--','connection.autoconnect-retries':'0','ipv4.dhcp-timeout':'2147483647'})
   run.reset_mock();member_link.reconcile(self.root);run.assert_not_called()
 def test_wrong_profile_or_interface_fails_closed(self):
  for key,value in [('connection.type','802-11-wireless'),('ipv4.method','manual'),('connection.interface-name','eth0')]:
   before=self.values[key];self.values[key]=value
   with patch.object(member_link,'output',side_effect=self.output),patch.object(member_link.subprocess,'run') as run:
    with self.assertRaises(ValueError):member_link.reconcile(self.root)
    run.assert_not_called()
   self.values[key]=before
class Legacy(unittest.TestCase):
 def test_migrate_owned_unit_only_preserve_user_and_timer(self):
  with tempfile.TemporaryDirectory() as t,patch.object(legacy_units.subprocess,'run') as run:
   root=Path(t);unit=root/'polari-isle-push.service';text='[Unit]\nDescription=Feed isle state into prf-isle (push-to-polari)\n[Service]\nUser=alice\nExecStart=/home/alice/polari-isle/push-to-polari.sh\n';unit.write_text(text)
   self.assertTrue(legacy_units.reconcile(root));self.assertFalse(legacy_units.reconcile(root));self.assertEqual(unit.read_text(),text);run.assert_called_once()
   self.assertIn('/usr/share/isle-mesh/polari-isle/push-to-polari.sh',(root/'polari-isle-push.service.d/90-isle-command.conf').read_text())
   unit.write_text('[Service]\nExecStart=/custom\n')
   with self.assertRaises(ValueError):legacy_units.reconcile(root)
 def test_optional_host_unit_never_enabled_and_versioned_relay_command(self):
  with tempfile.TemporaryDirectory() as t,patch.object(legacy_units.subprocess,'run') as run:
   root=Path(t);unit=root/'isle-host-agent.service';unit.write_text('[Service]\nExecStart=/usr/local/bin/isle-mesh/isle-host-agent-relay.sh\n');unit.chmod(0o600)
   self.assertTrue(legacy_units.reconcile(root));self.assertFalse(legacy_units.reconcile(root))
   calls=[c.args[0] for c in run.call_args_list]
   self.assertEqual(calls,[['systemctl','daemon-reload'],['systemctl','try-restart','isle-host-agent.service']]);self.assertEqual(unit.stat().st_mode & 0o777,0o644)
if __name__=='__main__':unittest.main()
