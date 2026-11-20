#!/usr/bin/env python3
"""Generate the isle-overlay.yml of `isle app deploy`: attach every service to isle-agent-net and, for a
storage-bound hosting app (contract section 4), add the read-only dataset bind on the deployed service."""
import argparse
import sys

import yaml

BIND_TARGET = '/data'
BIND_ENV = 'ISLE_DATASET_PATH'


def build(compose, service=None, bind_path=None):
    """Return the overlay dict for a parsed compose document."""
    services = (compose or {}).get('services') or {}
    overlay = {'services': {}, 'networks': {'isle-agent-net': {'external': True}}}
    for name in services:
        overlay['services'][name] = {'networks': ['isle-agent-net']}
        own = (services.get(name) or {}).get('networks')
        if own:
            nets = own if isinstance(own, list) else list(own)
            overlay['services'][name]['networks'] = sorted(set(nets + ['isle-agent-net']))
    if bind_path:
        if service not in services:
            raise ValueError('Bind service %r is not in the compose file.' % (service,))
        overlay['services'][service]['volumes'] = [
            {'type': 'bind', 'source': bind_path, 'target': BIND_TARGET, 'read_only': True,
             'bind': {'create_host_path': False}}]
        overlay['services'][service]['environment'] = {BIND_ENV: bind_path}
    return overlay


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('compose')
    parser.add_argument('--service')
    parser.add_argument('--bind-path')
    args = parser.parse_args(argv)
    try:
        with open(args.compose) as handle:
            print(yaml.safe_dump(build(yaml.safe_load(handle), args.service, args.bind_path), default_flow_style=False))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print('app_overlay: %s' % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
