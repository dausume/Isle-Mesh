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

import storage_placement

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
        (self.root/'apt_index_split.py').write_text(SOURCE.with_name('apt_index_split.py').read_text())
        (self.etc/'os-release').write_text('NAME="Ubuntu"\nVERSION_CODENAME=noble\nUBUNTU_CODENAME=noble\n')
        self.deps=self.repo/'deps/noble'
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
 for field in ('Origin','Label','Suite','Codename'):
  for value in [x.split('=',1)[1] for x in a if x.startswith('APT::FTPArchive::Release::'+field+'=')]:print(field+': '+value)
 if 'APT::FTPArchive::Release::Acquire-By-Hash=yes' in a:print('Acquire-By-Hash: yes')
 print('SHA256:')
 for name in ('Packages','Packages.gz'):
  p=pathlib.Path(name);data=p.read_bytes();print(' '+hashlib.sha256(data).hexdigest()+' '+str(len(data))+' '+name)
elif name=='gpg':
 if '--list-secret-keys' in a:print('apt@isle')
 elif '-o' in a:
  if '--clearsign' in a and os.environ.get('FAIL_SIGN'):sys.exit(1)
  if '--clearsign' in a and os.environ.get('FAIL_SIGN_DEPS') and 'Origin: isle-mesh-deps' in pathlib.Path(a[-1]).read_text():sys.exit(1)
  if '--clearsign' in a and os.environ.get('SLOW_SIGN'):time.sleep(0.05)
  target=pathlib.Path(a[a.index('-o')+1])
  target.write_text('fixture signed output'+('\\n'+pathlib.Path(a[-1]).read_text() if ('--clearsign' in a or '-abs' in a) else ''))
elif name=='openssl':print('sha256 Fingerprint=00:11:22')
elif name=='docker':
 if a[:2]==['ps','--format']:
  if not os.environ.get('NO_APT_CONTAINER'):print('isle-apt-apt-1')
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
        index=(self.repo/'Packages').read_text();dependency_index=(self.deps/'Packages').read_text()
        for version in ('0.9','1.0~rc1','1.0','1:0.5'):self.assertIn('Version: '+version+'\n',dependency_index)
        self.assertIn('Architecture: arm64',dependency_index);self.assertIn('Package: polari-platform',index)
        self.assertNotIn('libfoo',index);self.assertNotIn('polari-platform',dependency_index)
        self.assertEqual(dependency_index.count('Package: '),5);self.assertEqual(index.count('Package: '),1)
        self.assertEqual(gzip.decompress((self.repo/'Packages.gz').read_bytes()).decode(),index)
        self.assertEqual(gzip.decompress((self.deps/'Packages.gz').read_bytes()).decode(),dependency_index)
        self.assertFalse(list(self.deps.glob('*.deb')),'package files stay in the repository root')
        self.assertIn('1 isle and polari packages in the main index, 5 operating system packages in deps/noble',result.stdout)
        self.assertTrue(all((self.repo/name).is_file() for name in ('Release','Release.gpg','InRelease','isle-archive-keyring.gpg','isle-root.crt')))
        self.assertTrue(any(call[0]=='gpg' and '--clearsign' in call for call in self.calls()))
        self.assertFalse(any(call[0] in ('curl','isle','systemctl') for call in self.calls()))
    def test_the_two_indexes_carry_their_own_origin_and_are_both_signed_and_hashed(self):
        self.deb(self.platform,'isle-mesh-cli_1_all.deb','isle-mesh-cli','1','all')
        self.deb(self.platform,'polari-shell-core_1_all.deb','polari-shell-core','1','all')
        self.deb(self.dependencies,'libc6_2.39_amd64.deb','libc6','2.39')
        self.deb(self.dependencies,'islet_1_amd64.deb','islet','1')       # not isle-*: an operating system package
        result=self.publish('--dependencies-from',str(self.dependencies));self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        main=(self.repo/'Release').read_text();dependency=(self.deps/'Release').read_text()
        self.assertIn('Origin: isle-mesh\n',main);self.assertIn('Suite: isle\n',main);self.assertNotIn('Codename:',main)
        self.assertIn('Origin: isle-mesh-deps\n',dependency);self.assertIn('Suite: deps/noble\n',dependency);self.assertIn('Codename: noble\n',dependency)
        self.assertEqual(sorted(l for l in (self.repo/'Packages').read_text().splitlines() if l.startswith('Package')),['Package: isle-mesh-cli','Package: polari-shell-core'])
        self.assertEqual(sorted(l for l in (self.deps/'Packages').read_text().splitlines() if l.startswith('Package')),['Package: islet','Package: libc6'])
        for base in (self.repo,self.deps):
            self.assertTrue(all((base/name).is_file() for name in ('Packages','Packages.gz','Release','Release.gpg','InRelease')))
            self.assert_hashes_available((base/'InRelease').read_text(),canonical=True,base=base)
        self.assertFalse((self.deps/'isle-archive-keyring.gpg').exists())  # one key, in the repository root
        self.assertFalse(any(p.name.startswith('.publish-') for p in self.repo.rglob('*')))
        self.assertEqual({p.stat().st_mode&0o777 for p in (self.repo/'deps',self.deps)},{0o755})
    def test_no_dependency_index_is_written_when_only_isle_and_polari_packages_exist(self):
        self.deb(self.platform,'polari-platform_1_all.deb','polari-platform','1','all')
        result=self.publish();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse((self.repo/'deps').exists())
        self.assertIn('1 isle and polari packages in the main index, 0 operating system packages',result.stdout)
    def test_a_failure_in_either_index_leaves_both_as_they_were(self):
        self.deb(self.platform,'polari-platform_1_all.deb','polari-platform','1','all')
        self.deb(self.dependencies,'libfoo_1_amd64.deb','libfoo','1')
        first=self.publish('--dependencies-from',str(self.dependencies));self.assertEqual(first.returncode,0,first.stdout+first.stderr)
        names=('Packages','Packages.gz','Release','Release.gpg','InRelease')
        committed={base/name:(base/name).read_bytes() for base in (self.repo,self.deps) for name in names}
        self.deb(self.platform,'polari-platform_2_all.deb','polari-platform','2','all')
        self.deb(self.dependencies,'libfoo_2_amd64.deb','libfoo','2')
        for flag in ('FAIL_SIGN','FAIL_SIGN_DEPS'):
            with self.subTest(flag=flag):
                second=self.publish('--dependencies-from',str(self.dependencies),**{flag:'1'});self.assertNotEqual(second.returncode,0)
                self.assertIn('InRelease signing failed'+(' (dependency set)' if flag=='FAIL_SIGN_DEPS' else ''),second.stdout+second.stderr)
                for path,data in committed.items():self.assertEqual(path.read_bytes(),data,path)
                for base in (self.repo,self.deps):self.assert_hashes_available((base/'InRelease').read_text(),canonical=True,base=base)
        third=self.publish('--dependencies-from',str(self.dependencies));self.assertEqual(third.returncode,0,third.stdout+third.stderr)
        self.assertIn('Version: 2',(self.deps/'Packages').read_text());self.assertIn('Version: 2',(self.repo/'Packages').read_text())
    def test_the_dependency_index_is_committed_before_the_main_index(self):
        text=SOURCE.read_text()
        self.assertLess(text.index('commit_index "$METADATA/deps"'),text.index('commit_index "$METADATA/main"'))
        self.deb(self.platform,'polari-platform_1_all.deb','polari-platform','1','all')
        self.deb(self.dependencies,'libfoo_1_amd64.deb','libfoo','1')
        result=self.publish('--dependencies-from',str(self.dependencies));self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertLessEqual((self.deps/'InRelease').stat().st_mtime_ns,(self.repo/'InRelease').stat().st_mtime_ns)
    def test_publication_is_refused_unchanged_without_a_release_name_or_the_split_helper(self):
        self.deb(self.platform,'polari-platform_1_all.deb','polari-platform','1','all')
        (self.etc/'os-release').write_text('NAME="Something"\nVERSION_CODENAME="../x"\n')
        result=self.publish();self.assertNotEqual(result.returncode,0)
        self.assertIn("cannot read this core's Ubuntu release name",result.stdout+result.stderr)
        self.assertEqual(list(self.repo.iterdir()),[])
        (self.etc/'os-release').write_text('VERSION_CODENAME=noble\n');(self.root/'apt_index_split.py').unlink()
        result=self.publish();self.assertNotEqual(result.returncode,0)
        self.assertIn('apt_index_split.py is missing',result.stdout+result.stderr)
        self.assertEqual(list(self.repo.iterdir()),[])
    def assert_hashes_available(self,release,canonical=False,base=None):
        base=base or self.repo
        self.assertIn('Acquire-By-Hash: yes',release)
        rows=[]
        for row in release.split('SHA256:\n',1)[1].splitlines():
            if not row.startswith(' '):break
            rows.append(row)
        self.assertEqual(len(rows),2)
        for row in rows:
            digest,size,name=row.split()
            blob=(base/'by-hash/SHA256'/digest).read_bytes()
            self.assertEqual(hashlib.sha256(blob).hexdigest(),digest)
            self.assertEqual(len(blob),int(size))
            self.assertIn(name,('Packages','Packages.gz'))
            self.assertEqual((base/'by-hash/SHA512'/hashlib.sha512(blob).hexdigest()).read_bytes(),blob)
            if canonical:self.assertEqual(hashlib.sha256((base/name).read_bytes()).hexdigest(),digest)
    def test_stale_staging_files_are_removed_and_do_not_break_the_manifest(self):
        self.deb(self.platform,'platform_2_amd64.deb','polari-platform','2')
        stale=self.repo/'.x.part-999999';stale.write_text('stale');leftover=self.repo/'.publish-abc123';leftover.write_text('x')
        self.deps.mkdir(parents=True);nested=self.deps/'.publish-def456';nested.write_text('x')
        live=self.repo/('.y.part-%d'%os.getpid());live.write_text('live')
        result=self.publish();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse(stale.exists());self.assertFalse(leftover.exists());self.assertFalse(nested.exists())
        self.assertTrue(live.exists())  # owned by a live pid (this test process)
        self.assertIn('removed stale staging file',result.stdout+result.stderr)
        live.unlink()
        self.assertTrue((self.repo/'InRelease').is_file())
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
    def test_publish_runs_manifest_step_after_release_notice_and_survives_its_failure(self):
        text=SOURCE.read_text()
        line=[l for l in text.splitlines() if 'storage_placement.py' in l]
        self.assertEqual(len(line),1)
        self.assertIn('after-publication --repo "$REPO"',line[0])
        self.assertIn('|| echo "[storage] manifest step failed; local publication complete"',line[0])
        self.assertNotIn('apt-public-repo',text) # dataset name lives only in storage_placement.DATASET_APT
        self.assertLess(text.index('release_notice.py'),text.index('storage_placement.py'))
        self.deb(self.platform,'platform_2_amd64.deb','polari-platform','2')
        result=self.publish() # the fixture script copy has no sibling storage_placement.py
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('manifest step failed; local publication complete',result.stdout)

    def placement(self,state):
        path=self.etc/'isle-mesh/storage/placement.json';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({'datasets':{'apt-public-repo':{'dataset':'apt-public-repo','domain':'apt.isle','state':state}}}))
    def test_publish_does_not_redeploy_private_compose_once_cutover_or_hosting(self):
        text=SOURCE.read_text()
        self.assertIn('published on the core; hosting replica lags until sync',text)
        self.assertLess(text.index('hosting replica lags'),text.index('isle app deploy apt'))
        for state in ('cutover','hosting'):
            with self.subTest(state=state):
                self.log.unlink(missing_ok=True);self.placement(state);self.deb(self.platform,'platform_2_amd64.deb','polari-platform','2')
                result=self.publish(NO_APT_CONTAINER='1');self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertIn('published on the core; hosting replica lags until sync',result.stdout)
                self.assertNotIn('apt.isle deployed',result.stdout)
                self.assertFalse([c for c in self.calls() if c[0]=='isle'])
    def test_publish_deploys_as_before_when_not_placed_elsewhere(self):
        for state in (None,'authorized','migrated'):
            with self.subTest(state=state):
                self.log.unlink(missing_ok=True)
                if state:self.placement(state)
                self.deb(self.platform,'platform_2_amd64.deb','polari-platform','2')
                result=self.publish(NO_APT_CONTAINER='1')
                self.assertNotIn('hosting replica lags',result.stdout)
                self.assertTrue([c for c in self.calls() if c[0]=='isle' and c[1:3]==['app','deploy']],'the private deploy is still attempted')
    def test_snapshot_objects_survive_republish_with_changed_mutable_files(self):
        boot=self.root/'isle-bootstrap.sh';ca=self.etc/'isle-mesh/ca/isle-root.crt'
        boot.write_text('bootstrap v1');ca.write_text('CA v1')
        self.deb(self.platform,'platform_1_all.deb','polari-platform','1','all')
        first=self.publish();self.assertEqual(first.returncode,0,first.stdout+first.stderr)
        one=storage_placement.publish_manifest(self.repo,device='core-a')
        objects=self.repo/'snapshot/objects'
        boot.write_text('bootstrap v2 changed');ca.write_text('CA v2 changed')
        self.deb(self.platform,'platform_2_all.deb','polari-platform','2','all')
        second=self.publish();self.assertEqual(second.returncode,0,second.stdout+second.stderr)
        self.assertFalse(list(self.repo.glob('*.part-*')))
        for obj in objects.iterdir():self.assertEqual(hashlib.sha256(obj.read_bytes()).hexdigest(),obj.name)
        old=self.repo/'snapshot/manifests'/(one['manifestSha']+'.json')
        document=json.loads(old.read_bytes());document.pop('binding')
        for entry in document['entries']:
            if entry['kind']=='file':
                data=(objects/entry['sha256']).read_bytes()
                self.assertEqual((len(data),hashlib.sha256(data).hexdigest()),(entry['size'],entry['sha256']))
        self.assertIn(b'bootstrap v1',b''.join((objects/e['sha256']).read_bytes() for e in document['entries'] if e['path']=='isle-bootstrap.sh'))
        two=storage_placement.publish_manifest(self.repo,device='core-a')
        self.assertNotEqual(one['manifestSha'],two['manifestSha'])
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
