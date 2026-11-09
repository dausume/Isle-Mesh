import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from repository_snapshot import snapshot
from repository_lock import publication_lock
from storage_snapshot import encoded, inventory, verify

SCRIPTS = Path(__file__).resolve().parent


class Snapshots(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        (self.repo / 'Packages').write_text('fixture package index')
        (self.repo / 'runtime').mkdir()
        (self.repo / 'runtime' / 'image.tar').write_bytes(b'archive fixture')

    def manifest(self):
        raw = snapshot(self.repo)
        path = self.root / 'manifest.json'
        path.write_bytes(raw)
        return path, hashlib.sha256(raw).hexdigest()

    def test_public_tree_only_deterministic(self):
        (self.root / 'gnupg').mkdir()
        (self.root / 'gnupg' / 'private.key').write_text('DO NOT TRANSFER')
        first = snapshot(self.repo)
        self.assertEqual(first, snapshot(self.repo))
        self.assertNotIn(b'private', first)
        path, digest = self.manifest()
        result = verify(str(self.repo), path, digest)
        self.assertTrue(result['contentVerified'])
        self.assertFalse(result['serviceReadyVerified'])

    def test_extra_changed_missing_each_refuse(self):
        path, digest = self.manifest()
        extra = self.repo / 'unexpected'
        extra.write_text('extra')
        with self.assertRaisesRegex(ValueError, 'differs'): verify(str(self.repo), path, digest)
        extra.unlink()
        original = (self.repo / 'Packages').read_bytes()
        (self.repo / 'Packages').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'differs'): verify(str(self.repo), path, digest)
        (self.repo / 'Packages').write_bytes(original)
        (self.repo / 'runtime' / 'image.tar').unlink()
        with self.assertRaisesRegex(ValueError, 'differs'): verify(str(self.repo), path, digest)

    def test_tampered_manifest_refuses(self):
        path, digest = self.manifest()
        path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'checksum'): verify(str(self.repo), path, digest)

    def test_no_link_or_special_file_escape(self):
        p = self.repo / 'escape'
        for create in (lambda: p.symlink_to('/etc/passwd'), lambda: p.symlink_to('/etc'),
                       lambda: os.link(self.repo / 'Packages', p), lambda: os.mkfifo(p)):
            create()
            with self.assertRaises(ValueError): inventory(str(self.repo))
            p.unlink()

    def test_partial_and_hidden_refused(self):
        for name in ('image.tar.part', 'image.partial', '.secret'):
            path = self.repo / name
            path.write_text('incomplete')
            with self.assertRaisesRegex(ValueError, 'incomplete'): inventory(str(self.repo))
            path.unlink()

    def test_snapshot_waits_for_publication_lock(self):
        with publication_lock(self.repo):
            child = subprocess.Popen([sys.executable, '-c',
                'from pathlib import Path;from repository_snapshot import snapshot;import sys;sys.stdout.buffer.write(snapshot(Path(sys.argv[1])))', str(self.repo)],
                cwd=SCRIPTS, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                with self.assertRaises(subprocess.TimeoutExpired): child.communicate(timeout=0.2)
                (self.repo / 'Packages').write_text('completed publication')
            except BaseException:
                child.kill();child.communicate();raise
        stdout, stderr = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 0, stderr)
        self.assertEqual(stdout, encoded(inventory(str(self.repo))))

    def test_cli_snapshot_and_verify(self):
        cli = SCRIPTS.parent / 'index.js'
        first = subprocess.run(['node', str(cli), 'storage', 'snapshot', '--path', str(self.repo)], capture_output=True, check=True)
        path = self.root / 'manifest.json';path.write_bytes(first.stdout)
        result = subprocess.run(['node', str(cli), 'storage', 'verify', '--path', str(self.repo), '--manifest', str(path), '--sha256', hashlib.sha256(first.stdout).hexdigest()], capture_output=True, check=True)
        self.assertTrue(json.loads(result.stdout)['result']['contentVerified'])


if __name__ == '__main__': unittest.main()
