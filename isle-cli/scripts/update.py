#!/usr/bin/env python3
"""Exact local release update; same review/apply contract for CLI/native clients."""
import hashlib,json,os,re,subprocess,sys
from pathlib import Path
from operations import atomic, stage
OP_ROOT=Path('/var/lib/isle-mesh/operations')
API='https://api.polari.isle/api/islemesh/updates'
PACKAGES={'isle-mesh-cli','isle-app-store','polari-shell-core'}
def command(argv):
    p=subprocess.run(argv,capture_output=True,text=True,timeout=120)
    if p.returncode: raise RuntimeError(p.stderr.strip() or 'Command failed: '+argv[0])
    return p.stdout

def file_digest(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): digest.update(block)
    return digest.hexdigest()

def validate(release):
    packages=release['packages']
    if len(packages)!=3 or {p['name'] for p in packages}!=PACKAGES: raise ValueError('Incomplete/unsupported release package set')
    bundles=release.get('bundles',{})
    if not isinstance(bundles,dict) or set(bundles)-{'polari-complete','polari-complete-offline'}:raise ValueError('Unsupported package bundle')
    for name,p in bundles.items():
        if p.get('name')!=name:raise ValueError('Bundle identity mismatch')
    for p in packages+list(bundles.values()):
        if not re.fullmatch(r'[0-9A-Za-z.+:~_-]{1,100}',p['version']): raise ValueError('Invalid version')
        if not re.fullmatch(r'[a-zA-Z0-9_.+~-]+\.deb',p['file']): raise ValueError('Invalid package filename')
        if not re.fullmatch('[a-f0-9]{64}',p['sha256']): raise ValueError('Missing SHA256')
    from runtime_reconcile import targets
    for mode in ('core','remote'):targets(release,mode)
    extras={k:release[k] for k in ('bundles','runtimes') if k in release}
    identity={'packages':packages,**extras} if extras else packages
    digest=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    if release['id']!=digest: raise ValueError('Release ID does not match package manifest')
    return release

def fetch():
    data=json.loads(command(['curl','--http1.1','--fail','--silent','--show-error','--max-time','15',API]))
    if not data.get('ok'): raise ValueError('Release service unavailable')
    return validate(data['release'])

def installed(name):
    p=subprocess.run(['dpkg-query','-W','-f','${Status}\t${Version}',name],capture_output=True,text=True)
    status,_,version=p.stdout.partition('\t')
    return version.strip() if p.returncode==0 and status=='install ok installed' else None

def selected_packages(release):
    providers=[p for p in ('polari-complete','polari-complete-offline') if installed(p)]
    if len(providers)>1:raise ValueError('Multiple combined package providers; reconcile package state first')
    if providers:
        if providers[0] not in release.get('bundles',{}):raise ValueError('This release has no update for the installed combined package')
        return [release['bundles'][providers[0]]]
    return release['packages']

def main(args):
    action=args[0] if args else 'plan'
    if action=='plan' and len(args)<=1:
        release=fetch()
        print(json.dumps(dict(release=release,selectedPackages=selected_packages(release),installed={p:installed(p) for p in sorted(PACKAGES|{'polari-complete','polari-complete-offline'})},command='sudo isle update apply '+release['id'])))
        return 0
    if action!='apply' or len(args)!=2 or not re.fullmatch('[a-f0-9]{64}',args[1]): raise ValueError('Usage: isle update plan | sudo isle update apply <release-id>')
    ident=os.environ.get('ISLE_OPERATION_ID','')
    directory=OP_ROOT/ident
    if os.geteuid()!=0 or not re.fullmatch('[a-f0-9]{32}',ident): raise PermissionError('Use shared isle update apply operation')
    stage(ident,'validating-release')
    saved=directory/'release.json'
    release=validate(json.loads(saved.read_text())) if saved.exists() else fetch()
    if release['id']!=args[1]: raise ValueError('Release changed; review isle update plan again')
    if not saved.exists(): atomic(saved,release,0o600)
    stage(ident,'verifying-artifacts')
    packages=selected_packages(release)
    paths=[]
    for p in packages:
        current=installed(p['name'])
        if current and subprocess.run(['dpkg','--compare-versions',current,'gt',p['version']]).returncode==0:
            raise ValueError('Refusing implicit downgrade of '+p['name'])
        target=directory/p['file']
        if not target.exists() or file_digest(target)!=p['sha256']:
            temp=target.with_suffix('.part')
            command(['curl','--http1.1','--fail','--silent','--show-error','--max-time','90','--output',str(temp),'https://apt.isle/'+p['file']])
            if file_digest(temp)!=p['sha256']: raise ValueError('Package checksum mismatch: '+p['name'])
            temp.replace(target)
        for field,key in [('Package','name'),('Version','version')]:
            if command(['dpkg-deb','-f',str(target),field]).strip()!=p[key]: raise ValueError('Package metadata mismatch')
        paths.append(str(target))
    # Persisted operation and immutable manifest survive dpkg replacing this code.
    stage(ident,'installing-packages')
    result=subprocess.run(['apt-get','--no-remove','install','-y',*paths])
    if result.returncode: return result.returncode
    if any(installed(p['name'])!=p['version'] for p in packages): raise RuntimeError('Installed package verification failed')
    from runtime_reconcile import DESIRED
    atomic(DESIRED,release,0o600)
    stage(ident,'reconciling')
    return subprocess.run(['bash',str(Path(__file__).with_name('reconcile.sh')),'apply','device']).returncode
if __name__=='__main__':
    try:sys.exit(main(sys.argv[1:]))
    except (OSError,ValueError,KeyError,TypeError,RuntimeError,subprocess.TimeoutExpired) as e:
        print('update: '+str(e),file=sys.stderr);sys.exit(1)
