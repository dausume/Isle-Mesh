#!/usr/bin/env python3
"""Generate the isle-overlay.yml of `isle app deploy`: attach every service to isle-agent-net and, for a
storage-bound hosting app (contract section 4), add the read-only dataset bind on the deployed service.
An app that declares storage slots (`isle storage slot`) is given the folder of each slot on the service the
slot names, or on the deployed service when it names none."""
import argparse
import subprocess
import sys

import yaml

BIND_TARGET = '/data'
BIND_ENV = 'ISLE_DATASET_PATH'


def build(compose, service=None, bind_path=None, slots=()):
    """Return the overlay dict for a parsed compose document.  slots: what storage_slots.binds() gives,
    [{service, source, target, readOnly}]."""
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
    for slot in slots or ():
        name = slot['service'] or service
        if name not in services:
            raise ValueError('The storage slot at %s is for the service %r, which is not in the compose file.' % (slot['target'], name))
        volumes = overlay['services'][name].setdefault('volumes', [])
        if any(v['target'] == slot['target'] for v in volumes):
            raise ValueError('Two folders would be given to the service %s at %s.' % (name, slot['target']))
        # The folder is never made by the start: when its partition is away the start fails, and nothing is
        # written to the disk of the system in its place.
        volumes.append({'type': 'bind', 'source': slot['source'], 'target': slot['target'],
                        'read_only': bool(slot['readOnly']), 'bind': {'create_host_path': False}})
    return overlay


def slot_binds(app):
    """The folders of the storage slots an app declares.  ValueError when one cannot be given as it stands:
    then no overlay is written, and the app is not started."""
    if not app:
        return ()
    import storage_roles
    import storage_slots
    return storage_slots.binds(storage_roles.Io(), app)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('compose')
    parser.add_argument('--service')
    parser.add_argument('--bind-path')
    parser.add_argument('--slots-of', metavar='APP', help='give the app the folders of the storage slots it declares')
    args = parser.parse_args(argv)
    try:
        with open(args.compose) as handle:
            print(yaml.safe_dump(build(yaml.safe_load(handle), args.service, args.bind_path, slot_binds(args.slots_of)),
                                 default_flow_style=False))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError, yaml.YAMLError) as exc:
        print('app_overlay: %s' % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
