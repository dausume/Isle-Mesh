#!/usr/bin/env python3
"""isle release build --agents: the two agent images as the published image plus one layer of the commit's scripts.
A made-up Docker (images in memory, saved archives as real files), a made-up core and a made-up commit in a
temporary folder.  No Docker, no network and no build tool of this machine is used."""
import hashlib
import importlib.util
import io as bytes_io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest

import agent_images as ai
import release_build as rb
import release_run as rr

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('release_build_tests', HERE / 'test-release-build.py')
base = importlib.util.module_from_spec(spec); spec.loader.exec_module(base)

CORE_FILE = """# The agent of the core
FROM nginx:alpine
RUN apk add --no-cache bash curl
COPY registry-watcher.sh /usr/local/bin/registry-watcher.sh
RUN chmod +x /usr/local/bin/registry-watcher.sh
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
RUN mkdir -p /etc/nginx/configs
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
"""
REMOTE_FILE = """FROM nginx:alpine
RUN apk add --no-cache bash curl avahi && \\
    mkdir -p /run/dbus
COPY isle-vlan-agent/registry-watcher.sh /usr/local/bin/registry-watcher.sh
COPY isle-remote-agent/remote-entrypoint.sh /usr/local/bin/entrypoint.sh
COPY isle-remote-agent/lease-hook.sh /usr/local/bin/lease-hook.sh
RUN chmod +x /usr/local/bin/lease-hook.sh
RUN chmod +x /usr/local/bin/registry-watcher.sh \\
    /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
"""
COMMIT = {'isle-agent/isle-vlan-agent/Dockerfile': CORE_FILE, 'isle-agent/isle-remote-agent/Dockerfile.remote': REMOTE_FILE,
          'isle-agent/isle-vlan-agent/registry-watcher.sh': 'watch v2\n', 'isle-agent/isle-vlan-agent/entrypoint.sh': 'core entry v2\n',
          'isle-agent/isle-remote-agent/remote-entrypoint.sh': 'member entry v2\n', 'isle-agent/isle-remote-agent/lease-hook.sh': 'lease v2\n'}
CORE_REF, REMOTE_REF, BACKEND_REF = 'example/core-agent:2026.1', 'example/remote-agent:2026.1', 'example/backend:7'


class Docker:
    """Images in memory: {name: config}.  An image's id is the checksum of its config, as in a saved archive."""
    def __init__(self):
        self.images, self.calls, self.down, self.fail, self.platform, self.spoil = {}, [], False, None, True, None

    @staticmethod
    def config(layers, labels=None, files=None):
        return {'os': 'linux', 'rootfs': {'diff_ids': list(layers)}, 'config': {'Labels': labels}, 'files': files or {}}

    @staticmethod
    def ident(config): return 'sha256:' + hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()

    def archive(self, ref, config, path):
        """What `docker image save` writes (the older form: manifest.json, the config, one file per layer)."""
        raw = json.dumps(config, sort_keys=True).encode(); name = hashlib.sha256(raw).hexdigest() + '.json'
        layers = ['%d/layer.tar' % n for n, _ in enumerate(config['rootfs']['diff_ids'])]
        files = {'manifest.json': json.dumps([{'Config': name, 'RepoTags': [ref], 'Layers': layers}]).encode(), name: raw}
        files.update({layer: config['rootfs']['diff_ids'][n].encode() for n, layer in enumerate(layers)})
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(path, 'w') as tar:
            for member, data in files.items():
                info = tarfile.TarInfo(member); info.size = len(data); tar.addfile(info, bytes_io.BytesIO(data))

    def run(self, argv):
        self.calls.append(argv)
        if self.down: return 1, ''
        verb = argv[1] if argv[1] != 'image' else argv[2]
        if self.fail == verb: return 1, ''
        if verb == 'version': return 0, '27.0.0\n'
        if verb == 'inspect':
            config = self.images.get(argv[-1])
            if config is None: return 1, ''
            what = argv[argv.index('--format') + 1]
            if what == '{{.Id}}': return 0, self.ident(config) + '\n'
            if what == '{{json .Config.Labels}}': return 0, json.dumps(config['config']['Labels']) + '\n'
            if what == '{{json .RootFS.Layers}}': return 0, json.dumps(config['rootfs']['diff_ids']) + '\n'
        if verb == 'load':
            with tarfile.open(argv[argv.index('--input') + 1]) as tar:
                for entry in json.load(tar.extractfile('manifest.json')):
                    for tag in entry['RepoTags']: self.images[tag] = json.load(tar.extractfile(entry['Config']))
            return 0, ''
        if verb == 'build':
            assert argv[2:5] == ['--pull=false', '--network', 'none'], argv        # never the network
            context, ref = Path(argv[-1]), argv[argv.index('-t') + 1]
            lines = (context / 'Dockerfile').read_text().splitlines()
            assert [line.split()[0] for line in lines] == ['FROM', 'LABEL', 'COPY'], lines
            start = self.images[lines[0].split()[1]]
            labels = dict(start['config']['Labels'] or {}, **{k: v.strip('"') for k, v in (word.split('=') for word in lines[1].split()[1:])})
            names, target = lines[2].split()[1:-1], lines[2].split()[-1]
            assert all((context / name).stat().st_mode & 0o777 == 0o755 for name in names), 'a script that cannot be run'
            copied = {target + name: (context / name).read_text() for name in names}
            layer = 'sha256:' + hashlib.sha256(json.dumps(copied, sort_keys=True).encode()).hexdigest()
            self.images[ref] = self.config(start['rootfs']['diff_ids'] + [layer] + (['sha256:more'] if self.spoil == 'layers' else []),
                                           labels, dict(start['files'], **copied))
            return 0, ''
        if verb == 'save':
            if '--platform' in argv and not self.platform: return 1, ''
            self.archive(argv[-1], self.images[argv[-1]], argv[argv.index('-o') + 1])
            return 0, ''
        raise AssertionError('a Docker command the build should not run: %r' % argv)


class Machine(base.Fake):
    """The made-up build machine of test-release-build.py, with Docker and a core that serves a runtime release."""
    def __init__(self, tmp):
        super().__init__(tmp)
        self.docker, self.fetched, self.served, self.release = Docker(), [], {}, None
        self.tree = dict(self.tree, **COMMIT)

    def run(self, argv, log=None, env=None, cwd=None):
        argv = [str(a) for a in argv]
        if argv[0] == 'docker': return self.docker.run(argv)
        return super().run(argv, log=log, env=env, cwd=cwd)

    def fetch_json(self, url):
        assert url == 'https://apt.isle/runtime-release.json', url
        if self.release is None: raise rb.Refused('%s does not answer from here (made up)' % url)
        return json.loads(json.dumps(self.release))

    def fetch(self, item, target):
        self.fetched.append(item['url'])
        if item['url'] not in self.served: raise rb.Refused('%s could not be fetched: made up' % item['url'])
        Path(target).parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(self.served[item['url']], target)
        return Path(target)

    def publish(self, ref, config, held=True):
        """The core publishes this image: its row.  held: Docker here has it too."""
        path = self.tmp / 'core' / 'one.tar'; self.docker.archive(ref, config, path)
        sha = rr.sha256_file(path); final = path.with_name(sha + '.tar'); path.rename(final)
        url = 'https://apt.isle/runtime/%s.tar' % sha; self.served[url] = final
        if held: self.docker.images[ref] = config
        return {'ref': ref, 'configDigest': Docker.ident(config), 'archive': {'url': url, 'sha256': sha, 'size': final.stat().st_size}}


class Recipe(unittest.TestCase):
    def test_the_two_dockerfiles_of_the_repository_are_ones_a_layer_can_follow(self):
        root = HERE.parent.parent
        want = {'isle-vlan-agent': ['entrypoint.sh', 'generate-nginx-configs.sh', 'registry-watcher.sh'],
                'isle-remote-agent': ['entrypoint.sh', 'generate-nginx-configs.sh', 'lease-hook.sh', 'registry-watcher.sh', 'remote-health.sh']}
        for agent in ai.AGENTS:
            copies, other = ai.recipe((root / agent['dockerfile']).read_text(), agent['dockerfile'])
            self.assertEqual(sorted(name for _, name in copies), want[agent['container']])
            for source, _ in copies: self.assertTrue((root / agent['context'] / source).is_file(), source)
            self.assertRegex(other, r'^[0-9a-f]{64}$')

    def test_the_copied_files_and_their_names_in_the_image(self):
        copies, _ = ai.recipe(REMOTE_FILE, 'x')
        self.assertEqual(copies, [('isle-vlan-agent/registry-watcher.sh', 'registry-watcher.sh'), ('isle-remote-agent/remote-entrypoint.sh', 'entrypoint.sh'),
                                  ('isle-remote-agent/lease-hook.sh', 'lease-hook.sh')])

    def test_comments_spacing_and_the_copied_files_do_not_change_the_other_lines(self):
        same = ai.recipe(CORE_FILE, 'x')[1]
        for text in (CORE_FILE.replace('# The agent of the core', '# another comment\n\n'), CORE_FILE.replace('RUN mkdir -p', 'RUN   mkdir  -p'),
                     CORE_FILE.replace('RUN chmod +x /usr/local/bin/entrypoint.sh\n', ''),
                     CORE_FILE.replace('COPY entrypoint.sh', 'COPY other/start.sh'), CORE_FILE + 'COPY more.sh /usr/local/bin/more.sh\n'):
            self.assertEqual(ai.recipe(text, 'x')[1], same, text)
        for text in (CORE_FILE.replace('bash curl', 'bash curl jq'), CORE_FILE.replace('nginx:alpine', 'nginx:1.27-alpine'),
                     CORE_FILE + 'RUN chmod +x /usr/local/bin/other.sh\n', CORE_FILE + 'ENV A=1\n'):
            self.assertNotEqual(ai.recipe(text, 'x')[1], same, text)

    def test_a_copy_a_layer_cannot_follow_is_refused(self):
        for line in ('COPY --chown=1:1 a.sh /usr/local/bin/a.sh', 'COPY a.sh b.sh /usr/local/bin/', 'COPY a.sh /etc/a.sh', 'COPY ../a.sh /usr/local/bin/a.sh',
                     'COPY /a.sh /usr/local/bin/a.sh', 'COPY a.sh /usr/local/bin/sub/a.sh', 'COPY other.sh /usr/local/bin/entrypoint.sh', 'copy a.sh /usr/local/bin/'):
            with self.subTest(line=line), self.assertRaises(ai.Refused) as caught: ai.recipe(CORE_FILE + line + '\n', 'the file')
            self.assertIn('is not a copy of one file', str(caught.exception))
        with self.assertRaises(ai.Refused): ai.recipe('FROM nginx:alpine\nRUN true\n', 'the file')

    def test_the_name_of_an_image_without_its_tag(self):
        for ref, name in (('a/b:1', 'a/b'), ('a/b', 'a/b'), ('host:5000/a/b', 'host:5000/a/b'), ('host:5000/a/b:1', 'host:5000/a/b'), ('a/b@sha256:00', 'a/b')):
            self.assertEqual(ai.repository(ref), name)


class Agents(unittest.TestCase):
    def setUp(self):
        base.Build.setUp(self)
        self.io = Machine(self.tmp)
        self.rows(True)
        self.folder = self.tmp / 'core'           # the folder the core publishes its runtime images from

    def rows(self, held, labels=(None, None)):
        io = self.io
        core = io.publish(CORE_REF, Docker.config(['sha256:n', 'sha256:c'], labels[0], {'/usr/local/bin/entrypoint.sh': 'core entry v1\n'}), held)
        remote = io.publish(REMOTE_REF, Docker.config(['sha256:n', 'sha256:r'], labels[1]), held)
        backend = io.publish(BACKEND_REF, Docker.config(['sha256:b']), False)
        io.release = {'runtimes': {'core': [{'container': 'prf-isle-backend', 'image': backend, 'healthUrl': 'https://api.polari.isle/api/health'},
                                             {'container': 'isle-vlan-agent', 'image': core, 'healthUrl': 'https://api.polari.isle/api/health', 'readinessTimeoutSeconds': 90}],
                                    'remote': [{'container': 'isle-remote-agent', 'image': remote}]}}

    build = base.Build.build
    refused = base.Build.refused
    def verbs(self): return [call[2] if call[1] == 'image' else call[1] for call in self.io.docker.calls]
    def manifest(self): return json.loads((self.out / 'packet/runtime/manifest.json').read_text())['runtimes']

    def test_each_agent_image_is_the_published_one_and_one_layer_of_the_commit(self):
        self.assertEqual(self.build('--agents'), 0, self.io.text())
        images, was = self.io.docker.images, self.io.release['runtimes']
        core, remote = images['example/core-agent:release91'], images['example/remote-agent:release91']
        self.assertEqual(core['rootfs']['diff_ids'][:-1], ['sha256:n', 'sha256:c']); self.assertEqual(len(remote['rootfs']['diff_ids']), 3)
        self.assertEqual(core['files'], {'/usr/local/bin/entrypoint.sh': 'core entry v2\n', '/usr/local/bin/registry-watcher.sh': 'watch v2\n'})
        self.assertEqual(remote['files'], {'/usr/local/bin/entrypoint.sh': 'member entry v2\n', '/usr/local/bin/registry-watcher.sh': 'watch v2\n',
                                           '/usr/local/bin/lease-hook.sh': 'lease v2\n'})
        self.assertEqual(sorted(core['config']['Labels']), [ai.RECIPE_LABEL, ai.SCRIPTS_LABEL])
        now = self.manifest()
        self.assertEqual(now['core'][0], was['core'][0])                                 # the backend: the row the core publishes, no archive
        agent = now['core'][1]
        self.assertEqual(agent['image'], {'ref': 'example/core-agent:release91', 'configDigest': Docker.ident(core), 'archive': {'source': 'images/core-agent.tar'}})
        self.assertEqual((agent['healthUrl'], agent['readinessTimeoutSeconds']), ('https://api.polari.isle/api/health', 90))
        self.assertEqual(now['remote'][0]['image']['configDigest'], Docker.ident(remote))
        self.assertNotIn('load', self.verbs()); self.assertEqual(self.io.fetched, [])      # Docker here had both images

    def test_the_packet_is_one_the_runner_takes_and_names_what_it_keeps(self):
        self.assertEqual(self.build('--agents'), 0, self.io.text())
        pk = rr.load_packet(self.out / 'packet')
        self.assertEqual([p['kind'] for p in pk.publications], ['runtime', 'debs'] if pk.publications[0]['kind'] == 'runtime' else ['debs', 'runtime'])
        runtime = next(p for p in pk.publications if p['kind'] == 'runtime')
        self.assertEqual(runtime['archives'], ['runtime/images/core-agent.tar', 'runtime/images/remote-agent.tar'])
        self.assertEqual([k['container'] for k in runtime['kept']], ['prf-isle-backend'])
        sums = (self.out / 'packet/SHA256SUMS').read_text()
        for rel in ('runtime/manifest.json', 'runtime/images/core-agent.tar', 'runtime/images/remote-agent.tar'): self.assertIn('  ' + rel + '\n', sums)
        text = self.io.text()
        self.assertIn('carries no archive for the images this release does not change (core prf-isle-backend)', text)
        self.assertIn('For an older core, build with --all-images', text)
        made = json.loads((self.out / 'build.json').read_text())['agentImages']
        self.assertEqual([(m['container'], m['kept'], m['ref'], m['layers'], m['builtFrom']['ref']) for m in made],
                         [('isle-vlan-agent', False, 'example/core-agent:release91', 3, CORE_REF), ('isle-remote-agent', False, 'example/remote-agent:release91', 3, REMOTE_REF)])
        self.assertEqual(made[0]['files'], ['entrypoint.sh', 'registry-watcher.sh']); self.assertEqual(made[0]['recipeOfBase'], 'not labelled')
        self.assertEqual(made[0]['archiveSha256'], rr.sha256_file(self.out / 'packet/runtime/images/core-agent.tar'))

    def test_a_release_without_the_word_carries_no_image_and_asks_no_one(self):
        self.assertEqual(self.build(), 0, self.io.text())
        self.assertEqual(self.io.docker.calls, []); self.assertFalse((self.out / 'packet/runtime').exists())
        self.assertNotIn('agentImages', json.loads((self.out / 'build.json').read_text()))

    def test_an_image_docker_does_not_hold_comes_from_the_named_folder_and_else_from_the_core(self):
        self.io.docker.images.clear()
        self.assertEqual(self.build('--agents', '--runtime-archives', str(self.folder)), 0, self.io.text())
        self.assertEqual(self.verbs().count('load'), 2); self.assertEqual(self.io.fetched, [])
        self.assertEqual(json.loads((self.out / 'build.json').read_text())['agentImages'][0]['base'], 'loaded from the folder --runtime-archives names')
        self.io.docker.images.clear(); out = self.tmp / 'second'
        self.assertEqual(self.build('--agents', out=out), 0, self.io.text())
        self.assertEqual(len(self.io.fetched), 2)
        self.assertEqual(json.loads((out / 'build.json').read_text())['agentImages'][1]['base'], 'loaded from the core')
        self.assertIn('fetching the image of isle-remote-agent from the core', self.io.text())

    def test_a_file_in_the_folder_that_is_not_the_published_one_is_not_used(self):
        self.io.docker.images.clear()
        mine = self.tmp / 'mine'; shutil.copytree(self.folder, mine)
        for path in mine.iterdir(): path.write_bytes(path.read_bytes()[:-1] + b'x')
        self.assertEqual(self.build('--agents', '--runtime-archives', str(mine)), 0, self.io.text())
        self.assertEqual(len(self.io.fetched), 2)

    def test_all_images_carries_every_runtime_image_and_keeps_none(self):
        self.assertEqual(self.build('--agents', '--all-images', '--runtime-archives', str(self.folder)), 0, self.io.text())
        runtime = next(p for p in rr.load_packet(self.out / 'packet').publications if p['kind'] == 'runtime')
        self.assertEqual(runtime['kept'], [])
        backend = self.io.release['runtimes']['core'][0]['image']
        carried = self.out / 'packet/runtime/images' / (backend['archive']['sha256'] + '.tar')
        self.assertEqual(rr.sha256_file(carried), backend['archive']['sha256'])
        row = self.manifest()['core'][0]['image']
        self.assertEqual(row, dict(backend, archive={'source': 'images/' + carried.name}))
        self.assertEqual(self.io.fetched, []); self.assertIn('The packet carries every runtime image', self.io.text())
        out = self.tmp / 'second'                      # without the folder the unchanged image is fetched from the core
        self.assertEqual(self.build('--agents', '--all-images', out=out), 0, self.io.text())
        self.assertEqual(self.io.fetched, [backend['archive']['url']])

    def test_an_image_that_holds_the_scripts_already_is_left_as_it_is(self):
        self.assertEqual(self.build('--agents'), 0, self.io.text())
        made = self.io.docker.images
        self.io.served.clear()
        self.io.release = None; held = (made['example/core-agent:release91'], made['example/remote-agent:release91'])
        core = self.io.publish('example/core-agent:release91', held[0]); remote = self.io.publish('example/remote-agent:release91', held[1])
        self.io.release = {'runtimes': {'core': [{'container': 'isle-vlan-agent', 'image': core}], 'remote': [{'container': 'isle-remote-agent', 'image': remote}]}}
        before, out = len(self.io.docker.calls), self.tmp / 'second'
        self.assertEqual(self.build('--agents', '--all-images', out=out), 0, self.io.text())
        self.assertFalse({'build', 'save', 'load'} & set(self.verbs()[before:]))
        pk = rr.load_packet(out / 'packet')
        self.assertEqual([p['kind'] for p in pk.publications], ['debs']); self.assertFalse((out / 'packet/runtime').exists())
        self.assertIn('So the packet publishes no runtime image', self.io.text())
        self.assertEqual([m['kept'] for m in json.loads((out / 'build.json').read_text())['agentImages']], [True, True])
        # one script of the commit changes: that agent's image gets one more layer, the other stays the published row
        self.io.tree['isle-agent/isle-remote-agent/lease-hook.sh'] = 'lease v3\n'; out = self.tmp / 'third'
        self.assertEqual(self.build('--agents', out=out), 0, self.io.text())
        rows = json.loads((out / 'packet/runtime/manifest.json').read_text())['runtimes']
        self.assertEqual(rows['core'][0]['image'], core); self.assertEqual(rows['remote'][0]['image']['archive'], {'source': 'images/remote-agent.tar'})
        self.assertEqual(len(made['example/remote-agent:release91']['rootfs']['diff_ids']), 4)
        self.assertEqual(json.loads((out / 'build.json').read_text())['agentImages'][1]['recipeOfBase'], 'the same')

    def test_a_dockerfile_changed_in_another_line_than_copy_needs_the_image_built_whole(self):
        self.io.served.clear(); self.io.docker.images.clear()
        self.rows(True, ({ai.SCRIPTS_LABEL: 'a' * 64, ai.RECIPE_LABEL: ai.recipe(CORE_FILE, 'x')[1]}, {ai.SCRIPTS_LABEL: 'a' * 64, ai.RECIPE_LABEL: 'b' * 64}))
        self.refused('build that image whole', '--agents')
        self.assertIn('isle-remote-agent', self.io.text()); self.assertIn('Dockerfile.remote', self.io.text())

    def test_refused_before_anything_is_written(self):
        cases = []
        def docker_down(): self.io.docker.down = True
        cases.append((docker_down, 'Docker does not answer'))
        def no_core(): self.io.release = None
        cases.append((no_core, 'does not answer from here'))
        def no_agent_row(): self.io.release['runtimes']['remote'] = []
        cases.append((no_agent_row, 'names no image for isle-remote-agent'))
        def elsewhere(): self.io.release['runtimes']['core'][0]['image']['archive']['url'] = 'https://apt.isle/other/file.tar'
        cases.append((elsewhere, 'cannot be used'))
        def not_a_release(): self.io.release = {'runtimes': {'core': 'x'}}
        cases.append((not_a_release, 'cannot be used'))
        for change, words in cases:
            with self.subTest(words=words):
                self.setUp(); change()
                self.assertEqual(self.build('--agents'), 1, self.io.text())
                self.assertIn('REFUSED', self.io.text()); self.assertIn(words, self.io.text()); self.assertFalse(self.out.exists())
        self.setUp()
        self.assertEqual(self.build('--agents', '--runtime-archives', str(self.tmp / 'none')), 1)
        self.assertIn('is not a folder', self.io.text()); self.assertFalse(self.out.exists())

    def test_what_docker_gets_wrong_stops_the_build(self):
        def fails(verb):
            def change(): self.io.docker.fail = verb
            return change
        def more_layers(): self.io.docker.spoil = 'layers'
        def gone(): self.io.docker.images.clear(); self.io.served.clear()
        def another_image():                       # the core serves, under the right checksum, an archive of another image
            self.io.docker.images.clear(); row = self.io.release['runtimes']['core'][1]['image']
            row['configDigest'] = 'sha256:' + 'd' * 64
        def no_script(): del self.io.tree['isle-agent/isle-remote-agent/lease-hook.sh']
        for change, words in ((fails('build'), 'docker build of example/core-agent:release91 failed'), (fails('save'), 'docker image save example/core-agent:release91 failed'),
                              (more_layers, 'plus one layer'), (gone, 'could not be fetched'), (another_image, 'is not the image the core says it publishes'),
                              (no_script, 'the commit holds no such file')):
            with self.subTest(words=words):
                self.setUp(); change()
                self.refused(words, '--agents')

    def test_a_docker_that_does_not_know_platform_saves_what_it_has(self):
        self.io.docker.platform = False
        self.assertEqual(self.build('--agents'), 0, self.io.text())
        self.assertTrue((self.out / 'packet/runtime/images/core-agent.tar').is_file())

    def test_the_words_about_images_need_agents_and_a_release(self):
        for more in (['--all-images'], ['--runtime-archives', str(self.tmp)]):
            self.assertEqual(self.build(*more), 2); self.assertIn('which a build makes with --agents', self.io.text())
        self.assertEqual(rb.main([str(self.out), '--dependencies', str(self.tmp), '--agents'], self.io), 2)
        self.assertIn('--agents is for a release', self.io.text()); self.assertFalse(self.out.exists())


if __name__ == '__main__':
    unittest.main()
