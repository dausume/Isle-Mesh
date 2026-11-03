#!/usr/bin/env python3
"""Core-only authenticated service enrollment. No signing keys leave this host.

An operator pairs a named member once (authorize); that root-only credential
permits automated enrollment of unclaimed service names. Claims are durable,
exclusive per member, and never inferred from unauthenticated device reports.
"""
import argparse
import fcntl
import hashlib
import hmac
from http.server import HTTPServer, BaseHTTPRequestHandler
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import tempfile

spec=importlib.util.spec_from_file_location('cert_enroll',Path(__file__).with_name('certs-enroll.py'))
enroll=importlib.util.module_from_spec(spec);spec.loader.exec_module(enroll)
STATE=Path('/etc/isle-mesh')
CORE_NAMES={'polari.isle','api.polari.isle','auth.polari.isle','apt.isle',
            'trust.isle','registry.isle','certs.isle','router.isle','health.isle'}

class Authority:
    def __init__(self,state=STATE):
        self.state=Path(state);self.directory=self.state/'ca/enrollment'
        self.directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.members=self.directory/'members.json';self.claims=self.directory/'claims.json'
        self.enrollment=enroll.Enrollment(self.state)
    def read(self,path):
        return json.loads(path.read_text()) if path.exists() else {}
    def write(self,path,doc):
        fd,tmp=tempfile.mkstemp(dir=self.directory)
        try:
            with os.fdopen(fd,'w') as f:json.dump(doc,f);f.flush();os.fsync(f.fileno())
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    def authorize(self,device,output):
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}',device):raise ValueError('invalid member name')
        with (self.directory/'authority.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            members=self.read(self.members)
            # Existing credentials are not silently revoked by repeated setup.
            if device in members:raise ValueError('member already paired; revoke explicitly before replacing its credential')
            token=secrets.token_urlsafe(48)
            enroll.write_new(output,json.dumps({'device':device,'token':token,'endpoint':'https://certs.isle/enroll'}).encode(),0o600)
            members[device]=hashlib.sha256(token.encode()).hexdigest();self.write(self.members,members)
    def revoke(self,device):
        with (self.directory/'authority.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX);members=self.read(self.members)
            members.pop(device,None);self.write(self.members,members)
    def issue(self,device,token,domain,csr):
        domain=enroll.domain_name(domain)
        if not isinstance(csr,str) or len(csr)>16384:raise ValueError('invalid CSR size')
        with (self.directory/'authority.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            expected=self.read(self.members).get(device)
            if not expected or not hmac.compare_digest(expected,hashlib.sha256(token.encode()).hexdigest()):
                raise PermissionError('member credential not accepted')
            claims=self.read(self.claims);owner=claims.get(domain)
            if owner and owner!=device:raise PermissionError('service name belongs to another member')
            # Core leaves and core registry names cannot be claimed by members.
            registry=self.read(self.state/'agent/registry.json')
            reserved=CORE_NAMES|set(registry.get('domains',{}))|set(registry.get('subdomains',{}))
            reserved.update(a.get('domain','') for a in registry.get('apps',{}).values())
            if domain in reserved or (self.state/'agent/ssl/certs'/(domain+'.crt')).exists():
                raise PermissionError('service name belongs to the core')
            if len([v for v in claims.values() if v==device])>=128 and not owner:
                raise ValueError('member service limit reached')
            with tempfile.TemporaryDirectory(dir=self.directory) as temp:
                request=Path(temp)/'request';chain=Path(temp)/'chain'
                request.write_text(csr)
                self.enrollment.sign(domain,request,chain)
                # Commit ownership before returning any certificate; retries reuse it.
                claims[domain]=device;self.write(self.claims,claims)
                return chain.read_text()

class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup();self.connection.settimeout(8)
    def log_message(self,*args):pass # Never log credentials/CSR content.
    def respond(self,status,body):
        raw=json.dumps(body).encode();self.send_response(status)
        self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
        self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        self.respond(200 if self.path=='/health' else 404,{'ok':self.path=='/health'})
    def do_POST(self):
        if self.path!='/enroll':return self.respond(404,{'ok':False})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=20000:raise ValueError('invalid request size')
            data=json.loads(self.rfile.read(length))
            auth=self.headers.get('Authorization','')
            if not auth.startswith('Bearer '):raise PermissionError('member credential required')
            chain=self.server.authority.issue(data['device'],auth[7:],data['domain'],data['csr'])
            self.respond(200,{'ok':True,'certificate':chain})
        except PermissionError as exc:self.respond(403,{'ok':False,'error':str(exc)})
        except (ValueError,KeyError,TypeError):self.respond(400,{'ok':False,'error':'invalid enrollment request'})
        except OSError:self.respond(503,{'ok':False,'error':'certificate authority unavailable'})

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    a=sub.add_parser('authorize');a.add_argument('device');a.add_argument('--output',required=True)
    r=sub.add_parser('revoke');r.add_argument('device')
    s=sub.add_parser('serve');s.add_argument('--bind',required=True);s.add_argument('--port',type=int,default=9849)
    args=p.parse_args()
    if os.geteuid()!=0:p.error('run on the core as root')
    if not (STATE/'ca/signing/intermediate_ca_key').is_file():p.error('this device is not the certificate authority')
    authority=Authority()
    try:
        if args.action=='authorize':authority.authorize(args.device,args.output);print('Member credential written; transfer privately to that member only.')
        elif args.action=='revoke':authority.revoke(args.device)
        else:
            server=HTTPServer((args.bind,args.port),Handler);server.authority=authority;server.serve_forever()
    except (ValueError,OSError) as exc:p.exit(1,str(exc)+'\n')
if __name__=='__main__':main()
