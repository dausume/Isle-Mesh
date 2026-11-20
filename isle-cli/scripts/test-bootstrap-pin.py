#!/usr/bin/env python3
"""The join script writes the isle apt pin before it writes an isle source line or installs a package.

isle-bootstrap.sh runs before the isle CLI exists on the device, so it carries its own copy of the pin.
1. That copy is byte for byte the CLI's apt/isle-mesh.pref.
2. In the script the pin is written before the source line, before any apt-get and before the installer runs.
3. The pin lines, run for real against a temporary directory, produce exactly that file with mode 644.
"""
import re, subprocess, sys, tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
PIN = (SCRIPTS.parent / 'apt' / 'isle-mesh.pref').read_text()
text = (SCRIPTS / 'isle-bootstrap.sh').read_text()

block = re.search(r"\nPIN_TMP=\$\(mktemp\)\ncat > \"\$PIN_TMP\" <<'ISLEPIN'\n(.*?\n)ISLEPIN\n(install [^\n]*\n)", text, re.S)
assert block, 'isle-bootstrap.sh no longer writes the pin'
assert block.group(1) == PIN, 'the pin inside isle-bootstrap.sh differs from apt/isle-mesh.pref'
print('ok  the embedded pin equals apt/isle-mesh.pref')

at = text.index("<<'ISLEPIN'")
for what, marker in (('the isle source line', r'/etc/apt/sources\.list\.d/00-isle-mesh\.list'), ('apt-get', r'^\s*apt-get '),
                     ('the package installer', r'^\s*bash "\$APT_INSTALLER"'), ('isle apt-repo enable', r'^\s*isle apt-repo enable')):
    first = re.search(marker, text, re.M)
    assert first, 'isle-bootstrap.sh no longer has ' + what
    assert at < first.start(), 'the pin is written after ' + what
    print('ok  the pin is written before ' + what)
assert subprocess.run(['bash', '-n', str(SCRIPTS / 'isle-bootstrap.sh')]).returncode == 0

with tempfile.TemporaryDirectory(prefix='isle-bootstrap-pin-') as directory:
    target = Path(directory) / 'etc/apt/preferences.d/isle-mesh.pref'
    lines = text[text.index('\nPIN_TMP=$(mktemp)\n'):block.end()].replace('/etc/apt/preferences.d/isle-mesh.pref', str(target))
    assert str(target) in lines
    run = subprocess.run(['bash', '-c', 'set -u\ndie(){ echo "FAIL $*"; exit 1; }\n' + lines], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    assert target.read_text() == PIN and target.stat().st_mode & 0o777 == 0o644
    print('ok  the pin lines write the file (mode 644) into a directory that did not exist')
print('ALL PASS')
