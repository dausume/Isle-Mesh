#!/usr/bin/env python3
"""Migrate framework-owned legacy reporting units through shared reconcile."""
from pathlib import Path
import subprocess,hashlib

def reconcile(root=Path('/etc/systemd/system')):
    unit=root/'polari-isle-push.service'
    changed=False
    if unit.exists():
        text=unit.read_text()
        # Only migrate the unit produced by the historical standard installer.
        if 'Description=Feed isle state into prf-isle (push-to-polari)' not in text:
            raise ValueError('Unrecognized polari-isle-push.service; refusing to replace its command')
        drop=root/'polari-isle-push.service.d/90-isle-command.conf'
        desired='[Service]\nExecStart=\nExecStart=/usr/share/isle-mesh/polari-isle/push-to-polari.sh\n'
        if not drop.exists() or drop.read_text()!=desired:
            # This is a unit fragment, not JSON. Replace it atomically.
            drop.parent.mkdir(parents=True,exist_ok=True)
            temporary=drop.with_suffix('.tmp')
            temporary.write_text(desired);temporary.chmod(0o644);temporary.replace(drop)
            changed=True
    host=root/'isle-host-agent.service'
    host_changed=False
    if host.exists():
        contents=host.read_text()
        if 'ExecStart=/usr/local/bin/isle-mesh/isle-host-agent-relay.sh' not in contents:
            raise ValueError('Unrecognized isle-host-agent.service; refusing to replace its command')
        drop=root/'isle-host-agent.service.d/90-isle-command.conf'
        source=Path(__file__).resolve().parents[2]/'isle-agent/isle-host-agent/isle-host-agent-relay.sh'
        desired='# source-sha256='+hashlib.sha256(source.read_bytes()).hexdigest()+'\n[Service]\nExecStart=\nExecStart=/usr/share/isle-mesh/isle-agent/isle-host-agent/isle-host-agent-relay.sh\n'
        if not drop.exists() or drop.read_text()!=desired:
            drop.parent.mkdir(parents=True,exist_ok=True)
            temporary=drop.with_suffix('.tmp');temporary.write_text(desired);temporary.chmod(0o644);temporary.replace(drop)
            changed=host_changed=True
        # Framework unit has no secrets; credentials remain in EnvironmentFile.
        host.chmod(0o644)
    if changed:subprocess.run(['systemctl','daemon-reload'],check=True)
    if host_changed:
        # Optional remote services remain disabled/inactive.
        subprocess.run(['systemctl','try-restart','isle-host-agent.service'],check=True)
    return changed

if __name__=='__main__':
    import sys
    try:reconcile()
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        print('Legacy unit migration: '+str(error),file=sys.stderr);sys.exit(1)
