#!/usr/bin/env python3
"""isle store build, and the registry release it builds.  No Docker and no network: `docker` is a made-up
program on PATH that writes real image archives, and publish is run into a temporary folder."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import catalog_artifacts as ca
import catalog_build as cb
import catalog_publish
import image_identity
import storage_slots

SCRIPTS = Path(__file__).resolve().parent
RELEASES = SCRIPTS.parent / 'releases'
REGISTRY = RELEASES / 'registry'
BUILT = 'registry/registry:2026.11.1'
FAKE_DOCKER = r'''#!/usr/bin/env python3
import hashlib, io, json, os, sys, tarfile
a = sys.argv[1:]
open(os.environ['FAKE_LOG'], 'a').write(' '.join(a) + '\n')
have = os.environ['FAKE_HAVE']
h = lambda b: hashlib.sha256(b).hexdigest()
def tar(path, files):
    with tarfile.open(path, 'w') as t:
        for name, data in files.items():
            i = tarfile.TarInfo(name); i.size = len(data); t.addfile(i, io.BytesIO(data))
if a[:2] == ['image', 'inspect']:
    sys.exit(0 if a[-1] in open(have).read().split() else 1)
if a[0] == 'pull':
    if os.environ.get('FAKE_PULL_FAILS'): sys.exit(1)
    open(have, 'a').write(a[1] + '\n'); sys.exit(0)
if a[0] == 'tag': sys.exit(0)
if a[:2] == ['image', 'save']:
    rest = a[2:]
    platform = rest[:2] == ['--platform', 'linux/amd64']
    if platform and os.environ.get('FAKE_NO_PLATFORM'):
        print('unknown flag: --platform', file=sys.stderr); sys.exit(125)
    if platform: rest = rest[2:]
    if rest[0] != '-o' or len(rest) != 3 or os.environ.get('FAKE_SAVE_FAILS'):
        print('fake: no space left on device', file=sys.stderr); sys.exit(1)
    path, ref = rest[1], rest[2]
    layer, config = b'layer of ' + ref.encode(), json.dumps(dict(os='linux', architecture='amd64', of=ref)).encode()
    if not platform:
        tar(path, {'manifest.json': json.dumps([dict(Config=h(config) + '.json', RepoTags=[ref], Layers=['l/layer.tar'])]).encode(),
                   h(config) + '.json': config, 'l/layer.tar': layer}); sys.exit(0)
    manifest = json.dumps(dict(schemaVersion=2, config=dict(digest='sha256:' + h(config)), layers=[dict(digest='sha256:' + h(layer))])).encode()
    kind = 'application/vnd.oci.image.%s.v1+json' % ('index' if os.environ.get('FAKE_ROOT') == 'index' else 'manifest')
    index = dict(manifests=[dict(mediaType=kind, digest='sha256:' + h(manifest), annotations={'io.containerd.image.name': 'docker.io/' + ref})])
    tar(path, {'index.json': json.dumps(index).encode(), 'blobs/sha256/' + h(manifest): manifest,
               'blobs/sha256/' + h(config): config, 'blobs/sha256/' + h(layer): layer}); sys.exit(0)
sys.exit(1)
'''


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / 'bin').mkdir()
        fake = self.tmp / 'bin' / 'docker'
        fake.write_text(FAKE_DOCKER); fake.chmod(0o755)
        self.log, self.have, self.out = self.tmp / 'log', self.tmp / 'have', self.tmp / 'out'
        self.log.write_text(''); self.have.write_text('registry:2\n')

    def run_build(self, *words, env=None, verb=False):
        full = dict(os.environ, PATH='%s:%s' % (self.tmp / 'bin', os.environ['PATH']), FAKE_LOG=str(self.log), FAKE_HAVE=str(self.have), **(env or {}))
        head = ['bash', str(SCRIPTS / 'store.sh'), 'build'] if verb else [sys.executable, str(SCRIPTS / 'catalog_build.py')]
        return subprocess.run(head + [str(w) for w in words], capture_output=True, text=True, env=full, timeout=60)

    def calls(self):
        return self.log.read_text().splitlines()

    def definition(self, change=None, compose=None):
        """A copy of the registry definition, changed."""
        copy = self.tmp / 'definition'
        shutil.copytree(REGISTRY, copy)
        m = json.loads((copy / 'release.json').read_text())
        if change: change(m)
        (copy / 'release.json').write_text(json.dumps(m))
        if compose: (copy / 'docker-compose.yml').write_text(compose((copy / 'docker-compose.yml').read_text()))
        return copy

    def refused(self, words, definition, *more, **kw):
        done = self.run_build(definition, self.out, *more, **kw)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn(words, done.stderr)
        self.assertFalse((self.out / 'release.json').exists(), 'a refused build left something to publish')
        return done


class TheRegistryRelease(unittest.TestCase):
    def setUp(self):
        self.m = json.loads((REGISTRY / 'release.json').read_text())

    def test_it_is_a_definition_the_builder_reads(self):
        m, pairs = cb.read(REGISTRY)
        self.assertEqual((m['name'], m['category']), ('registry', 'infrastructure'))
        self.assertEqual([(image['buildFrom'], ref) for image, ref in pairs], [('registry:2', BUILT)])
        self.assertTrue((REGISTRY / 'PINS-PENDING').is_file())

    def test_its_images_live_in_one_slot_of_the_role_registry(self):
        (slot,) = storage_slots.declared(self.m['storageSlots'])
        self.assertEqual({k: slot[k] for k in ('name', 'role', 'kind', 'target', 'service', 'writable', 'distribution', 'association', 'copies', 'lifetime')},
                         dict(name='images', role='registry', kind='image-registry', target='/var/lib/registry', service='registry',
                              writable=True, distribution='served', association='isle', copies=1, lifetime='until-removed'))

    def test_it_answers_behind_the_agent_with_no_upload_limit(self):
        self.assertEqual(self.m['deploy'], dict(service='registry', port=5000, maxUpload='0'))
        self.assertEqual(self.m['health'], dict(path='/v2/'))
        self.assertNotIn('domain', self.m['deploy'])   # so its name is <instance>.isle: registry.isle, the name the CLI looks for

    def test_its_compose_file_opens_no_port_of_the_machine_and_keeps_no_data_of_its_own(self):
        compose = (REGISTRY / 'docker-compose.yml').read_text()
        self.assertIn('    image: registry/registry:PIN-AT-BUILD\n', compose)
        self.assertIn('    restart: "no"\n', compose)
        self.assertIn('    expose:\n      - "5000"\n', compose)
        for word in ('ports:', 'volumes:', 'privileged', 'network_mode', 'build:'):
            self.assertNotIn(word, compose)


class Build(Case):
    def test_the_registry_is_built_from_the_image_on_the_machine_and_nothing_is_downloaded(self):
        before = {p.name: p.read_bytes() for p in REGISTRY.iterdir()}
        done = self.run_build('registry', self.out, verb=True)   # the verb, and a definition named by its name
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.calls(), ['image inspect --format {{.Id}} registry:2', 'tag registry:2 ' + BUILT,
                                        'image save --platform linux/amd64 -o %s %s' % (self.out / 'images' / 'registry.tar', BUILT)])
        self.assertEqual({p.name: p.read_bytes() for p in REGISTRY.iterdir()}, before, 'the definition was changed')
        result = json.loads(done.stdout)['result']
        m = json.loads((self.out / 'release.json').read_text())
        (image,) = m['images']
        self.assertEqual(image['ref'], BUILT)
        self.assertNotIn('buildFrom', image)
        self.assertEqual(image_identity.verify_archive(self.out / 'images' / 'registry.tar', image), {image['configDigest'], image['manifestDigest']})
        self.assertEqual((result['images'][0]['configDigest'], result['images'][0]['builtFrom']), (image['configDigest'], 'registry:2'))
        compose = (self.out / 'docker-compose.yml').read_text()
        self.assertIn('    image: %s\n' % BUILT, compose)
        self.assertNotIn('PIN-AT-BUILD', compose + json.dumps(m))
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ['docker-compose.yml', 'images', 'release.json'])
        self.assertEqual((m['deploy']['maxUpload'], m['storageSlots'][0]['role']), ('0', 'registry'))
        # What publish stores is what the build said it would be.
        name, stored = catalog_publish._publish(self.out / 'release.json', self.tmp / 'repo')
        self.assertEqual((name, stored['sha256']), ('registry', result['manifestSha256']))
        kept = json.loads((self.tmp / 'repo' / 'catalog' / 'registry' / '2026.11.1' / (stored['sha256'] + '.json')).read_text())
        ca.validate_manifest(kept)
        self.assertIn('Nothing was published', done.stderr)

    def test_an_image_that_is_not_on_the_machine_stops_the_build(self):
        self.have.write_text('')
        self.refused('Nothing is downloaded without --pull', REGISTRY)
        self.assertEqual(self.calls(), ['image inspect --format {{.Id}} registry:2'])

    def test_pull_is_the_explicit_choice(self):
        self.have.write_text('')
        done = self.run_build(REGISTRY, self.out, '--pull')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.calls()[:2], ['pull registry:2', 'tag registry:2 ' + BUILT])

    def test_a_pull_that_fails_stops_the_build(self):
        self.refused('docker pull registry:2 failed', REGISTRY, '--pull', env=dict(FAKE_PULL_FAILS='1'))
        self.assertEqual(self.calls(), ['pull registry:2'])

    def test_an_archive_of_several_platforms_is_refused(self):
        self.refused('holds several platforms', REGISTRY, env=dict(FAKE_ROOT='index'))

    def test_a_docker_without_the_platform_option_gives_an_archive_without_a_manifest_digest(self):
        done = self.run_build(REGISTRY, self.out, env=dict(FAKE_NO_PLATFORM='1'))
        self.assertEqual(done.returncode, 0, done.stderr)
        (image,) = json.loads((self.out / 'release.json').read_text())['images']
        self.assertNotIn('manifestDigest', image)
        name, stored = catalog_publish._publish(self.out / 'release.json', self.tmp / 'repo')
        self.assertEqual(stored['sha256'], json.loads(done.stdout)['result']['manifestSha256'])

    def test_a_save_that_fails_leaves_nothing_to_publish(self):
        self.refused('no space left on device', REGISTRY, env=dict(FAKE_SAVE_FAILS='1'))

    def test_the_output_is_an_empty_place_outside_the_definition(self):
        copy = self.definition()
        self.out = copy / 'out'
        self.refused('outside the definition folder', copy)
        self.out = self.tmp / 'out'
        self.assertEqual(self.run_build(REGISTRY, self.out).returncode, 0)
        done = self.run_build(REGISTRY, self.out)
        self.assertEqual(done.returncode, 1)
        self.assertIn('holds a release already', done.stderr)

    def test_the_words_of_the_verb(self):
        for words in ([], [REGISTRY], [REGISTRY, self.out, 'more'], [REGISTRY, self.out, '--push']):
            done = self.run_build(*words)
            self.assertEqual((done.returncode, done.stderr.strip()), (2, cb.USAGE), words)
        self.assertEqual(self.calls(), [])
        usage = subprocess.run(['bash', str(SCRIPTS / 'store.sh'), 'nothing-like-this'], capture_output=True, text=True).stdout
        self.assertIn('build <definition> <output folder> [--pull]', usage)


class Definitions(Case):
    """Each is refused before Docker is asked anything."""

    def check(self, words, change=None, compose=None):
        self.refused(words, self.definition(change, compose))
        self.assertEqual(self.calls(), [], 'Docker was asked before the definition was read whole')

    def test_an_image_says_what_it_is_built_from(self):
        self.check('needs buildFrom', lambda m: m['images'][0].pop('buildFrom'))

    def test_an_image_is_never_built_from_itself_or_from_an_option(self):
        for bad in ('registry/registry:2026.11.1', '--all-tags', 'a b', ''):
            with self.subTest(bad):
                self.setUp()
                self.check('needs buildFrom', lambda m: m['images'][0].update(buildFrom=bad))

    def test_a_definition_carries_no_digest_of_its_own(self):
        self.check('already carries a digest', lambda m: m['images'][0].update(configDigest='sha256:' + 'a' * 64))

    def test_an_image_is_named_after_its_release(self):
        self.check('is named registry/<label>:PIN-AT-BUILD', lambda m: m['images'][0].update(ref='other/registry:PIN-AT-BUILD'))

    def test_the_version_is_also_a_tag(self):
        self.check('usable as the tag of an image', lambda m: m.update(version='1.0+b'))

    def test_no_path_leaves_the_folders(self):
        self.check('images/<name>.tar', lambda m: m['images'][0]['archive'].update(source='registry.tar'))
        self.setUp()
        self.check('inside the definition folder', lambda m: m['images'][0]['archive'].update(source='images/../../x.tar'))
        self.setUp()
        self.check('inside the definition folder', lambda m: m['compose'].update(source='/etc/hostname'))
        self.setUp()
        self.check('has no file', lambda m: m.update(files=[dict(path='x', source='missing.conf')]))

    def test_the_compose_file_runs_the_images_of_the_release_and_no_other(self):
        self.check('does not run', compose=lambda t: t.replace('registry/registry:PIN-AT-BUILD', 'registry:2'))
        self.setUp()
        self.check('an image the release does not hold', compose=lambda t: t + '  other:\n    image: nginx:latest\n')

    def test_what_an_install_would_refuse_is_refused_here(self):
        self.check('needs a role', lambda m: m['storageSlots'][0].update(role='images'))
        self.setUp()
        self.check('Invalid upload size', lambda m: m['deploy'].update(maxUpload='2GB'))
        self.setUp()
        self.check('Invalid release service/port', lambda m: m['deploy'].update(port='5000'))


if __name__ == '__main__':
    unittest.main()
