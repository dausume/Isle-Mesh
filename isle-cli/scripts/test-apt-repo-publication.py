#!/usr/bin/env python3
"""Publish fixture DEB inventories with fake signing/index/server commands.

No host repository, service, network or package installation is touched.
"""
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest

SOURCE=Path(__file__).with_name('apt-repo.sh')


class AptPublication(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.etc=self.root/'etc'
        self.platform=self.root/'platform';self.platform.mkdir()
        self.dependencies=self.root/'dependencies';self.dependencies.mkdir()
        self.repo=self.etc/'isle-mesh/apt/repo';self.repo.mkdir(parents=True)
        self.bin=self.root/'bin';self.bin.mkdir();self.log=self.root/'calls'
        self.script=self.root/'apt-repo.sh'
        self.script.write_text(SOURCE.read_text().replace('/etc/',str(self.etc)+'/'))
        ca=self.etc/'isle-mesh/ca/isle-root.crt';ca.parent.mkdir();ca.write_text('fixture CA')
        stub='''#!/usr/bin/python3
import hashlib,json,os,pathlib,subprocess,sys,time
name=pathlib.Path(sys.argv[0]).name;a=sys.argv[1:]
with open(os.environ['CALLS'],'a') as out:out.write(json.dumps([name]+a)+'\\n')
if name=='sudo':
 env=dict(os.environ)
 while a and '=' in a[0]:
  key,value=a.pop(0).split('=',1);env[key]=value
 sys.exit(subprocess.run(a,env=env).returncode)
elif name=='getent':
 print('127.0.0.1 apt.isle' if a[0]=='hosts' else 'fixture:x:1000:1000::'+os.environ['FIXTURE']+':/bin/bash')
elif name=='dpkg-deb':
 d=json.loads(pathlib.Path(a[-1]).read_text())
 for key in ('Package','Version','Architecture'):print(key+': '+d[key])
elif name=='dpkg-scanpackages':
 assert '--multiversion' in a
 for path in sorted(pathlib.Path('.').glob('*.deb')):
  d=json.loads(path.read_text())
  for key in ('Package','Version','Architecture'):print(key+': '+d[key])
  print('Filename: ./'+path.name+'\\n')
elif name=='apt-ftparchive':
 print('Origin: isle-mesh\\nSuite: isle')
 if 'APT::FTPArchive::Release::Acquire-By-Hash=yes' in a:print('Acquire-By-Hash: yes')
 print('SHA256:')
 for name in ('Packages','Packages.gz'):
  p=pathlib.Path(name);data=p.read_bytes();print(' '+hashlib.sha256(data).hexdigest()+' '+str(len(data))+' '+name)
elif name=='gpg':
 if '--list-secret-keys' in a:print('apt@isle')
 elif '-o' in a:
  if '--clearsign' in a and os.environ.get('FAIL_SIGN'):sys.exit(1)
  if '--clearsign' in a and os.environ.get('SLOW_SIGN'):time.sleep(0.05)
  target=pathlib.Path(a[a.index('-o')+1])
  target.write_text('fixture signed output'+('\\n'+pathlib.Path(a[-1]).read_text() if ('--clearsign' in a or '-abs' in a) else ''))
elif name=='openssl':print('sha256 Fingerprint=00:11:22')
elif name=='docker':
 if a[:2]==['ps','--format']:print('isle-apt-apt-1')
 else:sys.exit('unexpected Docker operation')
elif name in ('curl','isle','systemctl'):sys.exit('FORBIDDEN live operation')
else:sys.exit(90)
'''
        for name in ('sudo','getent','dpkg-deb','dpkg-scanpackages','apt-ftparchive','gpg','openssl','docker','curl','isle','systemctl'):
            path=self.bin/name;path.write_text(stub);path.chmod(0o755)
        self.env=dict(os.environ,PATH=str(self.bin)+':'+os.environ['PATH'],CALLS=str(self.log),FIXTURE=str(self.root))
    def deb(self,directory,filename,package,version,architecture='amd64',content='fixture'):
        path=directory/filename;path.write_text(json.dumps({'Package':package,'Version':version,'Architecture':architecture,'payload':content}));return path
    def publish(self,*args,**flags):
        return subprocess.run(['bash',str(self.script),'publish','--from',str(self.platform),*args],env=dict(self.env,**flags),capture_output=True,text=True,timeout=15)
    def calls(self):return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
    def test_platform_and_full_dependency_inventory_keep_versions_and_arches(self):
        self.deb(self.platform,'platform_2_amd64.deb','polari-platform','2')
        self.deb(self.dependencies,'libfoo_1_all.deb','libfoo','1','all')
        self.deb(self.dependencies,'libfoo_1.0~rc1_amd64.deb','libfoo','1.0~rc1')
        self.deb(self.dependencies,'libfoo_1.0_amd64.deb','libfoo','1.0')
        self.deb(self.dependencies,'libfoo_1:0.5_arm64.deb','libfoo','1:0.5','arm64')
        self.deb(self.repo,'libfoo_0.9_amd64.deb','libfoo','0.9')
        result=self.publish('--dependencies-from',str(self.dependencies));self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(len(list(self.repo.glob('*.deb'))),6)
        index=(self.repo/'Packages').read_text()
        for version in ('0.9','1.0~rc1','1.0','1:0.5'):self.assertIn('Version: '+version+'\n',index)
        self.assertIn('Architecture: arm64',index);self.assertIn('Package: polari-platform',index)
        self.assertEqual(gzip.decompress((self.repo/'Packages.gz').read_bytes()).decode(),index)
        self.assertTrue(all((self.repo/name).is_file() for name in ('Release','Release.gpg','InRelease','isle-archive-keyring.gpg','isle-root.crt')))
        self.assertTrue(any(call[0]=='gpg' and '--clearsign' in call for call in self.calls()))
        self.assertFalse(any(call[0] in ('curl','isle','systemctl') for call in self.calls()))
    def assert_hashes_available(self,release,canonical=False):
        self.assertIn('Acquire-By-Hash: yes',release)
        rows=[]
        for row in release.split('SHA256:\n',1)[1].splitlines():
            if not row.startswith(' '):break
            rows.append(row)
        self.assertEqual(len(rows),2)
        for row in rows:
            digest,size,name=row.split()
            blob=(self.repo/'by-hash/SHA256'/digest).read_bytes()
            self.assertEqual(hashlib.sha256(blob).hexdigest(),digest)
            self.assertEqual(len(blob),int(size))
            self.assertIn(name,('Packages','Packages.gz'))
            self.assertEqual((self.repo/'by-hash/SHA512'/hashlib.sha512(blob).hexdigest()).read_bytes(),blob)
            if canonical:self.assertEqual(hashlib.sha256((self.repo/name).read_bytes()).hexdigest(),digest)
    def test_by_hash_retains_previous_generation_and_clients_never_see_missing_indexes(self):
        self.deb(self.platform,'platform_1_all.deb','polari-platform','1','all')
        first=self.publish();self.assertEqual(first.returncode,0,first.stderr)
        previous=(self.repo/'InRelease').read_text();self.assert_hashes_available(previous)
        old_blobs={p.name:p.read_bytes() for p in (self.repo/'by-hash/SHA256').iterdir()}
        self.deb(self.platform,'platform_2_all.deb','polari-platform','2','all')
        errors=[];observations=[];stop=threading.Event()
        def reader():
            while not stop.is_set():
                try:
                    release=(self.repo/'InRelease').read_text()
                    self.assert_hashes_available(release,canonical=release!=previous)
                    observations.append(release)
                except BaseException as error:errors.append(error)
                time.sleep(0.001)
        worker=threading.Thread(target=reader);worker.start()
        try:second=self.publish(SLOW_SIGN='1')
        finally:stop.set();worker.join(timeout=2)
        self.assertEqual(second.returncode,0,second.stderr)
        self.assertFalse(errors,errors)
        self.assertTrue(observations)
        current=(self.repo/'InRelease').read_text();self.assertNotEqual(current,previous)
        self.assert_hashes_available(current,canonical=True)
        for name,data in old_blobs.items():self.assertEqual((self.repo/'by-hash/SHA256'/name).read_bytes(),data)
        self.assertFalse(any(p.name.startswith('.publish-') for p in self.repo.rglob('*')))
    def test_signing_failure_keeps_all_committed_metadata_unchanged(self):
        self.deb(self.platform,'platform_1_all.deb','polari-platform','1','all')
        first=self.publish();self.assertEqual(first.returncode,0,first.stderr)
        names=('Packages','Packages.gz','Release','Release.gpg','InRelease')
        committed={name:(self.repo/name).read_bytes() for name in names}
        self.deb(self.platform,'platform_2_all.deb','polari-platform','2','all')
        second=self.publish(FAIL_SIGN='1');self.assertNotEqual(second.returncode,0)
        for name,data in committed.items():self.assertEqual((self.repo/name).read_bytes(),data)
        self.assert_hashes_available((self.repo/'InRelease').read_text())
    def test_existing_callers_publish_platform_without_dependency_flag(self):
        self.deb(self.platform,'platform_2_all.deb','polari-platform','2','all')
        result=self.publish();self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('Package: polari-platform',(self.repo/'Packages').read_text())
    def test_same_filename_different_content_refused_before_publication(self):
        self.deb(self.platform,'collision.deb','package-a','1')
        self.deb(self.dependencies,'collision.deb','package-b','2')
        result=self.publish('--dependencies-from',str(self.dependencies));self.assertNotEqual(result.returncode,0)
        self.assertIn('filename collision',result.stderr)
        self.assertEqual(list(self.repo.iterdir()),[])
        self.assertFalse(any(call[0]=='gpg' for call in self.calls()))
    def test_same_identity_different_bytes_refused_even_with_different_filename(self):
        self.deb(self.platform,'one.deb','package-a','1',content='one')
        self.deb(self.dependencies,'two.deb','package-a','1',content='two')
        result=self.publish('--dependencies-from',str(self.dependencies));self.assertNotEqual(result.returncode,0)
        self.assertIn('identical package/version/architecture',result.stderr)
        self.assertEqual(list(self.repo.iterdir()),[])
    def test_identical_file_collision_is_idempotent(self):
        source=self.deb(self.platform,'same.deb','package-a','1')
        (self.dependencies/'same.deb').write_bytes(source.read_bytes())
        result=self.publish('--dependencies-from',str(self.dependencies));self.assertEqual(result.returncode,0,result.stderr)
        original=(self.repo/'same.deb').read_bytes()
        result=self.publish('--dependencies-from',str(self.dependencies));self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((self.repo/'same.deb').read_bytes(),original)
        self.assertEqual(len(list(self.repo.glob('*.deb'))),1)
    def test_explicit_missing_or_empty_dependency_directory_refused(self):
        self.deb(self.platform,'platform.deb','polari-platform','1')
        for folder in (self.dependencies,self.root/'absent'):
            with self.subTest(folder=folder):
                result=self.publish('--dependencies-from',str(folder));self.assertNotEqual(result.returncode,0)
                self.assertEqual(list(self.repo.iterdir()),[])
    def test_existing_conflicting_release_preserved_without_partial_copy(self):
        existing=self.deb(self.repo,'existing.deb','package-a','1',content='old').read_bytes()
        self.deb(self.platform,'new-name.deb','package-a','1',content='new')
        result=self.publish();self.assertNotEqual(result.returncode,0)
        self.assertEqual((self.repo/'existing.deb').read_bytes(),existing)
        self.assertEqual([p.name for p in self.repo.iterdir()],['existing.deb'])


if __name__=='__main__':unittest.main()
