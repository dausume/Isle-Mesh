#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import unittest

def load(name,file):
 s=importlib.util.spec_from_file_location(name,Path(__file__).with_name(file));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
base=load('fixture','test-certs-enroll.py');authority=load('authority','cert-authority.py')

class AuthorityTest(unittest.TestCase):
 setUpClass=classmethod(base.EnrollmentTest.setUpClass.__func__)
 tearDownClass=classmethod(base.EnrollmentTest.tearDownClass.__func__)
 def setUp(self):
  base.EnrollmentTest.setUp(self);self.authority=authority.Authority(self.core.state)
  self.credential=self.work/'credential';self.authority.authorize('member',self.credential)
  self.token=json.loads(self.credential.read_text())['token']
  self.member.request('board.isle',self.csr)
 def test_authenticated_issue_and_retry(self):
  first=self.authority.issue('member',self.token,'board.isle',self.csr.read_text())
  self.chain.write_text(first);self.member.install('board.isle',self.chain);self.member.check('board.isle')
  self.assertIn('CERTIFICATE',self.authority.issue('member',self.token,'board.isle',self.csr.read_text()))
  self.assertEqual(self.authority.read(self.authority.claims)['board.isle'],'member')
 def test_wrong_token_and_other_member_cannot_claim(self):
  with self.assertRaises(PermissionError):self.authority.issue('member','wrong','board.isle',self.csr.read_text())
  self.authority.issue('member',self.token,'board.isle',self.csr.read_text())
  other=self.work/'other';self.authority.authorize('other',other)
  with self.assertRaises(PermissionError):self.authority.issue('other',json.loads(other.read_text())['token'],'board.isle',self.csr.read_text())
 def test_core_leaf_reserved_and_revocation(self):
  with self.assertRaises(PermissionError):self.authority.issue('member',self.token,'auth.polari.isle',self.csr.read_text())
  self.core.certs.mkdir(parents=True);(self.core.certs/'board.isle.crt').write_text('reserved')
  with self.assertRaises(PermissionError):self.authority.issue('member',self.token,'board.isle',self.csr.read_text())
  self.authority.revoke('member')
  with self.assertRaises(PermissionError):self.authority.issue('member',self.token,'new.isle',self.csr.read_text())
 def test_invalid_csr_does_not_reserve(self):
  with self.assertRaises(ValueError):self.authority.issue('member',self.token,'bad.isle','bad CSR')
  self.assertNotIn('bad.isle',self.authority.read(self.authority.claims))
 def test_pairing_repeat_keeps_original_credential(self):
  with self.assertRaises(ValueError):self.authority.authorize('member',self.work/'duplicate')
  self.assertEqual(self.credential.stat().st_mode & 0o777,0o600)
if __name__=='__main__':unittest.main()
