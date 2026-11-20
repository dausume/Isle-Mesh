#!/usr/bin/env python3
"""The isle apt pin, proved with real apt in a disposable network-none container. The host is untouched.

Two flat repositories: `isle` (Release origin isle-mesh, as apt-repo.sh publishes) carrying isle and polari
packages AND operating system packages of another release, and `ubuntu` standing for the device's own archive.
1. Without the pin apt takes the operating system package from the isle source (the 2026-11-17 fault).
2. With the pin it takes only isle-* and polari-* from it, in a real upgrade and real installs.
3. A blanket block beside the pin (a recovery pin on everything) still lets isle-* and polari-* through.
4. `isle apt install` (apt-install.sh) reads every host pin, isle-mesh.pref too. One exception: a core whose
   own repository still has the one old index (no deps/ yet) leaves isle-mesh.pref out. The lines that do
   it are taken from the script itself and run here, so this fails if they are removed or changed.
The two indexes themselves (publish, deps/<release>/, the installer end to end) are in test-apt-deps-index.py.
"""
import re, subprocess, sys, tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
PIN = SCRIPTS.parent / 'apt' / 'isle-mesh.pref'
IMAGE = sys.argv[1] if len(sys.argv) > 1 else 'ubuntu:noble'

conf = re.search(r"\n# >>> host-pins[^\n]*\n(.*?)\n# <<< host-pins\n", (SCRIPTS / 'apt-install.sh').read_text(), re.S)
assert conf, 'apt-install.sh no longer carries its host-pins lines between the markers'

with tempfile.TemporaryDirectory(prefix='isle-apt-pin-') as directory:
    root = Path(directory); root.chmod(0o755)
    for name in ('isle', 'ubuntu', 'direct'): (root / name).mkdir()

    def package(where, name, version):
        stage = root / 'stage'
        subprocess.run(['rm', '-rf', str(stage)], check=True)
        (stage / 'DEBIAN').mkdir(parents=True)
        (stage / 'DEBIAN/control').write_text(
            f'Package: {name}\nVersion: {version}\nArchitecture: all\nMaintainer: Test <test@localhost>\n'
            'Description: isle apt pin test\n')
        subprocess.run(['dpkg-deb', '--build', str(stage), str(root / where / f'{name}_{version}_all.deb')],
                       stdout=subprocess.DEVNULL, check=True)

    package('direct', 'isle-supply', '1'); package('direct', 'libvictim', '1')   # what the device has
    package('ubuntu', 'libvictim', '1'); package('ubuntu', 'heldlib', '1')       # the device's own archive
    package('isle', 'isle-supply', '2'); package('isle', 'polari-thing', '1'); package('isle', 'isle-next', '1')
    package('isle', 'libvictim', '2'); package('isle', 'foreignonly', '1')       # the other release's packages
    for name, origin in (('isle', 'isle-mesh'), ('ubuntu', 'Ubuntu')):
        repo = root / name
        (repo / 'Packages').write_bytes(subprocess.check_output(
            ['dpkg-scanpackages', '--multiversion', '.', '/dev/null'], cwd=repo, stderr=subprocess.DEVNULL))
        (repo / 'Release').write_bytes(subprocess.check_output(
            ['apt-ftparchive', '-o', f'APT::FTPArchive::Release::Origin={origin}', 'release', '.'], cwd=repo))
    (root / 'isle-mesh.pref').write_bytes(PIN.read_bytes())
    (root / 'installer-host-pins.sh').write_text(conf.group(1) + '\n')
    for path in root.rglob('*'): path.chmod(0o755 if path.is_dir() else 0o644)

    script = r'''set -eu
fail(){ echo "FAIL: $*" >&2; exit 1; }
cand(){ apt-cache policy "$1" | sed -n 's/^  Candidate: //p'; }
have(){ dpkg-query -W -f='${Version}' "$1" 2>/dev/null || true; }
expect(){ [ "$2" = "$3" ] || fail "$1: expected '$3', got '$2'"; echo "ok  $1 = $3"; }
export DEBIAN_FRONTEND=noninteractive
dpkg -i /t/direct/isle-supply_1_all.deb /t/direct/libvictim_1_all.deb >/dev/null
rm -f /etc/apt/sources.list /etc/apt/sources.list.d/*
echo 'deb [trusted=yes] file:/t/ubuntu ./' > /etc/apt/sources.list.d/ubuntu.list
echo 'deb [trusted=yes] file:/t/isle ./' > /etc/apt/sources.list.d/00-isle-mesh.list
apt-get update >/dev/null

echo "1. no pin: the fault"
expect "candidate libvictim" "$(cand libvictim)" 2
expect "candidate foreignonly" "$(cand foreignonly)" 1

echo "2. with the pin"
install -m 644 /t/isle-mesh.pref /etc/apt/preferences.d/isle-mesh.pref
expect "candidate libvictim" "$(cand libvictim)" 1
expect "candidate foreignonly" "$(cand foreignonly)" "(none)"
expect "candidate isle-supply" "$(cand isle-supply)" 2
expect "candidate polari-thing" "$(cand polari-thing)" 1
apt-get -y dist-upgrade >/dev/null
expect "after upgrade isle-supply" "$(have isle-supply)" 2
expect "after upgrade libvictim" "$(have libvictim)" 1
apt-get -y install polari-thing >/dev/null
expect "installed polari-thing" "$(have polari-thing)" 1
if apt-get -y install foreignonly >/dev/null 2>&1; then fail "foreignonly installed from the isle source"; fi
expect "foreignonly not installed" "$(have foreignonly)" ""
if apt-get -y install libvictim=2 >/dev/null 2>&1 && [ "$(have libvictim)" = 2 ]; then
    echo "ok  an explicit version still installs (apt always allows a named version; stated, not a fault)"
fi

echo "3. a blanket block beside the pin"
printf 'Package: *\nPin: origin ""\nPin-Priority: -1\n' > /etc/apt/preferences.d/00-desktop-recovery-block-isle
expect "candidate isle-next" "$(cand isle-next)" 1
apt-get -y install isle-next >/dev/null
expect "installed isle-next" "$(have isle-next)" 1
rm /etc/apt/preferences.d/00-desktop-recovery-block-isle

echo "4. the isle installer reads every host pin, isle-mesh.pref too (one exception: a core on the old index)"
printf 'Package: heldlib\nPin: release o=Ubuntu\nPin-Priority: -1\n' > /etc/apt/preferences.d/device-own-pin
expect "host candidate foreignonly" "$(cand foreignonly)" "(none)"
expect "host candidate heldlib" "$(cand heldlib)" "(none)"
set -euo pipefail          # as apt-install.sh runs
icand(){ apt-cache "${OPTS[@]}" policy "$1" | sed -n 's/^  Candidate: //p'; }
load(){ WORK=$(mktemp -d); OPTS=(); . /t/installer-host-pins.sh; }
REPO=/tmp/no-repository; load                                   # a member: no repository on disk
expect "member: installer candidate foreignonly (the pin applies)" "$(icand foreignonly)" "(none)"
expect "member: installer candidate heldlib (the device's own pin applies)" "$(icand heldlib)" "(none)"
REPO=/tmp/core; mkdir -p "$REPO"; : > "$REPO/InRelease"; load   # a core, one old index: its own packages are in it
expect "core on the old index: installer candidate foreignonly (isle-mesh.pref left out)" "$(icand foreignonly)" 1
expect "core on the old index: installer candidate heldlib (the device's own pin still applies)" "$(icand heldlib)" "(none)"
mkdir "$REPO/deps"; load                                        # the same core after its first publication with two indexes
expect "core with two indexes: installer candidate foreignonly (the pin applies)" "$(icand foreignonly)" "(none)"
rm /etc/apt/preferences.d/*
REPO=/tmp/no-repository; load
expect "installer with no host pins at all" "$(icand heldlib)" 1
echo "ALL PASS"
'''
    result = subprocess.run(['docker', 'run', '--rm', '--network', 'none', '-v', f'{root}:/t:ro', IMAGE,
                             'bash', '-c', script])
    sys.exit(result.returncode)
