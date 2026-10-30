#!/usr/bin/env python3
"""Readable device-name suggestions and collision-aware instance-name planning."""
import argparse
import json
import re
import secrets
import sys
import socket
from pathlib import Path

ADJECTIVES = ('amber','bright','calm','clever','cosmic','dapper','eager','eccentric',
              'gentle','golden','happy','jolly','kind','lively','lucky','mellow',
              'merry','nimble','patient','quiet','rapid','silver','steady','sunny',
              'tidy','tranquil','velvet','vivid','warm','wild','wise','zesty')
ANIMALS = ('badger','beaver','bison','crane','dolphin','eagle','falcon','finch',
           'fox','gecko','heron','ibis','koala','lemur','lynx','marten',
           'otter','owl','panda','penguin','puffin','quail','raven','robin',
           'seal','sparrow','stoat','swan','tiger','turtle','whale','wren')
LABEL = re.compile(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\Z')

def valid_label(value):
    return isinstance(value,str) and len(value)<=63 and bool(LABEL.fullmatch(value))

def suggest_device(taken=()):
    taken=set(taken)
    available=[a+'-'+b for a in ADJECTIVES for b in ANIMALS if a+'-'+b not in taken]
    if not available:raise ValueError('All memorable names are taken; supply a device name explicitly.')
    return secrets.choice(available)

def occupied_names(catalog):
    if not isinstance(catalog,dict) or catalog.get('ok') is not True or not isinstance(catalog.get('entries'),list):
        raise ValueError('A current Isle catalog is required to plan an automatic name.')
    taken=set()
    for entry in catalog['entries']:
        if not isinstance(entry,dict):raise ValueError('Invalid catalog entry.')
        instances=entry.get('instances',[])
        if not isinstance(instances,list):raise ValueError('Invalid instance inventory.')
        for instance in instances:
            if not isinstance(instance,dict):raise ValueError('Invalid instance record.')
            if isinstance(instance.get('app'),str):taken.add(instance['app'])
            domain=instance.get('domain','')
            if isinstance(domain,str) and domain.endswith('.isle'):taken.add(domain[:-5])
    return taken

def instance_name(app, device, taken):
    if not valid_label(app) or not valid_label(device) or device in ('localhost','unknown'):
        raise ValueError('Choose a valid, named device before creating an automatic app name.')
    for number in range(1,10001):
        # Keep the device suffix at the end even for additional instances.
        candidate=f'{app}{"-"+str(number) if number>1 else ""}-{device}'
        if len(candidate)>41:
            raise ValueError('Automatic instance name is too long; use a shorter device name or an explicit --name.')
        if candidate not in taken:return candidate
    raise ValueError('No unused instance name found; supply --name explicitly.')

def current_device():
    for path in ('/etc/isle-mesh/device-name', '/etc/isle-mesh/agent/remote/hostname'):
        try:
            value=Path(path).read_text().strip()
        except FileNotFoundError:
            continue
        if not valid_label(value):raise ValueError('Invalid saved Isle device name')
        return value
    value=socket.gethostname().lower()
    return 'isle-core' if value == 'dustin-etts-mesh-core' else value

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('current-device',help='read the saved Isle identity, falling back to the OS hostname')
    device=sub.add_parser('suggest-device',help='suggest an adjective-animal name; does not rename or register a device')
    device.add_argument('--taken',action='append',default=[])
    inst=sub.add_parser('instance',help='plan an instance name using a catalog JSON document on stdin')
    inst.add_argument('--app',required=True);inst.add_argument('--device',required=True)
    args=parser.parse_args()
    try:
        if args.command=='current-device':print(current_device())
        elif args.command=='suggest-device':print(suggest_device(args.taken))
        else:print(instance_name(args.app,args.device,occupied_names(json.load(sys.stdin))))
    except (ValueError,TypeError) as exc:parser.exit(1,str(exc)+'\n')
if __name__=='__main__':main()
