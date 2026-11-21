#!/usr/bin/env python3
"""No isle CLI script calls a service without checking its certificate, or the router without checking its key,
except the places listed here, each with the reason it cannot be otherwise and what checks the result instead.
A new unchecked call fails this test until it is either routed through lib/tls.sh / router_ssh.py or listed."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent.parent        # isle-cli
CURL = re.compile(r'curl\b[^|;&]*\s(-[A-Za-z]*k[A-Za-z]*|--insecure)(\s|$|")')
WGET = re.compile(r'--no-check-certificate')
SSH = re.compile(r'StrictHostKeyChecking[= ](no|off|accept-new)|UserKnownHostsFile[= ]/dev/null')
PYTHON = re.compile(r'verify\s*=\s*False|_create_unverified_context|CERT_NONE|check_hostname\s*=\s*False')
ADDRESS = re.compile(r'192\.168\.1\.1(?!\d)')

# file -> (how many unchecked lines it may hold, the word that must stand in the file, why)
ALLOWED = {
    'scripts/lib/tls.sh': (1, 'ISLE_TLS_INSECURE', 'the knob itself: unchecked only when a person set it, and said at every call'),
    'scripts/trust.sh': (3, 'first-trust', 'the root certificate, accepted by its fingerprint or by the signature of the installed archive key'),
    'scripts/isle-bootstrap.sh': (2, 'first-trust', 'the root certificate, compared with the fingerprint the person typed (curl, or wget on a fresh machine)'),
    'scripts/core-install.sh': (2, 'first-trust', 'printed for a device that trusts nothing yet; the file is checked by sha256 before it is run'),
    'scripts/apt-repo.sh': (1, 'first-trust', 'the same instruction on the page of the package repository'),
    'shells/tools/store-setup.sh': (2, 'first-trust', 'the root certificate, compared with --fingerprint before the bootstrap is fetched with it (and the dry-run line)'),
    'scripts/test-connectivity.sh': (1, 'cannot-check', 'wget inside the agent container cannot be given a certificate; said at every run'),
}
# where the router's default management address may stand: the one function that reads this machine's record
ADDRESS_ALLOWED = {'scripts/host_network.py'}


def files():
    for path in sorted(ROOT.rglob('*')):
        if path.suffix not in ('.sh', '.py') or 'node_modules' in path.parts or not path.is_file(): continue
        if path.name.startswith('test-') and path.suffix == '.py': continue       # the tests name the forms they forbid
        yield path.relative_to(ROOT).as_posix(), path.read_text(errors='replace')


def unchecked(text):
    """The lines of a script that would call something unchecked.  A line that is only a comment is not a call."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith('#'): continue
        if 'StrictHostKeyChecking=yes' in line and not re.search(r'StrictHostKeyChecking[= ](no|off|accept-new)', line): ssh = None
        else: ssh = SSH.search(line)
        if CURL.search(line) or WGET.search(line) or ssh or PYTHON.search(line): found.append((number, line.strip()[:120]))
    return found


class Unverified(unittest.TestCase):
    def test_the_forms_are_recognised(self):
        for line in ('curl -sk https://x', 'CURL="curl -skf --max-time 8"', 'curl -ko f https://x', 'curl -fsS --insecure https://x',
                     'curl -k -s -o /dev/null "$url"', 'wget -q --no-check-certificate -O f u',
                     'ssh -o StrictHostKeyChecking=no root@r', 'ssh -o StrictHostKeyChecking=accept-new root@r',
                     'ssh -o UserKnownHostsFile=/dev/null root@r', 'requests.get(u, verify=False)', 'ssl._create_unverified_context()'):
            self.assertTrue(unchecked(line), line)
        for line in ('curl -sf --max-time 8 --cacert "$CA" https://x', 'isle_curl -sf https://x', '# curl -k is not used here',
                     'ssh -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/dev/null -o GlobalKnownHostsFile=/dev/null root@r',
                     'curl --silent --keepalive-time 5 https://x', 'curl -s -o "$out" https://x  # ok'):
            self.assertFalse(unchecked(line), line)

    def test_only_the_listed_places_call_unchecked(self):
        seen = {}
        for name, text in files():
            lines = unchecked(text)
            if lines: seen[name] = lines
        for name, lines in seen.items():
            self.assertIn(name, ALLOWED, '%s calls unchecked: %s' % (name, lines))
            self.assertEqual(len(lines), ALLOWED[name][0], '%s: %s' % (name, lines))
        for name, (count, word, why) in ALLOWED.items():
            self.assertIn(name, seen, 'listed but no longer unchecked (take it off the list): ' + name)
            self.assertIn(word, (ROOT / name).read_text(), name)
            self.assertTrue(why)

    def test_a_named_certificate_file_is_the_only_thing_trusted(self):
        # curl's --cacert and wget's --ca-certificate ADD the file to the system's certificates (measured on Ubuntu
        # 22.04 and 24.04).  Every call that names a file also shuts the system's ones out, so "checked against the
        # isle's root" means that root and no other.
        alone, seen = [], 0
        for name, text in files():
            lines = text.splitlines()
            for number, line in enumerate(lines, 1):
                if line.lstrip().startswith('#'): continue
                if '--cacert' in line:
                    seen += 1
                    only = ('$ISLE_TLS_ONLY' in line) if name == 'scripts/lib/tls.sh' else bool(
                        re.search(r"--cacert[ ,'\"]+[^ ,]+[ ,']+--capath[ ,']+/nonexistent", line))
                    if not only: alone.append('%s:%d' % (name, number))
                if '--ca-certificate=' in line:
                    seen += 1
                    if 'SSL_CERT_DIR=/nonexistent SSL_CERT_FILE=' not in ' '.join(lines[max(0, number - 2):number]): alone.append('%s:%d' % (name, number))
        self.assertEqual(alone, [])
        self.assertGreater(seen, 12)             # the search itself still finds them
        self.assertEqual((ROOT / 'scripts/lib/tls.sh').read_text().count('ISLE_TLS_ONLY="--capath /nonexistent"'), 1)

    def test_no_script_writes_the_address_of_the_router(self):
        for name, text in files():
            if name in ADDRESS_ALLOWED: continue
            self.assertIsNone(ADDRESS.search(text), name)

    def test_the_router_callers_ask_for_the_pinned_options(self):
        for name in ('security.sh', 'scan.sh', 'dns.sh', 'dns-register.sh', 'dns-reconcile.sh', 'test.sh', 'test-remote-reachability.sh'):
            self.assertIn('router_ssh.py" options', (ROOT / 'scripts' / name).read_text(), name)
        self.assertIn('router_ssh.options(router)', (ROOT / 'scripts' / 'storage_core.py').read_text())
        self.assertIn("options(plan['gateway'],KNOWN)", (ROOT / 'scripts' / 'router_forwarding.py').read_text())


if __name__ == '__main__':
    unittest.main()
