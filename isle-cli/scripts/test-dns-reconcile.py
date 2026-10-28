import pathlib,subprocess,os,json,socket
p=pathlib.Path(__file__).with_name('dns-reconcile.sh')
s=p.read_text(); body=s.split("python3 - <<'PYEOF'\n",1)[1].split("\nPYEOF",1)[0]
def check(instances,leases,expect):
 env=dict(os.environ,CAT=json.dumps({'entries':[{'instances':instances}]}),LEASES=leases,HOSTN='isle-core')
 r=subprocess.run(['python3','-c',body],env=env,capture_output=True,text=True,check=True)
 assert r.stdout.strip()==expect,(r.stdout,expect)
lease='9999999999 02:00:00:00:0a:0e 10.10.0.116 remote-one *'
check([{'device':socket.gethostname(),'domain':'whoami.isle'},{'device':'isle-core','domain':'whoami.isle'}],lease,'')
check([{'device':'unknown-device','domain':'example.isle'}],lease,'')
check([{'device':'remote-one','domain':'remote.isle'}],lease,'remote.isle 10.10.0.116')
check([{'device':'remote-one','domain':'remote.isle'}],'9999999999 02:00:00:00:0a:0e 10.10.0.116 * *','')
subprocess.run(['bash','-n',str(p)],check=True)
print('PASS: both core aliases excluded; unknown owner refused; exact remote accepted; unnamed lease refused; shell syntax valid')
