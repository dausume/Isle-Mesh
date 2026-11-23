#!/usr/bin/env python3
"""isle release build --store-app: a store app, as `isle store build` left it, in a release packet.
The made-up builders of test-release-build.py; a built store release is written by hand.  No Docker, no network."""
import hashlib
import importlib.util
import io as bytes_io
import json
import tarfile
from pathlib import Path
import unittest

import catalog_build as cb
import catalog_publish
import release_build as rb
import release_run as rr

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('release_build_tests', HERE / 'test-release-build.py')
base = importlib.util.module_from_spec(spec); spec.loader.exec_module(base)

def archive(path, ref):
    """An image archive as Docker saves one for a platform; -> what `isle store build` reads out of it."""
    h = lambda data: hashlib.sha256(data).hexdigest()
    layer, config = b'layer of ' + ref.encode(), json.dumps(dict(os='linux', architecture='amd64', of=ref)).encode()
    manifest = json.dumps(dict(schemaVersion=2, config=dict(digest='sha256:' + h(config)), layers=[dict(digest='sha256:' + h(layer))])).encode()
    index = dict(manifests=[dict(mediaType='application/vnd.oci.image.manifest.v1+json', digest='sha256:' + h(manifest),
                                 annotations={'io.containerd.image.name': 'docker.io/' + ref})])
    with tarfile.open(path, 'w') as tar:
        for name, data in (('index.json', json.dumps(index).encode()), ('blobs/sha256/' + h(manifest), manifest),
                           ('blobs/sha256/' + h(config), config), ('blobs/sha256/' + h(layer), layer)):
            info = tarfile.TarInfo(name); info.size = len(data); tar.addfile(info, bytes_io.BytesIO(data))
    found = cb.identity(path, ref)
    return {key: found[key] for key in ('configDigest', 'manifestDigest') if key in found}


def built(folder, name='esp-engines', version='2026.11.22-1'):
    """A folder as `isle store build` leaves it."""
    (folder / 'images').mkdir(parents=True)
    found = archive(folder / 'images/engine.tar', '%s/engine:%s' % (name, version))
    (folder / 'docker-compose.yml').write_text('services:\n  %s:\n    image: %s/engine:%s\n' % (name, name, version))
    (folder / 'notes.txt').write_text('a file of the release\n')
    release = {'schemaVersion': 1, 'name': name, 'version': version,
               'images': [{'ref': '%s/engine:%s' % (name, version), 'archive': {'source': 'images/engine.tar'}, **found}],
               'compose': {'source': 'docker-compose.yml'}, 'files': [{'path': 'notes.txt', 'source': 'notes.txt'}],
               'deploy': {'service': name, 'port': 9850, 'engine': 'esp'}, 'health': {'path': '/capability'}}
    (folder / 'release.json').write_text(json.dumps(release, indent=2) + '\n')
    return folder


class Apps(unittest.TestCase):
    setUp = base.Build.setUp
    build = base.Build.build
    refused = base.Build.refused

    def doc(self): return json.loads((self.out / 'packet/release-run.json').read_text())

    def test_a_release_carries_the_app_and_the_device_installs_it_after_its_update(self):
        esp = built(self.tmp / 'esp')
        self.assertEqual(self.build('--backup', '--store-app', 'econ:%s' % esp), 0, self.io.text())
        doc, sha = self.doc(), cb.published(json.loads((esp / 'release.json').read_text()), esp)[1]
        every = [{'verb': 'backup'}, {'verb': 'update-apply', 'releaseId': 'published'}]
        install = {'verb': 'store-install', 'app': 'esp-engines', 'version': '2026.11.22-1', 'manifestSha256': sha}
        self.assertEqual(doc['devices'], {'*': every, 'econ': [install]})
        self.assertEqual(rr.steps_for(doc, 'econ'), every + [install])                 # the runner: the update first
        self.assertEqual(doc['publish'], [{'kind': 'debs', 'dir': 'debs'}, {'kind': 'catalog', 'release': 'catalog/esp-engines/release.json'}])
        pk = rr.load_packet(self.out / 'packet')
        self.assertEqual([(p['kind'], p.get('app')) for p in pk.publications], [('debs', None), ('catalog', 'esp-engines')])
        for name in ('release.json', 'images/engine.tar', 'docker-compose.yml', 'notes.txt'):
            self.assertEqual((self.out / 'packet/catalog/esp-engines' / name).read_bytes(), (esp / name).read_bytes())
            self.assertIn('catalog/esp-engines/' + name, pk.files)
        made = json.loads((self.out / 'build.json').read_text())['storeApps']
        self.assertEqual([(m['name'], m['version'], m['manifestSha256'], m['devices']) for m in made], [('esp-engines', '2026.11.22-1', sha, ['econ'])])
        self.assertIn('The packet carries the store app esp-engines 2026.11.22-1', self.io.text())
        self.assertIn('econ installs it after its other steps', self.io.text())

    def test_the_sha_in_the_step_is_the_one_the_core_gets_when_it_publishes_the_folder_of_the_packet(self):
        esp = built(self.tmp / 'esp')
        self.assertEqual(self.build('--store-app', 'econ:%s' % esp), 0, self.io.text())
        step = self.doc()['devices']['econ'][0]
        name, stored = catalog_publish._publish(self.out / 'packet/catalog/esp-engines/release.json', self.tmp / 'served')
        self.assertEqual((name, stored['sha256']), ('esp-engines', step['manifestSha256']))
        kept = self.tmp / 'served/catalog/esp-engines/2026.11.22-1' / (stored['sha256'] + '.json')
        self.assertEqual(hashlib.sha256(kept.read_bytes()).hexdigest(), step['manifestSha256'])

    def test_several_apps_and_one_app_on_two_devices(self):
        esp, scope = built(self.tmp / 'esp'), built(self.tmp / 'scope', name='scope-engines')
        self.assertEqual(self.build('--device', 'core', '--device', 'econ', '--device', 'pol', '--store-app', 'econ:%s' % esp,
                                    '--store-app', 'pol:%s' % scope, '--store-app', 'pol:%s' % esp), 0, self.io.text())
        doc = self.doc()
        self.assertEqual([p['release'] for p in doc['publish'][1:]], ['catalog/esp-engines/release.json', 'catalog/scope-engines/release.json'])
        self.assertEqual([s.get('app') for s in doc['devices']['econ']], [None, 'esp-engines'])
        self.assertEqual([s.get('app') for s in doc['devices']['pol']], [None, 'scope-engines', 'esp-engines'])
        self.assertEqual([s['verb'] for s in doc['devices']['core']], ['update-apply'])
        made = {m['name']: m['devices'] for m in json.loads((self.out / 'build.json').read_text())['storeApps']}
        self.assertEqual(made, {'esp-engines': ['econ', 'pol'], 'scope-engines': ['pol']})
        rr.load_packet(self.out / 'packet')

    def test_store_apps_alone_build_no_package(self):
        esp = built(self.tmp / 'esp')
        self.assertEqual(rb.main([str(self.out), '--store-app', 'econ:%s' % esp, '--verify', 'https://esp-engines.isle/capability'], self.io), 0, self.io.text())
        doc = self.doc()
        self.assertEqual(doc['name'], 'store-apps-esp-engines')
        self.assertEqual(doc['publish'], [{'kind': 'catalog', 'release': 'catalog/esp-engines/release.json'}])
        self.assertEqual([s['verb'] for s in doc['devices']['econ']], ['store-install'])
        self.assertEqual(doc['verify'], [{'url': 'https://esp-engines.isle/capability', 'expectStatus': 200}])
        self.assertFalse((self.out / 'members').exists()); self.assertFalse((self.out / 'packet/debs').exists())
        self.assertFalse([call for call, _ in self.io.calls if call[0] in ('git', 'bash')])       # nothing was built
        pk = rr.load_packet(self.out / 'packet')
        self.assertEqual(json.loads((self.out / 'build.json').read_text())['runId'], pk.run_id)
        self.assertIn('No package of the isle is in it', self.io.text())

    def test_refused_before_anything_is_written(self):
        esp, other = built(self.tmp / 'esp'), built(self.tmp / 'other')
        definition = built(self.tmp / 'definition')
        (definition / 'release.json').write_text((definition / 'release.json').read_text().replace('engine:2026.11.22-1', 'engine:' + cb.PIN))
        missing = built(self.tmp / 'missing'); (missing / 'images/engine.tar').unlink()
        unfit = built(self.tmp / 'unfit')
        doc = json.loads((unfit / 'release.json').read_text()); doc['deploy']['port'] = 0
        (unfit / 'release.json').write_text(json.dumps(doc))
        for more, words in ((['--store-app', 'econ'], 'wants <device>:<folder'),
                            (['--store-app', 'econ core:%s' % esp], 'wants <device>:<folder'),
                            (['--store-app', 'econ:%s' % (self.tmp / 'none')], 'holds no release.json'),
                            (['--store-app', 'econ:%s' % definition], 'is a definition, not a built release'),
                            (['--store-app', 'econ:%s' % missing], 'which is not in the folder'),
                            (['--store-app', 'econ:%s' % unfit], 'is not a release the store takes'),
                            (['--store-app', 'econ:%s' % esp, '--store-app', 'pol:%s' % other], 'names two folders for the store app esp-engines'),
                            (['--store-app', 'econ:%s' % esp, '--store-app', 'econ:%s' % esp], 'on econ twice'),
                            (['--device', 'core', '--store-app', 'econ:%s' % esp], 'which this packet does not name')):
            with self.subTest(more=more):
                self.refused(words, *more); self.assertFalse(self.out.exists())

    def test_a_set_and_apps_without_a_release_are_not_one_packet(self):
        esp = built(self.tmp / 'esp')
        self.assertEqual(rb.main([str(self.out), '--dependencies', str(self.tmp), '--store-app', 'econ:%s' % esp], self.io), 2)
        self.assertFalse(self.out.exists())


if __name__ == '__main__':
    unittest.main()
