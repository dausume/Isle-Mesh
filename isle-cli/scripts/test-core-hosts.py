#!/usr/bin/env python3
"""Check local core registrations without touching host names or networking."""
import json, os, subprocess, tempfile
from pathlib import Path
source=Path(__file__).with_name('hosts-reconcile.sh').read_text()
with tempfile.TemporaryDirectory() as t:
    r=Path(t); (r/'router').mkdir(); hosts=r/'hosts'; hosts.write_text('127.0.0.1 localhost\n192.0.2.8 example.test\n')
    reg=r/'registry.json'; reg.write_text(json.dumps({'apps':{'a':{'domain':'whoami.isle'},'b':{'domain':'bad.isle\n1.2.3.4 evil.test'}}}))
    script=r/'reconcile.sh'; script.write_text(source.replace('/etc/isle-mesh/router',str(r/'router')).replace('/etc/isle-mesh/agent/registry.json',str(reg)).replace('/etc/hosts',str(hosts)))
    for name,body in {'curl':'exit 1','hostname':'echo test-core','sudo':'[[ "$1" != -n ]] || shift; [[ "$FAIL_CP" != 1 ]] || exit 1; exec "$@"'}.items():
        p=r/name; p.write_text('#!/bin/bash\n'+body+'\n'); p.chmod(0o755)
    env=dict(os.environ,PATH=t+':'+os.environ['PATH'])
    def run(**kw): return subprocess.run(['bash',str(script)],env=dict(env,**kw),capture_output=True)
    assert run().returncode==0
    result=hosts.read_text(); assert 'whoami.isle' in result and 'evil.test' not in result and '192.0.2.8 example.test' in result
    assert run().returncode==0 and hosts.read_text()==result
    reg.write_text(json.dumps({'apps':{'a':{'domain':'new.isle'}}}))
    assert run(FAIL_CP='1').returncode!=0 and hosts.read_text()==result
print('PASS: local names work without catalog; foreign hosts preserved; invalid names rejected; idempotent; write failure reported')
