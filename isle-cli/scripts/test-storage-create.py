"""isle storage partition create.

Fixtures only, the made-up machine of test-storage-roles.py; the probe, the table tool and the mkfs give made-up
answers.  The last class runs the real blkid, sfdisk and mkfs.ext4, on files in a temporary folder.  No real disk,
no real record and no network is touched.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import storage_create as sc
import storage_roles as sr
import storage_table

_spec = importlib.util.spec_from_file_location('storage_roles_fixtures', HERE / 'test-storage-roles.py')
fx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fx)

P3, ROLES = 'part-sdc3', 'registry,assets'
PLAN = dict(kind='isle.storage.partition.create', version=1, deviceSerial='S3', deviceModel='Dock', deviceSizeBytes=2000000, deviceClass='dock',
            layout='existing-partition', partitionUuid=P3, partitionSizeBytes=600000, filesystem='ext4', reservePercent=1,
            roles=['registry', 'assets'], label=None)
WHOLE = dict(kind='isle.storage.partition.create', version=1, deviceSerial='S5', deviceModel='Blank', deviceSizeBytes=3000000, deviceClass='dock',
             layout='whole-device', shownPartitionSizes=[1000000] * 4, filesystem='ext4', reservePercent=1, roles=['registry', 'assets'], label=None)
UUID4 = r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}'
EXT4 = dict(TYPE='ext4', UUID='old-one')
#: what the verb adds after it made a partition for registry and assets on the made-up machine, which has no program
LACKS = ['The role registry is storage of the kind image-registry. A machine that holds it needs: the program docker and an image of registry in Docker.',
         '  This machine does not have: the program docker; an image of registry in Docker.',
         'The role assets is storage of the kind file-pool. A machine that holds it needs: the program weed; or the program docker and an image of '
         'chrislusf/seaweedfs in Docker.',
         '  This machine does not have: the program weed; the program docker; an image of chrislusf/seaweedfs in Docker.',
         'Nothing was installed. See: isle storage kind list --table']


def ident(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def answer(command, code, out='', err=''):
    return subprocess.CompletedProcess(command, code, stdout=out, stderr=err)


class Case(fx.Case):
    """The made-up machine with one more disk, S5: no partition table, and four partitions shown that are not there."""

    def setUp(self):
        super().setUp()
        self.m.ready()
        for disk in self.m.disks:
            for child in disk.get('children') or []: child['partuuid'] = 'part-' + child['name']
        self.blank = dict(name='sde', type='disk', size=3000000, serial='S5', model='Blank', tran='usb', hotplug=True, children=[
            dict(name='sde%d' % n, type='part', size=1000000, fstype=None, uuid=None, partuuid=None, mountpoints=[None]) for n in (1, 2, 3, 4)])
        self.m.disks.append(self.blank)
        self.assertEqual(self.m.call('device', 'register', 'S5', '--class', 'dock'), 0, self.m.text())
        self.commands, self.inputs, self.slept, self.seen = [], [], [], {}
        self.on_disk, self.found, self.mkfs = {}, {}, (0, '')           # found: what the probe answers for one path
        self.rehearsal, self.sfdisk, self.settle = (0, ''), (0, ''), (0, '')
        self.old, self.pending, self.looks, self.shows_after, self.shown_as = None, None, 0, 0, str
        self.m.run, self.m.sleep, self.real_tree, self.m.tree = self.run_tool, self.slept.append, self.m.tree, self.tree
        self.before = (self.m.records.read_bytes(), self.m.listing())

    def tree(self):
        if self.pending is not None:                                     # a new table was written; it is shown after some looks
            if self.looks >= self.shows_after:
                self.blank.update(pttype='gpt', children=[self.pending])
                self.pending = None
            self.looks += 1
        return self.real_tree()

    def run_tool(self, command, **kwargs):
        self.commands.append(list(command))
        tool, path = command[0], command[-1]
        if tool == 'blkid':
            found = self.found.get(path, self.on_disk)
            if isinstance(found, BaseException): raise found
            if isinstance(found, tuple): return answer(command, found[0], found[1])
            lines = ['DEVNAME=' + path] + ['%s=%s' % pair for pair in sorted(found.items())]
            return answer(command, 0 if found else 2, '\n'.join(lines) + '\n' if found else '')
        if tool == 'sfdisk':
            self.inputs.append(kwargs.get('input'))
            result = self.rehearsal if '--no-act' in command else self.sfdisk
            if isinstance(result, FileNotFoundError): self.commands.pop()                # a tool that is not there ran nothing
            if isinstance(result, BaseException): raise result
            if result[0] == 0 and '--no-act' not in command:
                new = re.search(r'uuid=(\S+)', kwargs['input']).group(1)
                self.pending = dict(name='sde1', type='part', size=2990000, fstype=None, uuid=None, partuuid=self.shown_as(new), mountpoints=[None])
                if self.old: self.found['/dev/sde1'] = self.old          # where the new partition begins, a filesystem from before
            return answer(command, result[0], err=result[1])
        if tool == 'udevadm':
            if isinstance(self.settle, BaseException): raise self.settle
            return answer(command, self.settle[0])
        if tool == 'mkfs.ext4':
            if isinstance(self.mkfs, FileNotFoundError): self.commands.pop()
            if isinstance(self.mkfs, BaseException): raise self.mkfs
            root = Path(command[command.index('-d') + 1])
            self.seen = dict(stdin=kwargs.get('stdin'), files=sorted(p.name for p in root.iterdir()), marker=json.loads((root / sr.MARKER).read_text()),
                             rootMode=stat.S_IMODE(root.stat().st_mode), markerMode=stat.S_IMODE((root / sr.MARKER).stat().st_mode), root=root)
            made = dict(UUID=command[command.index('-U') + 1], TYPE='ext4', USAGE='filesystem')
            if self.mkfs[0] == 0 and path in self.found: self.found[path] = made
            elif self.mkfs[0] == 0: self.on_disk = made
            return answer(command, self.mkfs[0], err=self.mkfs[1])
        raise AssertionError(command)

    def create(self, *more, serial='S3', partition=P3, roles=ROLES):
        return self.m.call('partition', 'create', serial, *(('--partition', partition) if partition else ('--whole',)), '--role', roles, *more)

    def whole(self, *more, **named):
        return self.create(*more, **dict(dict(serial='S5', partition=None), **named))

    def plan_id(self, *more, **named):
        self.assertEqual(self.create(*more, **named), 0, self.m.text())
        return [line for line in self.m.said if line.startswith('Plan id: ')][0][len('Plan id: '):]

    def whole_id(self, *more, **named):
        return self.plan_id(*more, **dict(dict(serial='S5', partition=None), **named))

    def tools(self, *names):
        return [c for c in self.commands if c[0] in names]

    def untouched(self):
        self.assertEqual((self.m.records.read_bytes(), self.m.listing()), self.before)

    def refused(self, code, *words):
        super().refused(code, *words)
        self.assertEqual([c for c in self.commands if c[0] != 'blkid' and '--no-act' not in c], [])     # nothing that writes was started
        self.untouched()

    def failed(self, code, *words):
        self.assertEqual(code, 1, self.m.text())
        self.assertTrue(self.m.text().startswith('FAILED: partition create: '), self.m.text())
        self.assertNotIn('Nothing was changed', self.m.text())           # from the first write on, that would be untrue
        for word in words: self.assertIn(word, self.m.text())
        self.untouched()


class ThePlan(Case):
    def test_it_says_what_is_made_and_what_is_lost_and_writes_nothing(self):
        for root in (True, False):
            self.m.root_user = root
            self.assertEqual(self.create(), 0, self.m.text())
            self.assertEqual(self.m.said, [
                'Plan: make a filesystem on partition sdc3 of device S3 (Dock, 2.0 MB, dock).',
                '  The partition: 600.0 KB, partition identifier part-sdc3. This machine reports no filesystem on it; the partition itself is '
                'probed again before anything is written.',
                '  What is made: one ext4 filesystem over the whole partition (1% kept in reserve), with the marker of the roles registry, assets at its root.',
                '  What is lost: everything that is on that partition now. No other partition of the device is touched.',
                '  Afterwards: this machine records the new filesystem with the roles registry, assets. It is not mounted by this verb.',
                'Plan id: ' + ident(PLAN),
                'To do it, type: sudo isle storage partition create S3 --partition part-sdc3 --role registry,assets --expect ' + ident(PLAN)])
            self.assertEqual(self.commands, [])
            self.untouched()

    def test_for_a_whole_device(self):
        self.assertEqual(self.whole('--label', 'big'), 0, self.m.text())
        self.assertEqual(self.m.said, [
            'Plan: give device S5 (Blank, 3.0 MB, dock) a partition table with one partition over the whole device, and make a filesystem on it.',
            '  The device: this machine reports no partition table and no filesystem on it. The system shows 4 partitions without an identifier, as a '
            'disk filled with a test pattern does; the device itself is probed again before anything is written.',
            '  What is made: a GPT partition table, one partition, and on it one ext4 filesystem (1% kept in reserve), labelled big, with the marker of '
            'the roles registry, assets at its root.',
            '  What is lost: everything that is on the device now. One thing is kept: when the new partition shows a filesystem from before, the verb '
            'stops there and does not overwrite it.',
            '  Afterwards: this machine records the new filesystem with the roles registry, assets. It is not mounted by this verb.',
            'Plan id: ' + ident(dict(WHOLE, label='big')),
            'To do it, type: sudo isle storage partition create S5 --whole --role registry,assets --label big --expect ' + ident(dict(WHOLE, label='big'))])
        self.blank['children'] = []
        self.assertEqual(self.whole_id(), ident(dict(WHOLE, shownPartitionSizes=[])))
        self.assertIn('no partition table and no filesystem on it; the device itself is probed again', self.m.text())
        self.assertEqual(self.commands, [])
        self.untouched()

    def test_the_id_names_everything_that_is_erased_or_made(self):
        first = self.plan_id()
        self.assertEqual((first, self.plan_id()), (ident(PLAN), first))
        self.assertEqual(self.plan_id(roles='registry'), ident(dict(PLAN, roles=['registry'])))
        self.assertEqual(self.plan_id(roles='assets,registry'), ident(dict(PLAN, roles=['assets', 'registry'])))
        self.assertEqual(self.plan_id('--label', 'big.disk-1'), ident(dict(PLAN, label='big.disk-1')))
        self.assertIn(', labelled big.disk-1, with the marker', self.m.text())
        self.assertTrue(self.m.said[-1].endswith('--role registry,assets --label big.disk-1 --expect ' + ident(dict(PLAN, label='big.disk-1'))))
        self.m.disks[2]['children'][2]['size'] = 600001
        self.assertEqual(self.plan_id(), ident(dict(PLAN, partitionSizeBytes=600001)))
        self.m.disks[2]['size'] = 2000001
        self.assertEqual(self.plan_id(), ident(dict(PLAN, partitionSizeBytes=600001, deviceSizeBytes=2000001)))
        self.m.disks[2]['model'] = 'Other'
        self.assertEqual(self.plan_id(), ident(dict(PLAN, partitionSizeBytes=600001, deviceSizeBytes=2000001, deviceModel='Other')))
        self.assertEqual(self.m.call('device', 'register', 'S3', '--class', 'flash'), 0)
        self.assertEqual(self.plan_id(), ident(dict(PLAN, partitionSizeBytes=600001, deviceSizeBytes=2000001, deviceModel='Other', deviceClass='flash')))
        self.assertEqual(self.whole_id(), ident(WHOLE))
        self.blank['children'].pop()
        self.assertEqual(self.whole_id(), ident(dict(WHOLE, shownPartitionSizes=[1000000] * 3)))
        self.blank['children'][0]['size'] = 7
        self.assertEqual(self.whole_id(roles='git'), ident(dict(WHOLE, shownPartitionSizes=[7, 1000000, 1000000], roles=['git'])))
        self.assertEqual(self.commands, [])

    def test_what_a_device_holds(self):
        self.assertEqual(self.m.call('partition', 'create', 'S3'), 0, self.m.text())
        self.assertEqual(self.m.said, ['Device S3 (Dock, 2.0 MB, dock).', '1 of its 3 partitions have no filesystem:',
                                       '  sdc3 (600.0 KB, partition identifier part-sdc3)',
                                       'To read the plan for one of them: isle storage partition create S3 --partition <partition-identifier> --role <role>[,<role>...]'])
        self.assertEqual(self.m.call('partition', 'create', 'S2'), 0, self.m.text())
        self.assertEqual(self.m.said, ['Device S2 (Archive, 500.0 KB, fixed).', '0 of its 1 partitions have no filesystem.',
                                       'Nothing can be made here: the device has a partition table with 1 partition. --whole is for a device without one, '
                                       'and adding a partition to a table is not built.'])
        self.m.disks[2]['children'][2]['children'] = [dict(name='dm-0', type='crypt')]       # something stacked on it: not listed
        self.assertEqual(self.m.call('partition', 'create', 'S3'), 0)
        self.assertEqual(self.m.said[1], '0 of its 3 partitions have no filesystem.')
        self.assertEqual(self.m.call('partition', 'create', 'S5'), 0, self.m.text())
        self.assertEqual(self.m.said, ['Device S5 (Blank, 3.0 MB, dock).',
                                       'It has no partition table and no filesystem that this machine recognises. The system shows 4 partitions on it that '
                                       'have no identifier; a disk filled with a test pattern looks like that.',
                                       'To read the plan for one partition over the whole device: isle storage partition create S5 --whole --role <role>[,<role>...]'])
        self.blank['children'][2]['partuuid'] = 'part-real'              # one that is a partition: only it can take a filesystem
        self.assertEqual(self.m.call('partition', 'create', 'S5'), 0)
        self.assertEqual(self.m.said[1:3], ['1 of its 4 partitions have no filesystem:', '  sde3 (1.0 MB, partition identifier part-real)'])
        self.blank['children'] = []
        self.assertEqual(self.m.call('partition', 'create', 'S5'), 0)
        self.assertEqual(self.m.said[1], 'It has no partition table and no filesystem that this machine recognises.')
        self.blank['fstype'] = 'xfs'
        self.assertEqual(self.m.call('partition', 'create', 'S5'), 0)
        self.assertEqual(self.m.said[1:], ['0 of its 0 partitions have no filesystem.',
                                           'Nothing can be made here: the device holds xfs directly, with no partition table. This verb never overwrites a filesystem.'])
        self.refused(self.m.call('partition', 'create', 'S9'), 'no storage device with the serial S9')
        self.refused(self.m.call('partition', 'create', ''), 'not a hardware serial')
        self.assertEqual(self.commands, [])


class OnAPartition(Case):
    def test_one_mkfs_makes_the_filesystem_with_its_marker_and_the_record_follows(self):
        before = self.m.record()
        self.assertEqual(self.create('--expect', self.plan_id()), 0, self.m.text())
        made = self.commands[1][self.commands[1].index('-U') + 1]
        self.assertRegex(made, '^%s$' % UUID4)
        probe = ['blkid', '--probe', '--output', 'export', '/dev/sdc3']
        self.assertEqual(self.commands, [probe, ['mkfs.ext4', '-q', '-t', 'ext4', '-m', '1', '-U', made, '-d', str(self.seen['root']), '/dev/sdc3'], probe])
        self.assertEqual(self.seen['marker'], dict(kind='isle.storage.partition', version=1, filesystemUuid=made, deviceSerial='S3', role='registry',
                                                   roles=['registry', 'assets'], assignedAt='2026-01-01T00:00:00+00:00', assignedOn='test-machine'))
        self.assertEqual((self.seen['stdin'], self.seen['files'], self.seen['rootMode'], self.seen['markerMode']),
                         (subprocess.DEVNULL, [sr.MARKER], 0o755, 0o644))
        self.assertFalse(self.seen['root'].exists())                     # the folder the marker was handed over in is gone
        after = self.m.record()
        self.assertEqual(after['partitions'].pop(made), dict(deviceSerial='S3', assignedAt='2026-01-01T00:00:00+00:00', role='registry', roles=['registry', 'assets']))
        self.assertEqual(after, before)
        self.assertEqual(self.m.said, [
            'Partition sdc3 of device S3 now holds one ext4 filesystem, identifier %s. What was there before is gone.' % made,
            'It carries the roles registry, assets: the marker is at its root and this machine records it.',
            'It is not mounted. See: isle storage partition check --table'] + LACKS)
        # the marker that was made is one `partition check` accepts, once the filesystem is seen and mounted
        self.m.disks[2]['children'][2].update(fstype='ext4', uuid=made, mountpoints=[str(self.m.folders['whole'])])
        self.m.disks[3]['mountpoints'] = [None]
        self.m.mount_rows[3] = dict(target=str(self.m.folders['whole']), fsroot='/', uuid=made, options='rw')
        (self.m.folders['whole'] / sr.MARKER).write_text(json.dumps(self.seen['marker']))
        self.assertEqual(self.m.call('partition', 'check'), 0, self.m.text())
        row = [p for p in json.loads(self.m.said[0])['result']['partitions'] if p['filesystemUuid'] == made][0]
        self.assertEqual((row['roleState'], row['roles']), ('ok', ['registry', 'assets']))

    def test_a_label_and_one_role(self):
        self.assertEqual(self.create('--label', 'images', '--expect', self.plan_id('--label', 'images', roles='registry'), roles='registry'), 0, self.m.text())
        self.assertEqual(self.commands[1][8:10], ['-L', 'images'])
        self.assertEqual((self.seen['marker']['role'], 'roles' in self.seen['marker']), ('registry', False))
        self.assertIn('It carries the role registry:', self.m.text())

    def test_an_id_of_another_plan_does_nothing(self):
        other = self.plan_id(roles='registry')
        for wrong in (other, 'f' * 64, self.plan_id('--label', 'x'), self.whole_id()):
            self.refused(self.create('--expect', wrong), 'the plan is not the one with that id', 'Read it again: isle storage partition create S3 --partition part-sdc3 --role registry,assets')
        self.m.disks[2]['children'][2]['size'] = 600001                  # the partition is not the one that was read
        self.refused(self.create('--expect', ident(PLAN)), 'the plan is not the one with that id')
        self.assertEqual(self.commands, [])

    def test_without_root_it_prints_the_command_and_does_nothing(self):
        self.m.root_user = False
        self.assertEqual(self.create('--label', 'a', '--expect', self.plan_id('--label', 'a')), 1)
        self.assertEqual(self.m.said, ['This erases a partition. Type: sudo isle storage partition create S3 --partition part-sdc3 --role registry,assets --label a --expect '
                                       + ident(dict(PLAN, label='a'))])
        self.assertEqual(self.whole('--expect', self.whole_id()), 1)
        self.assertEqual(self.m.said, ['This erases a device. Type: sudo isle storage partition create S5 --whole --role registry,assets --expect ' + ident(WHOLE)])
        self.assertEqual(self.commands, [])
        self.untouched()

    def test_a_machine_that_changes_while_the_command_runs(self):
        approved, reads = self.plan_id(), []

        def tree():
            reads.append(1)
            if len(reads) == 2: self.m.disks[2]['children'][2]['size'] = 600001
            return self.real_tree()
        self.m.tree = tree
        self.refused(self.create('--expect', approved), 'the machine changed while this command ran')
        self.assertEqual((len(reads), self.commands), (2, []))

    def test_the_partition_itself_is_probed_and_what_is_found_stops_it(self):
        approved = self.plan_id()
        self.on_disk = EXT4                                              # the machine's report said: no filesystem
        self.refused(self.create('--expect', approved), 'partition sdc3 is not empty: the disk itself shows TYPE=ext4, UUID=old-one there')
        self.on_disk = dict(PTTYPE='gpt')
        self.refused(self.create('--expect', approved), 'shows PTTYPE=gpt there')
        for found, words in (((4, ''), 'could not be probed (blkid ended with exit 4)'), ((0, ''), 'could not be probed (blkid ended with exit 0)'),
                             ((8, 'TYPE=ext4\n'), 'could not be probed (blkid ended with exit 8)'),
                             (FileNotFoundError(2, 'No such file'), 'blkid is not installed on this machine (package util-linux)')):
            self.on_disk = found
            self.refused(self.create('--expect', approved), words)
        self.on_disk = dict(PART_ENTRY_NUMBER='3', PART_ENTRY_SCHEME='gpt', PART_ENTRY_TYPE='0fc63daf-8483-4772-8e79-3d69d8477de4', TYPE='ext4')
        self.refused(self.create('--expect', approved), 'partition sdc3 is not empty: the disk itself shows TYPE=ext4 there')   # the entry is not shown
        self.on_disk = (2, 'DEVNAME=/dev/sdc3\n')                        # the name alone is nothing found
        self.assertEqual(self.create('--expect', approved), 0, self.m.text())

    def test_the_partitions_own_entry_in_the_table_is_nothing_found(self):
        # what blkid --probe prints for an empty GPT partition: the entry alone, with exit 0 (seen on a real 2 TB dock disk)
        entry = ('DEVNAME=/dev/sdc3\nPART_ENTRY_SCHEME=gpt\nPART_ENTRY_UUID=e12c3873-29bd-42a3-a922-29159a33ce12\n'
                 'PART_ENTRY_TYPE=0fc63daf-8483-4772-8e79-3d69d8477de4\nPART_ENTRY_NUMBER=1\nPART_ENTRY_OFFSET=2048\n'
                 'PART_ENTRY_SIZE=3907027087\nPART_ENTRY_DISK=8:32\n')
        self.on_disk = (0, entry)
        self.assertEqual(self.create('--expect', self.plan_id()), 0, self.m.text())
        self.assertEqual(len(self.tools('mkfs.ext4')), 1)

    def test_a_missing_mkfs_is_a_refusal(self):
        self.mkfs = FileNotFoundError(2, 'No such file')
        self.refused(self.create('--expect', self.plan_id()), 'mkfs.ext4 is not installed on this machine (package e2fsprogs)')

    def test_an_identifier_that_is_already_recorded_is_not_used(self):
        approved, real = self.plan_id(), sc.uuid_module.uuid4
        sc.uuid_module.uuid4 = lambda: fx.ARCH
        self.addCleanup(setattr, sc.uuid_module, 'uuid4', real)
        self.refused(self.create('--expect', approved), 'the new identifier is already recorded')


class OnAWholeDevice(Case):
    def test_a_table_with_one_partition_then_the_filesystem_on_it(self):
        before = self.m.record()
        self.assertEqual(self.whole('--label', 'big', '--expect', self.whole_id('--label', 'big')), 0, self.m.text())
        new = re.search(r'uuid=(\S+)', self.inputs[0]).group(1)
        made = self.tools('mkfs.ext4')[0][7]
        self.assertRegex(new, '^%s$' % UUID4)
        self.assertNotEqual(new, made)
        sfdisk = ['sfdisk', '--quiet', '--lock', '--wipe', 'never', '--wipe-partitions', 'never', '/dev/sde']
        self.assertEqual(self.commands, [['blkid', '--probe', '--output', 'export', '/dev/' + name] for name in ('sde', 'sde1', 'sde2', 'sde3', 'sde4')] + [
            sfdisk[:2] + ['--no-act'] + sfdisk[2:], sfdisk, ['udevadm', 'settle', '--timeout=30'], ['blkid', '--probe', '--output', 'export', '/dev/sde1'],
            ['mkfs.ext4', '-q', '-t', 'ext4', '-m', '1', '-U', made, '-L', 'big', '-d', str(self.seen['root']), '/dev/sde1'],
            ['blkid', '--probe', '--output', 'export', '/dev/sde1']])
        self.assertEqual(self.inputs, ['label: gpt\ntype=0FC63DAF-8483-4772-8E79-3D69D8477DE4, uuid=%s\n' % new] * 2)      # the rehearsal is the same call
        self.assertEqual((self.seen['marker']['filesystemUuid'], self.seen['marker']['deviceSerial'], self.seen['marker']['roles']), (made, 'S5', ['registry', 'assets']))
        after = self.m.record()
        self.assertEqual(after['partitions'].pop(made), dict(deviceSerial='S5', assignedAt='2026-01-01T00:00:00+00:00', role='registry', roles=['registry', 'assets']))
        self.assertEqual(after, before)
        self.assertEqual(self.m.said, [
            'Device S5 now has a GPT partition table with one partition, sde1 (partition identifier %s).' % new,
            'Partition sde1 of device S5 now holds one ext4 filesystem, identifier %s. What was there before is gone.' % made,
            'It carries the roles registry, assets: the marker is at its root and this machine records it.',
            'It is not mounted. See: isle storage partition check --table'] + LACKS)
        self.assertEqual(self.slept, [])

    def test_the_device_and_every_partition_shown_on_it_are_probed_first(self):
        approved = self.whole_id()
        for path, found, words in (('/dev/sde', dict(PTTYPE='dos'), 'sde is not empty: the disk itself shows PTTYPE=dos there'),
                                   ('/dev/sde', EXT4, 'sde is not empty: the disk itself shows TYPE=ext4, UUID=old-one there'),
                                   ('/dev/sde4', EXT4, 'sde4 is not empty: the disk itself shows TYPE=ext4, UUID=old-one there, which this machine did not '
                                    'report. This verb never overwrites a filesystem'),
                                   ('/dev/sde2', (4, ''), 'the disk could not be probed (blkid ended with exit 4)')):
            self.found = {path: found}
            self.refused(self.whole('--expect', approved), words)
            self.assertEqual(self.tools('sfdisk', 'mkfs.ext4'), [])

    def test_a_table_tool_that_is_missing_or_refuses_the_call_is_found_by_the_rehearsal(self):
        approved = self.whole_id()
        for result, words in ((FileNotFoundError(2, 'No such file'), 'sfdisk is not installed on this machine (package fdisk). Type: sudo isle apt install fdisk'),
                              ((1, "sfdisk: unrecognized option '--lock'\nTry 'sfdisk --help' for more information.\n"),
                               "sfdisk would not write the table: exit 1 (Try 'sfdisk --help' for more information.)"),
                              ((1, ''), 'sfdisk would not write the table: exit 1. Nothing was changed')):
            self.rehearsal, self.commands = result, []
            self.refused(self.whole('--expect', approved), words)
            self.assertEqual(self.tools('sfdisk'), [] if isinstance(result, BaseException) else [
                ['sfdisk', '--quiet', '--no-act', '--lock', '--wipe', 'never', '--wipe-partitions', 'never', '/dev/sde']])

    def test_the_table_cannot_be_written(self):
        approved = self.whole_id()
        for self.sfdisk, why in (((1, 'sfdisk: cannot open /dev/sde: Device or resource busy\n'), 'sfdisk ended with exit 1 (sfdisk: cannot open /dev/sde: Device or resource busy)'),
                                 ((2, ''), 'sfdisk ended with exit 2. Writing'), (subprocess.TimeoutExpired('sfdisk', 600), 'timed out')):
            self.failed(self.whole('--expect', approved), why, 'Writing the partition table of device S5 had started, so the device may hold a part of one. '
                        'No filesystem was made and this machine records nothing for it. See: isle storage partition create S5')
            self.assertEqual(self.tools('mkfs.ext4', 'udevadm'), [])

    def test_the_machine_is_asked_until_it_shows_the_new_partition(self):
        self.shows_after, self.shown_as = 3, str.upper                   # a report in capitals is the same identifier
        self.assertEqual(self.whole('--expect', self.whole_id()), 0, self.m.text())
        self.assertEqual(self.slept, [1, 1, 1])
        self.assertEqual(len(self.tools('udevadm')), 1)

    def test_no_settle_tool_is_no_obstacle(self):
        for settle in (FileNotFoundError(2, 'No such file'), subprocess.TimeoutExpired('udevadm', 60), (1, '')):
            self.setUp()
            self.settle = settle
            self.assertEqual(self.whole('--expect', self.whole_id()), 0, self.m.text())

    def test_a_partition_that_is_not_shown_in_time_can_be_gone_on_with(self):
        self.shows_after = 99
        self.failed(self.whole('--label', 'big', '--expect', self.whole_id('--label', 'big')), 'this machine does not show the new partition yet. Device S5 now has')
        new = re.search(r'uuid=(\S+)', self.inputs[0]).group(1)
        self.assertEqual(self.m.said, [
            'FAILED: partition create: this machine does not show the new partition yet. Device S5 now has a partition table with one partition '
            '(partition identifier %s). No filesystem was made and this machine records nothing for it. When this machine shows that partition with no '
            'filesystem (unplug the device and plug it in again, or restart), go on with: isle storage partition create S5 --partition %s --role '
            'registry,assets --label big' % (new, new)])
        self.assertEqual((self.slept, self.tools('mkfs.ext4')), ([1] * (storage_table.LOOKS - 1), []))
        self.shows_after = 0                                             # plugged in again: the partition is shown
        self.found = {}
        self.assertEqual(self.create('--label', 'big', '--expect', self.plan_id('--label', 'big', serial='S5', partition=new), serial='S5', partition=new), 0, self.m.text())
        self.assertIn('Partition sde1 of device S5 now holds one ext4 filesystem', self.m.text())
        self.commands, self.before = [], (self.m.records.read_bytes(), self.m.listing())
        self.refused(self.whole(), 'the device has a partition table (gpt) with 1 partition. --whole is for a device without one')

    def test_a_filesystem_from_before_inside_the_new_partition_is_kept(self):
        self.old = EXT4
        self.failed(self.whole('--expect', self.whole_id()), 'the new partition shows TYPE=ext4, UUID=old-one, which was on the disk before. This verb never '
                    'overwrites a filesystem. Device S5 now has a partition table with one partition (partition identifier ',
                    'No filesystem was made and this machine records nothing for it.')
        self.assertEqual(self.tools('mkfs.ext4'), [])
        self.assertEqual(len(self.tools('sfdisk')), 2)

    def test_the_new_partitions_own_entry_does_not_stop_it(self):
        # the real 2 TB dock disk: right after the table is written the new partition shows only its entry in the table
        self.old = (0, 'DEVNAME=/dev/sde1\nPART_ENTRY_SCHEME=gpt\nPART_ENTRY_NUMBER=1\nPART_ENTRY_OFFSET=2048\nPART_ENTRY_DISK=8:64\n')
        self.assertEqual(self.whole('--expect', self.whole_id()), 0, self.m.text())
        self.assertIn('now holds one ext4 filesystem', self.m.text())

    def test_failures_once_the_table_is_written_are_never_refusals(self):
        approved = self.whole_id()
        for change, words in ((dict(shown_as=lambda new: None), 'does not show the new partition yet'),
                              (dict(mkfs=FileNotFoundError(2, 'No such file')), 'mkfs.ext4 is not installed on this machine (package e2fsprogs). Device S5 now has a partition table'),
                              (dict(mkfs=(1, 'mkfs.ext4: no\n')), 'mkfs.ext4 ended with exit 1 (mkfs.ext4: no). Making the filesystem on partition sde1 had started'),
                              (dict(old=(4, '')), 'the disk could not be probed (blkid ended with exit 4). Device S5 now has a partition table'),
                              (dict(old=FileNotFoundError(2, 'gone')), 'blkid is not installed on this machine (package util-linux). Device S5 now has')):
            self.setUp()
            self.shows_after = 99 if 'shown_as' in change else 0
            for key, value in change.items(): setattr(self, key, value)
            self.failed(self.whole('--expect', approved), *([words] if isinstance(words, str) else words))
        self.setUp()

        def named(command, **kwargs):
            done = self.run_tool(command, **kwargs)
            if command[0] == 'sfdisk' and self.pending: self.pending['name'] = '../sde1'
            return done
        self.m.run = named
        self.failed(self.whole('--expect', approved), 'the new partition has no name this CLI can use. Device S5 now has a partition table')


class AfterTheFilesystemWasStarted(Case):
    """From here on "Nothing was changed" would be untrue, so no line says it."""

    def test_mkfs_fails(self):
        self.mkfs = (1, 'mkfs.ext4: Device size reported to be zero.\n\n')
        self.failed(self.create('--expect', self.plan_id()), 'mkfs.ext4 ended with exit 1 (mkfs.ext4: Device size reported to be zero.)',
                    'had started, so what was on it is lost and it may hold a part of a filesystem. This machine records nothing for it.')
        self.mkfs = (1, '')
        self.failed(self.create('--expect', self.plan_id()), 'mkfs.ext4 ended with exit 1. Making the filesystem on partition sdc3 had started')
        self.mkfs = subprocess.TimeoutExpired('mkfs.ext4', 3600)
        self.failed(self.create('--expect', self.plan_id()), 'had started')

    def test_it_does_not_read_back_as_made(self):
        approved = self.plan_id()

        def run(command, **kwargs):
            done = self.run_tool(command, **kwargs)
            if command[0] == 'mkfs.ext4': self.on_disk = self.wrong(self.on_disk) if callable(self.wrong) else self.wrong
            return done
        self.m.run = run
        for self.wrong in (dict(UUID='another', TYPE='ext4'), dict(TYPE='ext4'), {}, (4, ''), FileNotFoundError(2, 'gone')):
            self.on_disk = {}
            self.failed(self.create('--expect', approved), 'Making the filesystem on partition sdc3 had started')
        for self.wrong in (dict(UUID='x', TYPE='xfs'), dict(UUID='another', TYPE='ext4'), lambda made: dict(made, TYPE='xfs')):
            self.on_disk = {}
            self.failed(self.create('--expect', approved), 'the new filesystem does not read back as it was made')

    def test_the_record_cannot_be_written(self):
        approved, real = self.plan_id(), sc.atomic

        def full(path, value): raise OSError(28, 'No space left on device')
        sc.atomic = full
        self.addCleanup(setattr, sc, 'atomic', real)
        self.failed(self.create('--expect', approved), 'No space left on device',
                    'The filesystem was made on partition sdc3 (identifier %s) and the marker of the roles registry, assets is at its root, but this machine '
                    'does not record it. Once it is mounted: sudo isle storage partition assign %s --role registry,assets' % ((self.on_disk['UUID'],) * 2))

        def stopped(path, value): raise ValueError('The record was not written.')
        sc.atomic, self.on_disk = stopped, {}
        self.failed(self.create('--expect', approved), 'FAILED: partition create: The record was not written. The filesystem was made on partition sdc3')


class Refusals(Case):
    def test_what_was_typed(self):
        for more, named, words in (((), dict(roles='attic'), 'the role must be one of'), ((), dict(roles='registry,registry'), 'the role must be one of'),
                                   ((), dict(roles=''), 'the role must be one of'), ((), dict(serial=''), 'not a hardware serial'),
                                   ((), dict(partition='x'), 'not a partition identifier'), ((), dict(partition='../sdc3'), 'not a partition identifier'),
                                   (('--label', 'seventeen-letters'), {}, 'a label is 1 to 16'), (('--label', 'two words'), {}, 'a label is 1 to 16'),
                                   (('--label', ''), {}, 'a label is 1 to 16'), (('--expect', 'abc'), {}, 'not a plan id'),
                                   (('--expect', 'F' * 64), {}, 'not a plan id'), (('--expect', ''), {}, 'not a plan id'),
                                   ((), dict(partition=None, roles='attic'), 'the role must be one of'), (('--label', 'a b'), dict(partition=None), 'a label is 1 to 16')):
            self.refused(self.create(*more, **named), words)
        self.assertEqual(self.commands, [])

    def test_the_device(self):
        for whole in (False, True):
            create = self.whole if whole else self.create
            self.refused(create(serial='S9'), 'no storage device with the serial S9')
        self.m.disks[1]['serial'] = 'S3'
        self.refused(self.create(), 'two devices report the serial S3')
        self.m.disks[1]['serial'] = 'S2'
        self.m.disks[2]['ro'] = True
        self.refused(self.create(), 'the device is read-only')
        self.m.disks[2].update(ro=False, size=0)
        self.refused(self.create(), 'the device reports no size')
        self.m.disks[2]['size'] = 2000000
        self.m.disks[2]['name'] = 'sd/c'
        self.refused(self.create(), 'the device has no name this CLI can use')
        self.m.disks[2]['name'] = 'sdc'
        self.assertEqual(self.m.call('device', 'retire', 'S5'), 0)
        self.before = (self.m.records.read_bytes(), self.m.listing())
        self.refused(self.whole(), 'the device is not registered. First: sudo isle storage device register S5 --class fixed|dock|flash')

    def test_the_system_disk(self):
        self.m.disks[0]['children'].append(dict(name='sda3', type='part', size=1000, fstype=None, uuid=None, partuuid='part-sda3', mountpoints=[None]))
        self.refused(self.create(serial='S1', partition='part-sda3'), 'the device is not registered')
        self.assertEqual(self.m.call('device', 'register', 'S1', '--class', 'fixed'), 0, self.m.text())
        self.before = (self.m.records.read_bytes(), self.m.listing())
        self.refused(self.create(serial='S1', partition='part-sda3'), 'the device carries the running system of this machine')
        self.refused(self.create('--expect', 'a' * 64, serial='S1', partition='part-sda3'), 'the device carries the running system of this machine')
        self.refused(self.whole(serial='S1'), 'the device carries the running system of this machine')
        self.refused(self.m.call('partition', 'create', 'S1'), 'the device carries the running system of this machine')

    def test_the_partition(self):
        empty = 'Its partitions with no filesystem: sdc3 (600.0 KB, partition identifier part-sdc3)'
        self.refused(self.create(partition='part-none'), 'the device has no partition with the identifier part-none. ' + empty)
        self.refused(self.create(partition='part-sdb1'), 'the device has no partition with the identifier part-sdb1. ' + empty)       # of another device
        self.refused(self.create(partition='part-sdc2'), 'partition sdc2 holds ext4 (identifier spare-0001). This verb never overwrites a filesystem')
        self.refused(self.create(partition='part-sdc1'), 'partition sdc1 holds ext4 (identifier dock-0001)')
        part = self.m.disks[2]['children'][2]
        for change, words in ((dict(fstype='crypto_LUKS'), 'partition sdc3 holds crypto_LUKS. This verb'), (dict(fstype='swap', uuid='sw-1'), 'holds swap (identifier sw-1)'),
                              (dict(children=[dict(name='dm-0', type='crypt')]), 'something is stacked on partition sdc3 (dm-0)'),
                              (dict(mountpoints=['/srv/somewhere']), 'partition sdc3 is in use (/srv/somewhere)'), (dict(ro=True), 'partition sdc3 is read-only'),
                              (dict(size=0), 'partition sdc3 reports no size'), (dict(size=None), 'partition sdc3 reports no size'),
                              (dict(name='../sdc3'), 'the partition has no name this CLI can use'), (dict(name=None), 'no name this CLI can use'),
                              (dict(type='crypt'), 'the device has no partition with the identifier part-sdc3')):
            kept = dict(part)
            part.update(change)
            self.refused(self.create(), words)
            part.clear(); part.update(kept)
        self.assertEqual(self.create(), 0)                               # and as it was, it is a plan again

    def test_a_device_that_is_not_bare_takes_no_new_table(self):
        self.refused(self.whole(serial='S3'), 'the device has a partition table with 3 partitions. --whole is for a device without one, and adding a partition to a '
                     'table is not built. For a partition of it that has no filesystem, --partition: sdc3 (600.0 KB, partition identifier part-sdc3)')
        self.refused(self.whole(serial='S2'), 'the device has a partition table with 1 partition. --whole is for a device without one, and adding a partition to a '
                     'table is not built. Nothing was changed')
        part = self.blank['children'][1]
        for where, change, words in ((self.blank, dict(pttype='gpt'), 'the device has a partition table (gpt) with 0 partitions'),
                                     (self.blank, dict(fstype='ext4'), 'the device holds ext4 directly, with no partition table. This verb never overwrites a filesystem'),
                                     (self.blank, dict(mountpoints=['/srv/x']), 'the device is in use (/srv/x)'),
                                     (part, dict(partuuid='part-real'), 'the device has a partition table with 1 partition'),
                                     (part, dict(fstype='ext4', uuid='u-1'), 'partition sde2 holds ext4 (identifier u-1). This verb never overwrites a filesystem'),
                                     (part, dict(mountpoints=['/srv/x']), 'partition sde2 is in use (/srv/x)'), (part, dict(ro=True), 'partition sde2 is read-only'),
                                     (part, dict(children=[dict(name='dm-1', type='crypt')]), 'something is stacked on partition sde2 (dm-1)'),
                                     (part, dict(type='crypt'), 'something is stacked on the device (sde2)'),
                                     (part, dict(name='sd e2'), 'the partition has no name this CLI can use')):
            kept = dict(where)
            where.update(change)
            self.refused(self.whole(), words)
            self.refused(self.whole('--expect', ident(WHOLE)), words)
            where.clear(); where.update(kept)
        self.assertEqual(self.whole(), 0)

    def test_an_identifier_that_two_partitions_report(self):
        self.m.disks[2]['children'][1]['partuuid'] = P3
        self.refused(self.create(), 'two partitions report the identifier part-sdc3')
        self.m.disks[2]['children'][1]['partuuid'] = 'part-sdc2'
        self.m.disks[1]['children'][0]['partuuid'] = P3                  # the copy is on another device
        self.refused(self.create(), 'two partitions report the identifier part-sdc3')

    def test_a_damaged_record(self):
        self.m.records.write_text('{')
        self.before = (self.m.records.read_bytes(), self.m.listing())
        self.refused(self.create(), 'are damaged')
        self.refused(self.whole(), 'are damaged')
        self.refused(self.m.call('partition', 'create', 'S3'), 'are damaged')

    def test_usage(self):
        for argv in (('partition', 'create'), ('partition', 'create', 'S3', 'more'), ('partition', 'create', 'S3', '--partition', P3),
                     ('partition', 'create', 'S3', '--role', 'registry'), ('partition', 'create', 'S3', '--partition', P3, '--role'),
                     ('partition', 'create', 'S3', '--partition', P3, '--role', 'registry', '--role', 'assets'),
                     ('partition', 'create', 'S3', '--partition', P3, '--role', 'registry', '--force'),
                     ('partition', 'create', 'S3', '--label', 'x'), ('partition', 'create', '--partition', P3, '--role', 'registry'),
                     ('partition', 'create', 'S5', '--whole'), ('partition', 'create', 'S5', '--whole', '--whole', '--role', 'registry'),
                     ('partition', 'create', 'S3', '--whole', '--partition', P3, '--role', 'registry'), ('partition', 'create', '--whole', '--role', 'registry')):
            self.assertEqual(self.m.call(*argv), 2, argv)
            self.assertIn('isle storage partition create <serial> [--partition <partition-identifier>|--whole --role <role>[,<role>...] [--label <label>]]', self.m.text())
            self.assertIn('--expect <plan-id>      (erases that partition or device)', self.m.text())
        self.assertEqual(self.commands, [])
        self.untouched()

    def test_the_machine_is_asked_for_what_the_checks_read(self):
        for column in ('PARTUUID', 'PTTYPE', 'FSTYPE', 'SERIAL', 'RO', 'MOUNTPOINTS'):
            self.assertIn(column, sc.storage_devices.COLUMNS.split(','))
        self.assertEqual(sr.Io().node('sdx1'), '/dev/sdx1')

    def test_the_code_has_no_force_and_starts_nothing_else(self):
        for name, calls in (('storage_create.py', 2), ('storage_table.py', 2)):
            source = (HERE / name).read_text().split('"""', 2)[2]
            for word in ("'-F'", '--force', 'wipefs', 'sgdisk', 'parted', "'dd'", "'mount'", "'umount'", 'shell=True', "'always'", '--append', '--delete'):
                self.assertNotIn(word, source, (name, word))
            self.assertEqual(source.count('io.run('), calls, name)       # the probe and the mkfs; the table tool and the settle


@unittest.skipUnless(all(shutil.which(tool) for tool in ('mkfs.ext4', 'blkid', 'debugfs', 'sfdisk')), 'mkfs.ext4, blkid, debugfs and sfdisk are needed')
class TheRealTools(Case):
    """The real probe, table tool and mkfs.  Files of 64 MiB in the temporary folder stand in for the device and for
    its partition; only the step in between is made up: the machine showing the partition of the new table."""

    def setUp(self):
        super().setUp()
        self.files = {}
        for name in ('device', 'partition', 'shown'):
            self.files[name] = self.m.tmp / name
            with open(self.files[name], 'wb') as out: out.truncate(64 * 1024 * 1024)
        with open(self.files['device'], 'r+b') as out: out.write(b'\x55' * (1 << 20))      # a disk filled with a test pattern
        self.m.run = subprocess.run
        self.m.node = lambda name: str(self.files['device' if name == 'sde' else 'partition' if self.table() or name == 'sdc3' else 'shown'])
        self.m.tree = self.tree_of_the_files
        self.before = (self.m.records.read_bytes(), self.m.listing())

    def table(self):
        done = subprocess.run(['sfdisk', '--json', str(self.files['device'])], capture_output=True, text=True)
        return json.loads(done.stdout)['partitiontable'] if done.stdout.startswith('{') else None

    def tree_of_the_files(self):
        table = self.table()
        if table:
            self.blank.update(pttype='gpt', children=[dict(name='sde1', type='part', size=table['partitions'][0]['size'] * 512, fstype=None, uuid=None,
                                                           partuuid=table['partitions'][0]['uuid'], mountpoints=[None])])
        return self.real_tree()

    def marker(self):
        return json.loads(subprocess.run(['debugfs', '-R', 'cat /' + sr.MARKER, str(self.files['partition'])], capture_output=True, text=True, check=True).stdout)

    def test_a_filesystem_is_made_with_the_marker_at_its_root(self):
        self.assertEqual(self.create('--label', 'made-here', '--expect', self.plan_id('--label', 'made-here')), 0, self.m.text())
        made = [i for i in self.m.record()['partitions'] if i != fx.ARCH][0]
        found = sc.probe(self.m, str(self.files['partition']))
        self.assertEqual((found.get('UUID'), found.get('LABEL'), found.get('TYPE')), (made, 'made-here', 'ext4'))
        marker = self.marker()
        self.assertEqual((marker['kind'], marker['filesystemUuid'], marker['deviceSerial'], sr.roles_of(marker)), ('isle.storage.partition', made, 'S3', ['registry', 'assets']))
        listing = subprocess.run(['debugfs', '-R', 'ls -l /', str(self.files['partition'])], capture_output=True, text=True, check=True).stdout
        self.assertRegex(listing, r'\s40755 \(2\)\s+\d+\s+\d+\s+\d+ \S+ \S+ \.\s')              # the root folder can be entered by anyone
        self.assertRegex(listing, r'\s100644 \(1\)\s+\d+\s+\d+\s+\d+ \S+ \S+ isle-partition\.json')

    def test_a_filesystem_that_the_report_did_not_show_is_found_by_the_probe(self):
        subprocess.run(['mkfs.ext4', '-q', str(self.files['partition'])], check=True, stdin=subprocess.DEVNULL, capture_output=True)
        before = self.files['partition'].read_bytes()[:1 << 20]
        code = self.create('--expect', self.plan_id())
        self.assertEqual(code, 1, self.m.text())
        self.assertIn('REFUSED', self.m.text())
        self.assertIn('partition sdc3 is not empty: the disk itself shows', self.m.text())
        self.assertIn('TYPE=ext4', self.m.text())
        self.assertEqual(self.files['partition'].read_bytes()[:1 << 20], before)             # and it was left as it was

    def test_a_pattern_filled_device_gets_its_table_and_the_filesystem(self):
        self.assertEqual(sc.probe(self.m, str(self.files['device'])), {})                      # the probe sees nothing on such a disk
        self.assertEqual(self.whole('--expect', self.whole_id()), 0, self.m.text())
        table = self.table()
        self.assertEqual((table['label'], len(table['partitions']), table['partitions'][0]['type'], table['partitions'][0]['start']),
                         ('gpt', 1, storage_table.LINUX_FILESYSTEM, 2048))
        self.assertEqual(table['partitions'][0]['start'] + table['partitions'][0]['size'] - 1, table['lastlba'])       # over the whole device
        self.assertIn('partition identifier %s)' % table['partitions'][0]['uuid'].lower(), self.m.said[0])
        made = [i for i in self.m.record()['partitions'] if i != fx.ARCH][0]
        self.assertEqual((self.marker()['filesystemUuid'], self.marker()['deviceSerial']), (made, 'S5'))
        self.assertEqual(sc.probe(self.m, str(self.files['device'])).get('PTTYPE'), 'gpt')

    def test_a_device_with_a_table_the_report_did_not_show_is_left_alone(self):
        subprocess.run(['sfdisk', '--quiet', str(self.files['device'])], input='label: dos\n', text=True, check=True, capture_output=True)
        before = self.files['device'].read_bytes()
        self.m.tree = self.real_tree                                     # the machine's report still shows a bare device
        code = self.whole('--expect', self.whole_id())
        self.assertEqual(code, 1, self.m.text())
        self.assertIn('REFUSED', self.m.text())
        self.assertIn('sde is not empty: the disk itself shows', self.m.text())
        self.assertIn('PTTYPE=dos', self.m.text())
        self.assertEqual(self.files['device'].read_bytes(), before)

    def test_a_filesystem_from_before_in_the_new_partition_is_left_as_it_was(self):
        approved = self.whole_id()
        subprocess.run(['mkfs.ext4', '-q', str(self.files['partition'])], check=True, stdin=subprocess.DEVNULL, capture_output=True)
        before = self.files['partition'].read_bytes()[:1 << 20]
        self.m.node = lambda name: str(self.files['device' if name == 'sde' else 'partition' if self.table() else 'shown'])
        self.failed(self.whole('--expect', approved), 'the new partition shows', 'TYPE=ext4', 'which was on the disk before. This verb never overwrites a filesystem')
        self.assertEqual(self.files['partition'].read_bytes()[:1 << 20], before)
        self.assertEqual(len(self.table()['partitions']), 1)


if __name__ == '__main__':
    unittest.main()
