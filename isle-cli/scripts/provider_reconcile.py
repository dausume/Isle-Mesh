#!/usr/bin/env python3
"""Regenerate existing defined instances through the same parameterized operation."""
import json,subprocess,sys
from pathlib import Path
BASE=Path('/etc/isle-mesh/polari')
def reconcile():
    for path in sorted(BASE.glob('*/deployment.json')):
        d=json.loads(path.read_text())
        if d.get('name')!=path.parent.name:raise ValueError('Instance directory/identity mismatch')
        argv=['bash',str(Path(__file__).with_name('polari.sh')),'instance','deploy',
            '--name',d['name'],'--role',d['role'],'--modules',d['modules'],
            '--backend-url',d['backend_url'],'--frontend-origin',d.get('frontend_origin','https://polari.isle')]
        subprocess.run(argv,check=True)
if __name__=='__main__':
    try:reconcile()
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as e:print('Provider reconciliation: '+str(e),file=sys.stderr);sys.exit(1)
