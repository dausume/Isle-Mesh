#!/usr/bin/env python3
"""lib/tls.sh: certificates are checked unless the named knob says otherwise, and that is said every time.
And the join door of the Isle App runs only what it fetched over a checked connection.
curl and openssl are stand-ins on PATH; nothing here reaches a network."""
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
TLS = HERE / 'lib' / 'tls.sh'
ROOT = '/etc/isle-mesh/ca/isle-root.crt'
ONLY = '--capath /nonexistent'
SETUP = HERE.parent / 'shells' / 'tools' / 'store-setup.sh'
GOOD = 'AA:BB:CC:DD'


class Knob(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.bin = self.dir / 'bin'
        self.bin.mkdir()
        self.tool('curl', 'printf "CURL:"; printf " %s" "$@"; echo')
        self.ca = self.dir / 'own-root.pem'
        self.ca.write_text('a certificate\n')

    def tool(self, name, body):
        path = self.bin / name
        path.write_text('#!/bin/bash\n' + body + '\n')
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def bash(self, script, **env):
        base = {k: v for k, v in os.environ.items() if not k.startswith('ISLE_TLS_')}
        return subprocess.run(['bash', '-c', 'source %s\n%s' % (TLS, script)], capture_output=True, text=True,
                              env={**base, 'PATH': '%s:%s' % (self.bin, os.environ['PATH']), **env})

    def test_the_default_checks(self):
        done = self.bash('isle_tls_mode; isle_curl -sf https://api.polari.isle/x; isle_curl_site -s https://app.local/')
        mode, own, site = done.stdout.splitlines()
        if os.access(ROOT, os.R_OK):
            self.assertEqual(mode, 'isle-root')
            self.assertEqual(own, 'CURL: --cacert %s %s -sf https://api.polari.isle/x' % (ROOT, ONLY))
        else:
            self.assertEqual(mode, 'system')
            self.assertEqual(own, 'CURL: -sf https://api.polari.isle/x')
        self.assertEqual(site, 'CURL: -s https://app.local/')       # a site that is not the isle's: the system's store
        self.assertEqual(done.stderr, '')
        self.assertNotIn('--insecure', done.stdout)
        self.assertNotIn(' -k', done.stdout)

    def test_a_named_certificate_file_is_the_only_thing_trusted(self):
        done = self.bash('isle_tls_mode; isle_curl -sf https://api.polari.isle/x; isle_curl_site -s https://app.local/', ISLE_TLS_CA=str(self.ca))
        self.assertEqual(done.stdout.splitlines(), ['cacert', 'CURL: --cacert %s %s -sf https://api.polari.isle/x' % (self.ca, ONLY),
                                                    'CURL: --cacert %s %s -s https://app.local/' % (self.ca, ONLY)])
        self.assertEqual(done.stderr, '')

    def test_a_named_file_that_is_not_there_refuses(self):
        done = self.bash('isle_curl -sf https://api.polari.isle/x; echo "status $?"', ISLE_TLS_CA=str(self.dir / 'absent.pem'))
        self.assertEqual(done.stdout, 'status 2\n')
        self.assertIn('is not a file that can be read', done.stderr)

    def test_unchecked_is_said_once_per_run_and_at_every_call(self):
        done = self.bash('isle_tls_mode\nisle_curl -sf https://api.polari.isle/x 2>/dev/null\nX=$(isle_curl -s https://apt.isle/ 2>/dev/null)\n'
                         'bash -c "source %s; isle_curl -s https://polari.isle/ 2>/dev/null"' % TLS, ISLE_TLS_INSECURE='1')
        self.assertEqual(done.stdout.splitlines()[0], 'insecure')
        self.assertIn('CURL: --insecure -sf https://api.polari.isle/x', done.stdout)
        warnings = done.stderr.splitlines()
        self.assertEqual(sum('NOT checked in this isle run' in line for line in warnings), 1)       # also not by the script it starts
        for url in ('https://api.polari.isle/x', 'https://apt.isle/', 'https://polari.isle/'):
            self.assertEqual(sum(line.endswith('certificate not checked (ISLE_TLS_INSECURE=1): ' + url) for line in warnings), 1, warnings)

    def test_other_words_do_not_switch_checking_off(self):
        for value in ('0', 'no', 'false', '', 'off'):
            done = self.bash('isle_tls_mode; isle_curl -s https://apt.isle/', ISLE_TLS_INSECURE=value)
            self.assertNotIn('insecure', done.stdout)
            self.assertEqual(done.stderr, '')

    def test_both_set_the_file_wins_and_that_is_said(self):
        done = self.bash('isle_tls_mode; isle_curl -sf https://api.polari.isle/x', ISLE_TLS_CA=str(self.ca), ISLE_TLS_INSECURE='1')
        self.assertEqual(done.stdout.splitlines(), ['cacert', 'CURL: --cacert %s %s -sf https://api.polari.isle/x' % (self.ca, ONLY)])
        self.assertEqual(done.stderr.count('ISLE_TLS_INSECURE is ignored'), 1)

    def test_a_command_kept_in_a_variable_works(self):       # module.sh, dns-reconcile.sh, core-install.sh
        done = self.bash('CURL="isle_curl -sf --max-time 8"; X=$($CURL https://apt.isle/); echo "$X"', ISLE_TLS_CA=str(self.ca))
        self.assertEqual(done.stdout.strip(), 'CURL: --cacert %s %s -sf --max-time 8 https://apt.isle/' % (self.ca, ONLY))

    def test_the_status_is_that_of_curl(self):
        self.tool('curl', 'exit 60')
        self.assertEqual(self.bash('isle_curl -s https://apt.isle/; echo "status $?"').stdout, 'status 60\n')

    def test_every_script_that_calls_it_reads_the_file_first(self):
        for path in sorted(HERE.glob('*.sh')):
            text = path.read_text()
            if 'isle_curl' not in text: continue
            self.assertIn('/lib/tls.sh"', text, path.name)
            self.assertLess(text.index('/lib/tls.sh"'), text.index('isle_curl'), path.name)
            self.assertEqual(subprocess.run(['bash', '-n', str(path)]).returncode, 0, path.name)

    # ---- the join door of the Isle App ----
    def join(self, offered, *words):
        calls = self.dir / 'calls'
        calls.write_text('')
        self.tool('curl', 'echo "curl $*" >> "%s"\nout=; while [ $# -gt 0 ]; do [ "$1" = -o ] && out=$2; shift; done\n'
                          'echo \'echo "BOOTSTRAP RAN: $*"\' > "$out"' % calls)
        self.tool('openssl', 'echo "sha256 Fingerprint=%s"' % offered)
        self.tool('isle', 'exit 0')
        done = subprocess.run(['bash', str(SETUP), 'join', *words], capture_output=True, text=True,
                              env={**os.environ, 'PATH': '%s:%s' % (self.bin, os.environ['PATH']), 'DRY': '0'})
        return done, calls.read_text().splitlines()

    def test_join_runs_only_what_came_over_a_checked_connection(self):
        done, calls = self.join(GOOD, '--fingerprint', 'aabbccdd', '--tier', 'access')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(calls), 2)
        self.assertIn('--insecure', calls[0])
        self.assertIn('https://apt.isle/isle-root.crt', calls[0])
        self.assertIn('--cacert', calls[1])
        self.assertNotIn('--insecure', calls[1])
        self.assertNotIn(' -k', calls[1])
        self.assertNotRegex(calls[1], r' -[a-zA-Z]*k')
        self.assertIn('https://apt.isle/isle-bootstrap.sh', calls[1])
        self.assertEqual(calls[0].split()[calls[0].split().index('-o') + 1], calls[1].split()[calls[1].split().index('--cacert') + 1])
        self.assertIn('BOOTSTRAP RAN: --fingerprint aabbccdd', done.stdout)

    def test_join_with_another_fingerprint_fetches_and_runs_nothing_more(self):
        done, calls = self.join('11:22:33:44', '--fingerprint', GOOD, '--tier', 'member')
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(len(calls), 1)                              # the root certificate only
        self.assertNotIn('BOOTSTRAP RAN', done.stdout)
        self.assertIn('does not have the fingerprint you gave', done.stderr)

    def test_join_by_the_address_of_the_core_still_checks_the_name(self):
        done, calls = self.join(GOOD, '--fingerprint', GOOD, '--tier', 'access', '--core', '10.10.0.2')
        self.assertEqual(done.returncode, 0, done.stderr)
        for call in calls:
            self.assertIn('--resolve apt.isle:443:10.10.0.2', call)
            self.assertNotIn('https://10.10.0.2', call)
        self.assertIn('--core 10.10.0.2', done.stdout)


if __name__ == '__main__':
    unittest.main()
