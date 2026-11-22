#!/usr/bin/env python3
"""isle release build --runtime-image / --module-add: an image built elsewhere as the image of a runtime container,
and a module a device adds to its Polari instance after its update.
The made-up Docker, core and commit of test-agent-images.py.  No Docker, no network, no build tool of this machine."""
import importlib.util
import json
from pathlib import Path
import unittest

import release_build as rb
import release_run as rr

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('agent_images_tests', HERE / 'test-agent-images.py')
agents = importlib.util.module_from_spec(spec); spec.loader.exec_module(agents)
base, Docker = agents.base, agents.Docker

NEW_REF = 'example/backend:8'


class Docker2(Docker):
    """The same Docker, which also says what platform an image is for."""
    other = ()

    def run(self, argv):
        if argv[1:3] == ['image', 'inspect'] and argv[argv.index('--format') + 1] == '{{.Os}}/{{.Architecture}}':
            self.calls.append(argv)
            if argv[-1] not in self.images: return 1, ''
            return 0, ('linux/arm64' if argv[-1] in self.other else 'linux/amd64') + '\n'
        return super().run(argv)


class Named(unittest.TestCase):
    def setUp(self):
        agents.Agents.setUp(self)
        self.io.docker = Docker2()
        self.rows(True)
        self.new = Docker.config(['sha256:b', 'sha256:more'], {'org.opencontainers.image.revision': 'c' * 40})
        self.io.docker.images[NEW_REF] = self.new
        self.word = 'prf-isle-backend=' + NEW_REF

    rows = agents.Agents.rows
    build = base.Build.build
    refused = base.Build.refused
    verbs = agents.Agents.verbs
    manifest = agents.Agents.manifest

    def test_the_named_image_becomes_the_image_of_that_container_and_the_rest_stays(self):
        self.assertEqual(self.build('--runtime-image', self.word), 0, self.io.text())
        now, was = self.manifest(), self.io.release['runtimes']
        self.assertEqual(now['core'][0]['image'], {'ref': NEW_REF, 'configDigest': Docker.ident(self.new), 'archive': {'source': 'images/prf-isle-backend.tar'}})
        self.assertEqual(now['core'][0]['healthUrl'], 'https://api.polari.isle/api/health')      # the row keeps what is not the image
        self.assertEqual((now['core'][1], now['remote']), (was['core'][1], was['remote']))        # the agents: the rows the core publishes
        self.assertNotIn('build', self.verbs())                                                   # taken as it is
        pk = rr.load_packet(self.out / 'packet')
        runtime = next(p for p in pk.publications if p['kind'] == 'runtime')
        self.assertEqual(runtime['archives'], ['runtime/images/prf-isle-backend.tar'])
        self.assertEqual(sorted(k['container'] for k in runtime['kept']), ['isle-remote-agent', 'isle-vlan-agent'])
        made = json.loads((self.out / 'build.json').read_text())
        self.assertNotIn('agentImages', made)
        self.assertEqual([(m['container'], m['kept'], m['ref'], m['configDigest'], m['layers'], m['sharedLayers'], m['replaces']['ref']) for m in made['namedImages']],
                         [('prf-isle-backend', False, NEW_REF, Docker.ident(self.new), 2, None, agents.BACKEND_REF)])
        self.assertEqual(made['namedImages'][0]['archiveSha256'], rr.sha256_file(self.out / 'packet/runtime/images/prf-isle-backend.tar'))
        text = self.io.text()
        self.assertIn('taken as Docker here holds it', text); self.assertIn('It replaces ' + agents.BACKEND_REF, text)
        self.assertIn('every Polari instance of a member is put onto the new backend and frontend', text)

    def test_the_layers_it_shares_with_the_published_image_are_counted_when_docker_holds_that_one(self):
        self.io.docker.images[agents.BACKEND_REF] = Docker.config(['sha256:b'])
        self.assertEqual(self.build('--runtime-image', self.word), 0, self.io.text())
        self.assertEqual(json.loads((self.out / 'build.json').read_text())['namedImages'][0]['sharedLayers'], 1)
        self.assertIn("1 of its 2 layers are the published image's", self.io.text())

    def test_with_the_agents_one_manifest_carries_all_three(self):
        self.assertEqual(self.build('--agents', '--runtime-image', self.word), 0, self.io.text())
        now = self.manifest()
        self.assertEqual([row['image']['archive'] for row in now['core'] + now['remote']],
                         [{'source': 'images/prf-isle-backend.tar'}, {'source': 'images/core-agent.tar'}, {'source': 'images/remote-agent.tar'}])
        pk = rr.load_packet(self.out / 'packet')
        self.assertEqual(next(p for p in pk.publications if p['kind'] == 'runtime')['kept'], [])
        made = json.loads((self.out / 'build.json').read_text())
        self.assertEqual((len(made['agentImages']), len(made['namedImages'])), (2, 1))

    def test_all_images_fetches_what_the_release_does_not_change(self):
        self.assertEqual(self.build('--runtime-image', self.word, '--all-images', '--runtime-archives', str(self.folder)), 0, self.io.text())
        now = self.manifest()
        self.assertTrue(all('source' in row['image']['archive'] for row in now['core'] + now['remote']))
        self.assertEqual(self.io.fetched, [])                                                     # they lay in the named folder

    def test_the_published_image_under_its_own_name_is_left_as_it_is(self):
        row = self.io.release['runtimes']['core'][0]['image']
        self.io.docker.images[agents.BACKEND_REF] = Docker.config(['sha256:b'])
        self.assertEqual(Docker.ident(self.io.docker.images[agents.BACKEND_REF]), row['configDigest'])
        self.assertEqual(self.build('--runtime-image', 'prf-isle-backend=' + agents.BACKEND_REF), 0, self.io.text())
        self.assertFalse((self.out / 'packet/runtime').exists())
        self.assertEqual([p['kind'] for p in rr.load_packet(self.out / 'packet').publications], ['debs'])
        self.assertIn('is the image the core publishes already', self.io.text())
        self.assertIn('the packet publishes no runtime image', self.io.text())

    def test_refused_before_anything_is_written(self):
        docker = self.io.docker
        docker.images[agents.BACKEND_REF] = self.new                                              # the published name, other contents
        docker.images['example/arm:1'] = Docker.config(['sha256:a']); docker.other = ('example/arm:1',)
        for word, words in (('prf-isle-backend', 'wants <container>=<image>'), ('=x', 'wants <container>=<image>'),
                            ('prf-nothing=' + NEW_REF, 'names no container prf-nothing'),
                            ('isle-vlan-agent=' + NEW_REF, 'is an agent of the isle'),
                            ('prf-isle-backend=example/none:1', 'holds no image named example/none:1'),
                            ('prf-isle-backend=' + agents.BACKEND_REF, 'A name does not change what it means'),
                            ('prf-isle-backend=example/arm:1', 'is for linux/arm64')):
            with self.subTest(word=word):
                self.refused(words, '--runtime-image', word)
        self.refused('names prf-isle-backend twice', '--runtime-image', self.word, '--runtime-image', self.word)
        self.io.release = None
        self.refused('does not answer from here', '--runtime-image', self.word)

    def test_a_save_docker_refuses_stops_the_build(self):
        self.io.docker.fail = 'save'
        self.refused('docker image save %s failed' % NEW_REF, '--runtime-image', self.word)

    def test_the_words_are_for_a_release(self):
        self.assertEqual(rb.main([str(self.out), '--dependencies', str(self.tmp), '--runtime-image', self.word], self.io), 2)
        self.assertEqual(rb.main([str(self.out), '--dependencies', str(self.tmp), '--module-add', 'pol:polari-pol:hal'], self.io), 2)
        self.assertFalse(self.out.exists())


class Modules(unittest.TestCase):
    setUp = base.Build.setUp
    build = base.Build.build
    refused = base.Build.refused

    def doc(self): return json.loads((self.out / 'packet/release-run.json').read_text())

    def test_a_device_adds_the_module_after_the_steps_every_device_takes(self):
        self.assertEqual(self.build('--backup', '--module-add', 'pol:polari-pol:hal', '--module-add', 'pol:polari-pol:scope'), 0, self.io.text())
        doc = self.doc()
        every = [{'verb': 'backup'}, {'verb': 'update-apply', 'releaseId': 'published'}]
        add = [{'verb': 'module-add', 'instance': 'polari-pol', 'module': m} for m in ('hal', 'scope')]
        self.assertEqual(doc['devices'], {'*': every, 'pol': add})
        self.assertEqual(rr.steps_for(doc, 'pol'), every + add)                                   # the runner: the update first
        self.assertEqual(rr.steps_for(doc, 'econ'), every)
        self.assertIn('After its update, pol adds the module hal to its Polari instance polari-pol.', self.io.text())
        rr.load_packet(self.out / 'packet')

    def test_with_named_devices_the_steps_follow_that_devices_own(self):
        self.assertEqual(self.build('--device', 'pol', '--device', 'core', '--module-add', 'pol:polari-pol:hal'), 0, self.io.text())
        doc = self.doc()
        self.assertEqual([s['verb'] for s in doc['devices']['pol']], ['update-apply', 'module-add'])
        self.assertEqual([s['verb'] for s in doc['devices']['core']], ['update-apply'])
        self.assertEqual(rr.steps_for(doc, 'pol'), doc['devices']['pol'])

    def test_refused_before_anything_is_written(self):
        for more, words in ((['--module-add', 'pol:hal'], 'wants <device>:<instance>:<module>'),
                            (['--module-add', 'pol:polari pol:hal'], 'wants <device>:<instance>:<module>'),
                            (['--module-add', 'pol:polari-pol:hal', '--module-add', 'pol:polari-pol:hal'], 'twice'),
                            (['--device', 'core', '--module-add', 'pol:polari-pol:hal'], 'which this packet does not name')):
            with self.subTest(more=more):
                self.refused(words, *more); self.assertFalse(self.out.exists())


if __name__ == '__main__':
    unittest.main()
