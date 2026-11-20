"""isle storage device register | partition assign | partition check | role list|show.

Fixtures only: the block device report and the mount report are made up, every "partition" is a temporary folder,
and the record is a temporary file.  No real disk, no real record and no network is touched.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import storage_roles as sr

ENTRY = Path(__file__).resolve().parents[1] / 'index.js'
ARCH, DOCK, SPARE, ROOT = 'arch-0001', 'dock-0001', 'spare-0001', 'root-0001'


class Machine(sr.Io):
    """A made-up machine: a system disk, a fixed disk with one partition, a dock disk with two, one disk without a serial."""

    def __init__(self, tmp, root=True):
        self.tmp = Path(tmp)
        self.records = self.tmp / 'etc' / 'device-records.json'
        self.root_user, self.said, self.bound = root, [], [dict(dataset='apt-public-repo', filesystemUuid=ARCH)]
        self.folders, self.unmounted = {}, set()
        for name in ('archive', 'dock', 'whole', 'system'):
            self.folders[name] = self.tmp / name
            self.folders[name].mkdir()
        (self.folders['archive'] / 'kept.txt').write_text('data that must stay\n')
        self.disks = [
            dict(name='sda', type='disk', size=128000, serial='S1', model='System', children=[
                dict(name='sda2', type='part', size=127000, fstype='ext4', uuid=ROOT, mountpoints=['/'])]),
            dict(name='sdb', type='disk', size=500000, serial='S2', model='Archive', children=[
                dict(name='sdb1', type='part', size=499000, fstype='ext4', uuid=ARCH, mountpoints=[str(self.folders['archive'])])]),
            dict(name='sdc', type='disk', size=2000000, serial='S3', model='Dock', tran='usb', hotplug=True, children=[
                dict(name='sdc1', type='part', size=700000, fstype='ext4', uuid=DOCK, mountpoints=[str(self.folders['dock'])]),
                dict(name='sdc2', type='part', size=700000, fstype='ext4', uuid=SPARE, mountpoints=[None]),
                dict(name='sdc3', type='part', size=600000, fstype=None, uuid=None, mountpoints=[None])]),
            dict(name='sdd', type='disk', size=250000, serial=None, fstype='ext4', uuid='whole-0001',
                 mountpoints=[str(self.folders['whole'])])]
        self.mount_rows = [dict(target='/', fsroot='/', uuid=ROOT, options='rw'),
                           dict(target=str(self.folders['archive']), fsroot='/', uuid=ARCH, options='rw,relatime'),
                           dict(target=str(self.folders['dock']), fsroot='/', uuid=DOCK, options='rw'),
                           dict(target=str(self.folders['whole']), fsroot='/', uuid='whole-0001', options='rw')]

    def tree(self): return dict(blockdevices=self.disks)
    def datasets(self): return self.bound
    def usage(self, path): return 1000, 400
    def is_root(self): return self.root_user
    def is_mount(self, path): return path not in self.unmounted
    def now(self): return '2026-01-01T00:00:00+00:00'
    def say(self, line=''): self.said.append(line)
    def machine(self): return 'test-machine'
    def mounts(self):
        # the made-up system partition is "mounted" on a temporary folder, never on the real /
        return [dict(m, target=str(self.folders['system'])) if m['target'] == '/' else m for m in self.mount_rows]

    def call(self, *argv):
        self.said = []
        return sr.main(list(argv), io=self)

    def text(self): return '\n'.join(self.said)
    def record(self): return json.loads(self.records.read_text()) if self.records.exists() else None
    def marker(self, name='archive'): return self.folders[name] / sr.MARKER

    def listing(self):
        return sorted(str(p.relative_to(self.tmp)) for p in self.tmp.rglob('*'))

    def ready(self):
        """The fixed disk and the dock disk registered, the archive partition assigned."""
        assert self.call('device', 'register', 'S2', '--class', 'fixed') == 0, self.text()
        assert self.call('device', 'register', 'S3', '--class', 'dock') == 0, self.text()
        assert self.call('partition', 'assign', ARCH, '--role', 'package-repo') == 0, self.text()


class Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.m = Machine(self._tmp.name)

    def refused(self, code, *words):
        self.assertEqual(code, 1, self.m.text())
        self.assertIn('REFUSED', self.m.text())
        self.assertIn('Nothing was changed', self.m.text())
        for word in words: self.assertIn(word, self.m.text())


class Records(Case):
    def test_absent_is_empty_and_damaged_is_an_error(self):
        self.assertEqual(sr.read_records(self.m.records), dict(version=1, devices={}, partitions={}))
        self.m.records.parent.mkdir()
        for bad in ('{not json', '[]', json.dumps(dict(version=2, devices={}, partitions={})),
                    json.dumps(dict(version=1, devices={'S': {'class': 'shelf'}}, partitions={})),
                    json.dumps(dict(version=1, devices={}, partitions={'x': dict(role='package-repo')})),
                    json.dumps(dict(version=1, devices={}, partitions={'x': dict(role='attic', deviceSerial='S')}))):
            self.m.records.write_text(bad)
            with self.assertRaises(ValueError, msg=bad): sr.read_records(self.m.records)

    def test_a_damaged_record_stops_every_verb_and_is_left_alone(self):
        self.m.records.parent.mkdir()
        self.m.records.write_text('{not json')
        self.refused(self.m.call('device', 'register', 'S2', '--class', 'fixed'), 'damaged')
        self.refused(self.m.call('partition', 'assign', ARCH, '--role', 'git'), 'damaged')
        for argv in (('partition', 'check'), ('role', 'list'), ('role', 'show', 'git'), ('device', 'list'), ('partition', 'list')):
            self.assertEqual(self.m.call(*argv), 1, argv)
            self.assertFalse(json.loads(self.m.said[0])['ok'], argv)
        self.assertEqual(self.m.records.read_text(), '{not json')
        self.assertFalse(self.m.marker().exists())

    def test_one_writer_at_a_time(self):
        self.m.records.parent.mkdir()
        import fcntl
        with open(str(self.m.records) + '.lock', 'a+') as other:
            fcntl.flock(other, fcntl.LOCK_EX)
            self.refused(self.m.call('device', 'register', 'S2', '--class', 'fixed'), 'another command')
        self.assertIsNone(self.m.record())


class Marker(Case):
    GOOD = dict(kind=sr.MARKER_KIND, version=1, filesystemUuid=ARCH, role='git', deviceSerial='S2')

    def test_write_then_read(self):
        folder = str(self.m.folders['dock'])
        self.assertEqual(sr.read_marker(folder), (None, None))
        sr.write_marker(folder, self.GOOD)
        self.assertEqual(sr.read_marker(folder), (self.GOOD, None))
        self.assertEqual(os.listdir(folder), [sr.MARKER])
        self.assertEqual(os.stat(self.m.marker('dock')).st_mode & 0o777, 0o644)

    def test_what_is_not_a_marker(self):
        folder, path = str(self.m.folders['dock']), self.m.marker('dock')
        for text in ('{not json', '[]', json.dumps(dict(self.GOOD, kind='other')), json.dumps(dict(self.GOOD, version=2)),
                     json.dumps(dict(self.GOOD, role='attic')), json.dumps(dict(self.GOOD, filesystemUuid=7)), 'x' * (sr.MARKER_MAX + 1)):
            path.write_text(text)
            marker, problem = sr.read_marker(folder)
            self.assertIsNone(marker)
            self.assertIn('not a partition marker', problem)
        path.unlink(); path.mkdir()
        self.assertIn('not a plain file', sr.read_marker(folder)[1])
        self.assertIn('cannot be opened', sr.read_marker(str(self.m.tmp / 'absent'))[1])

    def test_a_link_is_never_followed(self):
        folder, path = str(self.m.folders['dock']), self.m.marker('dock')
        elsewhere = self.m.tmp / 'elsewhere.json'
        elsewhere.write_text(json.dumps(self.GOOD))
        path.symlink_to(elsewhere)
        marker, problem = sr.read_marker(folder)
        self.assertIsNone(marker)                       # a good marker behind a link is still not read
        self.assertIn('not a plain file', problem)
        sr.write_marker(folder, dict(self.GOOD, role='blob'))
        self.assertFalse(path.is_symlink())             # the link itself was replaced
        self.assertEqual(json.loads(elsewhere.read_text()), self.GOOD)   # and what it pointed at is untouched


class WholeMount(unittest.TestCase):
    def test_whole_reachable_and_shortest(self):
        rows = [dict(target='/mnt/a/deep', fsroot='/', uuid='u'), dict(target='/mnt/a', fsroot='/', uuid='u'),
                dict(target='/mnt/sub', fsroot='/folder', uuid='v'), dict(target='/mnt/other', fsroot='/', uuid='w')]
        self.assertEqual(sr.whole_mount(rows, 'u'), (rows[1], True))
        self.assertEqual(sr.whole_mount(rows, 'v'), (None, True))        # only a folder of it is mounted
        self.assertEqual(sr.whole_mount(rows, 'absent'), (None, False))

    def test_a_covered_mount_is_not_used(self):
        for cover in ('/mnt/a', '/mnt', '/'):
            rows = [dict(target='/mnt/a', fsroot='/', uuid='u'), dict(target=cover, fsroot='/', uuid='w')]
            self.assertEqual(sr.whole_mount(rows, 'u'), (None, True), cover)
        rows = [dict(target='/mnt', fsroot='/', uuid='w'), dict(target='/mnt/a', fsroot='/', uuid='u'), dict(target='/mnt/ab', fsroot='/', uuid='w')]
        self.assertEqual(sr.whole_mount(rows, 'u'), (rows[1], True))     # an earlier mount above it and a neighbour cover nothing


class Register(Case):
    def test_registers_by_serial(self):
        self.assertEqual(self.m.call('device', 'register', 'S2', '--class', 'fixed'), 0)
        self.assertEqual(self.m.record()['devices'], {'S2': {'class': 'fixed', 'model': 'Archive', 'sizeBytes': 500000,
                                                              'registeredAt': '2026-01-01T00:00:00+00:00'}})
        self.assertEqual(os.stat(self.m.records).st_mode & 0o777, 0o644)
        before = self.m.records.read_bytes()
        self.assertEqual(self.m.call('device', 'register', 'S2', '--class', 'fixed'), 0)
        self.assertIn('Nothing was changed', self.m.text())
        self.assertEqual(self.m.records.read_bytes(), before)

    def test_a_class_can_be_corrected_and_says_so(self):
        self.m.call('device', 'register', 'S3', '--class', 'fixed')
        self.assertEqual(self.m.call('device', 'register', 'S3', '--class', 'dock'), 0)
        self.assertIn('Its class was fixed before', self.m.text())
        self.assertEqual(self.m.record()['devices']['S3']['class'], 'dock')

    def test_refusals_write_nothing(self):
        self.refused(self.m.call('device', 'register', 'S2', '--class', 'shelf'), 'class must be one of')
        self.refused(self.m.call('device', 'register', 'S9', '--class', 'fixed'), 'no storage device with the serial S9')
        self.refused(self.m.call('device', 'register', 'sdb', '--class', 'fixed'), 'no storage device')   # a name is not a serial
        self.m.disks[2]['serial'] = 'S2'
        self.refused(self.m.call('device', 'register', 'S2', '--class', 'fixed'), 'two devices report the serial')
        self.assertIsNone(self.m.record())

    def test_without_root_it_names_the_command_and_writes_nothing(self):
        self.m.root_user = False
        self.assertEqual(self.m.call('device', 'register', 'S2', '--class', 'fixed'), 1)
        self.assertIn('sudo isle storage device register S2 --class fixed', self.m.text())
        self.assertIsNone(self.m.record())
        self.assertFalse(self.m.records.parent.exists())


class Assign(Case):
    def test_writes_the_marker_and_the_record_and_nothing_else(self):
        self.m.call('device', 'register', 'S2', '--class', 'fixed')
        before = self.m.listing()
        self.assertEqual(self.m.call('partition', 'assign', ARCH, '--role', 'package-repo'), 0, self.m.text())
        self.assertIn('No data was moved', self.m.text())
        self.assertIn('apt-public-repo', self.m.text())
        self.assertEqual(self.m.listing(), sorted(before + ['archive/' + sr.MARKER]))
        self.assertEqual((self.m.folders['archive'] / 'kept.txt').read_text(), 'data that must stay\n')
        self.assertEqual(json.loads(self.m.marker().read_text()), dict(
            kind=sr.MARKER_KIND, version=1, filesystemUuid=ARCH, role='package-repo', deviceSerial='S2',
            assignedAt='2026-01-01T00:00:00+00:00', assignedOn='test-machine'))
        self.assertEqual(self.m.record()['partitions'], {ARCH: dict(role='package-repo', deviceSerial='S2', assignedAt='2026-01-01T00:00:00+00:00')})

    def test_again_changes_nothing_and_a_second_role_is_refused(self):
        self.m.ready()
        marker, record = self.m.marker().read_bytes(), self.m.records.read_bytes()
        self.assertEqual(self.m.call('partition', 'assign', ARCH, '--role', 'package-repo'), 0)
        self.assertIn('Nothing was changed', self.m.text())
        self.refused(self.m.call('partition', 'assign', ARCH, '--role', 'git'), 'already carries the role package-repo', 'one role')
        self.assertEqual((self.m.marker().read_bytes(), self.m.records.read_bytes()), (marker, record))

    def test_refusals_write_nothing(self):
        self.m.call('device', 'register', 'S3', '--class', 'dock')
        record = self.m.records.read_bytes()
        before = self.m.listing()
        self.refused(self.m.call('partition', 'assign', ARCH, '--role', 'attic'), 'role must be one of')
        self.refused(self.m.call('partition', 'assign', 'sdb1', '--role', 'git'), 'no partition')            # a name is not an identifier
        self.refused(self.m.call('partition', 'assign', 'gone-0001', '--role', 'git'), 'no partition')
        self.refused(self.m.call('partition', 'assign', ARCH, '--role', 'git'), 'is not registered', 'sudo isle storage device register S2')
        self.refused(self.m.call('partition', 'assign', 'whole-0001', '--role', 'git'), 'reports no serial')
        self.refused(self.m.call('partition', 'assign', SPARE, '--role', 'git'), 'is not mounted')
        self.m.call('device', 'register', 'S1', '--class', 'fixed')
        record = self.m.records.read_bytes()
        self.refused(self.m.call('partition', 'assign', ROOT, '--role', 'scratch'), 'carries the running system')
        self.m.mount_rows[2]['fsroot'] = '/folder'
        self.refused(self.m.call('partition', 'assign', DOCK, '--role', 'git'), 'not mounted whole')
        self.m.mount_rows[2].update(fsroot='/', options='ro,relatime')
        self.refused(self.m.call('partition', 'assign', DOCK, '--role', 'git'), 'read-only')
        self.m.mount_rows[2]['options'] = 'rw'
        self.m.mount_rows.append(dict(target=str(self.m.folders['dock']), fsroot='/', uuid='cover-0001', options='rw'))
        self.refused(self.m.call('partition', 'assign', DOCK, '--role', 'git'), 'another mount covers it')
        self.m.mount_rows.pop()
        self.m.disks[1]['children'][0]['uuid'] = DOCK
        self.refused(self.m.call('partition', 'assign', DOCK, '--role', 'git'), 'two partitions report the identifier')
        self.assertEqual(self.m.records.read_bytes(), record)
        self.assertEqual(self.m.listing(), before)

    def test_a_folder_that_is_no_longer_a_mount_gets_no_marker(self):
        self.m.call('device', 'register', 'S2', '--class', 'fixed')
        self.m.unmounted.add(str(self.m.folders['archive']))              # unmounted between the look and the write
        self.refused(self.m.call('partition', 'assign', ARCH, '--role', 'git'), 'no longer mounted')
        self.assertFalse(self.m.marker().exists())
        self.assertEqual(self.m.record()['partitions'], {})

    def test_a_serial_with_a_space_is_quoted_in_the_command_to_type(self):
        self.m.disks[1]['serial'] = 'WD 123'
        self.m.root_user = False
        self.assertEqual(self.m.call('device', 'register', 'WD 123', '--class', 'fixed'), 1)
        self.assertIn("sudo isle storage device register 'WD 123' --class fixed", self.m.text())
        self.refused(self.m.call('partition', 'assign', ARCH, '--role', 'git'), "register 'WD 123' --class")

    def test_a_file_of_that_name_that_is_not_a_marker_is_left_alone(self):
        self.m.call('device', 'register', 'S2', '--class', 'fixed')
        self.m.marker().write_text('somebody else wrote this')
        self.refused(self.m.call('partition', 'assign', ARCH, '--role', 'git'), 'not a partition marker')
        self.assertEqual(self.m.marker().read_text(), 'somebody else wrote this')
        self.assertEqual(self.m.record()['partitions'], {})

    def test_without_root_it_names_the_command_and_writes_nothing(self):
        self.m.call('device', 'register', 'S2', '--class', 'fixed')
        self.m.root_user = False
        self.assertEqual(self.m.call('partition', 'assign', ARCH, '--role', 'git'), 1)
        self.assertIn('sudo isle storage partition assign %s --role git' % ARCH, self.m.text())
        self.assertFalse(self.m.marker().exists())
        self.assertEqual(self.m.record()['partitions'], {})

    def test_a_missing_marker_is_written_again(self):
        self.m.ready()
        self.m.marker().unlink()
        self.assertEqual(self.m.call('partition', 'check'), 1)
        self.assertEqual(self.m.call('partition', 'assign', ARCH, '--role', 'package-repo'), 0)
        self.assertEqual(json.loads(self.m.marker().read_text())['role'], 'package-repo')
        self.assertEqual(self.m.call('partition', 'check'), 0)

    def test_a_partition_marked_on_another_machine_is_recorded_not_rewritten(self):
        self.m.call('device', 'register', 'S3', '--class', 'dock')
        theirs = dict(kind=sr.MARKER_KIND, version=1, filesystemUuid=DOCK, role='assets', deviceSerial='S3',
                      assignedAt='2025-05-05T00:00:00+00:00', assignedOn='another-machine')
        self.m.marker('dock').write_text(json.dumps(theirs))
        raw = self.m.marker('dock').read_bytes()
        self.m.call('role', 'show', 'assets')
        row = json.loads(self.m.said[0])['result']['partitions'][0]
        self.assertEqual((row['role'], row['roleState'], row['recordedRole']), ('assets', 'not recorded here', None))
        self.assertEqual(self.m.call('partition', 'check'), 0)           # known from its marker only: shown, not wrong
        self.refused(self.m.call('partition', 'assign', DOCK, '--role', 'git'), 'already carries the role assets')
        self.assertEqual(self.m.call('partition', 'assign', DOCK, '--role', 'assets'), 0)
        self.assertIn('another-machine', self.m.text())
        self.assertEqual(self.m.marker('dock').read_bytes(), raw)
        self.assertEqual(self.m.record()['partitions'][DOCK]['role'], 'assets')

    def test_a_copy_of_a_marked_filesystem_is_refused(self):
        self.m.ready()
        self.m.marker('dock').write_bytes(self.m.marker().read_bytes())   # as a block copy would carry it
        self.refused(self.m.call('partition', 'assign', DOCK, '--role', 'package-repo'), 'is a copy of that one')
        self.assertNotIn(DOCK, self.m.record()['partitions'])
        self.m.call('partition', 'check', '--table')
        self.assertIn('marker of another filesystem', self.m.text())

    def test_a_partition_that_changed_device_is_recorded_where_it_is(self):
        self.m.ready()
        self.m.disks[1]['serial'] = 'S7'                                  # the same partition, now behind another serial
        self.assertEqual(self.m.call('partition', 'check'), 1)
        self.assertEqual(json.loads(self.m.said[0])['result']['partitions'][0]['roleState'], 'on another device')
        self.refused(self.m.call('partition', 'assign', ARCH, '--role', 'package-repo'), 'is not registered')
        self.m.call('device', 'register', 'S7', '--class', 'fixed')
        marker = self.m.marker().read_bytes()
        self.assertEqual(self.m.call('partition', 'assign', ARCH, '--role', 'package-repo'), 0)
        self.assertIn('recorded it on device S2 before', self.m.text())
        self.assertEqual(self.m.record()['partitions'][ARCH]['deviceSerial'], 'S7')
        self.assertEqual(self.m.marker().read_bytes(), marker)
        self.assertEqual(self.m.call('partition', 'check'), 0)


class Check(Case):
    def states(self):
        code = self.m.call('partition', 'check')
        data = json.loads(self.m.said[0])
        self.assertEqual((data['readOnly'], data['operation'], data['ok']), (True, 'storage.partition.check', code == 0))
        return code, {p['filesystemUuid']: p['roleState'] for p in data['result']['partitions']}, data['result']['needAttention']

    def test_nothing_recorded_is_fine(self):
        self.assertEqual(self.states(), (0, {}, []))
        self.assertEqual(self.m.call('partition', 'check', '--table'), 0)
        self.assertIn('No partition of this machine carries a role yet', self.m.text())

    def test_ok_then_each_wrong_state(self):
        self.m.ready()
        self.m.call('partition', 'assign', DOCK, '--role', 'assets')
        self.assertEqual(self.states(), (0, {ARCH: 'ok', DOCK: 'ok'}, []))
        self.m.marker().write_text(json.dumps(dict(json.loads(self.m.marker().read_text()), role='git')))
        self.assertEqual(self.states(), (1, {ARCH: 'marker differs', DOCK: 'ok'}, [ARCH]))
        self.m.marker().write_text('{broken')
        self.assertEqual(self.states(), (1, {ARCH: 'marker cannot be read', DOCK: 'ok'}, [ARCH]))
        self.m.marker().unlink()
        self.assertEqual(self.states(), (1, {ARCH: 'marker missing', DOCK: 'ok'}, [ARCH]))
        self.assertEqual(self.m.call('partition', 'check', '--table'), 1)
        self.assertIn('1 of 2 need attention', self.m.text())

    def test_a_dock_partition_may_be_away_and_a_fixed_one_may_not(self):
        self.m.ready()
        self.m.call('partition', 'assign', DOCK, '--role', 'assets')
        dock = self.m.disks.pop(2)
        self.assertEqual(self.states(), (0, {ARCH: 'ok', DOCK: 'not present'}, []))
        self.m.disks.append(dock)
        dock['children'][0]['mountpoints'] = [None]
        self.m.mount_rows.pop(2)
        self.assertEqual(self.states(), (0, {ARCH: 'ok', DOCK: 'not mounted'}, []))
        self.m.disks.pop(1)
        self.assertEqual(self.states(), (1, {ARCH: 'not present', DOCK: 'not mounted'}, [ARCH]))

    def test_a_device_dropped_from_the_record_is_wrong(self):
        self.m.ready()
        record = self.m.record()
        del record['devices']['S2']
        self.m.records.write_text(json.dumps(record))
        self.assertEqual(self.states(), (1, {ARCH: 'device not registered'}, [ARCH]))

    def test_check_writes_nothing(self):
        self.m.ready()
        before, record = self.m.listing(), self.m.records.read_bytes()
        self.m.root_user = False
        for argv in (('partition', 'check'), ('partition', 'check', '--table'), ('role', 'list'), ('role', 'show', 'package-repo')):
            self.m.call(*argv)
        self.assertEqual((self.m.listing(), self.m.records.read_bytes()), (before, record))


class Roles(Case):
    def test_show_lists_the_partition_and_the_dataset_on_it(self):
        self.m.ready()
        self.assertEqual(self.m.call('role', 'show', 'package-repo'), 0)
        data = json.loads(self.m.said[0])
        self.assertEqual((data['operation'], data['result']['role']), ('storage.role.show', 'package-repo'))
        row = data['result']['partitions'][0]
        self.assertEqual((row['name'], row['filesystemUuid'], row['deviceSerial'], row['deviceClass'], row['roleState'], row['datasets'],
                          row['availableBytes']), ('sdb1', ARCH, 'S2', 'fixed', 'ok', ['apt-public-repo'], 400))
        self.assertNotIn('marker', row)
        self.assertEqual(self.m.call('role', 'show', 'package-repo', '--table'), 0)
        for word in ('package-repo', 'sdb1', ARCH, 'S2', 'fixed', 'apt-public-repo'): self.assertIn(word, self.m.text())
        self.assertEqual(self.m.call('role', 'show', 'git', '--table'), 0)
        self.assertIn('No partition of this machine carries the role git', self.m.text())

    def test_list_names_every_role(self):
        self.m.ready()
        self.assertEqual(self.m.call('role', 'list'), 0)
        rows = json.loads(self.m.said[0])['result']['roles']
        self.assertEqual([r['role'] for r in rows], list(sr.ROLES))
        self.assertEqual(rows[0], dict(role='package-repo', partitions=[ARCH], datasets=['apt-public-repo'], availableBytes=400))
        self.assertEqual(rows[1]['partitions'], [])
        self.assertEqual(self.m.call('role', 'list', '--table'), 0)
        self.assertEqual(len(self.m.text().splitlines()), 1 + len(sr.ROLES))

    def test_unknown_role_and_usage(self):
        self.assertEqual(self.m.call('role', 'show', 'attic'), 1)
        self.assertIn('Unknown role', json.loads(self.m.said[0])['error'])
        for argv in (('role',), ('role', 'show'), ('role', 'retire', 'git'), ('device', 'register', 'S2'), ('device', 'register', 'S2', 'fixed', '--class'),
                     ('partition', 'assign', ARCH), ('partition', 'assign', '--role', 'git', ARCH), ('partition', 'check', 'extra'), ()):
            self.assertEqual(self.m.call(*argv), 2, argv)
            self.assertIn('Usage', self.m.text())
        self.assertIsNone(self.m.record())


class Listings(Case):
    def run_fake(self, command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(self.m.tree()))

    def test_the_listings_carry_the_recorded_class_and_role(self):
        self.m.ready()
        self.m.run, self.m.root = self.run_fake, str(self.m.tmp / 'no-datasets')
        self.assertEqual(self.m.call('device', 'list'), 0)
        data = json.loads(self.m.said[0])
        self.assertEqual({d['serial']: d['class'] for d in data['result']['devices']}, {'S1': None, 'S2': 'fixed', 'S3': 'dock', None: None})
        self.assertIn('partition check', data['result']['note'])
        self.assertEqual(self.m.call('partition', 'list'), 0)
        roles = {p['name']: p['role'] for p in json.loads(self.m.said[0])['result']['partitions']}
        self.assertEqual((roles['sdb1'], roles['sdc1'], roles['sdc3']), ('package-repo', None, None))
        self.assertEqual(self.m.call('partition', 'list', '--table'), 0)
        self.assertIn('ROLE', self.m.said[0].splitlines()[0])
        self.assertEqual(self.m.call('device', 'list', '--table'), 0)
        self.assertIn('CLASS', self.m.said[0].splitlines()[0])

    def test_verbs_not_built_and_unknown_verbs_say_so(self):
        for argv, text in ((('partition', 'create'), 'not in this CLI yet'), (('device', 'retire'), 'not in this CLI yet'),
                           (('device', 'wipe'), 'Known: list, register'), (('partition', 'format'), 'Known: list, assign, check')):
            self.assertEqual(self.m.call(*argv), 1, argv)
            self.assertIn(text, json.loads(self.m.said[0])['error'])

    def test_through_the_public_dispatch(self):
        tools = self.m.tmp / 'bin'
        tools.mkdir()
        for name, document in (('lsblk', self.m.tree()), ('findmnt', dict(filesystems=[]))):
            (tools / name).write_text('#!/usr/bin/env python3\nprint(' + repr(json.dumps(document)) + ')\n')
            (tools / name).chmod(0o755)
        env = dict(os.environ, PATH=str(tools) + ':' + os.environ['PATH'])
        result = subprocess.run(['node', str(ENTRY), 'storage', 'role', 'list'], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([r['role'] for r in json.loads(result.stdout)['result']['roles']], list(sr.ROLES))
        result = subprocess.run(['node', str(ENTRY), 'storage', 'partition', 'assign'], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Usage', result.stdout)


if __name__ == '__main__':
    unittest.main()
