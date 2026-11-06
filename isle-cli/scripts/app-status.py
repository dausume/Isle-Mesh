#!/usr/bin/env python3
"""Read-only local deployment evidence, shared by CLI and native store.status."""
import json, subprocess, sys
from pathlib import Path
from datetime import datetime, timezone
from operations import NAME, latest
from health_probe import validate_path
APPS=Path('/etc/isle-mesh/apps')
PROVIDERS=Path('/etc/isle-mesh/polari')
REGISTRY=Path('/etc/isle-mesh/agent/registry.json')
def run(argv):
    return subprocess.run(argv,capture_output=True,text=True,timeout=8)
def status(name):
    if not NAME.fullmatch(name): raise ValueError('Invalid app name')
    out=dict(name=name,configured=(APPS/name/'docker-compose.yml').is_file(),deployed=False,
             runtimeStarted=False,routeReady=False,state='absent',observedAt=datetime.now(timezone.utc).isoformat(),operation=latest(name))
    provider=None
    if not out['configured'] and (PROVIDERS/name/'deployment.json').is_file():
        try:
            provider=json.loads((PROVIDERS/name/'deployment.json').read_text())
            if provider.get('name')!=name or provider.get('role') not in ('frontend','backend','both'):raise ValueError('Invalid provider identity/role')
            out.update(configured=(PROVIDERS/name/'docker-compose.yml').is_file(),kind='polari-instance',role=provider['role'],backendUrl=provider.get('backend_url'))
        except (OSError,ValueError) as exc:out['error']=str(exc);return out
    if not out['configured']: return out
    out['state']='staged'
    try:
        files=['-f',str(APPS/name/'docker-compose.yml'),'-f',str(APPS/name/'isle-overlay.yml')]
        if provider:files=['-f',str(PROVIDERS/name/'docker-compose.yml')]
        base=['docker','compose','-p',('prf-' if provider else 'isle-')+name,*files]
        expected=run(base+['config','--services'])
        containers=run(base+['ps','--all','--format','json'])
        if expected.returncode or containers.returncode: raise RuntimeError('Cannot inspect local runtime')
        raw=containers.stdout.strip()
        rows=json.loads(raw) if raw.startswith('[') else [json.loads(line) for line in raw.splitlines() if line]
        wanted=set(expected.stdout.split())
        ready={r.get('Service') for r in rows if r.get('State')=='running' and r.get('Health','') in ('','healthy')}
        out['runtimeStarted']=bool(wanted) and wanted<=ready
        if not out['runtimeStarted']: return out
        out['state']='runtime-started'
        registry=json.loads(REGISTRY.read_text())
        key=name+'-api' if provider and provider['role']=='backend' else name
        domain=registry.get('apps',{}).get(key,{}).get('domain','')
        import re
        if not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+isle',domain):
            out['error']='No valid registered Isle domain';return out
        health_path='/api/health' if provider and provider['role']=='backend' else '/'
        release_path=APPS/name/'release.json'
        if release_path.is_file():
            health_path=json.loads(release_path.read_text()).get('health',{}).get('path','/')
            validate_path(health_path)
        out['healthPath']=health_path
        result=run(['curl' ,'--http1.1','--fail','--silent','--show-error','--connect-timeout','2','--max-time','5','--output','/dev/null','https://'+domain+health_path])
        out['routeReady']=result.returncode==0
        if provider:
            from urllib.parse import urlsplit
            url=provider.get('backend_url','');u=urlsplit(url)
            if u.scheme!='https' or not u.hostname or u.username or u.password or u.path not in ('','/') or u.query or u.fragment:raise ValueError('Invalid provider backend origin')
            probe=run(['curl','--http1.1','--fail','--silent','--show-error','--max-time','5',url.rstrip('/')+'/api/health'])
            health=json.loads(probe.stdout) if probe.returncode==0 else {}
            out['backendReady']=health.get('ok') is True and health.get('phase')=='online'
            out['routeReady']=out['routeReady'] and out['backendReady']
        out['deployed']=out['routeReady']
        out['state']='ready' if out['deployed'] else 'runtime-started'
        if result.returncode: out['error']='Trusted local HTTPS readiness probe failed'
    except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as exc:
        out['error']=str(exc)
    return out
if __name__=='__main__':
    try:
        if len(sys.argv)!=2: raise ValueError('Usage: isle app status <name>')
        print(json.dumps(status(sys.argv[1])))
    except (ValueError,OSError) as exc: print(json.dumps(dict(error=str(exc))));sys.exit(1)
