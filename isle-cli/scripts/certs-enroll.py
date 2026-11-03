#!/usr/bin/env python3
"""Service identity lifecycle: paired HTTPS enrollment or explicit CSR exchange.
Private service keys stay on their member; only the core holds signing keys.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import json
import ssl
import urllib.request
import urllib.error


def openssl(*args):
    result = subprocess.run(['openssl', *map(str, args)], capture_output=True)
    if result.returncode:
        raise ValueError('OpenSSL operation failed: ' + result.stderr.decode(errors='replace').strip())
    return result.stdout


def domain_name(value):
    labels = value.split('.')
    if (len(value) > 253 or len(labels) < 2 or labels[-1] != 'isle'
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)):
        raise ValueError('expected an explicit lowercase .isle service name (no wildcard)')
    return value


def write_new(path, content, mode=0o644):
    # Never overwrite an existing exchange artifact, including symlinks.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(content)


class Enrollment:
    def __init__(self, state=Path('/etc/isle-mesh')):
        self.state = Path(state)
        self.keys = self.state / 'agent/ssl/keys'
        self.certs = self.state / 'agent/ssl/certs'
        self.root = self.state / 'ca/isle-root.crt'
        self.signing = self.state / 'ca/signing'

    def automatic(self, domain):
        """Use the member's paired credential over verified Isle HTTPS."""
        domain = domain_name(domain)
        credential = self.state / 'ca/member-enrollment.json'
        if not credential.is_file():
            raise ValueError('member hosting enrollment is missing; pair this device with the core before hosting apps')
        if credential.stat().st_mode & 0o077:
            raise ValueError('member enrollment credential must be readable only by root')
        config = json.loads(credential.read_text())
        if config.get('endpoint') != 'https://certs.isle/enroll':
            raise ValueError('invalid member enrollment endpoint')
        with tempfile.TemporaryDirectory() as temp:
            csr, chain = Path(temp)/'request.pem', Path(temp)/'chain.pem'
            self.request(domain, csr)
            body = json.dumps({'device': config['device'], 'domain': domain,
                               'csr': csr.read_text()}).encode()
            request = urllib.request.Request(config['endpoint'], body, headers={
                'Content-Type': 'application/json', 'Authorization': 'Bearer '+config['token']})
            # No redirects: a bearer credential must never follow an unrelated URL.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs): return None
            opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(
                context=ssl.create_default_context(cafile=str(self.root))))
            try:
                with opener.open(request, timeout=20) as reply:
                    result = json.loads(reply.read(65537))
            except urllib.error.URLError as exc:
                raise ValueError('core enrollment unavailable or refused: '+str(exc)) from exc
            if result.get('ok') is not True or not isinstance(result.get('certificate'), str):
                raise ValueError('core did not return a service certificate')
            chain.write_text(result['certificate'])
            self.install(domain, chain)
            self.check(domain)

    def request(self, domain, output):
        domain = domain_name(domain)
        output = Path(output)
        if output.exists() or output.is_symlink():
            raise ValueError('request output already exists; choose a new file')
        self.keys.mkdir(parents=True, exist_ok=True)
        key = self.keys / (domain + '.key')
        if key.is_symlink():
            raise ValueError('refusing a symlink for the service key')
        # Reuse the service key on renewal so an existing leaf keeps working.
        # A new key stays on this member; only the CSR leaves it.
        with tempfile.TemporaryDirectory(dir=self.keys) as temp:
            if not key.exists():
                generated = Path(temp) / 'key'
                openssl('genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:prime256v1', '-out', generated)
                generated.chmod(0o600)
                os.link(generated, key)  # exclusive creation, never replace a key
            csr = openssl('req', '-new', '-key', key, '-subj', '/CN=' + domain,
                          '-addext', 'subjectAltName=DNS:' + domain)
            write_new(output, csr)
        return hashlib.sha256(csr).hexdigest()

    def sign(self, domain, request, output):
        domain = domain_name(domain)
        if Path(output).exists() or Path(output).is_symlink():
            raise ValueError('certificate output already exists; choose a new file')
        # Snapshot the selected public request before validation and signing.
        raw = Path(request).read_bytes()
        if len(raw) > 65536:
            raise ValueError('request exceeds 64 KiB')
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            csr, leaf, ext = temp / 'request.pem', temp / 'leaf.pem', temp / 'extensions'
            csr.write_bytes(raw)
            openssl('req', '-in', csr, '-verify', '-noout')
            subject = openssl('req', '-in', csr, '-subject', '-noout', '-nameopt', 'RFC2253').decode().strip()
            if subject != 'subject=CN=' + domain:
                raise ValueError('CSR subject does not match the explicitly authorized service name')
            ext.write_text('subjectAltName=DNS:' + domain + '\nbasicConstraints=critical,CA:FALSE\n'
                           'keyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n')
            openssl('x509', '-req', '-in', csr, '-CA', self.signing / 'intermediate_ca.crt',
                    '-CAkey', self.signing / 'intermediate_ca_key',
                    '-passin', 'file:' + str(self.signing / 'password'),
                    '-set_serial', '0x' + secrets.token_hex(16), '-days', '365', '-sha256',
                    '-extfile', ext, '-out', leaf)
            chain = leaf.read_bytes() + (self.signing / 'intermediate_ca.crt').read_bytes()
            fullchain = temp / 'chain.pem'
            fullchain.write_bytes(chain)
            self.verify(domain, fullchain)
            write_new(output, chain)
        return hashlib.sha256(raw).hexdigest()

    def verify(self, domain, certificate):
        domain_name(domain)
        sans = openssl('x509', '-in', certificate, '-noout', '-ext', 'subjectAltName').decode()
        entries = [entry.strip() for line in sans.splitlines()[1:] for entry in line.split(',')]
        if entries != ['DNS:' + domain]:
            raise ValueError('certificate must contain only the authorized explicit service SAN')
        openssl('verify', '-CAfile', self.root, '-untrusted', certificate,
                '-purpose', 'sslserver', '-verify_hostname', domain, certificate)
        openssl('x509', '-in', certificate, '-noout', '-checkend', '2592000')

    def check(self, domain):
        domain = domain_name(domain)
        certificate = self.certs / (domain + '.crt')
        key = self.keys / (domain + '.key')
        if certificate.is_symlink() or key.is_symlink():
            raise ValueError('refusing symlinked service identity files')
        self.verify(domain, certificate)
        if openssl('x509', '-in', certificate, '-pubkey', '-noout') != openssl('pkey', '-in', key, '-pubout'):
            raise ValueError('certificate does not match this member service key')

    def install(self, domain, certificate):
        domain = domain_name(domain)
        key = self.keys / (domain + '.key')
        if key.is_symlink():
            raise ValueError('refusing a symlink for the service key')
        self.certs.mkdir(parents=True, exist_ok=True)
        destination = self.certs / (domain + '.crt')
        if destination.is_symlink():
            raise ValueError('refusing a symlink for the service certificate')
        with tempfile.TemporaryDirectory(dir=self.certs) as temp:
            staged = Path(temp) / 'chain.pem'
            raw = Path(certificate).read_bytes()
            if len(raw) > 65536 or b'PRIVATE KEY' in raw:
                raise ValueError('expected a public certificate chain, not private material')
            staged.write_bytes(raw)
            self.verify(domain, staged)
            public = openssl('x509', '-in', staged, '-pubkey', '-noout')
            if public != openssl('pkey', '-in', key, '-pubout'):
                raise ValueError('certificate does not match this member service key')
            staged.chmod(0o644)
            os.replace(staged, destination)
        return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    request = commands.add_parser('request', help='member: retain local key and export a public CSR')
    request.add_argument('domain')
    request.add_argument('--output', required=True)
    sign = commands.add_parser('sign', help='core operator: authorize the reviewed CSR for one service name')
    sign.add_argument('request')
    sign.add_argument('--domain', required=True)
    sign.add_argument('--output', required=True)
    install = commands.add_parser('install', help='member: verify and install the returned public chain')
    install.add_argument('domain')
    install.add_argument('--certificate', required=True)
    check = commands.add_parser('check', help='verify the installed service chain and key without changing them')
    check.add_argument('domain')
    automatic = commands.add_parser('automatic', help='paired member: enroll a service automatically over verified HTTPS')
    automatic.add_argument('domain')
    pair = commands.add_parser('pair', help='install a privately transferred core-issued member hosting credential')
    pair.add_argument('credential')
    args = parser.parse_args()
    enrollment = Enrollment()
    try:
        if args.action == 'pair':
            from names import current_device
            if os.geteuid() != 0: raise ValueError('pairing requires root')
            config = json.loads(Path(args.credential).read_text())
            if (config.get('device') != current_device() or config.get('endpoint') != 'https://certs.isle/enroll'
                    or not re.fullmatch(r'[A-Za-z0-9_-]{64}', config.get('token', ''))):
                raise ValueError('credential does not match this named member')
            destination = enrollment.state / 'ca/member-enrollment.json'
            if destination.exists() and json.loads(destination.read_text()) != config:
                raise ValueError('existing member credential differs; revoke/re-pair explicitly')
            if not destination.exists(): write_new(destination, json.dumps(config).encode(), 0o600)
            print('Member hosting credential installed for', config['device'])
        elif args.action == 'request':
            digest = enrollment.request(args.domain, args.output)
            print('Public CSR exported; private key retained on this member. CSR SHA256:', digest)
        elif args.action == 'sign':
            digest = enrollment.sign(args.domain, args.request, args.output)
            print('Signed public chain exported for', args.domain, '; CSR SHA256:', digest)
        elif args.action == 'install':
            destination = enrollment.install(args.domain, args.certificate)
            print('Verified chain installed:', destination)
            print('Next: sudo isle certs reload; then verify trusted HTTPS from another device. Installation alone is not hosting proof.')
        elif args.action == 'automatic':
            enrollment.automatic(args.domain)
            print('Service certificate automatically enrolled and verified for', args.domain)
        else:
            enrollment.check(args.domain)
            print('Installed service chain and private key verified for', args.domain)
    except (ValueError, OSError) as exc:
        parser.exit(1, 'certificate enrollment: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
