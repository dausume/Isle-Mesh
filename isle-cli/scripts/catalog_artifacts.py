#!/usr/bin/env python3
"""Versioned catalog artifact resolution behind the standard store operation."""
import hashlib,json,os,re,shutil,subprocess,sys,tempfile
from pathlib import Path,PurePosixPath
from urllib.parse import urlsplit
from operations import atomic,stage,NAME
from image_identity import verify_archive,admit_installed
CACHE=Path('/var/lib/isle-mesh/artifacts')
SCRIPTS=Path(__file__).resolve().parent
MAX_SIZE=20*1024**3

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def descriptor(item):
    if not isinstance(item,dict):raise ValueError('Missing release artifact descriptor')
    u=urlsplit(item['url'])
    if u.scheme!='https' or not u.hostname or not u.hostname.endswith('.isle') or u.username or u.password or u.fragment:
        raise ValueError('Artifacts require authenticated HTTPS on the Isle')
    if not re.fullmatch('[a-f0-9]{64}',item['sha256']):raise ValueError('Artifact SHA256 required')
    if type(item['size']) is not int or not 0<item['size']<=MAX_SIZE:raise ValueError('Invalid artifact size')
    return item

def fetch(item,target):
    descriptor(item)
    if target.is_file() and target.stat().st_size==item['size'] and sha(target)==item['sha256']:return target
    target.parent.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(target.parent).free<item['size']+512*1024**2:raise ValueError('Insufficient disk for artifact and reserve')
    temporary=target.with_name(target.name+'.part')
    try:
        subprocess.run(['curl','--http1.1','--fail','--silent','--show-error','--connect-timeout','10','--max-time','600','--max-filesize',str(item['size']),'--output',str(temporary),item['url']],check=True)
        if temporary.stat().st_size!=item['size'] or sha(temporary)!=item['sha256']:raise ValueError('Artifact size/checksum mismatch')
        temporary.replace(target)
    finally:
        if temporary.exists():temporary.unlink()
    return target

def manifest_document(release):
    descriptor(release)
    if release['size']>2*1024*1024:raise ValueError('Release manifest too large')
    directory=CACHE/release['sha256'];directory.mkdir(parents=True,exist_ok=True,mode=0o755)
    path=fetch(release,directory/'manifest.json');m=json.loads(path.read_text())
    if m.get('schemaVersion')!=1 or not NAME.fullmatch(m.get('name','')) or not re.fullmatch(r'[a-zA-Z0-9.+_-]{1,80}',m.get('version','')):raise ValueError('Unsupported release manifest')
    descriptor(m['compose'])
    if not isinstance(m.get('images'),list) or not m['images']:raise ValueError('Release has no verified images')
    return m,directory

def prepare(release, expected_name):
    m,directory=manifest_document(release)
    if m['name']!=expected_name:raise ValueError('Catalog/release app mismatch')
    compose=fetch(m['compose'],directory/'compose.yml')
    for file in m.get('files',[]):
        path=PurePosixPath(file['path'])
        if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] in ('manifest.json','compose.yml','resolved.json','admitted.json','images'):
            raise ValueError('Invalid release file path')
        fetch(file,directory.joinpath(*path.parts))
    # Compose is materialized from the verified directory, never the caller cwd.
    result=subprocess.run(['docker','compose','-f',str(compose),'config','--format','json'],capture_output=True,text=True,check=True)
    config=json.loads(result.stdout)
    services=config.get('services',{})
    if not services or any('build' in v for v in services.values()):raise ValueError('Published compose must use release images, not source builds')
    declared={i['ref']:i for i in m['images']}
    if len(declared)!=len(m['images']) or any(v.get('image') not in declared for v in services.values()):raise ValueError('Every service image must have an artifact identity')
    admitted={}
    for ref,image in declared.items():
        if not isinstance(ref,str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_./:@-]{0,255}',ref):raise ValueError('Invalid image reference')
        descriptor(image['archive'])
        archive=fetch(image['archive'],directory/'images'/(image['archive']['sha256']+'.tar'))
        verified=verify_archive(archive,image)
        subprocess.run(['docker','load','--input',str(archive)],check=True,stdout=sys.stderr)
        actual=subprocess.run(['docker','image','inspect','--format','{{.Id}}',ref],capture_output=True,text=True,check=True).stdout.strip()
        admitted[ref]=admit_installed(actual,verified)
    for service in services.values():service['image']=admitted[service['image']]
    atomic(directory/'resolved.json',config,0o600)
    atomic(directory/'admitted.json',dict(manifestSha256=release['sha256'],images=admitted))
    return m,directory/'resolved.json'

def install(catalog,instance):
    if os.geteuid()!=0:raise PermissionError('Use sudo isle store install')
    if not NAME.fullmatch(instance):raise ValueError('Invalid instance name')
    entry=json.loads(Path(catalog).read_text())['entry']
    ident=os.environ['ISLE_OPERATION_ID'];stage(ident,'resolving-artifacts')
    m,compose=prepare(entry['install_plan']['release'],entry['name'])
    d=m['deploy']; service=d['service'];port=d['port']
    if not isinstance(service,str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}',service) or type(port)is not int or not 1<=port<=65535:raise ValueError('Invalid release service/port')
    args=['bash',str(SCRIPTS/'app.sh'),'deploy',instance,'--compose',str(compose),'--service',service,'--port',str(port),'--domain',instance+'.isle']
    if d.get('engine'):
        if not NAME.fullmatch(d['engine']):raise ValueError('Invalid engine kind')
        args+=['--engine',d['engine']]
    health=m.get('health',{'path':'/'})
    path=health.get('path')
    if not isinstance(path,str) or not path.startswith('/') or len(path)>512 or any(ord(c)<33 for c in path):raise ValueError('Invalid release health path')
    stage(ident,'deploying-verified-release')
    result=subprocess.run(args).returncode
    installed=Path('/etc/isle-mesh/apps')/instance
    if (installed/'docker-compose.yml').is_file():
        atomic(installed/'release.json',dict(name=m['name'],version=m['version'],manifestSha256=entry['install_plan']['release']['sha256'],health=health))
    return result

def present(ref):
    # Only root-published artifact admissions extend offline supply, by content ID.
    if not re.fullmatch('sha256:[a-f0-9]{64}',ref):return False
    for record in CACHE.glob('*/admitted.json'):
        stat=record.stat()
        if stat.st_uid!=0 or stat.st_mode & 0o022:continue
        try:
            if ref in json.loads(record.read_text())['images'].values():
                p=subprocess.run(['docker','image','inspect','--format','{{.Id}}',ref],capture_output=True,text=True)
                if p.returncode==0 and p.stdout.strip()==ref:return True
        except (ValueError,KeyError):continue
    return False

if __name__=='__main__':
    try:
        if sys.argv[1]=='install' and len(sys.argv)==4:sys.exit(install(*sys.argv[2:]))
        if sys.argv[1]=='present' and len(sys.argv)==3:sys.exit(0 if present(sys.argv[2]) else 1)
        raise ValueError('Internal catalog artifact action invalid')
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as exc:
        print('catalog artifact: '+str(exc),file=sys.stderr);sys.exit(1)
