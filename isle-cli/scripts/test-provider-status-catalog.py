#!/usr/bin/env python3
import contextlib,importlib.util,io,json,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import catalog_show
spec=importlib.util.spec_from_file_location('app_status',Path(__file__).with_name('app-status.py'));status=importlib.util.module_from_spec(spec);spec.loader.exec_module(status)
class Checks(unittest.TestCase):
 def test_catalog_absent_invalid_and_transport_are_distinct(self):
  for code,body,message in [(22,'{"ok":false,"error":"no catalog entry electronics"}','no catalog entry'),(0,'','empty or invalid'),(6,'','transport/TLS')]:
   with patch.object(catalog_show.subprocess,'run',return_value=subprocess.CompletedProcess([],code,body)),self.assertRaisesRegex(ValueError,message):catalog_show.show('electronics')
 def test_two_catalog_definitions_use_same_reader(self):
  for name in ('whoami','board-engines'):
   body=json.dumps({'ok':True,'entry':{'title':name,'kind':'mesh-app','install_plan':{'ok':True,'steps':['isle store install '+name]}}})
   with patch.object(catalog_show.subprocess,'run',return_value=subprocess.CompletedProcess([],0,body)) as run,contextlib.redirect_stdout(io.StringIO()) as out:
    catalog_show.show(name)
    self.assertIn('installable: True',out.getvalue());self.assertTrue(run.call_args.args[0][-1].endswith('/'+name))
 def test_provider_runtime_status_and_backend_failure(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);provider=root/'providers'/'electronics';provider.mkdir(parents=True)
   (provider/'deployment.json').write_text(json.dumps({'name':'electronics','role':'frontend','backend_url':'https://api.worker.isle'}));(provider/'docker-compose.yml').write_text('{}')
   reg=root/'registry.json';reg.write_text(json.dumps({'apps':{'electronics':{'domain':'electronics.isle'}}}))
   def run(argv):
    if argv[-1]=='--services':return subprocess.CompletedProcess(argv,0,'frontend\n')
    if argv[-1]=='json':return subprocess.CompletedProcess(argv,0,'{"Service":"frontend","State":"running","Health":""}')
    return subprocess.CompletedProcess(argv,0,'{"ok":true,"phase":"online"}')
   with patch.object(status,'APPS',root/'apps'),patch.object(status,'PROVIDERS',root/'providers'),patch.object(status,'REGISTRY',reg),patch.object(status,'latest',return_value=None),patch.object(status,'run',side_effect=run):
    d=status.status('electronics');self.assertEqual(d['state'],'ready');self.assertEqual(d['kind'],'polari-instance');self.assertTrue(d['backendReady'])
    def failed(argv):return subprocess.CompletedProcess(argv,60,'') if argv[-1].endswith('/api/health') else run(argv)
    with patch.object(status,'run',side_effect=failed):
     d=status.status('electronics');self.assertTrue(d['runtimeStarted']);self.assertFalse(d['routeReady']);self.assertFalse(d['deployed'])
if __name__=='__main__':unittest.main()
