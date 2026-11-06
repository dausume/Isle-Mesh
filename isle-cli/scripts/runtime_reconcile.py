#!/usr/bin/env python3
"""Reconcile versioned images into existing Compose deployments, preserving config.
Targets come from the reviewed release; project/config/env come from Docker's
existing deployment labels. No rejoin, source build, network recreation or down.
"""
import json,os,re,subprocess,time
from pathlib import Path
from catalog_artifacts import fetch,CACHE,descriptor
from image_identity import verify_archive,admit_installed,digest
from operations import atomic
CANONICAL=Path('/etc/isle-mesh/device-name')
DESIRED=Path('/var/lib/isle-mesh/desired-release.json')
OVERLAYS=Path('/var/lib/isle-mesh/runtime-overlays')
def run(argv,**kwargs):return subprocess.run(argv,check=True,**kwargs)
def inspect(name):return json.loads(run(['docker','inspect',name],capture_output=True,text=True).stdout)[0]
def targets(release,mode,source=False):
    mapping=release.get('runtimes',{})
    if not isinstance(mapping,dict) or set(mapping)-{'core','remote'}:raise ValueError('Invalid runtime roles')
    rows=mapping.get(mode,[])
    if not isinstance(rows,list):raise ValueError('Invalid runtime target list')
    names=set()
    from urllib.parse import urlsplit
    for item in rows:
        if not isinstance(item,dict):raise ValueError('Invalid runtime target')
        name=item.get('container')
        if not isinstance(name,str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}',name) or name in names:raise ValueError('Invalid/duplicate runtime container')
        names.add(name)
        image=item.get('image',{})
        ref=image.get('ref')
        if not isinstance(ref,str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_./:@-]{0,255}',ref):raise ValueError('Invalid image reference')
        digest(image.get('configDigest'))
        if 'manifestDigest' in image:digest(image['manifestDigest'])
        if source:
            if not isinstance(image.get('archive',{}).get('source'),str):raise ValueError('Runtime image source required')
        else:descriptor(image.get('archive'))
        timeout=item.get('readinessTimeoutSeconds',180)
        if type(timeout)is not int or not 30<=timeout<=600:raise ValueError('Invalid runtime readiness timeout')
        if 'healthUrl' in item:
            u=urlsplit(item['healthUrl'])
            if u.scheme!='https' or not (u.hostname or '').endswith('.isle') or u.username or u.password or u.fragment:raise ValueError('Invalid runtime health URL')
    return rows

def reconcile(release,mode):
    rows=targets(release,mode)
    if not rows:return
    OVERLAYS.mkdir(parents=True,exist_ok=True,mode=0o700)
    for item in rows:
        name=item['container']
        if not isinstance(name,str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}',name):raise ValueError('Invalid runtime container')
        previous=inspect(name);labels=previous['Config'].get('Labels',{})
        project=labels['com.docker.compose.project'];service=labels['com.docker.compose.service']
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}',service):raise ValueError('Invalid Compose service')
        directory=Path(labels['com.docker.compose.project.working_dir'])
        files=[Path(p) for p in labels['com.docker.compose.project.config_files'].split(',')]
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,127}',project) or not directory.is_dir() or not files or any(not p.is_absolute() or not p.is_file() for p in files):raise ValueError('Existing Compose deployment metadata is incomplete')
        overlay=OVERLAYS/(project+'.json');files=[p for p in files if p!=overlay]
        image=item['image'];artifact=image['archive']
        path=fetch(artifact,CACHE/'runtime'/(artifact['sha256']+'.tar'))
        verified=verify_archive(path,image)
        current=subprocess.run(['docker','image','inspect','--format','{{.Id}}',image['ref']],capture_output=True,text=True)
        if current.returncode or current.stdout.strip() not in verified:run(['docker','load','--input',str(path)])
        actual=run(['docker','image','inspect','--format','{{.Id}}',image['ref']],capture_output=True,text=True).stdout.strip()
        admit_installed(actual,verified)
        config=json.loads(overlay.read_text()) if overlay.exists() else {'services':{}}
        config['services'][service]={'image':actual}
        if name in ('isle-vlan-agent','isle-remote-agent'):
            # Upgrade the standard main config with the admitted CLI package;
            # Compose merges mounts by target, preserving all registry/trust/data.
            main=Path(__file__).resolve().parents[2]/'isle-agent/isle-vlan-agent/nginx.conf'
            config['services'][service]['volumes']=[str(main)+':/etc/nginx/nginx.conf:ro']
        if name=='prf-isle-backend':
            from dns_publication import resolver
            config['services'][service].update({
                'environment':{'ISLE_CORE_API':'https://api.polari.isle','POLARI_INSTANCE_NAME':'polari','POLARI_MACHINE_NAME':CANONICAL.read_text().strip(),'SSL_CERT_FILE':'/etc/isle-trust/ca-certificates.crt',
                               'REQUESTS_CA_BUNDLE':'/etc/isle-trust/ca-certificates.crt'},
                'volumes':['/etc/ssl/certs:/etc/isle-trust:ro'], 'dns':[resolver()]})
        # Keep recovery information separate from immutable package files.
        before=OVERLAYS/(project+'-'+service+'-before.json')
        if not before.exists():atomic(before,{'image':previous['Image'],'files':[str(p) for p in files]},0o600)
        atomic(overlay,config,0o600)
        # Replay only variables referenced by the existing Compose files. Never
        # export a container PATH/HOME/LD_PRELOAD into privileged host commands.
        needed=set()
        for file in files:needed.update(re.findall(r'\$\{([A-Za-z_][A-Za-z0-9_]*)',file.read_text()))
        oldenv=dict(v.split('=',1) for v in previous['Config'].get('Env',[]) if '=' in v)
        env=dict(os.environ)
        for key in needed-{'PATH','HOME','LD_PRELOAD','LD_LIBRARY_PATH','PYTHONPATH'}:
            if key in oldenv:env[key]=oldenv[key]
        if labels.get('isle.role')=='remote-proxy':
            canonical=CANONICAL.read_text().strip()
            if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}',canonical):raise ValueError('Invalid canonical device identity')
            env['ISLE_HOSTNAME']=canonical
        args=['docker','compose','--project-directory',str(directory),'-p',project]
        for file in files+[overlay]:args+=['-f',str(file)]
        run(args+['up','-d','--no-deps','--no-build','--pull','never',service],env=env)
        deadline=time.monotonic()+120
        while True:
            state=inspect(name)
            admit_installed(state['Image'],verified)
            if state['Config'].get('Image')!=actual:raise ValueError('Running container does not request the admitted image')
            health=state['State'].get('Health',{}).get('Status')
            if state['State'].get('Running') and health in (None,'healthy'):break
            if time.monotonic()>deadline:raise RuntimeError('Runtime health did not converge: '+name)
            time.sleep(2)
    # Existing proxy must resolve upstreams again after containers change IPs.
    agent='isle-vlan-agent' if mode=='core' else 'isle-remote-agent'
    run(['docker','exec',agent,'nginx','-t']);run(['docker','exec',agent,'nginx','-s','reload'])
    for item in rows:
        if item.get('healthUrl'):
            from urllib.parse import urlsplit
            u=urlsplit(item['healthUrl'])
            if u.scheme!='https' or not (u.hostname or '').endswith('.isle') or u.username or u.password:raise ValueError('Invalid runtime health URL')
            wait_route(item['healthUrl'],item.get('readinessTimeoutSeconds',180))

def wait_route(url,timeout):
    deadline=time.monotonic()+timeout
    while True:
        try:
            run(['curl','--http1.1','--fail','--silent','--show-error','--connect-timeout','3','--max-time','8','--output','/dev/null',url],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            return
        except subprocess.CalledProcessError as error:
            if time.monotonic()>=deadline:raise RuntimeError('Trusted runtime readiness did not converge within '+str(timeout)+' seconds: '+url) from error
            time.sleep(2)


def main():
    if not DESIRED.exists():return
    if os.geteuid()!=0:raise PermissionError('Runtime reconciliation requires local administrator authorization')
    from update import validate
    release=validate(json.loads(DESIRED.read_text()))
    mode=Path('/etc/isle-mesh/agent/agent.mode').read_text().strip()
    if mode not in ('core','remote'):raise ValueError('No existing Isle membership')
    reconcile(release,mode)
if __name__=='__main__':
    import sys
    try:main()
    except (OSError,ValueError,KeyError,RuntimeError,subprocess.SubprocessError) as e:print('runtime reconcile: '+str(e),file=sys.stderr);sys.exit(1)
