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
 def test_a_server_that_says_it_is_not_ready_is_asked_again_and_only_that(self):
  busy=subprocess.CalledProcessError(22,['curl'],'','curl: (22) The requested URL returned error: 503\n')
  done=subprocess.CompletedProcess([],0,'{"ok":true}','')
  slept=[]
  with patch('engine_sync.subprocess.run',side_effect=[busy,busy,done]) as run:
   self.assertEqual(request('https://api.polari.isle/api/islemesh/ingest/registry',{},sleep=slept.append),{'ok':True})
   self.assertEqual((run.call_count,slept),(3,[3,3]))
  for code,text in ((22,'curl: (22) The requested URL returned error: 500\n'),(22,'curl: (22) The requested URL returned error: 404\n'),(7,'refused')):
   with patch('engine_sync.subprocess.run',side_effect=subprocess.CalledProcessError(code,['curl'],'',text)) as run:
    with self.assertRaises(subprocess.CalledProcessError):request('https://api.polari.isle/api/islemesh/ingest/registry',{},sleep=slept.append)
    self.assertEqual(run.call_count,1)
 def test_the_wait_for_a_server_that_is_not_ready_ends(self):
  busy=subprocess.CalledProcessError(22,['curl'],'','curl: (22) The requested URL returned error: 503\n')
  now=[0]
  def sleep(s):now[0]+=s
  with patch('engine_sync.subprocess.run',side_effect=busy) as run:
   with self.assertRaises(subprocess.CalledProcessError):request('https://api.polari.isle/api/islemesh/ingest/registry',{},wait=9,sleep=sleep,clock=lambda:now[0])
   self.assertEqual(run.call_count,4)
if __name__=='__main__':unittest.main()
