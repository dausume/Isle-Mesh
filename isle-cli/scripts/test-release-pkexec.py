import importlib.machinery,importlib.util,tempfile,unittest
from pathlib import Path
path=str(Path(__file__).with_name('release-pkexec'))
spec=importlib.util.spec_from_loader('rp',importlib.machinery.SourceFileLoader('rp',path));rp=importlib.util.module_from_spec(spec);spec.loader.exec_module(rp)
H='a'*64
class Pkexec(unittest.TestCase):
    def setUp(self):self.t=tempfile.TemporaryDirectory();self.d=self.t.name
    def tearDown(self):self.t.cleanup()
    def ok(self,*a):return rp.validate(list(a))
    def bad(self,*a):
        with self.assertRaises(ValueError):rp.validate(list(a))
    def test_accepted(self):
        self.assertEqual(self.ok('run',self.d,'--expect',H),['run',self.d,'--expect',H,'--yes'])
        self.assertEqual(self.ok('run',self.d,'--expect',H,'--no-wait')[-1],'--no-wait')
        self.assertEqual(self.ok('run',self.d,'--expect',H,'--wait','720')[-2:],['--wait','720'])
        self.assertEqual(self.ok('run',self.d,'--expect',H,'--wait','1')[-2:],['--wait','1'])
    def test_other_verbs(self):
        for v in ('follow','status','approval-key','--help','','Run'):self.bad(v,self.d,'--expect',H)
        self.bad()
    def test_relative_and_dotdot(self):
        self.bad('run','packet','--expect',H);self.bad('run','./x','--expect',H)
        self.bad('run',self.d+'/../etc','--expect',H);self.bad('run','/..','--expect',H)
    def test_missing_directory(self):self.bad('run',self.d+'/nope','--expect',H)
    def test_option_looking_directory(self):
        for x in ('--yes','-h','--expect'):self.bad('run',x,'--expect',H)
    def test_expect(self):
        self.bad('run',self.d);self.bad('run',self.d,'--expect');self.bad('run',self.d,'--no-wait','--expect',H)
        for h in ('A'*64,'a'*63,'a'*65,'g'*64,'a'*64+'\n',''):self.bad('run',self.d,'--expect',h)
    def test_extra_and_injected(self):
        self.bad('run',self.d,'--expect',H,'--yes');self.bad('run',self.d,'--yes','--expect',H)
        self.bad('run',self.d,'--expect',H,'--no-wait','--yes');self.bad('run',self.d,'--expect',H,'extra')
        self.bad('run',self.d,'--expect',H,'--wait');self.bad('run',self.d,'--expect',H,'--wait','5','--no-wait')
    def test_wait_bounds(self):
        for w in ('0','721','-1','07','x','1.5',''):self.bad('run',self.d,'--expect',H,'--wait',w)
    def test_main_refuses_with_exit_2(self):self.assertEqual(rp.main(['follow']),2)
if __name__=='__main__':unittest.main()
