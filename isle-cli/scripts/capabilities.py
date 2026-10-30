#!/usr/bin/env python3
"""Bounded read-only host observations. Publishing is explicit and uses verified TLS."""
import argparse
from datetime import datetime, timezone
import json
import platform
import re
import shutil
import socket
import subprocess
import sys


def run(args, timeout=8):
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, '', type(exc).__name__


def collect(device, images=()):
    facts = {}
    def record(dep, value, source, error=False):
        facts[dep] = dict(status='error' if error else 'observed', source=source,
                          observed_at=datetime.now(timezone.utc).isoformat())
        if not error: facts[dep]['value'] = value
    record('host.architecture', platform.machine(), 'platform.machine')
    try:
        with open('/proc/meminfo') as stream:
            available = next(int(line.split()[1]) // 1024 for line in stream if line.startswith('MemAvailable:'))
        record('resources.memory_available_mib', available, '/proc/meminfo:MemAvailable')
    except (OSError, ValueError, StopIteration):
        record('resources.memory_available_mib', None, '/proc/meminfo:MemAvailable', True)
    record('resources.root_disk_free_mib', shutil.disk_usage('/').free // (1024*1024), 'disk_usage:/')
    if not shutil.which('docker'):
        record('runtime.docker.usable', False, 'command lookup:docker')
        record('runtime.compose.usable', False, 'command lookup:docker')
    else:
        code, _, _ = run(['docker','info','--format','{{.ServerVersion}}'])
        record('runtime.docker.usable', code == 0, 'docker info', code != 0)
        code, _, _ = run(['docker','compose','version'])
        record('runtime.compose.usable', code == 0, 'docker compose version', code is None)
        code, output, _ = run(['docker','ps','--format','{{.Names}}'])
        present = bool(set(output.splitlines()) & {'isle-vlan-agent','isle-remote-agent'})
        record('agent.running', present, 'docker ps:agent name', code != 0)
    for endpoint, url in [('whoami','https://whoami.isle/'),('polari','https://api.polari.isle/api/health'),('polari-frontend','https://polari.isle/')]:
        code, output, _ = run(['curl','--silent','--show-error','--max-time','6','--output','/dev/null','--write-out','%{http_code}',url])
        # A completed TLS exchange and HTTP response proves access, not an app's
        # authorization or all of its functions. DNS/TLS/timeouts are probe errors.
        record('access.'+endpoint+'.https', output == '200', 'trusted HTTPS:'+url, code != 0)
    for name, digest in images:
        code, output, err = run(['docker','image','inspect',digest,'--format','{{json .}}'])
        if code == 0:
            try:
                image = json.loads(output)
                record('image.'+name+'.present', image.get('Id') == digest, 'docker image inspect:'+digest)
                record('image.'+name+'.architecture', image.get('Architecture',''), 'docker image inspect:'+digest)
            except (ValueError, TypeError):
                record('image.'+name+'.present', None, 'docker image inspect:'+digest, True)
        else:
            missing = 'No such image' in err or 'No such object' in err
            record('image.'+name+'.present', False, 'docker image inspect:'+digest, not missing)
    return {'device':device,'capability_evidence':{'schema_version':1,'facts':facts}}


def image_arg(value):
    name, sep, digest = value.partition('=')
    if not sep or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}',name) or not re.fullmatch(r'sha256:[a-f0-9]{64}',digest):
        raise argparse.ArgumentTypeError('use name=sha256:<64 lowercase hex>; mutable tags are not accepted')
    return name,digest


def main():
    from names import current_device
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['status'])
    parser.add_argument('--json',action='store_true',help='emit JSON (the default)')
    parser.add_argument('--device',default=current_device())
    parser.add_argument('--image',action='append',type=image_arg,default=[])
    parser.add_argument('--publish',help='explicit HTTPS Isle API base; does not change host configuration')
    args=parser.parse_args()
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}',args.device):parser.error('device must be an explicit canonical hostname')
    if args.publish and not re.fullmatch(r'https://[a-z0-9][a-z0-9.-]*\.isle(?::[0-9]+)?',args.publish):parser.error('publish requires an HTTPS .isle API origin')
    report=collect(args.device,args.image)
    body=json.dumps(report,indent=2)
    if args.publish:
        # No insecure fallback, no guessed localhost route and no embedded credentials.
        result=subprocess.run(['curl','--fail','--silent','--show-error','--max-time','12',
            '-H','Content-Type: application/json','--data-binary','@-',args.publish+'/api/islemesh/ingest/device'],input=body,text=True,capture_output=True)
        try: accepted=json.loads(result.stdout).get('ok') is True
        except (ValueError,AttributeError): accepted=False
        if result.returncode or not accepted:
            print('Capability report not accepted; local observations follow. Check API access/authorization.',file=sys.stderr)
            print(body);return 1
    print(body);return 0

if __name__=='__main__':sys.exit(main())
