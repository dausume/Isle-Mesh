import importlib.util
from pathlib import Path
import subprocess
import unittest
ROOT=Path(__file__).parent
spec=importlib.util.spec_from_file_location('config',ROOT/'polari-component-config.py');c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)

class Components(unittest.TestCase):
    def make(self,role,**kw):return c.generate('compute','islemesh,electronics',role,'/tmp/test-instance',images={'backend':'sha256:'+'a'*64,'frontend':'sha256:'+'b'*64},dns='10.10.0.1',**kw)
    def test_backend_only_no_frontend_image_or_volume(self):
        compose,runtime,_=self.make('backend')
        self.assertEqual(set(compose['services']),{'backend'})
        self.assertIn('backend-data',compose['volumes'])
        self.assertIn('https://polari.isle',compose['services']['backend']['environment']['CORS_ORIGINS'])
    def test_frontend_only_uses_remote_backend_without_database(self):
        compose,runtime,_=self.make('frontend',backend_url='https://api.compute.isle:8443')
        self.assertEqual(set(compose['services']),{'frontend'})
        self.assertNotIn('volumes',compose)
        self.assertNotIn('depends_on',compose['services']['frontend'])
        self.assertEqual(runtime['backend']['https']['port'],'8443')
    def test_frontend_requires_explicit_backend(self):
        with self.assertRaises(ValueError):self.make('frontend')
    def test_combined_retains_both_services(self):
        compose,_,_=self.make('both');self.assertEqual(set(compose['services']),{'frontend','backend'})
    def test_invalid_origins_rejected(self):
        for url in ['http://api.x.isle','https://user:pass@api.x.isle','https://api.x.isle/path','https://api.x.isle/?x=y']:
            with self.subTest(url=url),self.assertRaises(ValueError):self.make('frontend',backend_url=url)
    def test_sample_defaults_off_and_explicit_choice_controls_deployment(self):
        # Source only function definitions and replace every infrastructure action.
        stubs='\n'.join(f'{name}() {{ :; }}' for name in ['check_prerequisites','ensure_isle_dns','setup_mdns_system','setup_router','add_connected_cables','setup_agent','setup_discovery','show_completion'])
        for args,expected in [('--without-sample',False),('',False),('--with-sample',True)]:
            script=f'source "{ROOT}/create.sh"\n{stubs}\nisle_offline() {{ return 1; }}\nSKIP_ROUTER=true\nsetup_sample_app() {{ echo SAMPLE_REQUESTED; }}\nmain {args}'
            result=subprocess.run(['bash','-c',script],stdin=subprocess.DEVNULL,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual('SAMPLE_REQUESTED' in result.stdout,expected)

if __name__=='__main__':unittest.main()
