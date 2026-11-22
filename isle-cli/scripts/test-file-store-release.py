#!/usr/bin/env python3
"""The file store release (releases/file-store): one store on one partition, written as data.  No Docker and no
network: the build is run with the made-up `docker` of test-store-build.py."""
import importlib.util
import json
from pathlib import Path
import unittest

import catalog_artifacts as ca
import catalog_build as cb
import catalog_publish
import image_identity
import storage_kinds
import storage_slots
import yaml

SCRIPTS = Path(__file__).resolve().parent
STORE = SCRIPTS.parent / 'releases' / 'file-store'
FROM, BUILT = 'chrislusf/seaweedfs:4.47', 'file-store/seaweedfs:2026.11.1'
_spec = importlib.util.spec_from_file_location('store_build_tests', SCRIPTS / 'test-store-build.py')
sb = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sb)


class TheFileStoreRelease(unittest.TestCase):
    def setUp(self):
        self.m = json.loads((STORE / 'release.json').read_text())
        self.service = yaml.safe_load((STORE / 'docker-compose.yml').read_text())['services']['store']

    def test_it_is_a_definition_the_builder_reads(self):
        m, pairs = cb.read(STORE)
        self.assertEqual((m['name'], m['category'], m['version']), ('file-store', 'infrastructure', '2026.11.1'))
        self.assertEqual([(image['buildFrom'], ref) for image, ref in pairs], [(FROM, BUILT)])
        self.assertTrue((STORE / 'PINS-PENDING').is_file())

    def test_it_runs_the_stock_image_the_file_pool_kind_names(self):
        ways = storage_kinds.KINDS['file-pool']['requires']
        self.assertIn((('program', 'docker'), ('image', FROM.split(':')[0])), ways)

    def test_its_index_and_content_live_in_one_slot_of_a_file_pool_role(self):
        (slot,) = storage_slots.declared(self.m['storageSlots'])
        self.assertEqual({k: slot[k] for k in ('name', 'role', 'kind', 'target', 'service', 'writable', 'owner', 'distribution', 'association',
                                               'copies', 'lifetime')},
                         dict(name='files', role='blob', kind='file-pool', target='/data', service='store', writable=True, owner='1000:1000',
                              distribution='pooled', association='isle', copies=1, lifetime='until-removed'))

    def test_the_slot_is_the_folder_the_program_writes_everything_to(self):
        # The image's own start gives the program -dir=/data when the command names no folder.  A command that named
        # one would put the index or the content beside the slot, on the disk of the system.
        self.assertEqual([w for w in self.service['command'] if 'dir' in w.lower()], [])
        self.assertEqual(self.service['command'][0], 'server')
        self.assertEqual(self.m['storageSlots'][0]['target'], '/data')
        self.assertTrue(all(isinstance(word, str) for word in self.service['command']))

    def test_it_answers_behind_the_agent_at_its_s3_door_with_no_upload_limit(self):
        self.assertEqual(self.m['deploy'], dict(service='store', port=8333, maxUpload='0'))
        self.assertEqual(self.m['health'], dict(path='/healthz'))
        self.assertNotIn('domain', self.m['deploy'])   # so each install answers at <instance>.isle: an isle has several stores
        self.assertIn('-s3', self.service['command']); self.assertIn('-s3.port=8333', self.service['command'])
        self.assertEqual(self.service['expose'], ['8333'])

    def test_only_the_door_is_open_to_other_containers(self):
        command = self.service['command']
        self.assertIn('-ip=127.0.0.1', command); self.assertIn('-ip.bind=127.0.0.1', command)
        self.assertEqual([w for w in command if w.endswith('0.0.0.0')], ['-s3.ip.bind=0.0.0.0'])
        self.assertIn('-s3.port.iceberg=0', command); self.assertIn('-s3.port.lance=0', command)     # the two extra listeners are off
        for word in ('-webdav', '-sftp', '-iam', '-mq.broker', '-mq.agent', '-admin'):
            self.assertFalse([w for w in command if w == word or w.startswith(word + '=')], word)

    def test_it_does_not_switch_off_the_guard_against_links_into_the_isle(self):
        self.assertEqual([w for w in self.service['command'] if 'allowUntrusted' in w], [])

    def test_its_compose_file_opens_no_port_of_the_machine_keeps_no_data_of_its_own_and_gives_it_time_to_stop(self):
        self.assertEqual(set(self.service), {'image', 'restart', 'command', 'stop_grace_period', 'expose'})
        self.assertEqual((self.service['image'], self.service['restart'], self.service['stop_grace_period']),
                         ('file-store/seaweedfs:PIN-AT-BUILD', 'no', '60s'))
        self.assertEqual(set(yaml.safe_load((STORE / 'docker-compose.yml').read_text())), {'services'})

    def test_the_pooled_need_is_said_as_one_store_for_each_partition(self):
        meaning, plan = storage_kinds.NEEDS['pooled']
        self.assertIn('one store for each partition', meaning)
        self.assertNotIn('several machines', meaning)
        self.assertEqual(plan, 'start a store for each partition')


class Build(sb.Case):
    def setUp(self):
        super().setUp()
        self.have.write_text(FROM + '\n')

    def test_it_is_built_from_the_image_on_the_machine_and_nothing_is_downloaded(self):
        before = {p.name: p.read_bytes() for p in STORE.iterdir()}
        done = self.run_build('file-store', self.out, verb=True)   # the verb, and the definition named by its name
        self.assertEqual(done.returncode, 0, done.stderr)
        archive = self.out / 'images' / 'seaweedfs.tar'
        self.assertEqual(self.calls(), ['image inspect --format {{.Id}} ' + FROM, 'tag %s %s' % (FROM, BUILT),
                                        'image save --platform linux/amd64 -o %s %s' % (archive, BUILT)])
        self.assertEqual({p.name: p.read_bytes() for p in STORE.iterdir()}, before, 'the definition was changed')
        result = json.loads(done.stdout)['result']
        m = json.loads((self.out / 'release.json').read_text())
        (image,) = m['images']
        self.assertEqual(image['ref'], BUILT)
        self.assertEqual(image_identity.verify_archive(archive, image), {image['configDigest'], image['manifestDigest']})
        self.assertEqual(result['images'][0]['builtFrom'], FROM)
        compose = (self.out / 'docker-compose.yml').read_text()
        self.assertNotIn('PIN-AT-BUILD', compose + json.dumps(m))
        built = yaml.safe_load(compose)['services']['store']
        source = yaml.safe_load((STORE / 'docker-compose.yml').read_text())['services']['store']
        self.assertEqual(built, dict(source, image=BUILT))          # nothing but the name of the image differs
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ['docker-compose.yml', 'images', 'release.json'])
        self.assertEqual((m['deploy'], m['storageSlots']), (json.loads((STORE / 'release.json').read_text())['deploy'],
                                                             json.loads((STORE / 'release.json').read_text())['storageSlots']))
        # What publish stores is what the build said it would be.
        name, stored = catalog_publish._publish(self.out / 'release.json', self.tmp / 'repo')
        self.assertEqual((name, stored['sha256']), ('file-store', result['manifestSha256']))
        kept = json.loads((self.tmp / 'repo' / 'catalog' / 'file-store' / '2026.11.1' / (stored['sha256'] + '.json')).read_text())
        ca.validate_manifest(kept)

    def test_without_the_image_on_the_machine_the_build_stops_and_downloads_nothing(self):
        self.have.write_text('registry:2\n')
        done = self.run_build('file-store', self.out, verb=True)
        self.assertEqual(done.returncode, 1)
        self.assertIn('the image %s is not on this machine. Nothing is downloaded without --pull' % FROM, done.stderr)
        self.assertEqual(self.calls(), ['image inspect --format {{.Id}} ' + FROM])
        self.assertFalse((self.out / 'release.json').exists())


if __name__ == '__main__':
    unittest.main()
