#!/usr/bin/env python3
"""Run uninstall recovery against fake OS commands; never touch host networking."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

scripts = Path(__file__).parent
with tempfile.TemporaryDirectory(prefix='isle-uninstall-test-') as directory:
    root = Path(directory)
    fake = root / 'bin'
    fake.mkdir()
    log = root / 'calls.jsonl'
    stub = fake / 'stub'
    stub.write_text('''#!/usr/bin/env python3
import os,sys,json
from pathlib import Path
name=Path(sys.argv[0]).name
args=sys.argv[1:]
with open(os.environ['TEST_LOG'],'a') as f: f.write(json.dumps([name]+args)+'\\n')
if name=='id': print('0')
elif name=='systemctl':
 if 'is-active' in args: sys.exit(0 if args[-1]=='NetworkManager' else 1)
elif name=='nmcli':
 if args==['-g','UUID','connection','show']: print('test-uuid')
 elif 'dns-search' in ' '.join(args) and 'modify' not in args: print('corp.example,~isle')
 elif args[:3]==['-t','-f','DEVICE,STATE']: print('wlan-test:connected\\nlo:connected')
 elif 'IP4.DNS,IP6.DNS' in args: print('IP4.DNS[1]:192.0.2.53\\nIP4.DNS[2]:192.0.2.54\\nIP6.DNS[1]:2001:db8::53')
 elif 'IP4.DOMAIN,IP6.DOMAIN' in args: print('IP4.DOMAIN[1]:corp.example\\nIP4.DOMAIN[2]:~isle')
elif name=='ip':
 if args[:3]==['route','show','default'] or args[:4]==['-4','route','show','default']: print('default via 192.0.2.1 dev wlan-test')
elif name=='timeout': sys.exit(int(os.environ.get('TEST_DNS_FAIL','0')))
elif name=='dpkg-query': print('polari-complete installed\\nisle-app-demo installed\\npolari-complete-offline config-files\\nbash installed')
''')
    stub.chmod(0o755)
    for name in ['id','systemctl','nmcli','ip','resolvectl','timeout','dpkg-query']:
        (fake / name).symlink_to(stub)
    env = dict(os.environ, PATH=str(fake)+':'+os.environ['PATH'], TEST_LOG=str(log))
    etc = root / 'etc'
    (etc / 'systemd/network').mkdir(parents=True)
    (etc / 'hosts').write_text('127.0.0.1 localhost\n')
    handback = root / 'handback.sh'
    handback.write_text((scripts / 'network-handback.sh').read_text().replace('/etc/',str(etc)+'/'))
    result = subprocess.run(['bash',str(handback)],env=env,capture_output=True,text=True)
    assert result.returncode == 0, result.stdout+result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    modify = next(i for i,c in enumerate(calls) if c[:3]==['nmcli','connection','modify'])
    dns = next(i for i,c in enumerate(calls) if c[:3]==['resolvectl','dns','wlan-test'])
    assert modify < dns
    assert ['resolvectl','dns','wlan-test','192.0.2.53','192.0.2.54','2001:db8::53'] in calls
    assert ['resolvectl','domain','wlan-test','corp.example','~.'] in calls
    assert not any(c[:3]==['nmcli','connection','down'] or c[:3]==['nmcli','radio','wifi'] for c in calls)
    assert not any('NetworkManager' in c and ('restart' in c or 'stop' in c) for c in calls)
    assert all('corp.example' not in c for c in calls if c[:3]==['nmcli','connection','modify'])
    result = subprocess.run(['bash',str(handback)],env=dict(env,TEST_DNS_FAIL='1'),capture_output=True,text=True)
    assert result.returncode != 0 and 'did not verify' in result.stdout
    source = (scripts / 'uninstall.sh').read_text()
    function = source[source.index('installed_isle_packages() {'):source.index('\nis_isle_core()')]
    result = subprocess.run(['bash','-c',function+'\ninstalled_isle_packages'],env=env,capture_output=True,text=True,check=True)
    assert set(result.stdout.split())=={'polari-complete','polari-complete-offline','isle-app-demo'}
print('PASS: merged-package selection, DNS restored after domain cleanup, IPv4/IPv6 DNS, user domains preserved, no Wi-Fi flap, DNS failure returns nonzero')
