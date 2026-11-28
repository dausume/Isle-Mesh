#!/usr/bin/env python3
"""isle trust gives Docker the isle root through certs.d: no host trust or network changes."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile

source = Path(__file__).with_name("trust.sh").read_text()
body = source[source.index("import_docker() {"):source.index("import_browser() {")]
hosts = source[source.index("DOCKER_HOSTS="):].splitlines()[0]

with tempfile.TemporaryDirectory(prefix="isle-docker-trust-") as directory:
    root = Path(directory)
    cert = root / "root.crt"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(root / "key"), "-out", str(cert), "-days", "1",
        "-subj", "/CN=Isolated docker regression"], capture_output=True, check=True)
    bare = root / "bare"; bare.mkdir()                           # a PATH with no docker on it
    for tool in ("install", "cmp", "bash"):
        os.symlink(shutil.which(tool), bare / tool)
    tools = root / "tools"; tools.mkdir()
    (tools / "docker").write_text("#!/bin/sh\nexit 0\n"); (tools / "docker").chmod(0o755)
    certs = root / "certs.d"
    setup = ('set -eu\nsudo(){ "$@"; }; ok(){ :; }; fail(){ :; };\n' +
        f'CA={shlex.quote(str(cert))}\nDOCKER_CERTS={shlex.quote(str(certs))}\n{hosts}\n')
    def run(path):
        return subprocess.run(["bash", "-c", setup + body + "\nimport_docker"],
            capture_output=True, text=True, env={"PATH": path})

    assert run(str(bare)).returncode == 0 and not certs.exists()
    print("PASS: without Docker nothing is written")

    target = certs / "registry.isle" / "ca.crt"
    target.parent.mkdir(parents=True); target.write_text("an older root\n")
    assert run(f"{tools}:{bare}").returncode == 0
    assert target.read_text() == cert.read_text()
    assert target.stat().st_mode & 0o777 == 0o644
    print("PASS: the current root replaces an older one for registry.isle")

    assert 'DOCKER_HOSTS="registry.isle"' == hosts
    assert "import_docker || return 1" in source[source.index("do_import() {"):source.index("import_docker() {")]
    print("PASS: every import (install, fetch, rotation by update) gives Docker the root")
