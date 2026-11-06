#!/usr/bin/env python3
"""Real update -> Bash reconcile boundary, with isolated downstream fixtures.
Run with sudo: reconcile.sh is copied byte-for-byte, including its root guard.
No subprocess.run mock, network call, real apt or host reconciliation is used.
"""
import hashlib,json,os,shutil,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import update
import member_preflight
import runtime_reconcile

@unittest.skipUnless(os.geteuid()==0,'Run sudo python3 test-update-shell-boundary.py for the real root guard')
class ShellBoundary(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name);self.ident='c'*32
  self.directory=self.root/self.ident;self.directory.mkdir()
  self.trace=self.root/'trace';self.bin=self.root/'bin';self.bin.mkdir()
  p=patch.object(member_preflight,'check',return_value={'device':'fixture','mode':'core'});p.start();self.addCleanup(p.stop)
  p=patch.object(member_preflight,'repair',return_value={'device':'fixture','mode':'core'});p.start();self.addCleanup(p.stop)
  self.source=Path(__file__).with_name('reconcile.sh')
  shutil.copyfile(self.source,self.root/'reconcile.sh')
  apt=self.bin/'apt-get';apt.write_text('#!/bin/sh\nprintf "apt\\n" >> "$BOUNDARY_TRACE"\n');apt.chmod(0o755)
  for name in ('member_preflight','host_network','runtime_reconcile','provider_reconcile','engine_sync','capabilities'):
   (self.root/(name+'.py')).write_text('import json,os,sys\nfrom pathlib import Path\nassert os.environ["ISLE_OPERATION_ID"]=="'+self.ident+'"\nwith Path(os.environ["BOUNDARY_TRACE"]).open("a") as f:f.write(json.dumps(["'+name+'",*sys.argv[1:]])+"\\n")\nsys.exit(int(os.environ.get("BOUNDARY_RUNTIME_EXIT","0")) if "'+name+'"=="runtime_reconcile" else 0)\n')
  (self.root/'trust.sh').write_text('printf "trust %s\\n" "$1" >> "$BOUNDARY_TRACE"\n')
  (self.root/'hosts-reconcile.sh').write_text('printf "hosts\\n" >> "$BOUNDARY_TRACE"\n')
  blob=b'fixture-deb'
  self.packages=[dict(name=n,version='0.1.1',file=n+'_0.1.1_all.deb',sha256=hashlib.sha256(blob).hexdigest()) for n in sorted(update.PACKAGES)]
  self.release=dict(packages=self.packages,id=hashlib.sha256(json.dumps(self.packages,sort_keys=True).encode()).hexdigest())
  for item in self.packages:(self.directory/item['file']).write_bytes(blob)
  def metadata(argv):
   self.assertEqual(argv[:2],['dpkg-deb','-f'])
   item=next(p for p in self.packages if argv[2].endswith(p['file']))
   return item['name'] if argv[3]=='Package' else item['version']
  for obj,key,value in [(update,'__file__',str(self.root/'update.py')),(update,'OP_ROOT',self.root),(runtime_reconcile,'DESIRED',self.root/'desired.json'),(update,'stage',lambda *a:None)]:
   p=patch.object(obj,key,value);p.start();self.addCleanup(p.stop)
  for key,kwargs in [('fetch',dict(return_value=self.release)),('selected_packages',dict(return_value=self.packages)),('installed',dict(side_effect=[None]*3+['0.1.1']*3)),('command',dict(side_effect=metadata))]:
   p=patch.object(update,key,**kwargs);p.start();self.addCleanup(p.stop)
  p=patch.dict(os.environ,PATH=str(self.bin)+':'+os.environ['PATH'],ISLE_OPERATION_ID=self.ident,BOUNDARY_TRACE=str(self.trace));p.start();self.addCleanup(p.stop)
 def test_update_reaches_real_reconcile_with_inherited_operation(self):
  self.assertEqual((self.root/'reconcile.sh').read_bytes(),self.source.read_bytes())
  self.assertEqual(update.main(['apply',self.release['id']]),0)
  lines=self.trace.read_text().splitlines()
  self.assertEqual(lines,['apt','["member_preflight", "--repair"]','["runtime_reconcile"]','["provider_reconcile"]','hosts','["engine_sync"]','["capabilities", "status", "--publish", "https://api.polari.isle"]'])
  self.trace.unlink()
  bad=subprocess.run(['bash',str(self.root/'reconcile.sh')],capture_output=True,text=True)
  self.assertNotEqual(bad.returncode,0);self.assertIn('Invalid reconcile context',bad.stdout)
  self.assertFalse(self.trace.exists())
 def test_real_shell_propagates_helper_failure_without_later_mutations(self):
  with patch.dict(os.environ,BOUNDARY_RUNTIME_EXIT='7'):
   self.assertEqual(update.main(['apply',self.release['id']]),7)
  self.assertEqual(self.trace.read_text().splitlines(),['apt','["member_preflight", "--repair"]','["runtime_reconcile"]'])

if __name__=='__main__':unittest.main()
