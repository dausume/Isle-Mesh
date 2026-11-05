#!/usr/bin/env python3
"""Durable local operations shared by terminal and native callers.
No arbitrary command API. Retry reuses the saved request and catalog response.
"""
import fcntl, json, os, re, signal, subprocess, sys, tempfile, uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/var/lib/isle-mesh/operations')
SCRIPTS = Path(__file__).resolve().parent
NAME = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')

def now(): return datetime.now(timezone.utc).isoformat()
def atomic(path, value, mode=0o644):
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            os.fchmod(out.fileno(), mode); json.dump(value, out); out.write('\n'); out.flush(); os.fsync(out.fileno())
        os.replace(tmp, path)
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def path_for(ident):
    if not re.fullmatch(r'[a-f0-9]{32}', ident): raise ValueError('Invalid operation ID')
    return ROOT / ident

def read(ident):
    p=path_for(ident)
    data=json.loads((p/'status.json').read_text())
    # Process death/reboot cannot leave a stale green or forever-running status.
    if data['state']=='running':
        with (p/'lock').open('r') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
                data.update(state='interrupted', retryable=True)
            except BlockingIOError: pass
    return data

def latest(name):
    rows=[]
    if ROOT.exists():
        for p in ROOT.iterdir():
            if not p.is_dir(): continue
            try:
                d=read(p.name)
                if d.get('name')==name: rows.append(d)
            except (OSError, ValueError, KeyError): pass
    return max(rows,key=lambda d:d['createdAt']) if rows else None

def request(args):
    if len(args)==3 and args[:2]==['update','apply'] and re.fullmatch('[a-f0-9]{64}',args[2]): return list(args)
    if args==['reconcile','apply','device']: return list(args)
    if len(args)<3 or args[:2] not in (['store','install'],['store','uninstall']):
        raise ValueError('Supported operations: store install/uninstall <name> [--yes] [--purge]')
    if not NAME.fullmatch(args[2]): raise ValueError('Invalid app name')
    allowed={'--yes'} | ({'--purge'} if args[1]=='uninstall' else set())
    if any(x not in allowed for x in args[3:]): raise ValueError('Unsupported operation parameter')
    return list(args)

def execute(args=None, ident=None):
    if os.geteuid()!=0: raise PermissionError('Run this operation with sudo or the native PolicyKit action')
    ROOT.mkdir(parents=True,exist_ok=True,mode=0o755)
    os.chmod(ROOT,0o755)
    if ident:
        p=path_for(ident); d=read(ident); args=request(d['request'])
        if d['state']=='succeeded':
            print(json.dumps(d)); return 0
    else:
        args=request(args); ident=uuid.uuid4().hex; p=path_for(ident);p.mkdir(mode=0o755);os.chmod(p,0o755)
        d=dict(id=ident,name=args[2] if args[0]=='store' else 'device-update',request=args,state='pending',createdAt=now(),attempt=0,history=[])
    # Serialize all operations for one app, including retries/new requests.
    app_lock=ROOT/('app-'+(args[2] if args[0]=='store' else 'device-update')+'.lock')
    with app_lock.open('a+') as al, (p/'lock').open('a+') as lock:
        os.chmod(p/'lock',0o644)
        try:
            fcntl.flock(al,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('An operation for this app is already running')
        if ident and (p/'status.json').exists():
            d=json.loads((p/'status.json').read_text())
            if d['state']=='succeeded': print(json.dumps(d));return 0
        d.update(state='running',retryable=False,attempt=d['attempt']+1,updatedAt=now())
        d['history'].append(dict(attempt=d['attempt'],state='running',at=now()))
        atomic(p/'status.json',d)
        print('Operation: '+ident,flush=True)
        env=dict(os.environ,ISLE_OPERATION_ID=ident,ISLE_OPERATION_DIR=str(p))
        code=1
        try:
            code=subprocess.run(['bash',str(SCRIPTS/(args[0]+'.sh')),*args[1:]],env=env,pass_fds=(al.fileno(),lock.fileno())).returncode
        except (KeyboardInterrupt,OSError) as exc:
            print(str(exc),file=sys.stderr);code=130 if isinstance(exc,KeyboardInterrupt) else 1
        d=json.loads((p/'status.json').read_text())
        d.update(state='succeeded' if code==0 else 'cancelled' if code==130 else 'failed',exitCode=code,retryable=code!=0,updatedAt=now())
        d['history'].append(dict(attempt=d['attempt'],state=d['state'],at=now(),exitCode=code))
        atomic(p/'status.json',d)
        print('Operation '+ident+': '+d['state'],flush=True)
        return code if code>=0 else 128-code

def stage(ident, label):
    if os.geteuid()!=0: raise PermissionError('Operation stages require root')
    if not re.fullmatch(r'[a-z0-9-]{1,80}',label): raise ValueError('Invalid stage')
    p=path_for(ident); d=json.loads((p/'status.json').read_text())
    if d['state']!='running': raise ValueError('Operation is not running')
    d.update(stage=label,updatedAt=now())
    d['history'].append(dict(attempt=d['attempt'],stage=label,at=now()))
    atomic(p/'status.json',d)
    return 0

def main(args):
    if not args: raise ValueError('Usage: isle operation status <id> | retry <id>')
    if args[0]=='run': return execute(args[1:])
    if args[0]=='stage' and len(args)==3: return stage(args[1],args[2])
    if len(args)!=2: raise ValueError('Usage: isle operation status <id> | retry <id>')
    if args[0]=='status': print(json.dumps(read(args[1])));return 0
    if args[0]=='retry': return execute(ident=args[1])
    raise ValueError('Unknown operation action')

if __name__=='__main__':
    try: sys.exit(main(sys.argv[1:]))
    except (ValueError,OSError,RuntimeError,KeyError) as exc:
        print('operation: '+str(exc),file=sys.stderr);sys.exit(1)
