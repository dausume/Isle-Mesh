#!/usr/bin/env python3
"""Read a catalog definition; distinguish absence, transport and invalid payloads."""
import json,os,re,subprocess,sys
def show(name):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}',name):raise ValueError('Invalid catalog app name')
    base=os.environ.get('POLARI_ISLE_API','https://api.polari.isle').rstrip('/')
    p=subprocess.run(['curl','--http1.1','--fail-with-body','--silent','--show-error','--max-time','8',base+'/api/islemesh/catalog/'+name],capture_output=True,text=True)
    if p.returncode not in (0,22):raise ValueError('Catalog service unavailable (transport/TLS failure); no installability claim')
    try:d=json.loads(p.stdout)
    except ValueError:raise ValueError('Catalog service returned an empty or invalid JSON response') from None
    if not isinstance(d,dict):raise ValueError('Invalid catalog response')
    if not d.get('ok'):raise ValueError(str(d.get('error','Catalog entry unavailable')))
    if p.returncode:raise ValueError('Catalog request failed')
    try:
        e=d['entry'];plan=e['install_plan']
        print('%s — %s'%(e['title'],e['kind']));print(e.get('description',''))
        print('source: '+str(e.get('source_ref','')))
        if e.get('provides_engine'):print('provides engine: '+str(e['provides_engine']))
        print('installable: '+str(plan.get('ok') is True))
        print('install plan: '+str(plan.get('note','')))
        for step in plan.get('steps',[]):print('  $ '+str(step))
    except (KeyError,TypeError,AttributeError):raise ValueError('Catalog definition is incomplete; no installability claim') from None
if __name__=='__main__':
    try:
        if len(sys.argv)!=2:raise ValueError('Usage: isle store show <name>')
        show(sys.argv[1])
    except (ValueError,OSError,subprocess.SubprocessError) as exc:print('Catalog: '+str(exc),file=sys.stderr);sys.exit(1)
