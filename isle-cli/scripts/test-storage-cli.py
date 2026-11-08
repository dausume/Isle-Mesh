"""Exercise the public dispatch, mocking only the host inventory binaries."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ENTRY = Path(__file__).resolve().parents[1] / 'index.js'


class StorageCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.env = dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH'])

    def binary(self, name, output, status=0):
        path = self.bin / name
        path.write_text('#!/usr/bin/env python3\nimport sys\nprint(' + repr(json.dumps(output)) + ')\nsys.exit(' + str(status) + ')\n')
        path.chmod(0o755)

    def call(self, *args):
        result = subprocess.run(['node', str(ENTRY), 'storage', *args], env=self.env,
                                capture_output=True, text=True, timeout=20)
        return result, json.loads(result.stdout)

    def test_list_preserves_unmounted_identity(self):
        self.binary('lsblk', {'blockdevices': [dict(name='sdc', type='disk', size=1000,
                    rota=False, children=[dict(name='sdc1', type='part', uuid='keep-id', mountpoints=[None])])]})
        result, data = self.call('list')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data['result']['blockDevices'][0]['children'][0]['uuid'], 'keep-id')
        self.assertTrue(data['readOnly'])
        self.assertEqual(data['result']['volumes'], [])

    def test_check_and_wrong_disk_through_dispatch(self):
        self.binary('findmnt', {'filesystems': [dict(target=str(self.root), uuid='disk-id', fstype='ext4', options='rw')]})
        dest = self.root / 'not-created'
        result, data = self.call('check', '--path', str(dest), '--uuid', 'disk-id')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(data['result']['localMountVerified'])
        self.assertFalse(data['result']['bootDependencyVerified'])
        self.assertFalse(dest.exists())
        result, data = self.call('check', '--path', str(dest), '--uuid', 'different')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(data['ok'])
        self.assertIn('UUID', data['error'])
        self.assertFalse(dest.exists())

    def test_failed_inventory_is_not_empty_success(self):
        self.binary('lsblk', {}, 1)
        result, data = self.call('list')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(data['ok'])

    def test_malformed_inventory_is_reported(self):
        self.binary('lsblk', {'blockdevices': None})
        result, data = self.call('list')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(data['ok'])


if __name__ == '__main__':
    unittest.main()
