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
door_lease='9999999999 02:00:00:00:0a:0f 192.0.2.10 remote-one *'   # a documentation address (RFC 5737)
# a store release's doors (isle app deploy --door) go to the same device's agent address; a malformed one is never written
check([{'device':'remote-one','domain':'esp-engines.isle','doors':[{'name':'twin','domain':'twin.esp-engines.isle'}]}],door_lease,'esp-engines.isle 192.0.2.10\ntwin.esp-engines.isle 192.0.2.10')
check([{'device':'remote-one','domain':'esp-engines.isle','doors':[{'name':'x','domain':'bad name.isle'},'junk']}],door_lease,'esp-engines.isle 192.0.2.10')
check([{'device':'unknown-device','domain':'esp-engines.isle','doors':[{'name':'twin','domain':'twin.esp-engines.isle'}]}],door_lease,'')
subprocess.run(['bash','-n',str(p)],check=True)
print('PASS: both core aliases excluded; unknown owner refused; exact remote accepted; unnamed lease refused; doors at their device\'s address; shell syntax valid')
