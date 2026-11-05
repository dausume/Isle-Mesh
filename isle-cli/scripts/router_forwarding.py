#!/usr/bin/env python3
"""Scoped core-management -> Isle forwarding, derived from actual router zones."""
import ipaddress,json,re,shlex,subprocess
from pathlib import Path
KEY=Path('/etc/isle-mesh/router/ssh/isle_router_key')
KNOWN=Path('/etc/isle-mesh/router/ssh/known_hosts')
def sections(text):
    result={}
    for line in text.splitlines():
        if not line.startswith('firewall.') or '=' not in line:continue
        key,value=line[len('firewall.'):].split('=',1);part,_,field=key.partition('.')
        result.setdefault(part,{})[field or 'type']=shlex.split(value)
    return result

def changes(networks,firewall,gateway,subnets,source_address):
    mapping={}
    for row in networks['interface']:
        for addr in row.get('ipv4-address',[]):mapping[addr['address']]=row['interface']
    source_iface=mapping.get(gateway)
    dest_ifaces={row['interface'] for row in networks['interface'] for addr in row.get('ipv4-address',[]) if any(ipaddress.IPv4Address(addr['address']) in ipaddress.IPv4Network(s) for s in subnets)}
    zones=sections(firewall)
    def zone_for(iface):
        found=[v['name'][0] for v in zones.values() if v.get('type')==['zone'] and iface in v.get('network',[])]
        if len(found)!=1:raise ValueError('Cannot uniquely resolve router firewall zone')
        if not re.fullmatch('[a-zA-Z0-9_]{1,32}',found[0]):raise ValueError('Invalid router zone')
        return found[0]
    if not source_iface or not dest_ifaces:raise ValueError('Router interface evidence does not match core route plan')
    source=zone_for(source_iface);commands=[]
    for dest in sorted({zone_for(i) for i in dest_ifaces}):
        if dest==source:continue
        if any(v.get('type')==['forwarding'] and v.get('src')==[source] and v.get('dest')==[dest] for v in zones.values()):continue
        name='isle_core_host_to_'+dest
        if name in zones:raise ValueError('Conflicting owned forwarding section')
        commands += ['uci set firewall.'+name+'=forwarding','uci set firewall.'+name+'.src='+source,'uci set firewall.'+name+'.dest='+dest]
    # Members keep Docker's bridge as their Internet default route. Translate
    # only this core host's management traffic into each Isle subnet so replies
    # return through the router, without modifying member defaults or addresses.
    # fw4 NAT semantics: src selects the outgoing zone (parse_snat in fw4.uc).
    source_address=str(ipaddress.IPv4Address(source_address))
    for dest in sorted({zone_for(i) for i in dest_ifaces}):
        if dest==source:continue
        for subnet in subnets:
            import hashlib
            subnet=str(ipaddress.IPv4Network(subnet))
            name='isle_core_host_nat_'+hashlib.sha256((dest+subnet).encode()).hexdigest()[:12]
            desired={'type':['nat'],'src':[dest],'src_ip':[source_address+'/32'],'dest_ip':[subnet],'target':['MASQUERADE'],'proto':['all']}
            if name in zones:
                if any(zones[name].get(k)!=v for k,v in desired.items()):raise ValueError('Conflicting owned NAT section')
                continue
            commands += ['uci set firewall.'+name+'=nat']+[('uci set firewall.'+name+'.'+k+'='+v[0]) for k,v in desired.items() if k!='type']
    if commands:commands+=['fw4 check','uci commit firewall','/etc/init.d/firewall reload']
    return commands

def apply(plan):
    if not KEY.is_file() or not KNOWN.is_file():raise ValueError('Router key not pinned: verify its public key via VM console and use isle router trust-key')
    ssh=['ssh','-i',str(KEY),'-o','BatchMode=yes','-o','ConnectTimeout=5','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(KNOWN),'root@'+plan['gateway']]
    def read(command):return subprocess.run(ssh+[command],check=True,capture_output=True,text=True,timeout=15).stdout
    pending=read('uci changes firewall')
    if any('firewall.isle_core_host_' not in line for line in pending.splitlines() if line):raise ValueError('Unrelated uncommitted router firewall changes; refusing to commit them')
    commands=changes(json.loads(read('ubus call network.interface dump')),read('uci show firewall'),plan['gateway'],plan['subnets'],plan['source'])
    if pending and not commands:commands=['fw4 check','uci commit firewall','/etc/init.d/firewall reload']
    if commands:subprocess.run(ssh+['sh -s'],input='set -e\n'+'\n'.join(commands)+'\n',text=True,check=True,timeout=30)
