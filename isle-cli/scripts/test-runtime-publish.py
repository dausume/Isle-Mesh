#!/usr/bin/env python3
"""isle update publish-runtime: the files it adds, and an image a release keeps as the core publishes it.
Made-up image archives in a temporary folder that stands for the core's repository; no Docker, no network."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import runtime_publish


def archive(path, ref):
    """An image archive as `docker image save` writes one, with one layer.  Returns the row's image for it."""
    config, layer = json.dumps({'os': 'linux', 'of': ref}).encode(), b'layer of ' + ref.encode()
    name = hashlib.sha256(config).hexdigest() + '.json'
    files = {'manifest.json': json.dumps([{'Config': name, 'RepoTags': [ref], 'Layers': ['l/layer.tar']}]).encode(), name: config, 'l/layer.tar': layer}
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, 'w') as t:
        for member, data in files.items():
            info = tarfile.TarInfo(member); info.size = len(data); t.addfile(info, io.BytesIO(data))
    return {'ref': ref, 'configDigest': 'sha256:' + name[:-5]}


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


class Publish(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory(); self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name); self.repo = self.tmp / 'repo'; (self.repo / 'runtime').mkdir(parents=True)
        self.packet = self.tmp / 'packet'
        # the agent's image comes with the packet; the backend's is the one this core publishes already
        self.agent = dict(archive(self.packet / 'images/agent.tar', 'example/agent:2'), archive={'source': 'images/agent.tar'})
        held = self.tmp / 'held.tar'; backend = archive(held, 'example/backend:1')
        self.kept_file = self.repo / 'runtime' / (sha(held) + '.tar'); held.rename(self.kept_file)
        self.backend = dict(backend, archive={'url': 'https://apt.isle/runtime/' + self.kept_file.name, 'sha256': self.kept_file.stem, 'size': self.kept_file.stat().st_size})

    def publish(self, backend=None):
        m = {'runtimes': {'core': [{'container': 'prf-isle-backend', 'image': backend or self.backend}, {'container': 'isle-vlan-agent', 'image': self.agent}]}}
        (self.packet / 'manifest.json').write_text(json.dumps(m))
        with patch.object(runtime_publish, 'REPO', self.repo): runtime_publish.publish_runtime(self.packet / 'manifest.json')
        return json.loads((self.repo / 'runtime-release.json').read_text())['runtimes']['core']

    def test_a_kept_image_stays_the_published_file_and_the_carried_one_is_added(self):
        before = self.kept_file.read_bytes()
        backend, agent = self.publish()
        self.assertEqual(backend['image'], self.backend)
        added = sha(self.packet / 'images/agent.tar')
        self.assertEqual(agent['image']['archive'], {'url': 'https://apt.isle/runtime/%s.tar' % added, 'sha256': added, 'size': (self.packet / 'images/agent.tar').stat().st_size})
        self.assertEqual(sorted(p.name for p in (self.repo / 'runtime').iterdir()), sorted([self.kept_file.name, added + '.tar']))
        self.assertEqual(self.kept_file.read_bytes(), before)

    def refused(self, words, backend=None):
        with self.assertRaises(ValueError) as caught: self.publish(backend)
        self.assertIn(words, str(caught.exception))
        self.assertFalse((self.repo / 'runtime-release.json').exists(), 'a refused publication wrote the runtime release')
        self.assertFalse([p.name for p in (self.repo / 'runtime').iterdir() if p != self.kept_file], 'a refused publication added a file')

    def test_a_kept_image_this_core_does_not_publish_stops_before_any_file_is_added(self):
        self.kept_file.unlink()
        self.refused('this core does not publish that file')

    def test_a_published_file_that_is_no_longer_that_file_is_refused(self):
        data = bytearray(self.kept_file.read_bytes()); data[-1] ^= 1; self.kept_file.write_bytes(bytes(data))    # the same size, another content
        self.refused('this core does not publish that file')

    def test_a_kept_row_that_names_another_image_than_the_file_holds_is_refused(self):
        self.refused('not present in archive', dict(self.backend, configDigest='sha256:' + 'a' * 64))

    def test_a_row_that_names_a_file_elsewhere_is_refused(self):
        self.refused('Runtime image source required', dict(self.backend, archive=dict(self.backend['archive'], url='https://apt.isle/other/' + self.kept_file.name)))


if __name__ == '__main__':
    unittest.main()
