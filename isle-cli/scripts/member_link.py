#!/usr/bin/env python3
"""Persistent late-router policy for the saved member Ethernet profile only.
No connection down/up, Wi-Fi changes, route changes, or global wait-online edits.
"""
import re,subprocess
from pathlib import Path
STATE=Path('/etc/isle-mesh')
def output(argv):return subprocess.check_output(argv,text=True).strip()
def reconcile(state=STATE):
    remote=state/'agent/remote';nic=(remote/'interface.conf').read_text().strip()
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}',nic):raise ValueError('Invalid saved Isle interface')
    active=output(['nmcli','-g','GENERAL.CON-UUID','device','show',nic])
    saved=remote/'nm-profile.uuid'
    uuid=active if active and active!='--' else saved.read_text().strip() if saved.exists() else ''
    if not uuid:
        candidates=[]
        for candidate in output(['nmcli','-g','UUID','connection','show']).splitlines():
            if output(['nmcli','-g','connection.interface-name','connection','show','uuid',candidate])==nic:candidates.append(candidate)
        if len(candidates)!=1:raise ValueError('Cannot identify the saved Isle NM profile; activate the intended member connection once')
        uuid=candidates[0]
    if not re.fullmatch(r'[a-fA-F0-9-]{36}',uuid):raise ValueError('Invalid member profile UUID')
    def get(key):return output(['nmcli','-g',key,'connection','show','uuid',uuid])
    if get('connection.type')!='802-3-ethernet' or get('ipv4.method')!='auto' or get('connection.interface-name') not in ('','--',nic):
        raise ValueError('Saved member profile is not DHCP Ethernet on the joined interface')
    wanted={'connection.autoconnect':'yes','connection.autoconnect-retries':'0','ipv4.dhcp-timeout':'2147483647'}
    if any(get(k)!=v for k,v in wanted.items()):
        subprocess.run(['nmcli','connection','modify','uuid',uuid,*[x for pair in wanted.items() for x in pair]],check=True)
    if not saved.exists() or saved.read_text().strip()!=uuid:saved.write_text(uuid+'\n');saved.chmod(0o600)
    return {'interface':nic,'profile':uuid,'retry':'forever','dhcpTimeout':'infinity'}
