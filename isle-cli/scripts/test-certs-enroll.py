#!/usr/bin/env python3
"""Real CSR/sign/import exchanges with temporary CA and member directories."""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('enroll', Path(__file__).with_name('certs-enroll.py'))
enroll = importlib.util.module_from_spec(spec)
spec.loader.exec_module(enroll)


class EnrollmentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        root = Path(cls.fixture.name)
        cls.ca = root / 'ca'
        signing = cls.ca / 'signing'
        signing.mkdir(parents=True)
        cls.run_ssl = staticmethod(enroll.openssl)
        enroll.openssl('req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:prime256v1',
                       '-nodes', '-keyout', root / 'root.key', '-out', cls.ca / 'isle-root.crt',
                       '-days', '1000', '-subj', '/CN=Enrollment test root',
                       '-addext', 'basicConstraints=critical,CA:TRUE,pathlen:1',
                       '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
        (signing / 'password').write_text('temporary-test-passphrase\n')
        enroll.openssl('genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:prime256v1',
                       '-aes-256-cbc', '-pass', 'file:' + str(signing / 'password'),
                       '-out', signing / 'intermediate_ca_key')
        enroll.openssl('req', '-new', '-key', signing / 'intermediate_ca_key',
                       '-passin', 'file:' + str(signing / 'password'), '-subj', '/CN=Enrollment intermediate',
                       '-out', root / 'intermediate.csr')
        (root / 'ext').write_text('basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n')
        enroll.openssl('x509', '-req', '-in', root / 'intermediate.csr', '-CA', cls.ca / 'isle-root.crt',
                       '-CAkey', root / 'root.key', '-set_serial', '2', '-days', '700',
                       '-extfile', root / 'ext', '-out', signing / 'intermediate_ca.crt')

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.core = enroll.Enrollment(self.work / 'core')
        shutil.copytree(self.ca, self.core.state / 'ca')
        self.member = enroll.Enrollment(self.work / 'member')
        self.member.root.parent.mkdir(parents=True)
        shutil.copyfile(self.ca / 'isle-root.crt', self.member.root)
        self.csr, self.chain = self.work / 'request.pem', self.work / 'chain.pem'

    def exchange(self):
        digest = self.member.request('board.isle', self.csr)
        self.assertEqual(digest, self.core.sign('board.isle', self.csr, self.chain))
        return self.member.install('board.isle', self.chain)

    def test_member_exchange_keeps_private_keys_local(self):
        installed = self.exchange()
        self.assertEqual(installed.read_bytes(), self.chain.read_bytes())
        self.assertNotIn(b'PRIVATE KEY', self.csr.read_bytes() + self.chain.read_bytes())
        self.assertFalse(self.member.signing.exists())
        key = self.member.keys / 'board.isle.key'
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)
        self.member.check('board.isle')

    def test_installed_identity_check_rejects_mismatched_key(self):
        installed = self.exchange()
        old = installed.read_bytes()
        self.member.request('other.isle', self.work / 'other.csr')
        shutil.copyfile(self.member.keys / 'other.isle.key', self.member.keys / 'board.isle.key')
        with self.assertRaisesRegex(ValueError, 'does not match'):
            self.member.check('board.isle')
        self.assertEqual(installed.read_bytes(), old)

    def test_renewal_preserves_key_and_existing_certificate_until_import(self):
        installed = self.exchange()
        original_cert = installed.read_bytes()
        key = self.member.keys / 'board.isle.key'
        original_key = key.read_bytes()
        self.member.request('board.isle', self.work / 'renewal.pem')
        self.assertEqual(key.read_bytes(), original_key)
        self.assertEqual(installed.read_bytes(), original_cert)

    def test_sign_requires_explicit_matching_service(self):
        self.member.request('board.isle', self.csr)
        with self.assertRaisesRegex(ValueError, 'subject does not match'):
            self.core.sign('other.isle', self.csr, self.chain)
        self.assertFalse(self.chain.exists())

    def test_import_rejects_other_member_key_without_overwriting_leaf(self):
        installed = self.exchange()
        old = installed.read_bytes()
        other = enroll.Enrollment(self.work / 'other')
        other.request('board.isle', self.work / 'other.csr')
        self.core.sign('board.isle', self.work / 'other.csr', self.work / 'other.crt')
        with self.assertRaisesRegex(ValueError, 'does not match'):
            self.member.install('board.isle', self.work / 'other.crt')
        self.assertEqual(installed.read_bytes(), old)

    def test_import_rejects_wrong_root_without_overwriting_leaf(self):
        installed = self.exchange()
        old = installed.read_bytes()
        self.member.root.write_text('not the trusted root\n')
        with self.assertRaises(ValueError):
            self.member.install('board.isle', self.chain)
        self.assertEqual(installed.read_bytes(), old)

    def test_existing_exchange_files_are_preserved(self):
        self.exchange()
        old = self.chain.read_bytes()
        with self.assertRaises(ValueError):
            self.core.sign('board.isle', self.csr, self.chain)
        with self.assertRaises(ValueError):
            self.member.request('board.isle', self.csr)
        self.assertEqual(self.chain.read_bytes(), old)

    def test_invalid_names_refuse_before_creating_keys(self):
        for name in ('*.isle', '../board.isle', 'board.isle\nDNS:other.isle', 'board.local', 'a..isle'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.member.request(name, self.csr)
        self.assertFalse(self.member.keys.exists())

    def test_requested_extra_sans_are_not_granted(self):
        self.member.request('board.isle', self.csr)
        hostile = self.work / 'extra.csr'
        enroll.openssl('req', '-new', '-key', self.member.keys / 'board.isle.key',
                       '-subj', '/CN=board.isle', '-addext', 'subjectAltName=DNS:board.isle,DNS:admin.isle',
                       '-out', hostile)
        self.core.sign('board.isle', hostile, self.chain)
        self.member.install('board.isle', self.chain)
        sans = enroll.openssl('x509', '-in', self.chain, '-noout', '-ext', 'subjectAltName')
        self.assertNotIn(b'admin.isle', sans)


if __name__ == '__main__':
    unittest.main()
