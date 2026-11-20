#!/usr/bin/env python3
"""Enroll a router public key verified out of band (for example VM console)."""
import ipaddress,os,subprocess,sys,tempfile
from pathlib import Path
KNOWN=Path('/etc/isle-mesh/router/ssh/known_hosts')
def main(args):
    if len(args)!=3:raise ValueError('Usage: sudo isle router trust-key <router-ip> <public-key-file> <SHA256:fingerprint>')
    if os.geteuid()!=0:raise PermissionError('Router trust enrollment requires administrator authorization')
    host=str(ipaddress.ip_address(args[0]));key=Path(args[1]).read_text().strip().split()
    if len(key)<2 or key[0] not in ('ssh-ed25519','ssh-rsa','ecdsa-sha2-nistp256'):raise ValueError('Unsupported router public key')
    actual=subprocess.run(['ssh-keygen','-l','-E','sha256','-f',args[1]],check=True,capture_output=True,text=True).stdout.split()[1]
    if actual!=args[2]:raise ValueError('Public key fingerprint differs from independently verified value')
    KNOWN.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    lines=KNOWN.read_text().splitlines() if KNOWN.exists() else []
    lines=[line for line in lines if not line.startswith(host+' ')]
    lines.append(host+' '+key[0]+' '+key[1])
    fd,tmp=tempfile.mkstemp(dir=KNOWN.parent)
    with os.fdopen(fd,'w') as f:os.fchmod(f.fileno(),0o600);f.write('\n'.join(lines)+'\n');f.flush();os.fsync(f.fileno())
    os.replace(tmp,KNOWN);print('Pinned router '+host+' '+actual)
if __name__=='__main__':
    try:main(sys.argv[1:])
    except (OSError,ValueError,subprocess.SubprocessError) as e:print('router trust: '+str(e),file=sys.stderr);sys.exit(1)
