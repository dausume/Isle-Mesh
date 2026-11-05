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

def identity(value):
    # Lease addresses may change; network membership and MAC must not.
    return (value['Id'], value['Image'], value['Config'].get('Env'), value.get('Mounts'),
            {k: (v.get('NetworkID'), v.get('MacAddress'))
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
        if identity(before) != identity(after):
            raise RuntimeError('Agent identity or network membership changed during restart')
        state = after['State']
        if state.get('Running') and state.get('Health', {}).get('Status') == 'healthy':
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('Agent health did not converge within 180 seconds')
        time.sleep(2)
    stage(ident, 'agent-ready')

if __name__ == '__main__':
    try:
        restart()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print('agent restart: ' + str(exc), file=sys.stderr)
        sys.exit(1)
