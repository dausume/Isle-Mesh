#!/usr/bin/env python3
"""Publish a generic catalog release to the existing trusted Isle artifact supply."""
import copy,json,os,re,shutil,subprocess,sys
from pathlib import Path
from catalog_artifacts import sha,NAME
from image_identity import verify_archive
from health_probe import validate_path
REPO=Path('/etc/isle-mesh/apt/repo')

def publish_asset(d,source_dir,destination,repo):
    original=Path(d['source']);original=original if original.is_absolute() else source_dir/original
    h=sha(original);size=original.stat().st_size
    if 'sha256' in d and d['sha256']!=h:raise ValueError('Source checksum mismatch')
    target=destination/(h+original.suffix)
    if target.exists() and sha(target)!=h:raise ValueError('Existing immutable artifact differs')
    if not target.exists():
        tmp=target.with_name(target.name+'.part');shutil.copyfile(original,tmp)
        if sha(tmp)!=h:tmp.unlink();raise ValueError('Source changed during publication')
        tmp.chmod(0o644);tmp.replace(target)
    d.pop('source');d.update(url='https://apt.isle/'+str(target.relative_to(repo)),sha256=h,size=size)

def publish(source, repo=REPO):
    source=Path(source).resolve();m=json.loads(source.read_text());m=copy.deepcopy(m)
    if m.get('schemaVersion')!=1 or not NAME.fullmatch(m.get('name','')) or not re.fullmatch(r'[a-zA-Z0-9.+_-]{1,80}',m.get('version','')):raise ValueError('Invalid release identity')
    validate_path(m.get('health',{'path':'/'}).get('path'))
    destination=repo/'catalog'/m['name']/m['version'];destination.mkdir(parents=True,exist_ok=True)
    def asset(d):return publish_asset(d,source.parent,destination,repo)
    for image in m['images']:
        path=Path(image['archive']['source']);path=path if path.is_absolute() else source.parent/path
        verify_archive(path,image)
        asset(image['archive'])
    asset(m['compose'])
    for item in m.get('files',[]):asset(item)
    raw=(json.dumps(m,sort_keys=True,indent=2)+'\n').encode()
    import hashlib
    h=hashlib.sha256(raw).hexdigest();path=destination/(h+'.json')
    if path.exists() and path.read_bytes()!=raw:raise ValueError('Immutable release differs')
    if not path.exists():path.write_bytes(raw);path.chmod(0o644)
    return m['name'],dict(url='https://apt.isle/'+str(path.relative_to(repo)),sha256=h,size=len(raw))

def main(path):
    if os.geteuid()!=0:raise PermissionError('Use sudo isle store publish <release.json> on the core')
    if Path('/etc/isle-mesh/agent/agent.mode').read_text().strip()!='core':raise ValueError('Publish on the core artifact host')
    name,release=publish(path)
    code='''import json,os,sys,tempfile
from pathlib import Path
p=Path(os.environ['DATABASE_PATH']).parent/'isle-catalog-releases.json'
d=json.loads(p.read_text()) if p.exists() else {}
v=json.load(sys.stdin);d[v['name']]=v['release']
f=tempfile.NamedTemporaryFile(mode='w',dir=p.parent,delete=False);json.dump(d,f);f.flush();os.fsync(f.fileno());f.close();os.replace(f.name,p)
'''
    subprocess.run(['docker','exec','-i','prf-isle-backend','python3','-c',code],input=json.dumps(dict(name=name,release=release)),text=True,check=True)
    print(json.dumps(dict(name=name,release=release)))
if __name__=='__main__':
    try:
        if len(sys.argv)!=2:raise ValueError('Usage: isle store publish <release.json>')
        main(sys.argv[1])
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as e:print('catalog publish: '+str(e),file=sys.stderr);sys.exit(1)
