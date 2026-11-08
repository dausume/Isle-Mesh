#!/usr/bin/env python3
"""Regenerate existing defined instances through the same parameterized operation."""
import json,subprocess,sys
from pathlib import Path
BASE=Path('/etc/isle-mesh/polari')
def planned_targets(release, base=None):
    """Read-only preview of the same saved providers reconcile deploys."""
    from provider_images import definitions
    import re
    result=[]
    for path in sorted((BASE if base is None else Path(base)).glob('*/deployment.json')):
        d=json.loads(path.read_text())
        if d.get('name')!=path.parent.name or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}',d.get('name','')):
            raise ValueError('Instance directory/identity mismatch')
        if d.get('role') not in ('backend','frontend','both'):
            raise ValueError('Invalid saved provider role')
        # These fields are required by the shared deploy replay, too.
        for key in ('modules','backend_url'):
            if not isinstance(d.get(key),str):raise ValueError('Incomplete provider metadata: '+key)
        for component,image in definitions(release,d['role']).items():
            result.append(dict(container='prf-'+d['name']+'-'+component,
                instance=d['name'],component=component,project='prf-'+d['name'],
                image=image,configuration=str(path),operation='polari instance deploy',
                dataVolume='prf-'+d['name']+'_backend-data' if component=='backend' else None,
                dataPolicy='Preserve existing volume; no automatic database snapshot' if component=='backend' else 'Regenerate frontend runtime configuration'))
    return result

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
