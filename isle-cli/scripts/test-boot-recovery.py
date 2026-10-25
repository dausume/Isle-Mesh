#!/usr/bin/env python3
"""Exercise boot recovery with fake Docker/ip; never mutate host services."""
import os
from pathlib import Path
import subprocess
import tempfile

script = Path(__file__).with_name('boot-bringup.sh')
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    for name in ('ip', 'docker'):
        p = root / name
        p.write_text('''#!/bin/bash
printf '%s|%s|%s\\n' "$PWD" "${0##*/}" "$*" >> "$CALLS"
if [[ "${0##*/}" = docker && "$1 $2" = 'network inspect' ]]; then exit 1; fi
if [[ "${0##*/}" = docker && "$1 $2" = 'network create' && "$FAIL_CREATE" = 1 ]]; then exit 1; fi
if [[ "${0##*/}" = docker && "$1 $2" = 'compose up' && "$FAIL_AGENT" = 1 ]]; then exit 1; fi
''')
        p.chmod(0o755)
    (root / 'docker-compose.yml').touch()
    env = dict(os.environ, PATH=str(root)+':'+os.environ['PATH'], CALLS=str(root/'calls'))
    def run(command, **flags):
        return subprocess.run(['bash', '-c', 'source "$1"; AGENT_DIR="$2"; '+command, 'test', str(script), str(root)], env=dict(env, **flags), capture_output=True)
    assert run('resync_macvlan', FAIL_CREATE='1').returncode != 0
    assert run('resync_macvlan', FAIL_CREATE='0').returncode == 0
    assert run('bring_up_agent', FAIL_AGENT='1').returncode != 0
    assert run('bring_up_agent', FAIL_AGENT='0').returncode == 0
    calls=(root/'calls').read_text()
    assert str(root)+'|docker|compose up -d --no-build --force-recreate' in calls
print('PASS: macvlan/agent failures propagate; effective compose directory; no image build')
