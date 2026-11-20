#!/usr/bin/env python3
"""Announce local engine declarations and sync core registry to local providers."""
import json, re, subprocess, sys
from pathlib import Path
from urllib.parse import urlsplit
from member_preflight import identity
CORE='https://api.polari.isle'
APPS=Path('/etc/isle-mesh/apps')
PROVIDERS=Path('/etc/isle-mesh/polari')
def origin(url):
    u=urlsplit(url)
    if u.scheme!='https' or not (u.hostname or '').endswith('.isle') or u.username or u.password or u.path not in ('','/') or u.query or u.fragment:
        raise ValueError('Engine/provider requires a trusted Isle HTTPS origin')
    return url.rstrip('/')
def request(url,payload=None,post=False):
    # Registry publication can durably write many rows on constrained hosts.
    # A timed-out POST may still finish on the server: never blindly replay it
    # here. Surface failure to the shared operation journal for explicit retry.
    limit='60' if post or payload is not None else '15'
    argv=['curl','--http1.1','--fail','--silent','--show-error','--connect-timeout','10','--max-time',limit,url]
    if post:argv+=['--request','POST']
    if payload is not None:argv+=['-H','Content-Type: application/json','--data-binary',json.dumps(payload)]
    p=subprocess.run(argv,capture_output=True,text=True,check=True)
    d=json.loads(p.stdout)
    if d.get('ok') is not True:raise ValueError('Engine registry refused operation at '+url)
    return d
def payload(row,device=None,app=None):
    data={'device':device or row.get('device'),'app':app or row.get('app'),'provides':row.get('provides'),'url':origin(row.get('url',''))}
    if not all(isinstance(data[k],str) and re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}',data[k]) for k in ('device','app','provides')):
        raise ValueError('Engine declaration lacks a valid device/app/capability identity')
    return data
def sync():
    name=identity()
    for path in sorted(APPS.glob('*/engine.json')):
        d=payload(json.loads(path.read_text()),name,path.parent.name)
        request(CORE+'/api/islemesh/ingest/engine',d)
    # Recover declarations for already-installed apps from the shared catalog.
    catalog=request(CORE+'/api/islemesh/catalog')
    for entry in catalog.get('entries',[]):
        kind=entry.get('provides_engine')
        if not isinstance(kind,str) or not kind or entry.get('is_mock'):continue
        for instance in entry.get('instances',[]):
            if instance.get('is_mock') or not instance.get('device') or not instance.get('domain'):continue
            d=payload({'device':instance['device'],'app':instance.get('app') or entry['name'],
                       'provides':kind,'url':'https://'+instance['domain']})
            request(CORE+'/api/islemesh/ingest/engine',d)
    local=[]
    for path in sorted(PROVIDERS.glob('*/deployment.json')):
        d=json.loads(path.read_text())
        if d.get('role') in ('both','backend'):local.append(origin(d['backend_url']))
    if not local:return
    # The backend owns registry provenance, binding and retirement. Use the same
    # generic refresh door as `pol engines sync`, never mirror per-row ingests.
    for endpoint in sorted(set(local)):
        result=request(endpoint+'/api/islemesh/engines/sync',post=True)
        print(json.dumps({'provider':endpoint,'registryRefresh':result}))
if __name__=='__main__':
    try:sync()
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:print('Engine reconciliation: '+str(exc),file=sys.stderr);sys.exit(1)
