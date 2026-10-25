#!/usr/bin/env python3
"""Isolated CA onboarding regression: no host trust or network changes."""
import json
import os
from pathlib import Path
import pwd
import shlex
import subprocess
import tempfile

source = Path(__file__).with_name("trust.sh").read_text()
caller = pwd.getpwuid(os.getuid())
resolve = source[source.index('if [ -n "${PKEXEC_UID:-}" ]; then'):source.index('NICK=')]
for environment in [
    f"PKEXEC_UID={os.getuid()} SUDO_USER=root",
    f"unset PKEXEC_UID; SUDO_USER={shlex.quote(caller.pw_name)}",
    "unset PKEXEC_UID SUDO_USER",
]:
    result = subprocess.run(["bash", "-c", environment + "\n" + resolve +
        '\nprintf "%s:%s" "$TRUST_USER" "$TRUST_HOME"'],
        capture_output=True, text=True, check=True)
    assert result.stdout == caller.pw_name + ":" + caller.pw_dir
print("PASS: pkexec, sudo and direct caller selection")

with tempfile.TemporaryDirectory(prefix="isle-browser-trust-") as directory:
    root = Path(directory)
    cert = root / "root.crt"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(root / "key"), "-out", str(cert), "-days", "1",
        "-subj", "/CN=Isolated onboarding regression"], capture_output=True, check=True)
    body = source[source.index("import_browser() {"):source.index("install_trust() {")]
    setup = ('set -eu\nas_trust_user(){ "$@"; }; ok(){ :; }; fail(){ :; };\n' +
        f'CA={shlex.quote(str(cert))}\nTRUST_HOME={shlex.quote(directory)}\n' +
        f'NSSDB={shlex.quote("sql:" + directory + "/.pki/nssdb")}\n' +
        f'TRUST_USER={shlex.quote(caller.pw_name)}\nNICK="Isle Root CA"\n')
    result = subprocess.run(["bash", "-c", setup + body +
        '\nimport_browser\nimport_browser\ncertutil -d "$NSSDB" -L -n "$NICK" -a'],
        capture_output=True, text=True, check=True)
    assert cert.read_text().strip() in result.stdout
    assert (root / ".pki/nssdb/cert9.db").stat().st_uid == os.getuid()
    branch = source[source.index('        [ -f "$CA" ] && cmp -s'):source.index("    install)")]
    branch = branch.replace(" ;;", "").replace(
        "/usr/local/share/ca-certificates/isle-root.crt", str(root / "system-root.crt"))
    assert subprocess.run(["bash", "-c", setup + body + branch],
        capture_output=True).returncode != 0
    (root / "system-root.crt").write_text(cert.read_text())
    subprocess.run(["bash", "-c", setup + body + branch], capture_output=True, check=True)
print("PASS: repeated NSS import, certificate content, ownership and system-approval guard")
