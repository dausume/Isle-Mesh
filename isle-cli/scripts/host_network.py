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

def member_plan(state=Path('/etc/isle-mesh')):
    remote=state/'agent/remote'
    interface=(remote/'interface.conf').read_text().strip()
    import re
    if not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,15}',interface):raise ValueError('Invalid saved member interface')
    discovery=json.loads((remote/'discovery.json').read_text())
    gateway=str(ipaddress.IPv4Address(discovery['router_ip']))
    route=json.loads(run(['ip','-j','-4','route','get',gateway]))[0]
    if route['dev']!=interface:raise ValueError('Saved Isle router is not reachable on the joined interface; refusing to alter another interface')
    return dict(interface=interface,gateway=gateway)

def apply():
    mode=Path('/etc/isle-mesh/agent/agent.mode').read_text().strip()
    if mode not in ('core','remote'):raise ValueError('Unknown membership mode')
    if os.geteuid()!=0:raise PermissionError('Host network reconciliation requires administrator authorization')
    if mode=='remote':
        from member_link import reconcile
        reconcile()
    p=plan() if mode=='core' else member_plan();interface=p['interface'];gateway=p['gateway']
    if mode=='core':
        from router_forwarding import apply as forward
        forward(p)
    # Query before mutations so missing resolver support cannot half-apply silently.
    domains=run(['resolvectl','domain',interface]).partition(':')[2].strip().split()
    servers=run(['resolvectl','dns',interface]).partition(':')[2].strip().split()
    domains=list(dict.fromkeys(domains+['~isle']));servers=list(dict.fromkeys(servers+[gateway]))
    for subnet in p.get('subnets',[]):run(['ip','-4','route','replace',subnet,'via',gateway,'dev',interface,'proto','static'])
    run(['resolvectl','dns',interface,*servers]);run(['resolvectl','domain',interface,*domains])
    STATE.parent.mkdir(parents=True,exist_ok=True);atomic(STATE,p)
    print(mode+' host Isle routes and scoped DNS reconciled via '+gateway+' on '+interface)
if __name__=='__main__':
    try:
        if "--member-only" not in sys.argv or Path("/etc/isle-mesh/agent/agent.mode").read_text().strip()=="remote":apply()
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as e:print('host network: '+str(e),file=sys.stderr);sys.exit(1)
