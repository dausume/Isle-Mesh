#!/usr/bin/env python3
"""Publish the exact standalone remote package set after signed APT publication."""
import hashlib,json,re,subprocess,sys
from pathlib import Path
from datetime import datetime,timezone

START_SETTING_FILE='/etc/isle-mesh/release/start-setting.json'   # written by `isle release setting` (release_run.py)
def start_setting(path=START_SETTING_FILE):
    """The core's start setting record, or None (= the default, core-only). It is not part of the release id."""
    try:record=json.loads(Path(path).read_text())
    except (OSError,ValueError):return None
    return record if isinstance(record,dict) and record.get('start') in ('core-only','any-device') else None
def notice(repo):
    selected={};wanted={'isle-mesh-cli','isle-app-store','polari-shell-core'};bundled={'polari-complete','polari-complete-offline'}
    for block in (Path(repo)/'Packages').read_text().split('\n\n'):
        fields=dict(line.split(': ',1) for line in block.splitlines() if ': ' in line and not line.startswith(' '))
        name=fields.get('Package');version=fields.get('Version','')
        if name not in wanted|bundled or not re.fullmatch(r'[0-9A-Za-z.+:~_-]{1,100}',version):continue
        filename=Path(fields.get('Filename','')).name;sha=fields.get('SHA256','')
        if not re.fullmatch(r'[0-9a-f]{64}',sha) or not filename.endswith('.deb'):continue
        if name in selected and subprocess.run(['dpkg','--compare-versions',version,'le',selected[name]['version']]).returncode==0:continue
        selected[name]=dict(name=name,version=version,sha256=sha,file=filename)
    if not wanted<=set(selected):raise ValueError('Complete remote package set is not published')
    packages=[selected[k] for k in sorted(wanted)]
    extras={}
    bundles={k:selected[k] for k in sorted(bundled) if k in selected}
    if bundles:extras['bundles']=bundles
    runtime=Path(repo)/'runtime-release.json'
    if runtime.exists():extras['runtimes']=json.loads(runtime.read_text())['runtimes']
    identity={'packages':packages,**extras} if extras else packages
    digest=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    return dict(id=digest,publishedAt=datetime.now(timezone.utc).isoformat(),packages=packages,**extras,
                command='sudo isle update apply '+digest,
                note='Review the package transaction before confirming. Updates run locally and preserve app data, membership and installed capability packages. Package hooks may briefly restart the local agent. Reviewed runtime images are reconciled into existing deployments while preserving their data and configuration.')
# Runs inside prf-isle-backend: writes the notice atomically, carrying over the keys other verbs wrote: `run` (`isle release run`)
# and `intake` (the core's answers to packets its members offer, release_intake.py).
WRITE="""import sys,os,json,tempfile
from pathlib import Path
p=Path(os.environ['DATABASE_PATH']).parent/'isle-release.json'
d=json.loads(sys.stdin.read())
try:
    old=json.loads(p.read_text())
    for k in ('run','intake'):
        if k not in d and isinstance(old,dict) and k in old:d[k]=old[k]
except (OSError,ValueError):pass
f=tempfile.NamedTemporaryFile(mode='w',dir=p.parent,delete=False);f.write(json.dumps(d));f.flush();os.fsync(f.fileno());f.close();os.replace(f.name,p)
"""
if __name__=='__main__':
    document=notice(sys.argv[1]);setting=start_setting()
    if setting:document['startSetting']=setting
    from identity_provider import local_record,NOTICE_KEY   # the isle's Keycloak, set by `isle polari security provider`
    provider=local_record()
    if provider:document[NOTICE_KEY]=provider
    payload=json.dumps(document)
    subprocess.run(['docker','exec','-i','prf-isle-backend','python3','-c',WRITE],input=payload,text=True,check=True,timeout=15)
