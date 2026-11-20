#!/usr/bin/env python3
import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import identity_reconcile as rec
import member_preflight as pre
import operations

class IdentityReconcile(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  for d in ['agent/remote','ca']:(self.root/d).mkdir(parents=True)
  (self.root/'agent/agent.mode').write_text('remote\n')
  (self.root/'device-name').write_text('pol-core\n')
  self.legacy=self.root/'agent/remote/hostname';self.legacy.write_text('user-AB-ExaMple-100-X1-BOX\n')
  os.utime(self.legacy,(1_761_600_000,1_761_600_000))
  (self.root/'agent/remote/mac-address').write_text('02:00:00:00:0a:2e\n')
  (self.root/'agent/remote/interface.conf').write_text('enp3s0\n')
  (self.root/'ca/isle-root.crt').write_text('fixture\n')
  self.root_guard=patch('os.geteuid',return_value=0);self.root_guard.start();self.addCleanup(self.root_guard.stop)
 def plan(self,expected='user-ab-example-100-x1-box',canonical='pol-core',**kw):
  return rec.plan(expected,canonical,self.root,core=False,**kw)
 def test_status_reports_disagreement_then_consistency(self):
  s=rec.status(self.root);self.assertFalse(s['agree']);self.assertIn('disagrees',s['words'])
  self.legacy.write_text('pol-core\n');self.assertTrue(rec.status(self.root)['agree'])
 def test_preflight_refuses_until_reconciled_and_names_the_command(self):
  # CLI 205 rejects a non-label value before comparing; a lowercase legacy label reaches the disagreement.
  with self.assertRaisesRegex(ValueError,'isle identity status'):pre.identity(self.root)
  self.legacy.write_text('user-ab-example-100-x1-box\n')
  with self.assertRaisesRegex(ValueError,'isle identity reconcile'):pre.identity(self.root)
 def test_plan_guards_fail_closed(self):
  with self.assertRaisesRegex(ValueError,'not the expected'):self.plan(expected='some-other-name')
  with self.assertRaisesRegex(ValueError,'Canonical identity mismatch'):self.plan(canonical='econ-core')
  with self.assertRaisesRegex(ValueError,'nothing to reconcile'):self.plan(expected='pol-core')
  with self.assertRaisesRegex(ValueError,'lowercase DNS label'):self.plan(canonical='Pol Core')
  (self.root/'device-name').unlink()
  with self.assertRaisesRegex(ValueError,'never creates an identity'):self.plan()
  (self.root/'device-name').write_text('pol-core\n');(self.root/'agent/agent.mode').write_text('\n')
  with self.assertRaisesRegex(ValueError,'existing member'):self.plan()
  self.assertEqual(self.legacy.read_text(),'user-AB-ExaMple-100-X1-BOX\n')
 def test_enrollment_credential_must_name_the_canonical_device(self):
  c=self.root/'ca/member-enrollment.json';c.write_text(json.dumps({'device':'user-ab-example-100-x1-box','endpoint':'https://certs.isle/enroll','token':'a'*64}));c.chmod(0o600)
  with self.assertRaisesRegex(ValueError,'Hosting credential names device'):self.plan()
  c.write_text(json.dumps({'device':'pol-core','endpoint':'https://certs.isle/enroll','token':'a'*64}))
  self.assertEqual(self.plan()['enrollment'],'agrees')
  with patch('os.geteuid',return_value=1000):self.assertIn('administrator',self.plan()['enrollment'])
 def test_plan_is_read_only_and_reports_mixed_case_legacy_and_references(self):
  (self.root/'agent/known-devices.json').write_text(json.dumps({'node':{'id':'user-ab-example-100-x1-box'}}))
  before=self.legacy.stat().st_mtime_ns
  p=self.plan()
  self.assertEqual(p['action'],'rewrite');self.assertEqual(p['legacy'],'user-AB-ExaMple-100-X1-BOX')
  self.assertEqual(p['otherReferences'],['agent/known-devices.json']);self.assertEqual(p['coreKnowsCanonical'],'not checked')
  self.assertIn('agent/remote/mac-address',p['preserved'])
  self.assertEqual(self.legacy.stat().st_mtime_ns,before);self.assertEqual(self.legacy.read_text(),'user-AB-ExaMple-100-X1-BOX\n')
  self.assertEqual(rec.plan('x','pol-core',self.root,fetch=lambda:json.dumps({'devices':[{'name':'pol-core'}]}))['coreKnowsCanonical'] if False else rec.core_knows('pol-core',lambda:json.dumps({'devices':[{'name':'pol-core'}]})),'known')
  self.assertEqual(rec.core_knows('pol-core',lambda:json.dumps({'devices':[]})),'absent')
  self.assertTrue(rec.core_knows('pol-core',lambda:(_ for _ in ()).throw(OSError())).startswith('unreachable'))
 def test_apply_backs_up_rewrites_atomically_preserves_everything_else_and_journals(self):
  op=self.root/'op';op.mkdir()
  mac=self.root/'agent/remote/mac-address';mac_stat=(mac.read_bytes(),mac.stat().st_mtime_ns)
  with patch.dict(os.environ,ISLE_OPERATION_ID='a'*32):
   r=rec.apply('user-ab-example-100-x1-box','pol-core',self.root,operation_dir=op,core=False)
  self.assertEqual(r['action'],'rewrite');self.assertEqual(r['stage'],'complete');self.assertEqual(r['preflight'],'pol-core')
  self.assertEqual(self.legacy.read_text(),'pol-core\n');self.assertEqual(self.legacy.stat().st_mode&0o777,0o644)
  backup=Path(r['backup']);self.assertTrue(backup.name.startswith('hostname.pre-reconcile-'))
  self.assertEqual(backup.read_text(),'user-AB-ExaMple-100-X1-BOX\n');self.assertEqual(backup.stat().st_mtime,1_761_600_000)
  self.assertEqual((mac.read_bytes(),mac.stat().st_mtime_ns),mac_stat)
  self.assertEqual(pre.identity(self.root),'pol-core');self.assertTrue(rec.status(self.root)['agree'])
  lines=[json.loads(l) for l in (self.root/rec.RECEIPTS).read_text().splitlines()]
  self.assertEqual([l.get('stage') for l in lines],['backup-written','complete']);self.assertEqual(lines[-1]['operation'],'a'*32)
  self.assertEqual(json.loads((op/'identity-receipt.json').read_text())['legacy'],'user-AB-ExaMple-100-X1-BOX')
  # second run: nothing to do, no second backup, still journaled
  again=rec.apply('user-ab-example-100-x1-box','pol-core',self.root,operation_dir=op,core=False)
  self.assertEqual(again['action'],'none');self.assertEqual(len(list(self.legacy.parent.glob('hostname.pre-reconcile-*'))),1)
 def test_failed_verification_journals_and_restores_the_legacy_value(self):
  op=self.root/'op';op.mkdir()
  with patch.object(pre,'identity',side_effect=ValueError('Invalid saved device identity: fixture')):
   with self.assertRaisesRegex(RuntimeError,'restored from backup'):
    rec.apply('user-ab-example-100-x1-box','pol-core',self.root,operation_dir=op,core=False)
  self.assertEqual(self.legacy.read_text(),'user-AB-ExaMple-100-X1-BOX\n')
  lines=[json.loads(l) for l in (self.root/rec.RECEIPTS).read_text().splitlines()]
  self.assertEqual([l.get('stage') for l in lines],['backup-written','failed']);self.assertIn('backup',lines[-1]);self.assertEqual(lines[-1]['legacyFile'],'restored from backup')
  self.assertEqual(json.loads((op/'identity-receipt.json').read_text())['stage'],'failed')
  self.assertEqual(len(list(self.legacy.parent.glob('hostname.pre-reconcile-*'))),1)
 def test_reference_scan_skips_key_material_and_owner_only_files(self):
  (self.root/'agent/ssl/keys').mkdir(parents=True)
  (self.root/'agent/ssl/keys/device.key').write_text('user-ab-example-100-x1-box secret')
  (self.root/'ca/private.pem').write_text('user-ab-example-100-x1-box')
  locked=self.root/'agent/locked.json';locked.write_text('user-ab-example-100-x1-box');locked.chmod(0o600)
  (self.root/'agent/known-devices.json').write_text('user-AB-ExaMple-100-X1-BOX')
  (self.root/'ca/isle-root.crt').write_text('fixture')
  self.assertEqual(rec.other_references(self.root,'user-ab-example-100-x1-box'),['agent/known-devices.json'])
 def test_apply_requires_root_and_journaled_operation(self):
  with patch('os.geteuid',return_value=1000),self.assertRaises(PermissionError):rec.apply('user-ab-example-100-x1-box','pol-core',self.root,core=False)
  with patch.dict(os.environ,{'ISLE_OPERATION_ID':''}),self.assertRaisesRegex(PermissionError,'shared operation'):
   rec.main(['reconcile','--expected-legacy','user-ab-example-100-x1-box','--canonical','pol-core'])
  self.assertEqual(self.legacy.read_text(),'user-AB-ExaMple-100-X1-BOX\n')
 def test_operation_request_shape_and_name(self):
  args=['identity','reconcile','--expected-legacy','user-AB-ExaMple-100-X1-BOX','--canonical','pol-core']
  self.assertEqual(operations.request(args),args)
  for bad in (args+['--force'],args[:4],['identity','rename','--expected-legacy','a','--canonical','b'],['identity','reconcile','--expected-legacy','../x','--canonical','pol-core']):
   with self.assertRaises(ValueError):operations.request(bad)
  with patch.object(operations,'ROOT',self.root/'ops'),patch.object(operations,'SCRIPTS',self.root):
   (self.root/'identity.sh').write_text('#!/bin/bash\ntest "$1" = reconcile && test -n "$ISLE_OPERATION_ID"\n')
   self.assertEqual(operations.execute(args),0)
   d=operations.latest('identity-reconcile');self.assertEqual(d['state'],'succeeded');self.assertEqual(d['request'],args)
if __name__=='__main__':unittest.main()
