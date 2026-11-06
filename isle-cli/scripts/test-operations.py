#!/usr/bin/env python3
import importlib.util,json,os,subprocess,tempfile,unittest,sys
from pathlib import Path
from unittest.mock import patch
import operations as op
spec=importlib.util.spec_from_file_location('app_status',Path(__file__).with_name('app-status.py')); status=importlib.util.module_from_spec(spec);spec.loader.exec_module(status)
import update
import member_preflight
import runtime_reconcile
class Operations(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  p=patch.object(member_preflight,'repair',return_value={'device':'fixture','mode':'core'});p.start();self.addCleanup(p.stop)
  self.stack=[]
  p=patch.object(member_preflight,"check",return_value={"device":"fixture","mode":"core"});p.start();self.addCleanup(p.stop)
  for obj,key,value in [(op,'ROOT',self.root/'ops'),(op,'SCRIPTS',self.root),(update,'stage',lambda *args:0),(runtime_reconcile,'DESIRED',self.root/'desired.json')]:
   p=patch.object(obj,key,value);p.start();self.addCleanup(p.stop)
  p=patch('os.geteuid',return_value=0);p.start();self.addCleanup(p.stop)
  (self.root/'store.sh').write_text('''#!/bin/bash
if [ ! -e "$ISLE_OPERATION_DIR/once" ]; then
 echo pinned > "$ISLE_OPERATION_DIR/catalog.json"
 touch "$ISLE_OPERATION_DIR/once"
 exit 7
fi
test "$(cat "$ISLE_OPERATION_DIR/catalog.json")" = pinned
''')
 def test_unrelated_apps_retry_same_request_and_snapshot(self):
  for name in ['odoo','board-engines']:
   self.assertEqual(op.execute(['store','install',name,'--yes']),7)
   d=op.latest(name);self.assertEqual(d['state'],'failed');self.assertTrue(d['retryable'])
   self.assertEqual(op.execute(ident=d['id']),0)
   after=op.read(d['id']);self.assertEqual(after['request'],d['request']);self.assertEqual(after['attempt'],2)
   self.assertEqual(op.execute(ident=d['id']),0);self.assertEqual(op.read(d['id'])['attempt'],2)
 def test_real_store_two_apps_pins_catalog_and_records_stages(self):
  # Real store.sh, fake external commands only. No cwd compose or host mutation.
  source=Path(__file__).with_name('store.sh').read_text().replace('/var/lib/isle-mesh/operations',str(op.ROOT)).replace('$EUID == 0','1 == 1')
  (self.root/'store.sh').write_text(source)
  # Child stage writer uses the same temporary journal root.
  helper=Path(op.__file__).read_text().replace("Path('/var/lib/isle-mesh/operations')",repr(op.ROOT))
  helper=helper.replace('PosixPath(', 'Path(').replace('os.geteuid()!=0','False')
  (self.root/'operations.py').write_text(helper)
  bin=self.root/'bin';bin.mkdir()
  for cmd,body in {
   'docker':'echo isle-remote-agent',
   'curl':'cat "$CATALOG"',
   'isle':'echo "$*" >> "$CALLS"; if [ ! -e "$MARKER" ]; then touch "$MARKER"; exit 9; fi'
  }.items():
   f=bin/cmd;f.write_text('#!/bin/sh\n'+body+'\n');f.chmod(0o755)
  for name in ['alpha','bravo']:
   cat=self.root/(name+'.json');cat.write_text(json.dumps({'ok':True,'entry':{'kind':'mesh-app','name':name,'instances':[], 'install_plan':{'ok':True,'steps':['isle app deploy '+name+' --image example@sha256:'+'a'*64]}}}))
   env={'PATH':str(bin)+':'+os.environ['PATH'],'CATALOG':str(cat),'CALLS':str(self.root/'calls'),'MARKER':str(self.root/(name+'.once'))}
   with patch.dict(os.environ,env):
    self.assertNotEqual(op.execute(['store','install',name,'--yes']),0)
    d=op.latest(name);self.assertEqual(d['stage'],'step-1-failed')
    cat.write_text('broken newer catalog')
    self.assertEqual(op.execute(ident=d['id']),0)
    self.assertEqual(op.read(d['id'])['stage'],'step-1-complete')
  calls=(self.root/'calls').read_text();self.assertIn('app deploy alpha --image',calls);self.assertIn('app deploy bravo --image',calls)
 def test_invalid_input_and_permissions(self):
  for args in [['store','install','../bad'],['store','install','ok','--shell'],['shell','install','ok']]:
   with self.assertRaises(ValueError):op.execute(args)
  with patch('os.geteuid',return_value=1000),self.assertRaises(PermissionError):op.execute(['store','install','ok'])
 def test_interrupted_and_serialized(self):
  op.execute(['store','install','alpha']);d=op.latest('alpha');p=op.path_for(d['id']);d['state']='running';op.atomic(p/'status.json',d)
  self.assertEqual(op.read(d['id'])['state'],'interrupted')
  import fcntl
  with (op.ROOT/'app-alpha.lock').open('a+') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   with self.assertRaises(RuntimeError):op.execute(ident=d['id'])
 def test_worker_keeps_lock_if_controller_dies(self):
  import time,signal
  helper=Path(op.__file__).read_text().replace("Path('/var/lib/isle-mesh/operations')",repr(op.ROOT)).replace('PosixPath(', 'Path(').replace('os.geteuid()!=0','False')
  (self.root/'operations.py').write_text(helper)
  (self.root/'store.sh').write_text('touch "$ISLE_OPERATION_DIR/started"; sleep 2')
  proc=subprocess.Popen([sys.executable,str(self.root/'operations.py'),'run','store','install','alpha','--yes'],stdout=subprocess.DEVNULL,start_new_session=True)
  try:
   deadline=time.monotonic()+3
   while time.monotonic()<deadline:
    d=op.latest('alpha')
    if d and (op.path_for(d['id'])/'started').exists():break
    time.sleep(.02)
   self.assertIsNotNone(d)
   proc.kill();proc.wait()
   self.assertEqual(op.read(d['id'])['state'],'running')
   with self.assertRaises(RuntimeError):op.execute(ident=d['id'])
   time.sleep(2.1)
   self.assertEqual(op.read(d['id'])['state'],'interrupted')
  finally:
   try:os.killpg(proc.pid,signal.SIGKILL)
   except ProcessLookupError:pass
   proc.wait()
 def test_partial_directory_is_not_deployed(self):
  with patch.object(status,'APPS',self.root),patch.object(status,'latest',return_value=None):
   (self.root/'alpha').mkdir();(self.root/'alpha'/'docker-compose.yml').write_text('services: {}')
   with patch.object(status,'run',return_value=subprocess.CompletedProcess([],1,'','denied')):
    d=status.status('alpha');self.assertTrue(d['configured']);self.assertFalse(d['deployed']);self.assertEqual(d['state'],'staged')
 def test_running_requires_trusted_route(self):
  (self.root/'alpha').mkdir();(self.root/'alpha'/'docker-compose.yml').write_text('services: {}')
  reg=self.root/'registry';reg.write_text(json.dumps({'apps':{'alpha':{'domain':'alpha.isle'}}}))
  calls=[]
  def fake(argv):
   calls.append(argv)
   if '--services' in argv:return subprocess.CompletedProcess(argv,0,'web\n','')
   if 'ps' in argv:return subprocess.CompletedProcess(argv,0,'[{"Service":"web","State":"running","Health":"healthy"}]','')
   return subprocess.CompletedProcess(argv,7,'','unreachable')
  with patch.object(status,'APPS',self.root),patch.object(status,'REGISTRY',reg),patch.object(status,'latest',return_value=None),patch.object(status,'run',side_effect=fake):
   d=status.status('alpha');self.assertEqual(d['state'],'runtime-started');self.assertFalse(d['deployed'])
   self.assertNotIn('-k',calls[-1]);self.assertIn('https://alpha.isle/',calls[-1])
 def test_declared_worker_readiness_not_root_route(self):
  reg=self.root/'registry';reg.write_text(json.dumps({'apps':{n:{'domain':n+'.isle'} for n in ('alpha','bravo')}}))
  for name,path in [('alpha','/capability'),('bravo','/healthz')]:
   app=self.root/name;app.mkdir();(app/'docker-compose.yml').write_text('services: {}')
   (app/'release.json').write_text(json.dumps({'health':{'path':path}}));calls=[]
   def fake(argv):
    calls.append(argv)
    if '--services' in argv:return subprocess.CompletedProcess(argv,0,'web\n','')
    if 'ps' in argv:return subprocess.CompletedProcess(argv,0,'[{"Service":"web","State":"running","Health":"healthy"}]','')
    # API-only workers: / returns HTTP404, declared endpoint succeeds.
    return subprocess.CompletedProcess(argv,0 if argv[-1]=='https://'+name+'.isle'+path else 22,'','')
   with patch.object(status,'APPS',self.root),patch.object(status,'REGISTRY',reg),patch.object(status,'latest',return_value=None),patch.object(status,'run',side_effect=fake):
    d=status.status(name);self.assertTrue(d['deployed']);self.assertEqual(d['healthPath'],path)
    self.assertEqual(calls[-1][-1],'https://'+name+'.isle'+path);self.assertNotIn('-k',calls[-1])
    for invalid in ('relative','/health#fragment'):
     (app/'release.json').write_text(json.dumps({'health':{'path':invalid}}));calls.clear()
     d=status.status(name);self.assertFalse(d['deployed']);self.assertFalse(any(a[0]=='curl' for a in calls))
 def test_update_manifest_validation(self):
  packages=[dict(name=n,version='0.1.1',file=n+'_0.1.1_all.deb',sha256='a'*64) for n in sorted(update.PACKAGES)]
  import hashlib
  r=dict(packages=packages,id=hashlib.sha256(json.dumps(packages,sort_keys=True).encode()).hexdigest())
  self.assertEqual(update.validate(r),r)
  r['packages'][0]['file']='../bad.deb'
  with self.assertRaises(ValueError):update.validate(r)
 def test_update_exact_artifacts_and_checksum_failure(self):
  import hashlib
  blob=b'fixture-deb'
  packages=[dict(name=n,version='0.1.1',file=n+'_0.1.1_all.deb',sha256=hashlib.sha256(blob).hexdigest()) for n in sorted(update.PACKAGES)]
  release=dict(packages=packages,id=hashlib.sha256(json.dumps(packages,sort_keys=True).encode()).hexdigest())
  ident='c'*32;directory=self.root/ident;directory.mkdir();executed=[]
  def fake_command(argv):
   if argv[0]=='curl':Path(argv[argv.index('--output')+1]).write_bytes(blob);return ''
   p=next(p for p in packages if argv[-2].endswith(p['file']))
   return p['name'] if argv[-1]=='Package' else p['version']
  def process(argv,**kw):executed.append(argv);return subprocess.CompletedProcess(argv,0,'','')
  with patch.object(update,'OP_ROOT',self.root),patch.object(update,'fetch',return_value=release),patch.object(update,'command',side_effect=fake_command),patch.object(update,'selected_packages',return_value=packages),patch.object(update,'installed',side_effect=[None]*3+['0.1.1']*3),patch.object(update.subprocess,'run',side_effect=process),patch.dict(os.environ,ISLE_OPERATION_ID=ident):
   self.assertEqual(update.main(['apply',release['id']]),0)
  self.assertEqual(executed[0][:4],['apt-get','--no-remove','install','-y'])
  self.assertEqual(set(executed[0][4:]),{str(directory/p['file']) for p in packages})
  # Bad artifact fails before any package transaction.
  for p in packages:(directory/p['file']).unlink()
  executed.clear()
  def corrupt(argv):Path(argv[argv.index('--output')+1]).write_bytes(b'bad');return ''
  with patch.object(update,'OP_ROOT',self.root),patch.object(update,'command',side_effect=corrupt),patch.object(update,'installed',return_value=None),patch.object(update.subprocess,'run',side_effect=process),patch.dict(os.environ,ISLE_OPERATION_ID=ident):
   with self.assertRaises(ValueError):update.main(['apply',release['id']])
  self.assertEqual(executed,[])
 def test_preserve_combined_provider_update(self):
  bundle={'name':'polari-complete-offline','version':'0.1.74'}
  release={'packages':[{'name':'isle-mesh-cli'}],'bundles':{'polari-complete-offline':bundle}}
  with patch.object(update,'installed',side_effect=lambda p:'0.1.73' if p=='polari-complete-offline' else None):
   self.assertEqual(update.selected_packages(release),[bundle])
   with self.assertRaises(ValueError):update.selected_packages({'packages':release['packages']})
  with patch.object(update,'installed',return_value=None):self.assertEqual(update.selected_packages(release),release['packages'])
 def test_dpkg_empty_version_not_installed(self):
  with patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,'unknown ok not-installed\t','')):
   self.assertIsNone(update.installed('isle-mesh-cli'))
if __name__=='__main__':unittest.main()
