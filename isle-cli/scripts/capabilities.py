#!/usr/bin/env python3
"""Bounded read-only host observations. Publishing is explicit and uses verified TLS."""
import argparse
from datetime import datetime, timezone
import json
import platform
import os
from pathlib import Path
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
    code,versions,_=run(['dpkg-query','-W','-f','${Package}=${Version}\n','isle-mesh-cli','isle-app-store','polari-shell-core','polari-complete-offline'])
    packages=dict(line.split('=',1) for line in versions.splitlines() if '=' in line)
    record('isle.package_versions',json.dumps(packages),'dpkg-query exact installed package versions')
    record('host.architecture', platform.machine(), 'platform.machine')
    try:
        with open('/proc/meminfo') as stream:
            available = next(int(line.split()[1]) // 1024 for line in stream if line.startswith('MemAvailable:'))
        record('resources.memory_available_mib', available, '/proc/meminfo:MemAvailable')
    except (OSError, ValueError, StopIteration):
        record('resources.memory_available_mib', None, '/proc/meminfo:MemAvailable', True)
    record('resources.root_disk_free_mib', shutil.disk_usage('/').free // (1024*1024), 'disk_usage:/')
    record('resources.root_disk_total_mib', shutil.disk_usage('/').total // (1024*1024), 'disk_usage:/')
    record('resources.logical_cpus', os.cpu_count(), 'os.cpu_count')
    try:
        with open('/proc/meminfo') as stream:
            total = next(int(line.split()[1]) // 1024 for line in stream if line.startswith('MemTotal:'))
        record('resources.memory_total_mib', total, '/proc/meminfo:MemTotal')
    except (OSError, ValueError, StopIteration):
        record('resources.memory_total_mib', None, '/proc/meminfo:MemTotal', True)
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
    qemu = {'x86_64':'qemu-system-x86_64','aarch64':'qemu-system-aarch64'}.get(platform.machine())
    record('runtime.qemu.installed', bool(qemu and shutil.which(qemu)), 'local QEMU executable for '+platform.machine())
    vm_allocations = []
    vm_status = 'libvirt not available'
    # A real running KVM guest is direct evidence that VM hosting is in use.
    if shutil.which('virsh'):
        code, guests, _ = run(['virsh','-c','qemu:///system','list','--name','--state-running'])
        record('runtime.libvirt.usable', code == 0, 'virsh system list', code != 0)
        vm_status = 'observed (up to 32 running domains)' if code == 0 else 'libvirt not readable'
        kvm_running = False
        router_running = False
        if code == 0:
            for guest in guests.splitlines()[:32]:
                if not guest.strip(): continue
                result, xml, _ = run(['virsh','-c','qemu:///system','dumpxml',guest])
                if result == 0:
                    import xml.etree.ElementTree as ET
                    try: is_kvm = ET.fromstring(xml).get('type') == 'kvm'
                    except ET.ParseError: is_kvm = False
                    try:
                        from resource_snapshot import vm_allocation
                        vm_allocations.append(vm_allocation(guest, xml))
                    except (ValueError, ET.ParseError): vm_status = 'some domain allocations not readable'
                    kvm_running = kvm_running or is_kvm
                    router_running = router_running or (is_kvm and guest == 'openwrt-isle-router')
        record('hardware.openwrt.running', router_running, 'virsh running domains and XML', code != 0)
        record('runtime.kvm.usable', kvm_running or os.access('/dev/kvm', os.R_OK | os.W_OK), 'running KVM guest or /dev/kvm access', code != 0 and not os.access('/dev/kvm', os.R_OK | os.W_OK))
    else:
        for dep in ('runtime.libvirt.usable','runtime.kvm.usable','hardware.openwrt.running'):
            record(dep, False, 'command lookup:virsh')
    from resource_snapshot import collect as collect_resources
    record('resources.detail_json', json.dumps(collect_resources(run, vm_allocations, vm_status), separators=(',', ':')), 'host proc/sysfs, lsblk, running libvirt XML and Docker HostConfig')
    record('runtime.shell.installed', Path('/usr/bin/polari-app-shell').is_file(), 'local shell executable')
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
    device_facts = {'machine_name':device}
    for evidence, field in [('agent.running','agent_present'),('hardware.openwrt.running','router_running')]:
        if facts.get(evidence, {}).get('status') == 'observed':
            device_facts[field] = facts[evidence]['value']
    payload = {'device':device,'facts':device_facts,'capability_evidence':{'schema_version':1,'facts':facts}}
    code, addresses, _ = run(['ip','-j','address','show'])
    if code == 0:
        try:
            interfaces = json.loads(addresses)
            if not isinstance(interfaces, list): raise ValueError('expected interface list')
            uplinks = []
            connected = set()
            for link in interfaces:
                name = link.get('ifname', '')
                if not re.fullmatch(r'[a-zA-Z0-9_.:-]+', name): continue
                base = Path('/sys/class/net') / name
                if not (base / 'device').exists(): continue
                kind = 'wifi' if (base / 'wireless').exists() else 'ethernet'
                up = link.get('operstate') == 'UP'
                uplinks.append({'interface':name, 'kind':kind, 'link_up':up,
                                'measured_at':datetime.now(timezone.utc).isoformat()})
                if up and any(a.get('family') == 'inet' and a.get('scope') == 'global' for a in link.get('addr_info', [])):
                    connected.add(kind)
            payload['uplinks'] = uplinks
            if connected == {'ethernet','wifi'}: device_facts['connectivity_mode'] = 'dual-home'
        except (ValueError, TypeError, AttributeError):
            pass  # Failed observation must not clear an existing network report.
    return payload


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
