#!/usr/bin/env python3
"""Run lease hook and health check with fake interface/tools; no live networking."""
import os,subprocess,tempfile,unittest,time
from pathlib import Path
AGENT=Path(__file__).resolve().parents[2]/'isle-agent'/'isle-remote-agent'
class DHCP(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.state=self.root/'lease';self.pid=self.root/'pid';self.pid.write_text(str(os.getpid()))
  for name in ['lease-hook.sh','remote-health.sh']:
   text=(AGENT/name).read_text().replace('/run/isle-lease',str(self.state)).replace('/run/isle-dhcp.pid',str(self.pid))
   (self.root/name).write_text(text)
  for name,body in {'ip':'echo "$*" >> "$CALLS"; [ "$1 $2" != "-4 -o" ] || echo "2: eth9 inet ${ACTUAL_IP:-10.10.0.147}/24 scope global"','ifconfig':'echo "$*" >> "$CALLS"','wget':'exit "${HTTP_RESULT:-0}"'}.items():
   p=self.root/name;p.write_text('#!/bin/sh\n'+body+'\n');p.chmod(0o755)
 def run_script(self,name,*args,**extra):
  env=dict(os.environ,PATH=str(self.root)+':'+os.environ['PATH'],CALLS=str(self.root/'calls'),interface='eth9',ip='10.10.0.147',subnet='255.255.255.0',lease='60');env.update(extra)
  return subprocess.run(['sh',str(self.root/name),*args],env=env,capture_output=True)
 def test_renew_deconfig_and_no_resolver_mutation(self):
  self.assertEqual(self.run_script('lease-hook.sh','bound').returncode,0)
  self.assertEqual(self.run_script('remote-health.sh').returncode,0)
  self.assertEqual(self.run_script('lease-hook.sh','renew',ip='10.10.0.148').returncode,0)
  self.assertNotEqual(self.run_script('remote-health.sh').returncode,0)
  self.assertEqual(self.run_script('remote-health.sh',ACTUAL_IP='10.10.0.148').returncode,0)
  self.assertEqual(self.run_script('lease-hook.sh','deconfig').returncode,0)
  self.assertFalse(self.state.exists());self.assertNotEqual(self.run_script('remote-health.sh').returncode,0)
  calls=(self.root/'calls').read_text();self.assertNotIn('route',calls);self.assertNotIn('eth0',calls)
 def test_startup_uses_canonical_identity_and_exact_mac(self):
  net=self.root/'net';(net/'eth3').mkdir(parents=True);(net/'eth9').mkdir()
  (net/'eth3'/'address').write_text('02:00:00:00:00:03');(net/'eth9'/'address').write_text('02:00:00:00:00:09')
  name=self.root/'device-name';name.write_text('econ-core\n')
  source=(AGENT/'remote-entrypoint.sh').read_text().split('# --- Step 2:')[0]
  source=source.replace('/sys/class/net',str(net)).replace('/etc/isle-mesh/device-name',str(name)).replace('/run/isle-lease',str(self.state)).replace('/run/isle-dhcp.pid',str(self.pid))
  source=source.replace('    sleep 1', '    sleep 0.01')
  source+='\nkill "$DHCP_PID"; wait "$DHCP_PID" 2>/dev/null || true\n'
  (self.root/'start.sh').write_text(source)
  stub=self.root/'udhcpc';stub.write_text('#!/bin/sh\necho "$*" > "$CALLS"\nsleep 0.55\nprintf "eth9 10.10.0.147 9999999999\\n" > "$STATE"\nexec sleep 10\n');stub.chmod(0o755)
  result=self.run_script('start.sh',VIRTUAL_MAC='02:00:00:00:00:09',ISLE_HOSTNAME='old-os-name',STATE=str(self.state))
  self.assertEqual(result.returncode,0,result.stderr)
  args=(self.root/'calls').read_text().split();self.assertIn('hostname:econ-core',args);self.assertNotIn('-q',args);self.assertNotIn('-n',args);self.assertIn('-f',args);self.assertEqual(args[args.index('-i')+1],'eth9')
  self.assertNotEqual(self.run_script('start.sh',VIRTUAL_MAC='02:00:00:00:00:99').returncode,0)
 def test_expiry_dead_client_and_nginx(self):
  self.run_script('lease-hook.sh','bound')
  self.assertNotEqual(self.run_script('remote-health.sh',HTTP_RESULT='1').returncode,0)
  self.state.write_text('eth9 10.10.0.147 1\n');self.assertNotEqual(self.run_script('remote-health.sh').returncode,0)
  self.run_script('lease-hook.sh','bound');self.pid.write_text('99999999');self.assertNotEqual(self.run_script('remote-health.sh').returncode,0)
if __name__=='__main__':unittest.main()
