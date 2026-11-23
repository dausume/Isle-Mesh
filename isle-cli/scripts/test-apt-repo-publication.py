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
        self.cli=self.root/'cli/scripts';self.cli.mkdir(parents=True);(self.root/'cli/apt').mkdir()
        self.script=self.cli/'apt-repo.sh'
        self.script.write_text(SOURCE.read_text().replace('/etc/',str(self.etc)+'/'))
        for name in ('apt_index_split.py','supported_os.py'):(self.cli/name).write_text(SOURCE.with_name(name).read_text())
        (self.root/'cli/apt/supported-os.json').write_text((SOURCE.parent.parent/'apt/supported-os.json').read_text())
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
 if os.environ.get('SLOW_SCAN'):time.sleep(float(os.environ['SLOW_SCAN']))
 if os.environ.get('FAIL_SCAN'):sys.exit(1)
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
                self.assertIn('InRelease signing failed'+(' (dependency set noble)' if flag=='FAIL_SIGN_DEPS' else ''),second.stdout+second.stderr)
                for path,data in committed.items():self.assertEqual(path.read_bytes(),data,path)
                for base in (self.repo,self.deps):self.assert_hashes_available((base/'InRelease').read_text(),canonical=True,base=base)
        third=self.publish('--dependencies-from',str(self.dependencies));self.assertEqual(third.returncode,0,third.stdout+third.stderr)
        self.assertIn('Version: 2',(self.deps/'Packages').read_text());self.assertIn('Version: 2',(self.repo/'Packages').read_text())
    def test_the_dependency_index_is_committed_before_the_main_index(self):
        text=SOURCE.read_text()
        self.assertLess(text.index('commit_index "$METADATA/deps/$name"'),text.index('commit_index "$METADATA/main"'))
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
        (self.etc/'os-release').write_text('VERSION_CODENAME=noble\n');(self.cli/'apt_index_split.py').unlink()
        result=self.publish();self.assertNotEqual(result.returncode,0)
        self.assertIn('apt_index_split.py is missing',result.stdout+result.stderr)
        self.assertEqual(list(self.repo.iterdir()),[])
    def listed(self,release):
        return sorted(l.split('/')[-1] for l in (self.repo/'deps'/release/'Packages').read_text().splitlines() if l.startswith('Filename:'))
    def test_a_set_built_for_another_release_is_filed_under_it_and_the_first_set_stays(self):
        self.deb(self.platform,'polari-platform_1_all.deb','polari-platform','1','all')
        self.deb(self.dependencies,'libfoo_1-24.04_amd64.deb','libfoo','1-24.04')
        self.deb(self.repo,'libold_1_amd64.deb','libold','1')                    # in the repository before there were sets: this core's release
        first=self.publish('--dependencies-from',str(self.dependencies));self.assertEqual(first.returncode,0,first.stdout+first.stderr)
        other=self.root/'jammy-set';other.mkdir()
        self.deb(other,'libfoo_1-22.04_amd64.deb','libfoo','1-22.04');self.deb(other,'libbar_3_all.deb','libbar','3','all')
        second=self.publish('--dependencies-from',str(other),'--dependencies-release','jammy');self.assertEqual(second.returncode,0,second.stdout+second.stderr)
        self.assertIn('dependency set filed under deps/jammy (as typed)',second.stdout)
        self.assertIn('2 operating system packages in deps/jammy, 2 operating system packages in deps/noble',second.stdout)
        self.assertEqual(self.listed('noble'),['libfoo_1-24.04_amd64.deb','libold_1_amd64.deb'])
        self.assertEqual(self.listed('jammy'),['libbar_3_all.deb','libfoo_1-22.04_amd64.deb'])
        release=(self.repo/'deps/jammy/Release').read_text()
        for line in ('Origin: isle-mesh-deps\n','Suite: deps/jammy\n','Codename: jammy\n'):self.assertIn(line,release)
        self.assertEqual(len(list(self.repo.glob('*.deb'))),5);self.assertFalse(list((self.repo/'deps').rglob('*.deb')))
        self.assertEqual([l for l in (self.repo/'Packages').read_text().splitlines() if l.startswith('Package')],['Package: polari-platform'])
        third=self.publish();self.assertEqual(third.returncode,0,third.stdout+third.stderr)   # a later publication keeps every file where it was filed
        self.assertEqual(self.listed('noble'),['libfoo_1-24.04_amd64.deb','libold_1_amd64.deb'])
        self.assertEqual(self.listed('jammy'),['libbar_3_all.deb','libfoo_1-22.04_amd64.deb'])
        again=self.root/'noble-again';again.mkdir();self.deb(again,'libbar_3_all.deb','libbar','3','all')   # the same file, now also given for noble
        fourth=self.publish('--dependencies-from',str(again),'--dependencies-release','noble');self.assertEqual(fourth.returncode,0,fourth.stdout+fourth.stderr)
        self.assertIn('libbar_3_all.deb',self.listed('noble'));self.assertIn('libbar_3_all.deb',self.listed('jammy'))
    def test_a_set_from_an_offline_medium_says_its_release_itself(self):
        self.deb(self.platform,'polari-platform_1_all.deb','polari-platform','1','all')
        self.deb(self.dependencies,'libfoo_1-22.04_amd64.deb','libfoo','1-22.04');(self.dependencies/'ubuntu-22.04.txt').write_text('ubuntu-22.04\n')
        (self.dependencies/'CLOSURE_URIS.txt').write_text('not a marker\n')
        result=self.publish('--dependencies-from',str(self.dependencies));self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('dependency set filed under deps/jammy (the directory is marked ubuntu-22.04)',result.stdout)
        self.assertEqual(self.listed('jammy'),['libfoo_1-22.04_amd64.deb']);self.assertFalse((self.repo/'deps/noble').exists())
    def test_a_release_that_is_not_in_the_record_or_does_not_match_is_refused_unchanged(self):
        self.deb(self.platform,'polari-platform_1_all.deb','polari-platform','1','all')
        self.deb(self.dependencies,'libfoo_1_amd64.deb','libfoo','1')
        def refused(words,*args):
            result=self.publish(*args);self.assertNotEqual(result.returncode,0,result.stdout)
            self.assertIn(words,result.stdout+result.stderr);self.assertIn('epository unchanged',result.stdout+result.stderr)
            self.assertEqual(list(self.repo.iterdir()),[])
        refused('jammi is not a release name in the record of supported operating systems (noble, jammy)','--dependencies-from',str(self.dependencies),'--dependencies-release','jammi')
        refused('../x is not a release name','--dependencies-from',str(self.dependencies),'--dependencies-release','../x')
        refused('it needs that directory too','--dependencies-release','jammy')
        refused('dependency directory missing','--dependencies-from',str(self.root/'absent'))
        (self.dependencies/'ubuntu-22.04.txt').write_text('ubuntu-22.04\n')
        refused('is built for ubuntu-22.04 (jammy), not for noble','--dependencies-from',str(self.dependencies),'--dependencies-release','noble')
        (self.dependencies/'ubuntu-24.04.txt').write_text('ubuntu-24.04\n')
        refused('built for more than one system (ubuntu-22.04, ubuntu-24.04)','--dependencies-from',str(self.dependencies))
        (self.dependencies/'ubuntu-22.04.txt').unlink();(self.dependencies/'ubuntu-24.04.txt').rename(self.dependencies/'debian-12.txt')
        refused('built for debian-12, which is not in the record','--dependencies-from',str(self.dependencies))
        (self.cli/'supported_os.py').unlink();(self.dependencies/'debian-12.txt').unlink()
        refused('supported_os.py is missing','--dependencies-from',str(self.dependencies))
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
        boot=self.cli/'isle-bootstrap.sh';ca=self.etc/'isle-mesh/ca/isle-root.crt'
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
    def leftovers(self,token):
        """Processes still carrying this run's token in their environment (the script and anything it started)."""
        found=[]
        for entry in Path('/proc').iterdir():
            if entry.name.isdigit() and int(entry.name)!=os.getpid():
                try:
                    if ('ISLE_TEST_RUN='+token).encode() in (entry/'environ').read_bytes():found.append(entry.name)
                except OSError:pass
        return found
    def progress_fixture(self):
        self.deb(self.repo,'old.deb','package-old','1');self.deb(self.platform,'a_1_all.deb','polari-a','1','all')
        self.deb(self.platform,'b_1_all.deb','polari-b','1','all')
    def test_progress_lines_are_said_and_the_published_result_is_the_same(self):
        self.progress_fixture()
        quiet=self.publish(ISLE_APT_PROGRESS_S='99999',ISLE_APT_HEARTBEAT_S='99999')
        self.assertEqual(quiet.returncode,0,quiet.stderr)
        self.assertIn('   checking 1 package (',quiet.stderr);self.assertIn('already in the repository, and the new ones: every file is read once',quiet.stderr)
        self.assertNotIn('   checked ',quiet.stderr)
        self.assertIn('   building the package index: every package is read once more',quiet.stdout)
        self.assertRegex(quiet.stdout,r'   package index built in \d+ s')
        packages=(self.repo/'Packages').read_text()
        for name in ('a_1_all.deb','b_1_all.deb'):(self.repo/name).unlink()
        loud=self.publish(ISLE_APT_PROGRESS_S='0',ISLE_APT_HEARTBEAT_S='1')
        self.assertEqual(loud.returncode,0,loud.stderr)
        self.assertIn('   checked 3 of 3 packages (',loud.stderr)
        self.assertEqual((self.repo/'Packages').read_text(),packages)
        self.assertEqual(sorted(l for l in loud.stdout.splitlines() if l.startswith('   + ')),['   + a_1_all.deb','   + b_1_all.deb'])
    def test_an_interval_that_is_not_a_number_falls_back_and_the_publish_passes(self):
        self.progress_fixture()
        result=self.publish(ISLE_APT_PROGRESS_S='soon',ISLE_APT_HEARTBEAT_S='-3')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn('   checked ',result.stderr);self.assertIn('package index built in',result.stdout)
    def test_the_heartbeat_speaks_while_the_scan_runs_and_never_outlives_the_script(self):
        self.progress_fixture()
        for flags in ({},{'FAIL_SCAN':'1'}):
            token=os.urandom(8).hex()
            result=self.publish(ISLE_TEST_RUN=token,ISLE_APT_HEARTBEAT_S='1',SLOW_SCAN='2.5',**flags)
            self.assertIn('   still building the package index (',result.stdout)
            if flags:
                self.assertNotEqual(result.returncode,0);self.assertIn('index build failed',result.stdout)
                self.assertNotIn('package index built in',result.stdout)
            else:self.assertEqual(result.returncode,0,result.stderr)
            time.sleep(1.5)  # the stand-in sleep command of a last tick may still be finishing
            self.assertEqual(self.leftovers(token),[])
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
    def only(self,*args):
        return subprocess.run(['bash',str(self.script),'publish','--only-dependencies',*args],env=self.env,capture_output=True,text=True,timeout=15)
    def test_only_a_dependency_set_is_added_and_no_package_is_looked_for(self):
        self.deb(self.platform,'polari-platform_1_all.deb','polari-platform','1','all')
        first=self.publish();self.assertEqual(first.returncode,0,first.stdout+first.stderr)
        main=(self.repo/'Packages').read_bytes()
        home=self.root/'polari-shells';home.mkdir();self.deb(home,'polari-left-behind_9_all.deb','polari-left-behind','9','all')
        self.deb(self.dependencies,'libfoo_1-22.04_amd64.deb','libfoo','1-22.04');(self.dependencies/'ubuntu-22.04.txt').write_text('ubuntu-22.04\n')
        result=self.only('--dependencies-from',str(self.dependencies));self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(self.listed('jammy'),['libfoo_1-22.04_amd64.deb'])
        self.assertEqual((self.repo/'Packages').read_bytes(),main,'the main index lists what it listed')
        self.assertEqual(sorted(p.name for p in self.repo.glob('*.deb')),['libfoo_1-22.04_amd64.deb','polari-platform_1_all.deb'])
        self.assertIn('1 isle and polari packages in the main index, 1 operating system packages in deps/jammy',result.stdout)
    def test_only_dependencies_is_refused_unchanged_with_packages_or_without_a_set(self):
        self.deb(self.platform,'polari-platform_1_all.deb','polari-platform','1','all')
        self.deb(self.dependencies,'libfoo_1_amd64.deb','libfoo','1')
        for words,args in (('it does not go with --from',('--from',str(self.platform),'--dependencies-from',str(self.dependencies))),
                           ('--only-dependencies needs the set',()),
                           ('dependency directory missing',('--dependencies-from',str(self.root/'absent')))):
            with self.subTest(words=words):
                result=self.only(*args);self.assertNotEqual(result.returncode,0,result.stdout)
                self.assertIn(words,result.stdout+result.stderr);self.assertIn('epository unchanged',result.stdout+result.stderr)
                self.assertEqual(list(self.repo.iterdir()),[])
        (self.dependencies/'libfoo_1_amd64.deb').unlink()
        result=self.only('--dependencies-from',str(self.dependencies));self.assertNotEqual(result.returncode,0,result.stdout)
        self.assertEqual(list(self.repo.iterdir()),[])
    def test_a_set_added_alone_may_not_hold_an_isle_or_polari_package(self):
        # the release the core announces is read from those packages, and a set added alone must leave it as it is
        self.deb(self.dependencies,'libfoo_1_amd64.deb','libfoo','1')
        for name in ('isle-mesh-cli','polari-shell-core'):
            with self.subTest(name=name):
                extra=self.deb(self.dependencies,name+'_9_all.deb',name,'9','all')
                result=self.only('--dependencies-from',str(self.dependencies));text=result.stdout+result.stderr
                self.assertNotEqual(result.returncode,0,text);self.assertIn('adds operating system packages only',text);self.assertIn(extra.name,text)
                self.assertEqual(list(self.repo.iterdir()),[]);extra.unlink()
        # the same folder given beside the packages of a release is taken, as before
        self.deb(self.platform,'polari-platform_1_all.deb','polari-platform','1','all')
        self.deb(self.dependencies,'isle-mesh-cli_9_all.deb','isle-mesh-cli','9','all')
        result=self.publish('--dependencies-from',str(self.dependencies));self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    def test_existing_conflicting_release_preserved_without_partial_copy(self):
        existing=self.deb(self.repo,'existing.deb','package-a','1',content='old').read_bytes()
        self.deb(self.platform,'new-name.deb','package-a','1',content='new')
        result=self.publish();self.assertNotEqual(result.returncode,0)
        self.assertEqual((self.repo/'existing.deb').read_bytes(),existing)
        self.assertEqual([p.name for p in self.repo.iterdir()],['existing.deb'])


if __name__=='__main__':unittest.main()
