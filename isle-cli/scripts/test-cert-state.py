#!/usr/bin/env python3
"""Real OpenSSL chains in temporary directories; never access the host CA."""
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('certs.sh')


class CertStateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        # Only load definitions; certs.sh defaults to inspecting host state.
        cls.definitions = cls.root / 'definitions.sh'
        cls.definitions.write_text(SCRIPT.read_text().split('case "${1:-status}" in', 1)[0])
        for ca in ('trusted', 'other'):
            cls.openssl('req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:prime256v1',
                        '-nodes', '-keyout', ca + '.key', '-out', ca + '.crt', '-days', '365',
                        '-subj', '/CN=Polari Root CA identical name',
                        '-addext', 'basicConstraints=critical,CA:TRUE',
                        '-addext', 'keyUsage=critical,keyCertSign,cRLSign')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def openssl(cls, *args):
        subprocess.run(['openssl', *args], cwd=cls.root, check=True, capture_output=True)

    def leaf(self, san='app.isle', ca='trusted', days='90'):
        self.openssl('req', '-new', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:prime256v1',
                     '-nodes', '-keyout', 'leaf.key', '-out', 'leaf.csr', '-subj', '/CN=app.isle')
        (self.root / 'ext').write_text('subjectAltName=DNS:' + san + '\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n')
        self.openssl('x509', '-req', '-in', 'leaf.csr', '-CA', ca + '.crt', '-CAkey', ca + '.key',
                     '-CAcreateserial', '-days', days, '-extfile', 'ext', '-out', 'app.isle.crt')

    def state(self):
        result = subprocess.run(['bash', '-c', '''source "$1"
sudo(){ "$@"; }
CRTD="$2"; ROOT_CA="$2/trusted.crt"
cert_state app.isle
''', 'test', str(self.definitions), str(self.root)], capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def test_current_isle_signature(self):
        self.leaf()
        self.assertEqual(self.state(), 'ok')

    def test_fullchain_signed_by_intermediate(self):
        self.openssl('req', '-new', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:prime256v1',
                     '-nodes', '-keyout', 'intermediate.key', '-out', 'intermediate.csr',
                     '-subj', '/CN=Isle intermediate')
        (self.root / 'intermediate.ext').write_text('basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n')
        self.openssl('x509', '-req', '-in', 'intermediate.csr', '-CA', 'trusted.crt',
                     '-CAkey', 'trusted.key', '-CAcreateserial', '-days', '180',
                     '-extfile', 'intermediate.ext', '-out', 'intermediate.crt')
        self.leaf(ca='intermediate')
        with (self.root / 'app.isle.crt').open('a') as stream:
            stream.write((self.root / 'intermediate.crt').read_text())
        self.assertEqual(self.state(), 'ok')

    def test_same_issuer_name_different_ca_rejected(self):
        self.leaf(ca='other')
        self.assertEqual(self.state(), 'untrusted')

    def test_exact_san_required(self):
        for san in ('app.isle.evil', 'appXisle', '*.isle'):
            with self.subTest(san=san):
                self.leaf(san=san)
                self.assertEqual(self.state(), 'wrong-san')

    def test_renewal_window(self):
        self.leaf(days='2')
        self.assertEqual(self.state(), 'expiring')

    def test_reload_targets_actual_hosting_agent_and_propagates_failure(self):
        for agent, failure in (('isle-vlan-agent', '0'), ('isle-remote-agent', '0'),
                               ('isle-remote-agent', '1'), ('unrelated-container', '0')):
            with self.subTest(agent=agent, failure=failure):
                result = subprocess.run(['bash', '-c', '''source "$1"
mock_agent="$2"; failure="$3"
docker(){
    if [ "$1" = ps ]; then echo "$mock_agent"; return; fi
    echo "reload command: $*"
    return "$failure"
}
reload_agent
''', 'test', str(self.definitions), agent, failure], capture_output=True, text=True)
                self.assertEqual(result.returncode == 0,
                                 agent != 'unrelated-container' and failure == '0', result.stdout)
                if agent == 'unrelated-container':
                    self.assertNotIn('reload command:', result.stdout)
                else:
                    self.assertIn('exec ' + agent, result.stdout)

    def test_sync_never_hides_issuance_reload_or_inventory_failure(self):
        for failure in ('issue', 'reload', 'inventory', 'none'):
            with self.subTest(failure=failure):
                result = subprocess.run(['bash', '-c', '''source "$1"
mock_failure="$2"
require_signing(){ :; }
domains_from_registry(){ [ "$mock_failure" != inventory ] || return 1; echo app.isle; }
cert_state(){ echo missing; }
issue_leaf(){ [ "$mock_failure" != issue ]; }
reload_agent(){ [ "$mock_failure" != reload ]; }
sync_certs
''', 'test', str(self.definitions), failure], capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, failure == 'none', result.stdout)
                self.assertNotIn('all leaves current', result.stdout)

    def test_ensure_reuses_valid_identity_and_never_signs_on_member(self):
        for scenario in ('valid', 'member_missing', 'core_missing', 'core_sign_failure'):
            with self.subTest(scenario=scenario):
                result = subprocess.run(['bash', '-c', '''source "$1"
scenario="$2"; checks=0
sudo(){ [[ "$scenario" == core* ]]; }
check_leaf(){ checks=$((checks+1)); [[ "$scenario" == valid || "$checks" -gt 1 ]]; }
issue_leaf(){ echo ISSUING; [[ "$scenario" != core_sign_failure ]]; }
ensure_leaf app.isle
''', 'test', str(self.definitions), scenario], capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, scenario in ('valid', 'core_missing'), result.stdout)
                self.assertEqual('ISSUING' in result.stdout, scenario.startswith('core'))
                if scenario == 'member_missing':
                    self.assertIn('Create a CSR:', result.stdout)


if __name__ == '__main__':
    unittest.main()
