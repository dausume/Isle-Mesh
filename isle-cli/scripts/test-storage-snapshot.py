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
from storage_snapshot import RESERVED_TOP_LEVEL, encoded, inventory, verify

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

    def _object(self, source, name=None):
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        objects = self.repo / 'snapshot' / 'objects'
        objects.mkdir(parents=True, exist_ok=True)
        os.link(source, objects / (name or sha))
        return sha

    def test_object_link_is_the_only_allowed_extra_link(self):
        sha = self._object(self.repo / 'Packages')
        self.assertEqual(os.stat(self.repo / 'Packages').st_nlink, 2)
        entries = {e['path']: e for e in inventory(str(self.repo))['entries']}
        self.assertEqual(entries['Packages']['sha256'], sha)

    def test_link_elsewhere_or_wrong_object_refused(self):
        os.link(self.repo / 'Packages', self.repo / 'alias')
        with self.assertRaises(ValueError): inventory(str(self.repo))
        (self.repo / 'alias').unlink()
        self._object(self.repo / 'Packages', name='f' * 64)  # linked, but not named by its sha
        with self.assertRaises(ValueError): inventory(str(self.repo))
        (self.repo / 'snapshot' / 'objects' / ('f' * 64)).unlink()
        sha = self._object(self.repo / 'Packages')
        os.link(self.repo / 'Packages', self.repo / 'alias')  # nlink 3
        with self.assertRaises(ValueError): inventory(str(self.repo))
        (self.repo / 'alias').unlink()
        # an object that is a different inode with the same content does not excuse a link
        (self.repo / 'snapshot' / 'objects' / sha).unlink()
        (self.repo / 'snapshot' / 'objects' / sha).write_bytes((self.repo / 'Packages').read_bytes())
        inventory(str(self.repo))  # nlink 1 again, fine
        os.link(self.repo / 'Packages', self.repo / 'alias')
        with self.assertRaises(ValueError): inventory(str(self.repo))

    def test_in_place_write_of_linked_file_refused_naming_object(self):
        old = self._object(self.repo / 'Packages')
        with open(self.repo / 'Packages', 'r+b') as f:  # corrupts the shared inode, and so the object
            f.write(b'CORRUPT')
        self.assertEqual(os.stat(self.repo / 'Packages').st_nlink, 2)
        with self.assertRaises(ValueError) as ctx: inventory(str(self.repo))
        self.assertIn('in-place write corrupted snapshot object ' + old, str(ctx.exception))
        # sibling not identifiable (object removed, link is elsewhere): the generic wording
        (self.repo / 'snapshot' / 'objects' / old).unlink()
        os.link(self.repo / 'Packages', self.repo / 'alias')
        with self.assertRaises(ValueError) as ctx: inventory(str(self.repo))
        self.assertIn('extra hard link that is not its own snapshot object', str(ctx.exception))

    def test_duplicate_content_distinct_inodes_accepted(self):
        (self.repo / 'copy-of-packages').write_text('fixture package index')
        entries = inventory(str(self.repo))['entries']
        shas = [e['sha256'] for e in entries if e['path'] in ('Packages', 'copy-of-packages')]
        self.assertEqual(len(shas), 2); self.assertEqual(shas[0], shas[1])

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

    def test_root_snapshot_directory_is_reserved_and_excluded(self):
        before = encoded(inventory(str(self.repo)))
        (self.repo / 'snapshot').mkdir()
        (self.repo / 'snapshot' / 'latest.json').write_text('{}')
        (self.repo / 'snapshot' / 'deep').mkdir()
        after = inventory(str(self.repo))
        self.assertEqual(RESERVED_TOP_LEVEL, ('snapshot',))
        self.assertFalse(any(e['path'].startswith('snapshot') for e in after['entries']))
        self.assertEqual(before, encoded(after))

    def test_nested_snapshot_directory_is_ordinary_content(self):
        (self.repo / 'runtime' / 'snapshot').mkdir()
        (self.repo / 'runtime' / 'snapshot' / 'f').write_text('x')
        paths = [e['path'] for e in inventory(str(self.repo))['entries']]
        self.assertIn('runtime/snapshot', paths)
        self.assertIn('runtime/snapshot/f', paths)

    def test_root_file_named_snapshot_is_content(self):
        (self.repo / 'snapshot').write_text('a file, not the reserved directory')
        paths = [e['path'] for e in inventory(str(self.repo))['entries']]
        self.assertIn('snapshot', paths)

    def _bound(self, mutate=lambda d: None):
        document = dict(inventory(str(self.repo)), binding=dict(dataset='d', sourceDevice='core-a', takenAt='t', lockPath='/l', planId=None, itemId=None))
        mutate(document)
        raw = encoded(document)
        path = self.root / 'bound.json'; path.write_bytes(raw)
        return path, hashlib.sha256(raw).hexdigest()

    def test_manifest_with_binding_verifies(self):
        path, digest = self._bound()
        self.assertTrue(verify(str(self.repo), path, digest)['contentVerified'])
        self.assertNotIn('binding', inventory(str(self.repo)))

    def test_binding_content_does_not_affect_content_comparison(self):
        path, digest = self._bound(lambda d: d['binding'].update(sourceDevice='member-b', extra=1))
        self.assertTrue(verify(str(self.repo), path, digest)['contentVerified'])

    def test_manifest_with_differing_inventory_still_fails_despite_binding(self):
        path, digest = self._bound(lambda d: d['entries'].pop())
        with self.assertRaises(ValueError): verify(str(self.repo), path, digest)
        path, _ = self._bound()
        with self.assertRaises(ValueError): verify(str(self.repo), path, '0' * 64)

    def test_cli_snapshot_and_verify(self):
        cli = SCRIPTS.parent / 'index.js'
        first = subprocess.run(['node', str(cli), 'storage', 'snapshot', '--path', str(self.repo)], capture_output=True, check=True)
        path = self.root / 'manifest.json';path.write_bytes(first.stdout)
        result = subprocess.run(['node', str(cli), 'storage', 'verify', '--path', str(self.repo), '--manifest', str(path), '--sha256', hashlib.sha256(first.stdout).hexdigest()], capture_output=True, check=True)
        self.assertTrue(json.loads(result.stdout)['result']['contentVerified'])


if __name__ == '__main__': unittest.main()
