#!/usr/bin/env python3
"""Merge only Isle public-root trust into Firefox policy and existing profiles.
Policy reference: https://mozilla.github.io/policy-templates/#certificates--install
"""
import json,os,re,subprocess,sys
from pathlib import Path
from operations import atomic
CA=Path('/etc/isle-mesh/ca/isle-root.crt')
POLICY=Path('/etc/firefox/policies')
def profiles(home):
    roots=[home/'.mozilla/firefox',home/'snap/firefox/common/.mozilla/firefox']
    return [p.parent for base in roots for p in base.glob('*/cert9.db')]
def policy(directory=POLICY,ca=CA,remove=False):
    path=directory/'policies.json';cert=directory/'isle-root.crt'
    if remove and not path.exists():cert.unlink(missing_ok=True);return
    data=json.loads(path.read_text()) if path.exists() else {}
    policies=data.setdefault('policies',{});certificates=policies.setdefault('Certificates',{})
    current=certificates.get('Install',[])
    if not isinstance(current,list):raise ValueError('Existing Firefox Certificates.Install policy is not a list; preserving it')
    current=[p for p in current if p!=str(cert)]
    if not remove:
        directory.mkdir(parents=True,exist_ok=True)
        cert.write_bytes(ca.read_bytes());cert.chmod(0o644);current.append(str(cert))
    else:cert.unlink(missing_ok=True)
    if current:certificates['Install']=current
    else:certificates.pop('Install',None)
    if not certificates:policies.pop('Certificates',None)
    if not policies:data.pop('policies',None)
    if data:atomic(path,data)
    else:path.unlink(missing_ok=True)
def import_profiles(home):
    for directory in profiles(home):
        args=['certutil','-d','sql:'+str(directory)]
        subprocess.run(args+['-D','-n','Isle Root CA'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        subprocess.run(args+['-A','-n','Isle Root CA','-t','C,,','-i',str(CA)],check=True)
        exceptions=directory/'cert_override.txt'
        if exceptions.exists():
            old=exceptions.read_text();new=''.join(line for line in old.splitlines(keepends=True) if not re.match(r'[^\s:]+\.isle:\d+\s',line))
            if old!=new:
                backup=directory/'cert_override.before-isle-root.txt'
                if not backup.exists():backup.write_text(old);backup.chmod(0o600)
                exceptions.write_text(new)
        print('Firefox root trust installed:',directory,'(restart an already-open browser)')
if __name__=='__main__':
    try:
        if sys.argv[1]=='policy':
            if os.geteuid()!=0:raise PermissionError('System Firefox policy requires administrator authorization')
            policy()
        elif sys.argv[1]=='profiles':import_profiles(Path.home())
        else:raise ValueError('Unsupported Firefox trust action')
    except (OSError,ValueError,subprocess.SubprocessError) as exc:print('Firefox trust: '+str(exc),file=sys.stderr);sys.exit(1)
