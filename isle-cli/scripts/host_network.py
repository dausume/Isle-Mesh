#!/usr/bin/env python3
"""Core host routing to the existing Isle via its configured management gateway.
No host/member static addresses, Wi-Fi changes or default routes. Docker's core
macvlan network supplies the served subnet; management defaults match router.sh.
"""
import ipaddress,json,os,subprocess,sys
from pathlib import Path
from operations import atomic
CONFIG=Path('/etc/isle-mesh/host-network.json')
STATE=Path('/var/lib/isle-mesh/host-network.json')
def run(argv):return subprocess.run(argv,check=True,capture_output=True,text=True).stdout

def plan():
    config=json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    gateway=str(ipaddress.IPv4Address(config.get('managementGateway',os.environ.get('OPENWRT_IP','192.168.1.1'))))
    route=json.loads(run(['ip','-j','-4','route','get',gateway]))[0];interface=route['dev']
    if route.get('gateway') or Path('/sys/class/net',interface,'wireless').exists() or interface.startswith('wl'):
        raise ValueError('Management gateway must be directly reachable on a dedicated wired/bridge link')
    networks=json.loads(run(['docker','inspect','isle-vlan-agent']))[0]['NetworkSettings']['Networks']
    subnets=[]
    for name,endpoint in networks.items():
        network=json.loads(run(['docker','network','inspect',name]))[0]
        if network.get('Driver')!='macvlan':continue
        subnet=ipaddress.IPv4Network(endpoint['IPAddress']+'/'+str(endpoint['IPPrefixLen']),strict=False)
        if ipaddress.IPv4Address(gateway) in subnet:raise ValueError('Management and Isle subnets overlap')
        subnets.append(str(subnet))
    if not subnets:raise ValueError('No core macvlan subnet is available')
    for subnet in subnets:
        existing=json.loads(run(['ip','-j','-4','route','show','exact',subnet]))
        if any(r.get('gateway')!=gateway or r.get('dev')!=interface for r in existing):raise ValueError('Conflicting existing route for '+subnet)
    return dict(gateway=gateway,interface=interface,source=str(ipaddress.IPv4Address(route['prefsrc'])),subnets=sorted(set(subnets)))

def apply():
    mode=Path('/etc/isle-mesh/agent/agent.mode').read_text().strip()
    if mode!='core':return
    if os.geteuid()!=0:raise PermissionError('Host network reconciliation requires administrator authorization')
    p=plan();interface=p['interface'];gateway=p['gateway']
    from router_forwarding import apply as forward
    forward(p)
    # Query before mutations so missing resolver support cannot half-apply silently.
    domains=run(['resolvectl','domain',interface]).partition(':')[2].strip().split()
    servers=run(['resolvectl','dns',interface]).partition(':')[2].strip().split()
    domains=list(dict.fromkeys(domains+['~isle']));servers=list(dict.fromkeys(servers+[gateway]))
    for subnet in p['subnets']:run(['ip','-4','route','replace',subnet,'via',gateway,'dev',interface,'proto','static'])
    run(['resolvectl','dns',interface,*servers]);run(['resolvectl','domain',interface,*domains])
    STATE.parent.mkdir(parents=True,exist_ok=True);atomic(STATE,p)
    print('Core host Isle routes and scoped DNS reconciled via '+gateway+' on '+interface)
if __name__=='__main__':
    try:apply()
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as e:print('host network: '+str(e),file=sys.stderr);sys.exit(1)
