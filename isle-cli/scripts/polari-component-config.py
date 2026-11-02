#!/usr/bin/env python3
"""Generate role-scoped Compose and runtime configuration; no host mutation on import."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit


def generate(name, modules, role, directory, backend_url='', frontend_origin='https://polari.isle'):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,40}', name):
        raise ValueError('invalid deployment name')
    if modules and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]*(,[A-Za-z_][A-Za-z0-9_.-]*)*', modules):
        raise ValueError('invalid module identifiers')
    if role not in ('both', 'frontend', 'backend'):
        raise ValueError('role must be both, frontend or backend')
    if role == 'frontend' and not backend_url:
        raise ValueError('frontend-only deployment requires --backend-url https://api.<name>.isle')
    backend_url = backend_url or 'https://api.' + name + '.isle'
    urls = [urlsplit(backend_url), urlsplit(frontend_origin)]
    for url in urls:
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment:
            raise ValueError('backend URL and frontend origin must be HTTPS origins without credentials, paths, queries or fragments')
        if not re.fullmatch(r'[a-zA-Z0-9.-]+',url.hostname):
            raise ValueError('invalid hostname')
        _ = url.port  # validates range and syntax
    backend = urls[0]
    services = {}
    if role in ('both', 'backend'):
        services['backend'] = dict(image='prf-backend:staging', container_name=f'prf-{name}-backend', restart='unless-stopped', networks=['isle-agent-net'],
            environment={'WEBSOCKET_ENABLED':'true','WEBSOCKET_PORT':'3001','POLARI_MODULES':modules,'POLARI_LAZY_BOOT':'1','DATABASE_PATH':'/data/polari.db','CORS_ENABLED':'true','CORS_ORIGINS':f'https://{name}.isle,{frontend_origin}'},
            volumes=['backend-data:/data'],mem_limit='2g')
    if role in ('both', 'frontend'):
        services['frontend'] = dict(image='prf-frontend:staging',container_name=f'prf-{name}-frontend',restart='unless-stopped',networks=['isle-agent-net'],volumes=[f'{directory}/runtime-config.json:/usr/share/nginx/html/assets/runtime-config.json:ro'],mem_limit='128m')
    compose = {'services':services,'networks':{'isle-agent-net':{'external':True}}}
    if 'backend' in services:compose['volumes']={'backend-data':{}}
    runtime = {'backend':{'https':{'protocol':'https','url':backend.hostname,'port':str(backend.port or 443)},'ws':{'protocol':'wss','url':backend.hostname,'port':str(backend.port or 443)},'preferHttps':True},'frontend':{'https':{'protocol':'https','url':name+'.isle','port':'443'}}}
    metadata = dict(name=name,role=role,backend_url=backend_url.rstrip('/'),frontend_origin=frontend_origin,modules=modules)
    return compose,runtime,metadata


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--name',required=True);parser.add_argument('--modules',default='islemesh');parser.add_argument('--role',default='both');parser.add_argument('--directory',required=True);parser.add_argument('--backend-url',default='');parser.add_argument('--frontend-origin',default='https://polari.isle')
    a=parser.parse_args()
    compose,runtime,metadata=generate(a.name,a.modules,a.role,a.directory,a.backend_url,a.frontend_origin)
    path=Path(a.directory);path.mkdir(parents=True,exist_ok=True)
    # JSON is valid Compose YAML and avoids shell interpolation of user inputs.
    for filename,data in [('docker-compose.yml',compose),('runtime-config.json',runtime),('deployment.json',metadata)]:
        (path/filename).write_text(json.dumps(data,indent=2)+'\n')
if __name__=='__main__':main()
