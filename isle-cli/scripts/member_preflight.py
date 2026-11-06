#!/usr/bin/env python3
"""Validate saved membership before changes; never invent an identity or rejoin."""
import json, os, re, subprocess, sys
from pathlib import Path

STATE=Path('/etc/isle-mesh')
def identity(state=STATE, migrate=False):
    canonical=state/'device-name'
    candidates=[]
    for path in [canonical,state/'agent/remote/hostname']:
        if path.exists():
            value=path.read_text().strip()
            if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',value):
                raise ValueError('Invalid saved device identity: '+str(path))
            candidates.append(value)
    if not candidates:
        raise ValueError('Canonical device identity missing: restore /etc/isle-mesh/device-name from this member registration; refusing to guess or rejoin')
    if len(set(candidates))!=1:
        raise ValueError('Saved registration and canonical device identity disagree; resolve membership identity before updating')
    if migrate and not canonical.exists():
        # Exclusive creation: never overwrite a concurrent declaration.
        with canonical.open('x') as out: out.write(candidates[0]+'\n')
        canonical.chmod(0o644)
    return candidates[0]

def check(state=STATE, hosting=False, migrate=False):
    mode=(state/'agent/agent.mode').read_text().strip()
    if mode not in ('core','remote'):raise ValueError('No configured core/member mode; use isle join for a new member')
    name=identity(state,migrate)
    ca=state/'ca/isle-root.crt'
    if not ca.is_file():raise ValueError('Missing Isle trust: use sudo isle trust fetch --fingerprint <independently-verified-SHA256> before updating')
    try:subprocess.run(['openssl','x509','-in',str(ca),'-noout','-checkend','0'],check=True,capture_output=True)
    except subprocess.SubprocessError as exc:raise ValueError('Invalid or expired Isle root; run sudo isle trust update or use independently verified fingerprint recovery') from exc
    if hosting and mode=='remote':
        credential=state/'ca/member-enrollment.json'
        if not credential.is_file():
            raise ValueError('Hosting credential missing: obtain this member enrollment file privately from the core, then run sudo isle certs pair <credential-file>; no deployment changed')
        if credential.stat().st_mode & 0o077:raise ValueError('Hosting credential permissions must be root-only (0600)')
        if os.geteuid()!=0:return {'mode':mode,'device':name,'hostingChecked':False,'credentialCheck':'administrator validation required at apply'}
        c=json.loads(credential.read_text())
        if c.get('device')!=name or c.get('endpoint')!='https://certs.isle/enroll' or not re.fullmatch(r'[A-Za-z0-9_-]{64}',c.get('token','')):
            raise ValueError('Hosting credential does not match this member; use supported isle certs pair after correcting registration')
    return {'mode':mode,'device':name,'hostingChecked':hosting}

def repair():
    if os.geteuid()!=0:raise PermissionError('Membership repair requires administrator authorization')
    identity(migrate=True)
    from host_network import apply
    apply()
    script=Path(__file__).with_name('trust.sh')
    subprocess.run(['bash',str(script),'update'],check=True)
    subprocess.run(['bash',str(script),'install','--yes'],check=True)
    return check(hosting=any((STATE/'polari').glob('*/deployment.json')))

if __name__=='__main__':
    try:
        if any(x not in ('--hosting','--migrate','--repair') for x in sys.argv[1:]):raise ValueError('Unsupported preflight option')
        print(json.dumps(repair() if '--repair' in sys.argv else check(hosting='--hosting' in sys.argv,migrate='--migrate' in sys.argv)))
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        print('Membership preflight: '+str(exc),file=sys.stderr);sys.exit(1)
