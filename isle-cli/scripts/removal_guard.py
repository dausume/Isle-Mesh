#!/usr/bin/env python3
"""Verify critical service continuity before a local app teardown."""
import json
import os
from pathlib import Path
import sys
import urllib.parse
import urllib.request

def check(app):
    path=Path('/etc/isle-mesh/device-name')
    if not path.exists():raise RuntimeError('Device identity unavailable; refresh Isle membership before removing apps.')
    device=path.read_text().strip()
    query=urllib.parse.urlencode({'app':app,'device':device})
    url=os.environ.get('POLARI_ISLE_API','https://api.polari.isle').rstrip('/')+'/api/islemesh/removal-check?'+query
    with urllib.request.urlopen(url,timeout=8) as response:result=json.load(response)
    if result.get('ok') is not True or result.get('allowed') is not True:raise RuntimeError(' '.join(result.get('reasons',[])) or 'Removal could not be verified.')
if __name__=='__main__':
    try:check(sys.argv[1])
    except Exception as exc:
        print('Removal blocked: '+str(exc),file=sys.stderr);sys.exit(1)
