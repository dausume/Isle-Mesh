#!/usr/bin/env python3
"""Deployment-owned Keycloak switch. Never changes backend authorization rules."""
import argparse
import fcntl
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def update(config, action, feature=None):
    kc = config.setdefault('keycloak', {})
    required = kc.get('requiredBy', [])
    if not isinstance(required, list) or any(not isinstance(x, str) or not x.strip() for x in required):
        raise ValueError('keycloak.requiredBy must contain nonempty feature names')
    if action == 'disable':
        if required:
            raise ValueError('Cannot disable Keycloak: required by ' + ', '.join(required))
        kc['enabled'] = False
    elif action in ('enable', 'require'):
        authority = urlsplit(kc.get('authority', ''))
        if authority.scheme != 'https' or not authority.hostname or not kc.get('clientId'):
            raise ValueError('Configure keycloak.authority (HTTPS) and clientId before enabling sign-in')
        if action == 'require':
            if not feature or not feature.strip():
                raise ValueError('require needs --feature <app-or-feature-name>')
            kc['requiredBy'] = sorted(set(required + [feature]))
        kc['enabled'] = True
    elif action == 'release':
        if not feature or feature not in required:
            raise ValueError('release needs --feature naming an existing dependency')
        kc['requiredBy'] = [x for x in required if x != feature]
    return config


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['status', 'enable', 'disable', 'require', 'release'])
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--feature')
    a = p.parse_args()
    if a.action != 'status' and os.geteuid() != 0:
        p.error('Run with sudo on the device hosting this frontend')
    try:
        # Keep inode: Docker binds this individual file into the frontend.
        with a.config.open('r+' if a.action != 'status' else 'r') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            original = f.read()
            cfg = json.loads(original)
            if a.action != 'status':
                update(cfg, a.action, a.feature)
                backup = a.config.with_name(a.config.name + '.before-security')
                if not backup.exists():
                    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, 'w') as b:
                        b.write(original)
                f.seek(0); f.write(json.dumps(cfg, indent=2) + '\n'); f.truncate(); f.flush(); os.fsync(f.fileno())
            kc = cfg.get('keycloak', {})
            print(json.dumps({'enabled': bool(kc) and kc.get('enabled', True), 'requiredBy': kc.get('requiredBy', []), 'config': str(a.config)}))
            if a.action != 'status':
                print('Reload Isle App. Backend authorization is unchanged. Enabled sign-in still requires a healthy Keycloak service.')
    except (ValueError, OSError) as e:
        p.exit(1, str(e) + '\n')


if __name__ == '__main__':
    main()
