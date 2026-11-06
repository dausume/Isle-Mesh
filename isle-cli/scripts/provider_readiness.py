#!/usr/bin/env python3
"""Bounded declared health readiness used by deploy, verify and reconcile."""
import json, subprocess, time, sys
from urllib.parse import urlsplit
def wait(url,timeout=300,http_only=False):
    u=urlsplit(url)
    if u.scheme!='https' or not u.hostname or u.username or u.password or u.fragment:
        raise ValueError('Readiness requires an HTTPS URL without credentials/fragments')
    deadline=time.monotonic()+timeout
    while True:
        p=subprocess.run(['curl','--http1.1','--fail','--silent','--show-error','--connect-timeout','3','--max-time','8',url],capture_output=True,text=True)
        if p.returncode==0:
            if http_only:return
            try:
                d=json.loads(p.stdout)
                if d.get('ok') is True and d.get('phase')=='online':return
            except (ValueError,AttributeError):pass
        if time.monotonic()>=deadline:raise RuntimeError('Declared health did not become ready within '+str(timeout)+' seconds: '+url+'; configuration retained, retry the same operation')
        time.sleep(2)
if __name__=='__main__':
    try:wait(sys.argv[1],http_only='--http-only' in sys.argv[2:])
    except (OSError,ValueError,RuntimeError) as e:print(str(e),file=sys.stderr);sys.exit(1)
