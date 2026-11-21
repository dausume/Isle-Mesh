"""isle storage device list | partition list: fixtures only, no real disk is read.

The class and the role the listings carry come from the storage records and are tested in test-storage-roles.py."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import storage_devices as sd

ENTRY = Path(__file__).resolve().parents[1] / 'index.js'

TREE = {'blockdevices': [
    dict(name='loop0', type='loop', size=10, mountpoints=['/snap/x']),
    dict(name='sda', type='disk', size=128000000000, rota=False, tran='sata', rm=False, hotplug=False, ro=False,
         serial=' S1 ', model='System SSD ', fstype=None, children=[
             dict(name='sda1', type='part', size=500000000, fstype='vfat', uuid='EFI-1', partuuid='p1', mountpoints=['/boot/efi']),
             dict(name='sda2', type='part', size=127000000000, fstype='ext4', uuid='root-1', partuuid='p2',
                  mountpoints=['/var/snap/thing', '/'])]),
    dict(name='sdb', type='disk', size=500000000000, rota=True, tran='sata', rm=False, hotplug=False, ro=False,
         serial='S2', model='Archive', children=[
             dict(name='sdb1', type='part', size=499000000000, fstype='ext4', uuid='arch-1', label='Media',
                  mountpoints=['/mnt/archive'])]),
    dict(name='sdc', type='disk', size=2000000000000, rota=True, tran='usb', rm=False, hotplug=True, ro=False,
         serial='S3', model='Blank', children=[
             dict(name='sdc1', type='part', size=700000000000, fstype=None, uuid=None, mountpoints=[None]),
             dict(name='sdc2', type='part', size=700000000000, fstype='ext4', uuid='spare-1', mountpoints=[None]),
             dict(name='sdc3', type='part', size=600000000000, fstype='crypto_LUKS', uuid='luks-1', mountpoints=[None],
                  children=[dict(name='open', type='crypt', fstype='ext4', uuid='inner', mountpoints=['/home'])])]),
    dict(name='sdd', type='disk', size=250000000000, rota=False, tran='usb', rm=True, hotplug=False, ro=True,
         serial=None, model=None, fstype='ext4', uuid='whole-1', mountpoints=['/mnt/whole']),
    dict(name='nvme0n1', type='disk', size=1000, rota=False, tran='nvme', serial='S5'),
    dict(name='sr0', type='rom', size=1, serial='R'),
]}
DATASETS = [dict(dataset='apt-public-repo', filesystemUuid='arch-1'), dict(dataset='zeta', filesystemUuid='arch-1'),
            dict(dataset='hidden', filesystemUuid='luks-1')]


def by(rows, name):
    return next(r for r in rows if r['name'] == name)


class Devices(unittest.TestCase):
    def test_only_disks_with_identity(self):
        rows = sd.devices(TREE)
        self.assertEqual([r['name'] for r in rows], ['sda', 'sdb', 'sdc', 'sdd', 'nvme0n1'])
        self.assertEqual((by(rows, 'sda')['serial'], by(rows, 'sda')['model']), ('S1', 'System SSD'))
        self.assertEqual(by(rows, 'sda')['identity'], 'serial')
        self.assertIsNone(by(rows, 'sdd')['serial'])
        self.assertTrue(by(rows, 'sdd')['identity'].startswith('none'))

    def test_medium_system_hotplug(self):
        rows = sd.devices(TREE)
        self.assertEqual([r['medium'] for r in rows], ['solid-state', 'rotational', 'rotational', 'solid-state', 'nvme'])
        self.assertEqual([r['system'] for r in rows], [True, False, True, False, False])  # sdc: /home inside encryption
        self.assertEqual([r['hotplug'] for r in rows], [False, False, True, True, False])
        self.assertTrue(by(rows, 'sdd')['readOnly'])
        self.assertEqual(sd._medium({}), 'unknown')

    def test_partition_counts(self):
        rows = sd.devices(TREE)
        self.assertEqual([r['partitions'] for r in rows], [2, 1, 3, 0, 0])
        self.assertTrue(by(rows, 'sdd')['wholeDeviceFilesystem'])
        self.assertFalse(by(rows, 'nvme0n1')['wholeDeviceFilesystem'])


class Partitions(unittest.TestCase):
    def rows(self, usage=lambda path: (1000, 400)):
        return sd.partitions(TREE, DATASETS, usage)

    def test_states(self):
        rows = self.rows()
        self.assertEqual([(r['name'], r['state']) for r in rows], [
            ('sda1', 'mounted'), ('sda2', 'mounted'), ('sdb1', 'mounted'), ('sdc1', 'no filesystem reported'),
            ('sdc2', 'not mounted'), ('sdc3', 'not a plain filesystem'), ('sdd', 'mounted')])

    def test_identity_and_device(self):
        rows = self.rows()
        self.assertEqual(by(rows, 'sdb1')['filesystemUuid'], 'arch-1')
        self.assertEqual((by(rows, 'sdb1')['device'], by(rows, 'sdb1')['deviceSerial']), ('sdb', 'S2'))
        self.assertIsNone(by(rows, 'sdc1')['filesystemUuid'])
        self.assertIsNone(by(rows, 'sdc3')['filesystemUuid'])  # an encryption header is not a filesystem to bind to
        self.assertTrue(by(rows, 'sdd')['wholeDevice'])
        self.assertFalse(by(rows, 'sdb1')['wholeDevice'])

    def test_system_and_shortest_mount(self):
        rows = self.rows()
        self.assertEqual(by(rows, 'sda2')['mount'], '/')
        self.assertEqual([r['name'] for r in rows if r['system']], ['sda1', 'sda2', 'sdc3'])

    def test_datasets_only_on_the_exact_plain_filesystem(self):
        rows = self.rows()
        self.assertEqual(by(rows, 'sdb1')['datasets'], ['apt-public-repo', 'zeta'])
        self.assertEqual(by(rows, 'sdc3')['datasets'], [])
        self.assertEqual(by(rows, 'sdc2')['datasets'], [])

    def test_capacity_only_where_mounted(self):
        rows = self.rows()
        self.assertEqual((by(rows, 'sdb1')['totalBytes'], by(rows, 'sdb1')['availableBytes']), (1000, 400))
        self.assertNotIn('availableBytes', by(rows, 'sdc2'))

        def broken(path):
            raise OSError('gone')
        rows = self.rows(broken)
        self.assertEqual(by(rows, 'sdb1')['capacityStatus'], 'not readable')
        self.assertNotIn('availableBytes', by(rows, 'sdb1'))


class LocalDatasets(unittest.TestCase):
    def test_reads_bindings_and_skips_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(sd.local_datasets(str(root / 'absent')), ([], 'none'))
            (root / 'good').mkdir()
            (root / 'good' / 'binding.json').write_text(json.dumps(dict(
                dataset='good', filesystemUuid='arch-1', relativePath='isle/datasets/good', hostApp='host')))
            (root / 'empty').mkdir()
            (root / 'broken').mkdir()
            (root / 'broken' / 'binding.json').write_text('{not json')
            (root / 'no-uuid').mkdir()
            (root / 'no-uuid' / 'binding.json').write_text(json.dumps(dict(dataset='no-uuid')))
            rows, status = sd.local_datasets(str(root))
            self.assertEqual(status, 'read')
            self.assertEqual(rows, [dict(dataset='good', filesystemUuid='arch-1', relativePath='isle/datasets/good', hostApp='host')])


class Main(unittest.TestCase):
    def call(self, *argv, tree=TREE, fail=False):
        calls, lines = [], []

        def run(command, **kwargs):
            calls.append(command)
            if fail:
                raise subprocess.CalledProcessError(1, command)
            return SimpleNamespace(stdout=json.dumps(tree))
        with tempfile.TemporaryDirectory() as tmp:
            code = sd.main(list(argv), run=run, root=tmp, usage=lambda path: (1000, 400), out=lines.append)
        return code, lines, calls

    def test_json_documents(self):
        code, lines, calls = self.call('device', 'list')
        data = json.loads(lines[0])
        self.assertEqual((code, data['ok'], data['readOnly'], data['operation']), (0, True, True, 'storage.device.list'))
        self.assertEqual(len(data['result']['devices']), 5)
        self.assertEqual(calls[0][0], 'lsblk')
        code, lines, _ = self.call('partition', 'list')
        data = json.loads(lines[0])
        self.assertEqual((code, data['operation'], data['result']['datasetsStatus']), (0, 'storage.partition.list', 'read'))
        self.assertEqual(len(data['result']['partitions']), 7)

    def test_table(self):
        code, lines, _ = self.call('partition', 'list', '--table')
        self.assertEqual(code, 0)
        text = lines[0].splitlines()
        self.assertTrue(text[0].startswith('PARTITION'))
        self.assertEqual(len(text), 8)
        self.assertIn('not mounted', lines[0])
        code, lines, _ = self.call('--table', 'device', 'list')
        self.assertEqual(code, 0)
        self.assertIn('(none)', lines[0])
        self.assertIn('whole device', lines[0])

    def test_refusals_read_nothing(self):
        for argv, text in [(('device',), 'Usage'), (('device', 'list', 'extra'), 'Usage'), (('volume', 'list'), 'Usage'),
                           (('device', 'register'), 'not a listing'), (('partition', 'assign'), 'not a listing'),
                           (('partition', 'check'), 'not a listing'), (('device', 'health'), 'not a listing'),
                           (('partition', 'create'), 'not a listing'), (('partition', 'mount'), 'not a listing'),
                           (('partition', 'unmount'), 'not a listing'), (('device', 'wipe'), 'Unknown verb')]:
            code, lines, calls = self.call(*argv)
            data = json.loads(lines[0])
            self.assertEqual((code, data['ok'], calls), (1, False, []), argv)
            self.assertIn(text, data['error'])

    def test_failed_or_malformed_report_is_not_an_empty_list(self):
        for kwargs in (dict(fail=True), dict(tree={'blockdevices': None}), dict(tree=[]), dict(tree={'blockdevices': [dict(type='disk', size='x')]})):
            code, lines, _ = self.call('device', 'list', **kwargs)
            self.assertEqual((code, json.loads(lines[0])['ok']), (1, False), kwargs)

    def test_through_the_public_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / 'lsblk'
            fake.write_text('#!/usr/bin/env python3\nprint(' + repr(json.dumps(TREE)) + ')\n')
            fake.chmod(0o755)
            env = dict(os.environ, PATH=tmp + ':' + os.environ['PATH'])
            result = subprocess.run(['node', str(ENTRY), 'storage', 'device', 'list'], env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([d['name'] for d in json.loads(result.stdout)['result']['devices']], ['sda', 'sdb', 'sdc', 'sdd', 'nvme0n1'])
            result = subprocess.run(['node', str(ENTRY), 'storage', 'partition', 'create'], env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 2)                       # the verb is built: without a serial it is the usage text
            self.assertIn('isle storage partition create <serial>', result.stdout)
            result = subprocess.run(['node', str(ENTRY), 'storage', 'partition', 'unmount'], env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 2)
            self.assertIn('isle storage partition unmount <filesystem-identifier>', result.stdout)


if __name__ == '__main__':
    unittest.main()
