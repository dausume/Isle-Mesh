#!/usr/bin/env python3
"""Real apt transactions in a disposable network-none container, host untouched."""
import argparse,gzip,os
from pathlib import Path
import subprocess,tempfile
SCRIPT=Path(__file__).with_name('apt-install.sh')
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('medium',nargs='?',type=Path,default=Path('/usr/share/polari-offline-installer/medium'))
medium=parser.parse_args().medium.resolve()
bootstrap=SCRIPT.parents[3]/'polari-cli/shells/offline/offline-apt.sh' 
with tempfile.TemporaryDirectory(prefix='isle-apt-priority-') as directory:
    root=Path(directory);root.chmod(0o755)
    for name in ('local','upstream','direct','gnupg'):(root/name).mkdir()
    (root/'gnupg').chmod(0o700)
    def package(where,name,version,marker):
        stage=root/'stage';subprocess.run(['rm','-rf',str(stage)],check=True);(stage/'DEBIAN').mkdir(parents=True);(stage/'usr/share'/name).mkdir(parents=True)
        (stage/'DEBIAN/control').write_text(f'Package: {name}\nVersion: {version}\nArchitecture: all\nMaintainer: Test <test@localhost>\nDescription: local supply test\n')
        (stage/'usr/share'/name/'source').write_text(marker)
        subprocess.run(['dpkg-deb','--build',str(stage),str(root/where/f'{name}_{version}_all.deb')],stdout=subprocess.DEVNULL,check=True)
    package('local','isle-supply-proof','1','local')
    package('local','isle-supply-https','1','authenticated-local-https')
    package('upstream','isle-supply-proof','1','upstream-same-version')
    package('upstream','isle-supply-proof','2','upstream-new-version')
    package('upstream','isle-supply-extra','1','upstream-only')
    package('direct','isle-supply-proof','3','direct-local-deb')
    subprocess.run(['openssl','req','-x509','-newkey','ed25519','-nodes','-keyout',str(root/'ca.key'),'-out',str(root/'ca.crt'),'-subj','/CN=apt.isle','-addext','subjectAltName=DNS:apt.isle','-days','1'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
    gpg=['gpg','--homedir',str(root/'gnupg'),'--batch','--pinentry-mode','loopback','--passphrase','']
    subprocess.run(gpg+['--quick-gen-key','Isle Apt Test <test@localhost>','ed25519','sign','0'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
    for name in ('local','upstream'):
        repo=root/name
        data=subprocess.check_output(['dpkg-scanpackages','--multiversion','.', '/dev/null'],cwd=repo,stderr=subprocess.DEVNULL)
        (repo/'Packages').write_bytes(data);(repo/'Packages.gz').write_bytes(gzip.compress(data))
        release=subprocess.check_output(['apt-ftparchive','release','.'],cwd=repo);(repo/'Release').write_bytes(release)
        subprocess.run(gpg+['--yes','--clearsign','--output',str(repo/'InRelease'),str(repo/'Release')],check=True)
        with (repo/'isle-archive-keyring.gpg').open('wb') as output:subprocess.run(gpg+['--export'],stdout=output,check=True)
    script=r'''set -euo pipefail
# The minimum container lacks desktop Python. Bootstrap it from the bundled
# pool with real apt, still with the container's networking disabled.
POOL=/bootstrap-medium
WORK=$(mktemp -d)
TARGETS=(python3)
source /bootstrap-helper.sh
offline_apt_prepare
offline_apt_install
unset APT_CONFIG
mkdir -p /etc/polari /test
cp /fixture/apt-install.sh /test/install.sh
# The ordinary host legacy main list is deliberately an upstream source.
printf 'deb [trusted=yes] copy:/fixture/upstream ./\n' > /etc/apt/sources.list
rm -f /etc/apt/sources.list.d/*
printf online > /etc/polari/install-mode
bash /test/install.sh isle-supply-proof > /test/local.log 2>&1
cat /test/local.log
! grep -q 'copy:/fixture/upstream' /test/local.log
test "$(cat /usr/share/isle-supply-proof/source)" = local
printf offline > /etc/polari/install-mode
if bash /test/install.sh isle-supply-extra > /test/missing.log 2>&1; then echo 'FAIL offline fetched an upstream-only package'; exit 1; fi
! grep -q 'copy:/fixture/upstream' /test/missing.log
! dpkg-query -W -f='${db:Status-Status}' isle-supply-extra 2>/dev/null | grep -qx installed
printf online > /etc/polari/install-mode
bash /test/install.sh isle-supply-proof=2
test "$(cat /usr/share/isle-supply-proof/source)" = upstream-new-version
printf offline > /etc/polari/install-mode
bash /test/install.sh isle-supply-proof
test "$(dpkg-query -W -f='${Version}' isle-supply-proof)" = 2
bash /test/install.sh /fixture/direct/isle-supply-proof_3_all.deb
test "$(cat /usr/share/isle-supply-proof/source)" = direct-local-deb
test -f /var/cache/isle-mesh/archives/isle-supply-proof_3_all.deb
bash /test/install.sh isle-supply-proof
test "$(dpkg-query -W -f='${Version}' isle-supply-proof)" = 3
# Exercise the member's actual authenticated HTTPS + apt signature path.
mkdir -p /etc/isle-mesh/ca /usr/share/keyrings
cp /fixture/ca.crt /etc/isle-mesh/ca/isle-root.crt
cp /fixture/local/isle-archive-keyring.gpg /usr/share/keyrings/isle-archive-keyring.gpg
printf '\n127.0.0.1 apt.isle\n' >> /etc/hosts
cat > /test/server.py <<'SERVER'
import functools,http.server,ssl
server=http.server.HTTPServer(('127.0.0.1',443),functools.partial(http.server.SimpleHTTPRequestHandler,directory='/fixture/local'))
context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain('/fixture/ca.crt','/fixture/ca.key')
server.socket=context.wrap_socket(server.socket,server_side=True)
server.serve_forever()
SERVER
python3 /test/server.py > /test/server.log 2>&1 &
SERVER_PID=$!
sleep 1
sed 's@REPO=/fixture/local@REPO=/nonexistent@' /test/install.sh > /test/https-install.sh
bash /test/https-install.sh isle-supply-https
test "$(cat /usr/share/isle-supply-https/source)" = authenticated-local-https
kill "$SERVER_PID"
wait "$SERVER_PID" || true
# An unavailable core must not prevent using complete protected media.
mkdir -p /usr/share/isle-test-medium/apt /usr/share/isle-test-medium/scripts /etc/isle-mesh/ca /usr/share/keyrings
cp /fixture/upstream/* /usr/share/isle-test-medium/apt/
printf fixture > /usr/share/isle-test-medium/ISLE_OFFLINE_BUNDLE
printf '#!/bin/bash\nexit 0\n' > /usr/share/isle-test-medium/scripts/verify-offline.sh
cp /fixture/local/isle-archive-keyring.gpg /usr/share/keyrings/isle-archive-keyring.gpg
cp /fixture/ca.crt /etc/isle-mesh/ca/isle-root.crt
printf '\n127.0.0.1 apt.isle\n' >> /etc/hosts
sed 's@REPO=/fixture/local@REPO=/nonexistent@' /test/install.sh > /test/medium-install.sh
printf /usr/share/isle-test-medium > /etc/polari/offline-source
bash /test/medium-install.sh isle-supply-extra
test "$(cat /usr/share/isle-supply-extra/source)" = upstream-only
# Mutable media must never execute a root verifier after staging approval.
chmod o+w /usr/share/isle-test-medium
printf '#!/bin/bash\ntouch /test/unsafe-verifier-ran\n' > /usr/share/isle-test-medium/scripts/verify-offline.sh
if bash /test/medium-install.sh isle-supply-proof=4 > /test/mutable.log 2>&1; then exit 1; fi
! test -e /test/unsafe-verifier-ran
grep -q 'Untrusted mutable medium' /test/mutable.log
chmod o-w /usr/share/isle-test-medium
# Signature/hash corruption must not be hidden by online fallback.
cp -r /fixture/local /test/bad-repo
printf '\nPackage: tampered\n' >> /test/bad-repo/Packages
printf tampered > /test/bad-repo/Packages.gz
sed 's@REPO=/fixture/local@REPO=/test/bad-repo@' /test/install.sh > /test/bad-install.sh
printf online > /etc/polari/install-mode
if bash /test/bad-install.sh isle-supply-proof=4 > /test/bad.log 2>&1; then exit 1; fi
! grep -q 'checking configured online repositories' /test/bad.log
printf '\nPASS local source tie preference, online version fallback, offline refusal, no downgrade, direct DEB, persistent archive reuse, unavailable-core medium fallback, mutable-media refusal, signed-index corruption refusal, authenticated HTTPS member supply\n'
'''
    (root/'apt-install.sh').write_text(SCRIPT.read_text().replace('REPO=/etc/isle-mesh/apt/repo','REPO=/fixture/local'))
    subprocess.run(['docker','run','--rm','--pull','never','--network','none','--memory','384m','--cpus','1','--mount',f'type=bind,src={root},dst=/fixture,readonly','--mount',f'type=bind,src={medium},dst=/bootstrap-medium,readonly','--mount',f'type=bind,src={bootstrap},dst=/bootstrap-helper.sh,readonly','ubuntu:24.04','bash','-c',script],check=True,timeout=300)
