#!/usr/bin/env python3
"""Publish the exact standalone remote package set after signed APT publication."""
import hashlib,json,re,subprocess,sys
from pathlib import Path
from datetime import datetime,timezone

def notice(repo):
    selected={};wanted={'isle-mesh-cli','isle-app-store','polari-shell-core'}
    for block in (Path(repo)/'Packages').read_text().split('\n\n'):
        fields=dict(line.split(': ',1) for line in block.splitlines() if ': ' in line and not line.startswith(' '))
        name=fields.get('Package');version=fields.get('Version','')
        if name not in wanted or not re.fullmatch(r'[0-9A-Za-z.+:~_-]{1,100}',version):continue
        filename=Path(fields.get('Filename','')).name;sha=fields.get('SHA256','')
        if not re.fullmatch(r'[0-9a-f]{64}',sha) or not filename.endswith('.deb'):continue
        if name in selected and subprocess.run(['dpkg','--compare-versions',version,'le',selected[name]['version']]).returncode==0:continue
        selected[name]=dict(name=name,version=version,sha256=sha,file=filename)
    if set(selected)!=wanted:raise ValueError('Complete remote package set is not published')
    packages=[selected[k] for k in sorted(selected)];digest=hashlib.sha256(json.dumps(packages,sort_keys=True).encode()).hexdigest()
    return dict(id=digest,publishedAt=datetime.now(timezone.utc).isoformat(),packages=packages,
                command='isle apt-repo enable && sudo apt-get update && sudo apt-get --no-remove install '+' '.join(p['name']+'='+p['version'] for p in packages),
                note='Review the package transaction before confirming. Updates run locally and preserve app data, membership and installed capability packages. Package hooks may briefly restart the local agent; running app containers are not redeployed.')
if __name__=='__main__':
    payload=json.dumps(notice(sys.argv[1]))
    code="import sys,os,tempfile; from pathlib import Path; p=Path(os.environ['DATABASE_PATH']).parent/'isle-release.json'; f=tempfile.NamedTemporaryFile(mode='w',dir=p.parent,delete=False); f.write(sys.stdin.read()); f.flush(); os.fsync(f.fileno()); f.close(); os.replace(f.name,p)"
    subprocess.run(['docker','exec','-i','prf-isle-backend','python3','-c',code],input=payload,text=True,check=True,timeout=15)
