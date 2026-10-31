#!/usr/bin/env python3
"""Audit/remove known generated Isle runtime state; never remove source checkouts."""
import argparse
import os
from pathlib import Path
import pwd
import shutil
import signal
import subprocess
import time


def homes():
    users = {'root'}
    if os.environ.get('PKEXEC_UID'):
        users.add(pwd.getpwuid(int(os.environ['PKEXEC_UID'])).pw_name)
    else:
        users.add(os.environ.get('SUDO_USER') or pwd.getpwuid(os.getuid()).pw_name)
    return [Path(pwd.getpwnam(user).pw_dir) for user in sorted(users)]


def generated_deployment(path):
    if path.is_symlink() or (path/'.git').exists():
        return False
    try:
        return 'prf-isle-' in (path/'docker-compose.yml').read_text()
    except (OSError, UnicodeError):
        return False


def paths(user_homes):
    found = [Path(p) for p in ['/usr/local/share/ca-certificates/isle-root.crt',
        '/etc/polari/install-mode', '/etc/polari/offline-source', '/etc/polari/posture.json']]
    for home in user_homes:
        deploy = home/'polari-isle'
        if generated_deployment(deploy): found.append(deploy)
        found.append(home/'.local/share/polari-shell/profiles/isle-store')
        desktop = home/'Desktop/isle-app-store.desktop'
        if desktop.is_symlink() and os.readlink(desktop) == '/usr/share/applications/isle-app-store.desktop':
            found.append(desktop)
    return [p for p in found if p.exists() or p.is_symlink()]


def shell_process(args):
    if not args: return False
    exe=Path(args[0]).name
    if exe == 'java':
        return any(a=='--config' and i+1<len(args) and args[i+1]=='/usr/share/isle-app-store/polari-shell.json' for i,a in enumerate(args))
    return exe=='jcef_helper' and any(a.startswith('--user-data-dir=') and a.endswith('/polari-shell/profiles/isle-store') for a in args)


def processes():
    found=[]
    for p in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            args=p.read_bytes().decode(errors='replace').rstrip('\0').split('\0')
            if shell_process(args): found.append(int(p.parent.name))
        except (OSError, ValueError): pass
    return found


def nss_certs(user_homes):
    found=[]
    for home in user_homes:
        db=home/'.pki/nssdb'
        if not (db/'cert9.db').exists(): continue
        result=subprocess.run(['certutil','-d','sql:'+str(db),'-L','-n','Isle Root CA'],capture_output=True)
        if result.returncode==0: found.append(db)
    return found


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cleanup',metavar='BACKUP_DIRECTORY')
    args=parser.parse_args(); user_homes=homes()
    if args.cleanup:
        if os.geteuid()!=0: parser.error('cleanup requires root')
        backup=Path(args.cleanup)/'runtime-residue'
        backup.mkdir(mode=0o700,parents=True,exist_ok=True)
        for pid in processes():
            try:os.kill(pid,signal.SIGTERM)
            except ProcessLookupError:pass
        for _ in range(30):
            if not processes(): break
            time.sleep(.1)
        if processes():
            print('Isle shell still running; close it and retry cleanup');return 1
        for db in nss_certs(user_homes):
            # Preserve public trust material, never a browser's unrelated DB.
            result=subprocess.run(['certutil','-d','sql:'+str(db),'-L','-n','Isle Root CA','-a'],capture_output=True,check=True)
            (backup/(str(db).strip('/').replace('/','_')+'.crt')).write_bytes(result.stdout)
            subprocess.run(['certutil','-d','sql:'+str(db),'-D','-n','Isle Root CA'],check=True)
        for path in paths(user_homes):
            dest=backup/str(path).strip('/').replace('/','_')
            if dest.exists() or dest.is_symlink():raise RuntimeError('Backup destination already exists: '+str(dest))
            shutil.move(str(path),str(dest))
            print('Archived',path)
        subprocess.run(['update-ca-certificates'],check=True,stdout=subprocess.DEVNULL)
        try:Path('/etc/polari').rmdir()
        except OSError:pass
    remaining=[str(p) for p in paths(user_homes)]+['NSS Isle Root CA: '+str(p) for p in nss_certs(user_homes)]+['Isle shell process: '+str(p) for p in processes()]
    for value in remaining:print('Remaining:',value)
    return 1 if remaining else 0

if __name__=='__main__':raise SystemExit(main())
