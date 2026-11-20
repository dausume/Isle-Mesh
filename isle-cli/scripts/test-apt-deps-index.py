#!/usr/bin/env python3
"""The two indexes of the isle repository, proved end to end with the real tools. The host is untouched.

  test-apt-deps-index.py [image]        default image: ubuntu:noble

A. The REAL `apt-repo.sh publish` (real dpkg-scanpackages, apt-ftparchive, gpg; only sudo, getent, docker
   and the /etc prefix are stood in) publishes real package files into a temporary repository, indexed
   under the release name of the image under test.
B. This host's own apt, unprivileged, with private directories: the signed deps/<release>/ index resolves
   its package files in the repository root, and the pin does not touch it.
C. The lines of apt-install.sh that ask the core whether it serves a dependency set (real curl, verified
   TLS) against a local TLS server: served, not served, wrong CA, no server.
D. In a disposable network-none container, as a core (the repository on disk):
   the device's own apt sees only isle-* and polari-* in the main index, even without a pin;
   the real apt-install.sh installs a polari package with its operating system dependency from
   deps/<release>/; on another release it refuses the same package.
E. A core whose own repository still has the one old index: the real apt-install.sh leaves the pin out.
F. As a member over https (needs python3 in the image for the TLS server; curl is stood in by a small
   python program when the image has none): the same, plus a device's own blanket block on apt.isle.
"""
import os, re, shutil, socket, subprocess, sys, tempfile, time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
IMAGE = sys.argv[1] if len(sys.argv) > 1 else 'ubuntu:noble'


def run(cmd, **kw):
    return subprocess.run(cmd, text=True, capture_output=True, **kw)


def must(cmd, **kw):
    p = run(cmd, **kw)
    assert p.returncode == 0, '%s\n%s%s' % (cmd, p.stdout, p.stderr)
    return p.stdout


def ok(text): print('ok  ' + text, flush=True)


CODENAME = must(['docker', 'run', '--rm', '--network', 'none', IMAGE, 'sh', '-c',
                 '. /etc/os-release; printf %s "${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"'])
assert re.fullmatch(r'[a-z][a-z0-9]*', CODENAME), 'cannot read the release name of ' + IMAGE
HAS_PYTHON = run(['docker', 'run', '--rm', '--network', 'none', IMAGE, 'sh', '-c', 'command -v python3']).returncode == 0
print('image %s = release %s, %s' % (IMAGE, CODENAME, must(['docker', 'run', '--rm', '--network', 'none', IMAGE, 'apt-get', '--version']).splitlines()[0]))

SERVER = r'''import functools, http.server, ssl, sys
root, cert, key, port = sys.argv[1:5]
class Handler(http.server.SimpleHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
server = http.server.ThreadingHTTPServer(('127.0.0.1', int(port)), functools.partial(Handler, directory=root))
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(cert, key)
server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
'''
# Stands in for curl ONLY inside an image that has none. It does what the one curl line of deps_served asks:
# GET the URL over TLS verified with --cacert, print the status code.
CURL_STAND_IN = r'''#!/usr/bin/env python3
import ssl, sys, urllib.error, urllib.request
a = sys.argv[1:]
try:
    urllib.request.urlopen(a[-1], timeout=8, context=ssl.create_default_context(cafile=a[a.index('--cacert') + 1]))
    print(200, end='')
except urllib.error.HTTPError as error: print(error.code, end='')
except Exception as error: print('000', end=''); print('curl stand-in: %s' % error, file=sys.stderr); sys.exit(7)
'''

with tempfile.TemporaryDirectory(prefix='isle-apt-deps-') as directory:
    root = Path(directory); root.chmod(0o755)
    etc = root / 'etc'
    for name in ('platform', 'dependencies', 'ubuntu', 'direct', 'bin', 'tls', 'etc/isle-mesh/ca', 'fixture'): (root / name).mkdir(parents=True)

    def package(where, name, version, depends=''):
        stage = root / 'stage'
        shutil.rmtree(stage, ignore_errors=True); (stage / 'DEBIAN').mkdir(parents=True)
        (stage / 'DEBIAN/control').write_text(
            f'Package: {name}\nVersion: {version}\nArchitecture: all\nMaintainer: Test <test@localhost>\n'
            + (f'Depends: {depends}\n' if depends else '') + 'Description: isle dependency index test\n')
        must(['dpkg-deb', '--root-owner-group', '--build', str(stage), str(root / where / f'{name}_{version}_all.deb')])

    package('direct', 'isle-supply', '1'); package('direct', 'libvictim', '1')            # what the device has
    package('ubuntu', 'libvictim', '1'); package('ubuntu', 'heldlib', '1')                # the device's own archive
    package('platform', 'isle-supply', '2'); package('platform', 'isle-next', '1')
    package('platform', 'polari-thing', '1', 'libdep (>= 1)')
    package('dependencies', 'libdep', '1'); package('dependencies', 'libvictim', '2'); package('dependencies', 'foreignonly', '1')
    must(['sh', '-c', 'dpkg-scanpackages --multiversion . /dev/null > Packages 2>/dev/null && gzip -kf Packages && '
          'apt-ftparchive -o APT::FTPArchive::Release::Origin=Ubuntu release . > Release'], cwd=root / 'ubuntu')

    # the isle root and the certificate of apt.isle
    tls = root / 'tls'
    must(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'ca.key', '-out', 'ca.crt', '-subj', '/CN=isle test root', '-days', '2',
          '-addext', 'keyUsage=critical,keyCertSign,cRLSign'], cwd=tls)
    must(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'other.key', '-out', 'other.crt', '-subj', '/CN=another root', '-days', '2'], cwd=tls)
    must(['openssl', 'req', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'server.key', '-out', 'server.csr', '-subj', '/CN=apt.isle'], cwd=tls)
    (tls / 'san').write_text('subjectAltName=DNS:apt.isle\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n'
                             'extendedKeyUsage=serverAuth\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n')
    must(['openssl', 'x509', '-req', '-in', 'server.csr', '-CA', 'ca.crt', '-CAkey', 'ca.key', '-CAcreateserial', '-out', 'server.crt', '-days', '2', '-extfile', 'san'], cwd=tls)
    shutil.copy(tls / 'ca.crt', etc / 'isle-mesh/ca/isle-root.crt')
    (root / 'server.py').write_text(SERVER); (root / 'curl-stand-in').write_text(CURL_STAND_IN); (root / 'curl-stand-in').chmod(0o755)

    # ---- A. the real publication
    fixture = root / 'fixture'
    (fixture / 'apt-repo.sh').write_text((SCRIPTS / 'apt-repo.sh').read_text().replace('/etc/', str(etc) + '/'))
    for name in ('apt_index_split.py', 'isle-bootstrap.sh', 'apt-install.sh'): shutil.copy(SCRIPTS / name, fixture / name)
    (etc / 'os-release').write_text(f'NAME="Test"\nVERSION_CODENAME={CODENAME}\n')
    stand_ins = {'sudo': '#!/bin/bash\nwhile [[ "${1:-}" == *=* ]]; do export "$1"; shift; done\nexec "$@"\n',
                 'getent': '#!/bin/bash\necho "fixture:x:1000:1000::%s:/bin/bash"\n' % root,
                 'docker': '#!/bin/bash\n[ "$1" = ps ] && echo isle-apt-apt-1\n',
                 'isle': '#!/bin/bash\necho "FORBIDDEN live operation" >&2; exit 1\n'}
    for name, text in stand_ins.items(): (root / 'bin' / name).write_text(text); (root / 'bin' / name).chmod(0o755)
    environment = dict(os.environ, PATH=str(root / 'bin') + ':' + os.environ['PATH'])
    repo, gnupg = etc / 'isle-mesh/apt/repo', etc / 'isle-mesh/apt/gnupg'
    try:
        published = run(['bash', str(fixture / 'apt-repo.sh'), 'publish', '--from', str(root / 'platform'),
                         '--dependencies-from', str(root / 'dependencies')], env=environment, timeout=120)
        assert published.returncode == 0, published.stdout + published.stderr
        deps = repo / 'deps' / CODENAME
        names = lambda path: sorted(re.findall(r'^Package: (.+)$', path.read_text(), re.M))
        assert names(repo / 'Packages') == ['isle-next', 'isle-supply', 'polari-thing'], names(repo / 'Packages')
        assert names(deps / 'Packages') == ['foreignonly', 'libdep', 'libvictim'], names(deps / 'Packages')
        assert len(list(repo.glob('*.deb'))) == 6 and not list(deps.glob('*.deb'))
        for base, origin in ((repo, 'isle-mesh'), (deps, 'isle-mesh-deps')):
            release = (base / 'Release').read_text()
            assert f'Origin: {origin}\n' in release and 'Acquire-By-Hash: yes\n' in release, release
            listed = sorted(set(re.findall(r'^ [0-9a-f]{64} +\d+ (\S+)$', release, re.M)))
            # apt-ftparchive also lists the Release file it is writing (empty at that moment); the live repository has
            # always carried that row and apt ignores it
            assert [name for name in listed if name != 'Release'] == ['Packages', 'Packages.gz'], listed
            must(['gpgv', '--keyring', str(repo / 'isle-archive-keyring.gpg'), str(base / 'Release.gpg'), str(base / 'Release')])
            must(['gpgv', '--keyring', str(repo / 'isle-archive-keyring.gpg'), str(base / 'InRelease')])
        assert f'Codename: {CODENAME}\n' in (deps / 'Release').read_text() and 'Codename:' not in (repo / 'Release').read_text()
        assert f'Suite: deps/{CODENAME}\n' in (deps / 'Release').read_text()
        ok('A. the real publish wrote two signed indexes: 3 isle and polari packages in ./, 3 operating system packages in deps/%s/' % CODENAME)

        # the same package files with the ONE old index (a core before its first publication with CLI 89)
        old = root / 'oldrepo'; old.mkdir()
        for path in list(repo.glob('*.deb')) + [repo / 'isle-archive-keyring.gpg']: shutil.copy(path, old / path.name)
        must(['sh', '-c', 'dpkg-scanpackages --multiversion . /dev/null > Packages 2>/dev/null && gzip -kf Packages && '
              'apt-ftparchive -o APT::FTPArchive::Release::Origin=isle-mesh -o APT::FTPArchive::Release::Suite=isle release . > Release'], cwd=old)
        sign = ['gpg', '--homedir', str(gnupg), '--batch', '--pinentry-mode', 'loopback', '--passphrase', '', '--yes']
        must(sign + ['--clearsign', '-o', str(old / 'InRelease'), str(old / 'Release')])
    finally:
        run(['gpgconf', '--homedir', str(gnupg), '--kill', 'gpg-agent'])
    shutil.copy(SCRIPTS.parent / 'apt' / 'isle-mesh.pref', root / 'isle-mesh.pref')
    for path in list(root.rglob('*')):
        if gnupg not in path.parents and path != gnupg and not path.is_symlink(): path.chmod(0o755 if path.is_dir() or path.parent.name == 'bin' or path.name == 'curl-stand-in' else 0o644)

    # ---- B. this host's apt, unprivileged
    work = root / 'hostapt'
    for name in ('lists/partial', 'archives/partial', 'cache', 'sourceparts', 'preferences.d', 'log'): (work / name).mkdir(parents=True)
    (work / 'status').write_text(''); (work / 'apt.conf').write_text('Dir::Etc::main "/dev/null";\nDir::Etc::parts "/dev/null";\n')
    options = ['-o', f'Dir::Etc::SourceList={work}/sources.list', '-o', f'Dir::Etc::SourceParts={work}/sourceparts',
               '-o', f'Dir::State::lists={work}/lists', '-o', f'Dir::Cache={work}/cache', '-o', f'Dir::Cache::archives={work}/archives',
               '-o', f'Dir::State::status={work}/status', '-o', f'Dir::Etc::PreferencesParts={work}/preferences.d',
               '-o', 'Dir::Etc::Preferences=/dev/null', '-o', f'Dir::Log={work}/log', '-o', 'Debug::NoLocking=1',
               '-o', 'APT::Get::List-Cleanup=0', '-o', 'APT::Update::Error-Mode=any']
    private = dict(os.environ, APT_CONFIG=str(work / 'apt.conf'))
    keyring = repo / 'isle-archive-keyring.gpg'

    def candidate(name):
        found = re.search(r'^  Candidate: (.+)$', must(['apt-cache'] + options + ['policy', name], env=private), re.M)
        return found.group(1) if found else '(none)'      # a package no index lists has no policy output at all

    def host_sources(*lines):
        (work / 'sources.list').write_text(''.join(line + '\n' for line in lines))
        shutil.rmtree(work / 'lists'); (work / 'lists/partial').mkdir(parents=True)
        update = run(['apt-get'] + options + ['update'], env=private)
        assert update.returncode == 0 and 'W:' not in update.stdout + update.stderr, update.stdout + update.stderr

    version = must(['apt-get', '--version']).splitlines()[0]
    host_sources(f'deb [signed-by={old}/isle-archive-keyring.gpg] copy:{old} ./')
    assert candidate('foreignonly') == '1'                                          # the one old index, no pin: the fault
    shutil.copy(root / 'isle-mesh.pref', work / 'preferences.d/isle-mesh.pref')
    assert (candidate('foreignonly'), candidate('libvictim'), candidate('isle-supply')) == ('(none)', '(none)', '2')
    host_sources(f'deb [signed-by={keyring}] copy:{repo} ./')
    (work / 'preferences.d/isle-mesh.pref').unlink()
    assert (candidate('foreignonly'), candidate('libdep'), candidate('isle-supply'), candidate('polari-thing')) == ('(none)', '(none)', '2', '1')
    ok('B. %s: the main index offers no operating system package, pin or no pin' % version)
    shutil.copy(root / 'isle-mesh.pref', work / 'preferences.d/isle-mesh.pref')
    host_sources(f'deb [signed-by={keyring}] copy:{repo} ./', f'deb [signed-by={keyring}] copy:{repo} deps/{CODENAME}/')
    assert (candidate('libdep'), candidate('foreignonly'), candidate('isle-supply')) == ('1', '1', '2')
    policy = must(['apt-cache'] + options + ['policy'], env=private)
    assert 'o=isle-mesh-deps' in policy and f'n={CODENAME}' in policy, policy
    must(['apt-get'] + options + ['--download-only', '--no-remove', 'install', '-y', '--no-install-recommends', 'polari-thing'], env=private)
    fetched = sorted(p.name for p in (work / 'archives').glob('*.deb'))
    assert fetched == ['libdep_1_all.deb', 'polari-thing_1_all.deb'], fetched
    assert (work / 'archives/libdep_1_all.deb').read_bytes() == (repo / 'libdep_1_all.deb').read_bytes()
    ok('B. %s: deps/%s/ is verified (signature, by-hash) and its package file is fetched from the repository root; the pin leaves it alone' % (version, CODENAME))

    # ---- C. the probe of apt-install.sh, with real curl
    probe = re.search(r"\n# >>> deps-set[^\n]*\n(.*?)\n# <<< deps-set\n", (SCRIPTS / 'apt-install.sh').read_text(), re.S)
    assert probe, 'apt-install.sh no longer carries its deps-set lines between the markers'
    (root / 'installer-deps-set.sh').write_text(probe.group(1) + '\n')
    with socket.socket() as free: free.bind(('127.0.0.1', 0)); port = free.getsockname()[1]
    server = subprocess.Popen([sys.executable, str(root / 'server.py'), str(repo), str(tls / 'server.crt'), str(tls / 'server.key'), str(port)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def served(codename, ca):
        script = ('set -euo pipefail\ncurl(){ command curl --connect-to "apt.isle:443:127.0.0.1:%d" "$@"; }\nCA=%s\n. %s\n'
                  '[ -n "$CODENAME" ] || { echo "the lines read no release name from this host"; exit 9; }\nCODENAME=%s\ndeps_served' % (port, ca, root / 'installer-deps-set.sh', codename))
        p = run(['bash', '-c', script]); assert p.returncode in (0, 1), p.stdout + p.stderr
        return p.returncode == 0
    try:
        for _ in range(50):
            if served(CODENAME, tls / 'ca.crt'): break
            time.sleep(0.1)
        assert served(CODENAME, tls / 'ca.crt'), 'a served dependency set was not seen'
        assert not served('otherrel', tls / 'ca.crt'), 'a release the core does not serve was reported as served'
        assert not served(CODENAME, tls / 'other.crt'), 'the probe accepted a certificate the isle root did not sign'
    finally:
        server.terminate(); server.wait()
    assert not served(CODENAME, tls / 'ca.crt'), 'no server, yet reported as served'
    ok('C. the probe (real curl, verified TLS): served = yes; another release, a wrong CA, no answer = no')

    # ---- D, E, F. real apt and the real apt-install.sh in a container
    prelude = r'''set -eu
fail(){ echo "FAIL: $*" >&2; [ ! -f /tmp/out ] || sed 's/^/    | /' /tmp/out >&2; exit 1; }
cand(){ local c; c=$(apt-cache policy "$1" | sed -n 's/^  Candidate: //p'); echo "${c:-(none)}"; }   # unknown to every index = none
have(){ if dpkg -s "$1" 2>/dev/null | grep -q '^Status: install ok installed'; then dpkg-query -W -f='${Version}' "$1"; fi; }
expect(){ [ "$2" = "$3" ] || fail "$1: expected '$3', got '$2'"; echo "ok  $1 = $3"; }
installs(){ bash /s/apt-install.sh "$@" > /tmp/out 2>&1 || fail "isle apt install $* failed"
            if grep -q '^W:' /tmp/out; then fail "isle apt install $* went through with an apt warning"; fi; }
refuses(){ if bash /s/apt-install.sh "$@" > /tmp/out 2>&1; then fail "isle apt install $* went through"; fi
           grep -q 'no internet fallback' /tmp/out || fail "isle apt install $* failed for another reason"; }
other_release(){ sed 's/^\(UBUNTU_CODENAME\|VERSION_CODENAME\)=.*/\1=otherrel/' /etc/os-release > /tmp/os-release
                 rm -f /etc/os-release; cp /tmp/os-release /etc/os-release; }
export DEBIAN_FRONTEND=noninteractive
dpkg -i /t/direct/isle-supply_1_all.deb /t/direct/libvictim_1_all.deb >/dev/null
rm -f /etc/apt/sources.list /etc/apt/sources.list.d/*
echo 'deb [trusted=yes] file:/t/ubuntu ./' > /etc/apt/sources.list.d/ubuntu.list
mkdir -p /etc/polari; echo offline > /etc/polari/install-mode
'''
    core = prelude + r'''
echo "D. a core with the two indexes"
echo 'deb [signed-by=/etc/isle-mesh/apt/repo/isle-archive-keyring.gpg] file:/etc/isle-mesh/apt/repo ./' > /etc/apt/sources.list.d/00-isle-mesh.list
apt-get update >/dev/null
expect "device apt, no pin: candidate foreignonly" "$(cand foreignonly)" "(none)"
expect "device apt, no pin: candidate libdep" "$(cand libdep)" "(none)"
expect "device apt, no pin: candidate libvictim (its own archive)" "$(cand libvictim)" 1
apt-get -y dist-upgrade >/dev/null
expect "after a full upgrade without a pin: isle-supply" "$(have isle-supply)" 2
expect "after a full upgrade without a pin: libvictim" "$(have libvictim)" 1
install -m 644 /t/isle-mesh.pref /etc/apt/preferences.d/isle-mesh.pref
installs polari-thing
expect "isle apt install polari-thing" "$(have polari-thing)" 1
expect "its operating system dependency libdep, from deps/@C@/" "$(have libdep)" 1
grep -q 'copy:/etc/isle-mesh/apt/repo deps/@C@/ InRelease' /tmp/out || fail "the dependency set was not read"
installs foreignonly
expect "isle apt install foreignonly (same release)" "$(have foreignonly)" 1
dpkg -r foreignonly >/dev/null
expect "device apt still has no candidate for it" "$(cand foreignonly)" "(none)"
other_release
refuses foreignonly
expect "on another release isle apt install refuses it" "$(have foreignonly)" ""
if grep -q 'deps/' /tmp/out; then fail "a dependency set of another release was read"; fi
echo "ALL PASS (core)"
'''.replace('@C@', CODENAME)
    old_core = prelude + r'''
echo "E. a core whose repository still has the one old index"
install -m 644 /t/isle-mesh.pref /etc/apt/preferences.d/isle-mesh.pref
echo 'deb [signed-by=/etc/isle-mesh/apt/repo/isle-archive-keyring.gpg] file:/etc/isle-mesh/apt/repo ./' > /etc/apt/sources.list.d/00-isle-mesh.list
apt-get update >/dev/null
expect "device apt, with the pin: candidate foreignonly" "$(cand foreignonly)" "(none)"
installs polari-thing
expect "isle apt install polari-thing" "$(have polari-thing)" 1
expect "its operating system dependency libdep, from the old index" "$(have libdep)" 1
echo "ALL PASS (core, old index)"
'''
    member = prelude + r'''
echo "F. a member, over https"
install -D -m 644 /t/tls/ca.crt /etc/isle-mesh/ca/isle-root.crt
install -D -m 644 /srv/repo/isle-archive-keyring.gpg /usr/share/keyrings/isle-archive-keyring.gpg
command -v curl >/dev/null 2>&1 || { install -m 755 /t/curl-stand-in /usr/local/bin/curl; echo "    (this image has no curl: a python stand-in answers the probe)"; }
python3 /t/server.py /srv/repo /t/tls/server.crt /t/tls/server.key 443 > /tmp/server.log 2>&1 &
for i in $(seq 50); do [ "$(curl --silent --output /dev/null --cacert /t/tls/ca.crt --write-out '%{http_code}' https://apt.isle/InRelease 2>/dev/null || true)" = 200 ] && break; sleep 0.1; done
[ "$(curl --silent --output /dev/null --cacert /t/tls/ca.crt --write-out '%{http_code}' https://apt.isle/InRelease || true)" = 200 ] || fail "the test TLS server does not answer"
echo 'Acquire::https::apt.isle::CaInfo "/etc/isle-mesh/ca/isle-root.crt";' > /etc/apt/apt.conf.d/99-isle-test
echo 'deb [signed-by=/usr/share/keyrings/isle-archive-keyring.gpg] https://apt.isle ./' > /etc/apt/sources.list.d/00-isle-mesh.list
apt-get update >/dev/null
expect "device apt, no pin: candidate foreignonly" "$(cand foreignonly)" "(none)"
expect "device apt, no pin: candidate isle-supply" "$(cand isle-supply)" 2
install -m 644 /t/isle-mesh.pref /etc/apt/preferences.d/isle-mesh.pref
installs polari-thing
expect "isle apt install polari-thing" "$(have polari-thing)" 1
expect "its operating system dependency libdep, from https://apt.isle deps/@C@/" "$(have libdep)" 1
grep -q 'https://apt.isle deps/@C@/ InRelease' /tmp/out || fail "the dependency set was not read"
printf 'Package: *\nPin: origin apt.isle\nPin-Priority: -1\n' > /etc/apt/preferences.d/00-desktop-recovery-block-isle
installs isle-next
expect "with the device's own block on apt.isle: isle-next still installs" "$(have isle-next)" 1
refuses foreignonly
expect "with the device's own block on apt.isle: no operating system package from the core" "$(have foreignonly)" ""
rm /etc/apt/preferences.d/00-desktop-recovery-block-isle
other_release
refuses foreignonly
expect "on another release isle apt install refuses it" "$(have foreignonly)" ""
grep -q 'GET /deps/otherrel/InRelease HTTP/1.1" 404' /tmp/server.log || fail "the member did not ask the core for its own release"
if grep -q 'deps/@C@' /tmp/out; then fail "a dependency set of another release was read"; fi
echo "ALL PASS (member)"
'''.replace('@C@', CODENAME)

    def container(script, *mounts, extra=()):
        command = ['docker', 'run', '--rm', '--network', 'none', '-v', f'{root}:/t:ro', '-v', f'{SCRIPTS}:/s:ro']
        for source, target in mounts: command += ['-v', f'{source}:{target}:ro']
        return subprocess.run(command + list(extra) + [IMAGE, 'bash', '-c', script]).returncode

    failed = container(core, (repo, '/etc/isle-mesh/apt/repo')) or container(old_core, (old, '/etc/isle-mesh/apt/repo'))
    if not failed:
        if HAS_PYTHON: failed = container(member, (repo, '/srv/repo'), extra=('--add-host', 'apt.isle:127.0.0.1'))
        else: print('F. NOT RUN: %s has no python3 for the TLS server. Run this test with an image that has it.' % IMAGE)
    print('FAILED' if failed else ('ALL PASS' if HAS_PYTHON else 'PASS (A to E; F not run)'))
    sys.exit(1 if failed else 0)
