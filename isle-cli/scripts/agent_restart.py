#!/usr/bin/env python3
"""Restart existing agent services without teardown, setup or membership changes."""
import json, os, subprocess, sys, time
from pathlib import Path
from operations import stage

MODE = Path('/etc/isle-mesh/agent/agent.mode')

def run(argv, **kwargs):
    return subprocess.run(argv, check=True, timeout=45, **kwargs)

def inspect(name):
    return json.loads(run(['docker', 'inspect', name], capture_output=True, text=True).stdout)[0]

def identity(value, fixed_mac_networks):
    # Lease addresses and generated bridge MACs may change; preserve Isle-facing MACs.
    return (value['Id'], value['Image'], value['Config'].get('Env'),
            sorted(json.dumps(m, sort_keys=True) for m in value.get('Mounts', [])),
            {k: (v.get('NetworkID'), v.get('MacAddress') if k in fixed_mac_networks else None)
             for k, v in value['NetworkSettings']['Networks'].items()})

def restart():
    if os.geteuid() != 0:
        raise PermissionError('Use sudo isle agent restart')
    mode = MODE.read_text().strip() if MODE.exists() else 'core'
    if mode not in ('core', 'remote'):
        raise ValueError('Invalid agent mode')
    name = 'isle-remote-agent' if mode == 'remote' else 'isle-vlan-agent'
    ident = os.environ['ISLE_OPERATION_ID']
    stage(ident, 'agent-preflight')
    try:
        before = inspect(name)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError('Existing agent missing; inspect isle update plan and use isle reconcile. Restart never rejoins.') from exc
    # No successful restart can be declared without a defined health check.
    if not before['Config'].get('Healthcheck') or before['Config']['Healthcheck'].get('Test', ['NONE'])[0] == 'NONE':
        raise RuntimeError('Agent has no health check; update/reconcile its versioned runtime first')
    fixed_mac_networks = set()
    for network, endpoint in before['NetworkSettings']['Networks'].items():
        driver = json.loads(run(['docker', 'network', 'inspect', endpoint['NetworkID']], capture_output=True, text=True).stdout)[0]['Driver']
        if driver != 'bridge':
            fixed_mac_networks.add(network)
    host_active = subprocess.run(['systemctl', 'is-active', '--quiet', 'isle-host-agent'], timeout=15).returncode == 0
    stage(ident, 'agent-restart')
    run(['docker', 'restart', '--time', '20', name])
    if host_active:
        run(['systemctl', 'restart', 'isle-host-agent'])
        run(['systemctl', 'is-active', '--quiet', 'isle-host-agent'])
    stage(ident, 'agent-readiness')
    deadline = time.monotonic() + 180
    while True:
        after = inspect(name)
        if identity(before, fixed_mac_networks) != identity(after, fixed_mac_networks):
            raise RuntimeError('Agent identity or network membership changed during restart')
        state = after['State']
        if state.get('Running') and state.get('Health', {}).get('Status') == 'healthy':
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('Agent health did not converge within 180 seconds')
        time.sleep(2)
    stage(ident, 'agent-host-ingress')
    run(['bash', str(Path(__file__).with_name('hosts-reconcile.sh'))])
    stage(ident, 'agent-ready')

if __name__ == '__main__':
    try:
        restart()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print('agent restart: ' + str(exc), file=sys.stderr)
        sys.exit(1)
