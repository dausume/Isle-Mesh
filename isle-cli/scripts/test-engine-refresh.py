#!/usr/bin/env python3
"""Shared refresh routing; only temporary state and mocked transport."""
import contextlib,io,json,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import engine_sync as engines
class Refresh(unittest.TestCase):
 def test_announces_to_core_then_refreshes_each_local_backend_without_mirror(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);apps=root/'apps';providers=root/'providers'
   app=apps/'worker';app.mkdir(parents=True);(app/'engine.json').write_text(json.dumps({'provides':'board','url':'https://worker.isle'}))
   for name,role,url in [('one','backend','https://api.one.isle'),('two','both','https://api.two.isle'),('duplicate','backend','https://api.one.isle/'),('viewer','frontend','https://api.external.isle')]:
    d=providers/name;d.mkdir(parents=True);(d/'deployment.json').write_text(json.dumps({'role':role,'backend_url':url}))
   def response(url,payload=None,post=False):
    if url.endswith('/catalog'):return {'ok':True,'entries':[]}
    if url.endswith('/engines/sync'):return {'ok':True,'bound':[],'unbound':[{'engine':'other@cad','note':'module absent'}],'words':'recorded, not functional readiness'}
    return {'ok':True}
   output=io.StringIO()
   with patch.object(engines,'APPS',apps),patch.object(engines,'PROVIDERS',providers),patch.object(engines,'identity',return_value='member-one'),patch.object(engines,'request',side_effect=response) as request,contextlib.redirect_stdout(output):engines.sync()
   calls=request.call_args_list
   self.assertEqual(calls[0].args[0],'https://api.polari.isle/api/islemesh/ingest/engine');self.assertEqual(calls[0].args[1]['device'],'member-one')
   self.assertEqual([c.args[0] for c in calls[2:]],['https://api.one.isle/api/islemesh/engines/sync','https://api.two.isle/api/islemesh/engines/sync'])
   self.assertTrue(all(c.kwargs=={'post':True} and len(c.args)==1 for c in calls[2:]))
   self.assertIn('module absent',output.getvalue());self.assertNotIn('"ready": true',output.getvalue())
 def test_refresh_uses_verified_http11_post_without_caller_rows(self):
  with patch.object(engines.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'{"ok":true}')) as run:
   engines.request('https://api.one.isle/api/islemesh/engines/sync',post=True)
  args=run.call_args.args[0]
  self.assertIn('--http1.1',args);self.assertEqual(args[args.index('--request')+1],'POST');self.assertNotIn('-k',args);self.assertNotIn('--data-binary',args)
 def test_refused_or_unreachable_refresh_fails_without_ingest_fallback(self):
  with patch.object(engines.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'{"ok":false,"words":"refused"}')):
   with self.assertRaises(ValueError):engines.request('https://api.one.isle/api/islemesh/engines/sync',post=True)
  with patch.object(engines.subprocess,'run',side_effect=subprocess.CalledProcessError(22,['curl'])) as run:
   with self.assertRaises(subprocess.CalledProcessError):engines.request('https://api.one.isle/api/islemesh/engines/sync',post=True)
   run.assert_called_once()
if __name__=='__main__':unittest.main()
