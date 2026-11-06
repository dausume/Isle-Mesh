#!/usr/bin/env python3
import json,os,subprocess,tempfile,unittest
from pathlib import Path
SOURCE=Path(__file__).resolve().parents[2]/'isle-agent/isle-host-agent/isle-host-agent-relay.sh'
class Relay(unittest.TestCase):
 def test_unchanged_service_does_not_rewrite_and_changed_address_does(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);registry=root/'registry.json'
   registry.write_text(json.dumps({'apps':{'health':{'name':'health'}},'services':{'api.polari.isle':{'addresses':['127.0.0.1'],'port':443,'protocol':'https','created_at':'original','updated_at':'original'}}}))
   script=root/'test.sh';script.write_text(SOURCE.read_text().split('trap cleanup SIGTERM')[0]+'\nlog() { :; }; avahi-resolve() { return 1; }; update_registry api.polari.isle "${TEST_IP:-127.0.0.1}"\n')
   before=registry.stat();content=registry.read_bytes()
   env=dict(os.environ,REGISTRY_FILE=str(registry))
   subprocess.run(['bash',str(script)],env=env,check=True)
   self.assertEqual(content,registry.read_bytes());self.assertEqual(before.st_mtime_ns,registry.stat().st_mtime_ns)
   env['TEST_IP']='10.10.0.2';subprocess.run(['bash',str(script)],env=env,check=True)
   row=json.loads(registry.read_text())['services']['api.polari.isle'];self.assertEqual(row['addresses'],['10.10.0.2']);self.assertEqual(row['created_at'],'original');self.assertEqual(before.st_ino,registry.stat().st_ino)
if __name__=='__main__':unittest.main()
