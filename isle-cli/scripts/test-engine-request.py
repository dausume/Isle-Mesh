import json,subprocess,unittest
from unittest.mock import patch
from engine_sync import request
class Request(unittest.TestCase):
 def test_bounded_mutation_wait_and_verified_transport(self):
  for args,limit in [((None,False),'15'),(({},False),'60'),((None,True),'60')]:
   with patch('engine_sync.subprocess.run',return_value=subprocess.CompletedProcess([],0,'{"ok":true}','')) as run:
    request('https://api.polari.isle/api/islemesh/engines/sync',args[0],args[1])
    argv=run.call_args.args[0];self.assertEqual(argv[argv.index('--max-time')+1],limit)
    self.assertIn('--http1.1',argv);self.assertNotIn('-k',argv);self.assertNotIn('--retry',argv)
 def test_timeout_stays_visible_without_automatic_mutation_replay(self):
  with patch('engine_sync.subprocess.run',side_effect=subprocess.CalledProcessError(28,['curl'])) as run:
   with self.assertRaises(subprocess.CalledProcessError):request('https://api.polari.isle/api/islemesh/ingest/registry',{})
   self.assertEqual(run.call_count,1)
if __name__=='__main__':unittest.main()
