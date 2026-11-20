#!/usr/bin/env python3
"""Verify authoritative DNS after the standard registry/core relay path."""
import ipaddress,json,re,subprocess,sys,time
from pathlib import Path
from member_preflight import identity
def resolver():
    from host_network import member_plan
    mode=Path('/etc/isle-mesh/agent/agent.mode').read_text().strip()
    if mode=='remote':return member_plan()['gateway']
    networks=json.loads(subprocess.check_output(['docker','inspect','isle-vlan-agent'],text=True))[0]['NetworkSettings']['Networks']
    for name,endpoint in networks.items():
        net=json.loads(subprocess.check_output(['docker','network','inspect',name],text=True))[0]
        if net['Driver']=='macvlan':return str(ipaddress.IPv4Address(endpoint['Gateway']))
    raise ValueError('No Isle DNS gateway in existing membership')
def publish(domain,address,timeout=90):
    if not re.fullmatch(r'(?:[a-zA-Z0-9-]+\.)+isle',domain):raise ValueError('Invalid Isle domain')
    address=str(ipaddress.IPv4Address(address));server=resolver()
    registry=json.loads(Path('/etc/isle-mesh/agent/registry.json').read_text())
    from engine_sync import request
    request('https://api.polari.isle/api/islemesh/ingest/registry',{'device':identity(),'registry':registry})
    mode=Path('/etc/isle-mesh/agent/agent.mode').read_text().strip()
    if mode=='core':
        subprocess.run(['isle','dns','register',domain,address],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    deadline=time.monotonic()+timeout
    while True:
        p=subprocess.run(['dig','+time=2','+tries=1','+short','@'+server,domain,'A'],capture_output=True,text=True)
        if p.returncode==0 and address in p.stdout.split():
            print('DNS published by core and verified: '+domain+' -> '+address);return
        if time.monotonic()>=deadline:raise RuntimeError('Registry accepted; core DNS publication still pending for '+domain+'; configuration retained, retry the same operation')
        time.sleep(2)
if __name__=='__main__':
    try:publish(*sys.argv[1:])
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as e:print(str(e),file=sys.stderr);sys.exit(1)
