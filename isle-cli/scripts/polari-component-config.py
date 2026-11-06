#!/usr/bin/env python3
"""Generate role-scoped Compose and runtime configuration; no host mutation on import."""
import argparse
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit


def generate(name, modules, role, directory, backend_url='', frontend_origin='https://polari.isle', images=None, dns=None):
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
    images=images or {}
    for component in ('backend','frontend'):
        if role in ('both',component) and not re.fullmatch(r'sha256:[a-f0-9]{64}',images.get(component,'')):
            raise ValueError('An admitted immutable '+component+' image is required')
    import ipaddress
    if dns: dns=str(ipaddress.IPv4Address(dns))
    backend = urls[0]
    services = {}
    if role in ('both', 'backend'):
        services['backend'] = dict(image=images['backend'], container_name=f'prf-{name}-backend', restart='unless-stopped', networks=['isle-agent-net'],
            environment={'WEBSOCKET_ENABLED':'true','WEBSOCKET_PORT':'3001','POLARI_MODULES':modules,'POLARI_LAZY_BOOT':'1','DATABASE_PATH':'/data/polari.db','SSL_CERT_FILE':'/etc/isle-trust/ca-certificates.crt','REQUESTS_CA_BUNDLE':'/etc/isle-trust/ca-certificates.crt','CORS_ENABLED':'true','CORS_ORIGINS':f'https://{name}.isle,{frontend_origin}'},
            volumes=['backend-data:/data','/etc/ssl/certs:/etc/isle-trust:ro'],mem_limit='2g')
    if role in ('both', 'frontend'):
        services['frontend'] = dict(image=images['frontend'],container_name=f'prf-{name}-frontend',restart='unless-stopped',networks=['isle-agent-net'],volumes=[f'{directory}/runtime-config.json:/usr/share/nginx/html/assets/runtime-config.json:ro'],mem_limit='128m')
    for service in services.values():
        if dns:service['dns']=[dns]
    compose = {'services':services,'networks':{'isle-agent-net':{'external':True}}}
    if 'backend' in services:compose['volumes']={'backend-data':{}}
    runtime = {'backend':{'https':{'protocol':'https','url':backend.hostname,'port':str(backend.port or 443)},'ws':{'protocol':'wss','url':backend.hostname,'port':str(backend.port or 443)},'preferHttps':True},'frontend':{'https':{'protocol':'https','url':name+'.isle','port':'443'}}}
    metadata = dict(name=name,role=role,backend_url=backend_url.rstrip('/'),frontend_origin=frontend_origin,modules=modules)
    return compose,runtime,metadata


def write_configuration(path, compose, runtime, metadata, operation):
    path=Path(path)
    if not re.fullmatch(r'[a-f0-9]{32}',operation):raise ValueError('Provider configuration requires a shared operation')
    marker=path/'.operation-id'
    if path.exists() and not (path/'deployment.json').exists():
        if not marker.exists() or marker.read_text().strip()!=operation:
            raise ValueError('Untracked provider directory belongs to another operation')
    path.mkdir(parents=True,exist_ok=True)
    marker.write_text(operation+'\n');marker.chmod(0o600)
    # Keep runtime-config inode stable for an existing container bind mount.
    for filename,data in [('docker-compose.yml',compose),('runtime-config.json',runtime),('deployment.json',metadata)]:
        (path/filename).write_text(json.dumps(data,indent=2)+'\n')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--name',required=True);parser.add_argument('--modules',default='islemesh');parser.add_argument('--role',default='both');parser.add_argument('--directory',required=True);parser.add_argument('--backend-url',default='');parser.add_argument('--frontend-origin',default='https://polari.isle')
    a=parser.parse_args()
    from provider_images import resolve
    from dns_publication import resolver
    dns=resolver()
    images=resolve(a.role)
    compose,runtime,metadata=generate(a.name,a.modules,a.role,a.directory,a.backend_url,a.frontend_origin,images,dns)
    write_configuration(a.directory,compose,runtime,metadata,os.environ.get('ISLE_OPERATION_ID',''))
if __name__=='__main__':main()
