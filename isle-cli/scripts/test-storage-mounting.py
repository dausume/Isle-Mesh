"""isle storage partition mount | unmount.

Fixtures only, the made-up machine of test-storage-roles.py; systemctl gives made-up answers, the unit folder and the
fstab are files in a temporary folder.  No real unit, no real mount, no real record and no network is touched.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import storage_mounting as sm
import storage_roles as sr
from storage_mount import mount_unit_name

_spec = importlib.util.spec_from_file_location('storage_roles_fixtures', HERE / 'test-storage-roles.py')
fx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fx)

SPARE, DOCK, ARCH, ROOT = fx.SPARE, fx.DOCK, fx.ARCH, fx.ROOT
FSTAB = '# a comment that names UUID=spare-0001 /somewhere\n\nlonely\nUUID=root-0001 / ext4 defaults 0 1\n/dev/sdz9 %s ext4 defaults 0 2\n#/dev/sdz8 %s ext4 defaults 0 2\n'


def answer(command, code, err=''):
    return subprocess.CompletedProcess(command, code, stdout='', stderr=err)


class Case(fx.Case):
    def setUp(self):
        super().setUp()
        self.m.ready()
        self.tmp = Path(self._tmp.name).resolve()
        self.m.unit_dir, self.m.fstab = self.tmp / 'units', self.tmp / 'fstab'
        self.m.unit_dir.mkdir()
        self.at, self.other = str(self.tmp / 'mnt' / 'spare'), str(self.tmp / 'mnt' / 'other')
        self.m.fstab.write_text(FSTAB % (self.other, self.at))
        self.unit = mount_unit_name(self.at)
        self.commands, self.answers, self.takes, self.lets_go, self.was_there = [], {}, True, True, None
        self.m.run = self.run_tool
        self.keep()

    def keep(self):
        self.before = self.state()

    def state(self):
        return (self.m.records.read_bytes(), self.m.listing(), {p.name: p.read_text() for p in sorted(self.m.unit_dir.iterdir()) if p.is_file()},
                json.dumps(self.m.mount_rows, sort_keys=True))

    def run_tool(self, command, **kwargs):
        self.commands.append(list(command))
        self.assertEqual(command[0], 'systemctl')
        verb = command[1]
        result = self.answers.get(verb, (0, ''))
        if callable(result): result = result()
        if isinstance(result, FileNotFoundError): self.commands.pop()               # a tool that is not there ran nothing
        if isinstance(result, BaseException): raise result
        if result[0] == 0 and verb == 'start' and self.takes:
            lines = (self.m.unit_dir / command[2]).read_text().splitlines()
            where = [line[len('Where='):] for line in lines if line.startswith('Where=')][0]
            what = [line[len('What='):] for line in lines if line.startswith('What=')][0]
            self.was_there = Path(where).exists()
            Path(where).mkdir(parents=True, exist_ok=True)                            # systemd makes the folder of a mount unit
            (Path(where) / 'on-the-partition.txt').write_text('what the partition holds\n')
            self.m.mount_rows.append(dict(target=where, fsroot='/', uuid=what.rsplit('/', 1)[1], options='rw'))
        if result[0] == 0 and verb == 'stop' and self.lets_go:
            for row in [m for m in self.m.mount_rows if mount_unit_name(m['target']) == command[2]]:
                self.m.mount_rows.remove(row)
                (Path(row['target']) / 'on-the-partition.txt').unlink()               # what the partition holds goes out of sight with it
        return answer(command, result[0], result[1] if kwargs.get('text') else result[1].encode())

    def mount(self, identifier=SPARE, at=None):
        self.commands = []
        return self.m.call('partition', 'mount', identifier, '--at', self.at if at is None else at)

    def unmount(self, *more, identifier=SPARE):
        self.commands = []
        return self.m.call('partition', 'unmount', identifier, *more)

    def verbs(self):
        return [c[1:] for c in self.commands]

    def refused(self, code, *words):
        super().refused(code, *words)
        self.assertEqual([c for c in self.commands if c[1] != '--version'], [])      # nothing that changes was started
        self.assertEqual(self.state(), self.before)

    def failed(self, code, verb, *words):
        self.assertEqual(code, 1, self.m.text())
        self.assertTrue(self.m.text().startswith('FAILED: partition %s: ' % verb), self.m.text())
        self.assertNotIn('Nothing was changed', self.m.text())
        for word in words: self.assertIn(word, self.m.text())

    def give_role(self, identifier=SPARE, role='scratch'):
        doc = self.m.record()
        doc['partitions'][identifier] = dict(role=role, deviceSerial='S3', assignedAt='2026-01-01T00:00:00+00:00')
        self.m.records.write_text(json.dumps(doc))


class TheUnit(Case):
    def test_it_names_the_filesystem_by_its_identifier_and_is_wanted_by_the_device(self):
        self.assertEqual(sm.unit_text('spare-0001', '/mnt/isle/one', 'ext4'), '\n'.join([
            '# Written by: isle storage partition mount spare-0001',
            '[Unit]',
            'Description=Isle storage partition spare-0001',
            '',
            '[Mount]',
            'What=/dev/disk/by-uuid/spare-0001',
            'Where=/mnt/isle/one',
            'Type=ext4',
            '',
            '[Install]',
            'WantedBy=dev-disk-by\\x2duuid-spare\\x2d0001.device',
            '']))
        self.assertNotIn('local-fs', sm.unit_text('spare-0001', '/mnt/isle/one', 'ext4'))   # a start of the machine never waits for it

    def test_the_code_starts_systemctl_only_and_never_writes_the_fstab(self):
        source = (HERE / 'storage_mounting.py').read_text()
        self.assertEqual(source.count('io.run('), 1)
        self.assertIn("io.run(['systemctl'] + list(args)", source)
        for word in ('subprocess.run', 'subprocess.call', 'Popen', 'shell=True', 'write_text', 'open(', '--force', '--lazy', 'rmdir', 'rmtree', 'umount'):
            self.assertNotIn(word, source, word)
        self.assertEqual(source.count('_write('), 1)                    # the unit; the fstab is only read
        self.assertEqual(source.count('os.unlink('), 1)                 # the unit, in unmount


class Mounting(Case):
    def test_without_root_it_says_what_it_would_do_and_changes_nothing(self):
        self.m.root_user = False
        self.assertEqual(self.mount(), 1)
        self.assertEqual(self.m.said, ['This writes the mount unit %s and mounts partition sdc2 at %s. Type: sudo isle storage partition mount %s --at %s' % (
            self.unit, self.at, SPARE, self.at)])
        self.assertEqual(self.commands, [])
        self.assertEqual(self.state(), self.before)

    def test_it_writes_the_unit_enables_it_and_starts_it(self):
        self.assertEqual(self.mount(), 0, self.m.text())
        self.assertEqual(self.verbs(), [['--version'], ['daemon-reload'], ['enable', self.unit], ['start', self.unit]])
        self.assertEqual((self.m.unit_dir / self.unit).read_text(), sm.unit_text(SPARE, self.at, 'ext4'))
        self.assertEqual((self.m.unit_dir / self.unit).stat().st_mode & 0o777, 0o644)
        self.assertEqual(sorted(p.name for p in self.m.unit_dir.iterdir()), [self.unit])
        self.assertEqual(self.m.said, [
            'Partition sdc2 (%s) is mounted at %s.' % (SPARE, self.at),
            'It is mounted again each time its device is present (unit %s). A start of this machine does not wait for it.' % self.unit,
            'It carries no role on this machine. To give it one: sudo isle storage partition assign %s --role <role>[,<role>...]' % SPARE])
        self.assertEqual(self.m.records.read_bytes(), self.before[0])                 # the unit is the record of the mount; nothing else keeps it
        self.assertIs(self.was_there, False)                                          # systemd makes the folder, not this verb

    def test_a_trailing_slash_names_the_same_folder(self):
        self.assertEqual(self.mount(at=self.at + '/'), 0, self.m.text())
        self.assertIn('Where=%s\n' % self.at, (self.m.unit_dir / self.unit).read_text())

    def test_a_partition_with_a_role_is_pointed_at_the_check(self):
        self.give_role()
        self.assertEqual(self.mount(), 0, self.m.text())
        self.assertEqual(self.m.said[-1], 'See: isle storage partition check --table')

    def test_an_empty_folder_that_is_there_is_taken(self):
        Path(self.at).mkdir(parents=True)
        self.assertEqual(self.mount(), 0, self.m.text())

    def test_a_second_time_changes_nothing(self):
        self.assertEqual(self.mount(), 0, self.m.text())
        self.keep()
        self.assertEqual(self.mount(), 0, self.m.text())
        self.assertEqual(self.m.said, ['Partition sdc2 (%s) is mounted at %s by this verb already. Nothing was changed.' % (SPARE, self.at)])
        self.assertEqual(self.commands, [])
        self.assertEqual(self.state(), self.before)

    def test_a_step_that_fails_says_what_is_there_and_the_same_command_goes_on(self):
        for verb, extra in (('daemon-reload', 'is written, and'), ('enable', 'is written, and'), ('start', 'is written and enabled, and')):
            with self.subTest(verb=verb):
                self.setUp()
                self.answers[verb] = (1, 'first line\nJob failed. See the journal.\n\n')
                self.failed(self.mount(), 'mount', 'systemctl %s ended with exit 1 (Job failed. See the journal.). The unit %s %s the partition is not mounted by it.' % (
                    verb, self.unit, extra), 'To try again, type the same command; to take the unit away: sudo isle storage partition unmount %s' % SPARE)
                self.assertEqual(self.verbs()[-1][0], verb)                           # nothing was started after the step that failed
                self.assertEqual((self.m.unit_dir / self.unit).read_text(), sm.unit_text(SPARE, self.at, 'ext4'))
                self.assertEqual(json.dumps(self.m.mount_rows, sort_keys=True), self.before[3])
                del self.answers[verb]
                self.assertEqual(self.mount(), 0, self.m.text())
                self.assertEqual(self.m.said[0], 'Partition sdc2 (%s) is mounted at %s.' % (SPARE, self.at))

    def test_a_start_that_mounts_nothing_is_a_failure(self):
        self.takes = False
        self.failed(self.mount(), 'mount', 'the unit started, and the partition does not show as mounted at %s' % self.at, 'is written and enabled')

    def test_a_start_that_mounts_it_somewhere_else_is_a_failure(self):
        self.takes = False
        self.answers['start'] = lambda: self.m.mount_rows.append(dict(target=str(self.tmp / 'elsewhere'), fsroot='/', uuid=SPARE, options='rw')) or (0, '')
        self.failed(self.mount(), 'mount', 'does not show as mounted at %s' % self.at)
        self.keep()                                                                   # the unit is there, and the partition is mounted another way
        self.refused(self.mount(), 'partition sdc2 is mounted already (%s), and not by a unit of this verb for that folder' % (self.tmp / 'elsewhere'))

    def test_a_mount_made_by_hand_at_that_folder_is_not_taken_over(self):
        Path(self.at).mkdir(parents=True)
        self.m.mount_rows.append(dict(target=self.at, fsroot='/', uuid=SPARE, options='rw'))
        self.keep()
        self.refused(self.mount(), 'partition sdc2 is mounted already (%s), and not by a unit of this verb for that folder. This verb does not take over a mount' % self.at)

    def test_the_real_places_are_the_ones_of_systemd(self):
        self.assertEqual((sr.Io.unit_dir, sr.Io.fstab), ('/etc/systemd/system', '/etc/fstab'))

    def test_a_tool_error_after_the_unit_was_written_is_a_failure_not_a_refusal(self):
        self.answers['enable'] = subprocess.TimeoutExpired('systemctl', 180)
        self.failed(self.mount(), 'mount', 'The unit %s is written, and' % self.unit)

    def test_what_it_refuses(self):
        cases = [
            (dict(identifier='no'), 'that is not a filesystem identifier'),
            (dict(identifier='gone-0001'), 'this machine sees no filesystem with the identifier gone-0001'),
            (dict(identifier=ROOT), 'partition sda2 carries the running system of this machine'),
            (dict(identifier='whole-0001'), 'its device is not registered. First: sudo isle storage device register <serial> --class fixed|dock|flash'),
            (dict(identifier=DOCK), 'partition sdc1 is mounted already (%s), and not by a unit of this verb for that folder. This verb does not take over a mount'
             % self.m.folders['dock']),
            (dict(at='mnt/spare'), 'The folder must be an absolute path'),
            (dict(at='/'), 'the folder must be a plain absolute path, and not /'),
            (dict(at=self.at + '//x'), 'the folder must be a plain absolute path, and not /'),
            (dict(at=self.at + '/../x'), 'without ".."'),
            (dict(at=str(self.m.folders['archive'])), 'something is mounted at %s already' % self.m.folders['archive']),
            (dict(at=self.other), '/etc/fstab names that folder (for /dev/sdz9). This verb does not write a second way to mount it'),
        ]
        for named, why in cases:
            with self.subTest(why=why): self.refused(self.mount(**named), why)

    def test_a_partition_that_is_recorded_and_away_is_refused(self):
        self.give_role()
        self.m.disks[2]['children'].pop(1)
        self.keep()
        self.refused(self.mount(), 'this machine records the partition %s, and it is not present' % SPARE)

    def test_two_partitions_with_one_identifier_are_refused(self):
        self.m.disks[2]['children'][2].update(fstype='ext4', uuid=SPARE)
        self.refused(self.mount(), 'two partitions report the identifier %s' % SPARE)

    def test_a_device_that_was_retired_is_refused(self):
        self.assertEqual(self.m.call('device', 'retire', 'S3'), 0, self.m.text())
        self.keep()
        self.refused(self.mount(), 'its device is not registered')

    def test_what_is_not_a_plain_filesystem_is_refused(self):
        self.m.disks[2]['children'][1]['fstype'] = 'crypto_LUKS'
        self.refused(self.mount(), 'this machine sees no filesystem with the identifier %s' % SPARE)
        for odd in ('ext 4', 'ext4,x', 'Ext4', ''):
            self.m.disks[2]['children'][1]['fstype'] = odd
            with self.subTest(odd=odd): self.refused(self.mount(), 'holds no filesystem this verb can name' if odd else 'sees no filesystem with the identifier')

    def test_a_folder_that_holds_something_or_is_not_a_folder_is_refused(self):
        Path(self.at).mkdir(parents=True)
        (Path(self.at) / 'kept.txt').write_text('stays\n')
        self.keep()
        self.refused(self.mount(), '%s is not empty: what is in it would be hidden by the mount' % self.at)
        self.refused(self.mount(at=self.at + '/kept.txt'), 'kept.txt is there and is not a folder')
        (self.tmp / 'link').symlink_to(self.tmp / 'mnt')
        self.keep()
        self.refused(self.mount(at=str(self.tmp / 'link' / 'spare')), 'is reached through a link')
        self.refused(self.mount(at=str(self.tmp / 'link')), 'is reached through a link')

    def test_a_folder_something_is_mounted_on_is_refused_even_when_empty(self):
        Path(self.at).mkdir(parents=True)
        self.m.mount_rows.append(dict(target=self.at, fsroot='/', uuid='other-0001', options='rw'))
        self.keep()
        self.refused(self.mount(), 'something is mounted at %s already' % self.at)

    def test_a_filesystem_the_fstab_names_is_refused(self):
        for line in ('UUID=SPARE-0001 /mnt/x ext4 defaults 0 2', '/dev/disk/by-uuid/spare-0001\t/mnt/x\text4', 'UUID=other %s/ ext4' % self.at):
            with self.subTest(line=line):
                self.m.fstab.write_text(FSTAB % (self.other, self.at) + line + '\n')
                self.refused(self.mount(), '/etc/fstab names', 'This verb does not write a second way to mount it')
        self.m.fstab.unlink()                                                         # a machine without the file names nothing
        self.assertEqual(self.mount(), 0, self.m.text())

    def test_a_unit_that_is_there_and_is_not_this_one_is_refused(self):
        path = self.m.unit_dir / self.unit
        for text in ('[Mount]\nWhat=/dev/sdz1\nWhere=%s\n' % self.at, sm.unit_text('other-0001', self.at, 'ext4'), sm.unit_text(SPARE, self.at, 'xfs')):
            with self.subTest(text=text):
                path.write_text(text)
                self.keep()
                self.refused(self.mount(), 'a unit for that folder exists (%s) and it is not the one this verb would write' % self.unit)
        path.unlink()
        (self.tmp / 'same.mount').write_text(sm.unit_text(SPARE, self.at, 'ext4'))
        path.symlink_to(self.tmp / 'same.mount')                                      # a link, even to the same text, is not a unit this verb wrote
        self.keep()
        self.refused(self.mount(), 'a unit for that folder exists')

    def test_a_partition_with_a_unit_for_another_folder_is_refused(self):
        other = str(self.tmp / 'mnt' / 'first')
        (self.m.unit_dir / mount_unit_name(other)).write_text(sm.unit_text(SPARE, other, 'ext4'))
        self.keep()
        self.refused(self.mount(), 'it has a mount unit for %s already. First: sudo isle storage partition unmount %s' % (other, SPARE))

    def test_no_systemctl_is_a_refusal_before_anything_is_written(self):
        for result, why in ((FileNotFoundError(2, 'none'), 'systemctl is not on this machine'), ((1, ''), 'systemctl does not answer (exit 1)')):
            self.answers['--version'] = result
            self.refused(self.mount(), why)

    def test_a_partition_mounted_while_the_command_ran_is_refused(self):
        self.answers['--version'] = lambda: self.m.mount_rows.append(dict(target=str(self.tmp / 'by-hand'), fsroot='/', uuid=SPARE, options='rw')) or (0, '')
        self.assertEqual(self.mount(), 1)
        self.assertIn('REFUSED: partition mount: partition sdc2 was mounted while this command ran. Nothing was changed.', self.m.text())
        self.assertEqual(self.verbs(), [['--version']])
        self.assertEqual(sorted(p.name for p in self.m.unit_dir.iterdir()), [])

    def test_the_words_must_fit(self):
        for argv in (('partition', 'mount'), ('partition', 'mount', SPARE), ('partition', 'mount', SPARE, '--at'), ('partition', 'mount', SPARE, '--to', self.at),
                     ('partition', 'mount', SPARE, '--at', self.at, 'more'), ('partition', 'mount', SPARE, '--at', self.at, '--table'),
                     ('partition', 'unmount'), ('partition', 'unmount', SPARE, 'more'), ('partition', 'unmount', SPARE, '--with-datasets', '--with-datasets'),
                     ('partition', 'unmount', SPARE, '--table')):
            with self.subTest(argv=argv):
                self.assertEqual(self.m.call(*argv), 2, argv)
                self.assertIn('sudo isle storage partition mount <filesystem-identifier> --at <folder>', self.m.text())
                self.assertIn('sudo isle storage partition unmount <filesystem-identifier> [--with-datasets]', self.m.text())
        self.assertEqual(self.state(), self.before)


class Unmounting(Case):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.mount(), 0, self.m.text())
        self.keep()

    def test_without_root_it_prints_the_command(self):
        self.m.root_user = False
        self.assertEqual(self.unmount(), 1)
        self.assertEqual(self.m.said, ['This stops and removes the mount unit %s. Type: sudo isle storage partition unmount %s' % (self.unit, SPARE)])
        self.assertEqual(self.commands, [])
        self.assertEqual(self.state(), self.before)

    def test_it_stops_the_unit_and_removes_it_and_leaves_the_folder(self):
        self.assertEqual(self.unmount(), 0, self.m.text())
        self.assertEqual(self.verbs(), [['stop', self.unit], ['disable', self.unit], ['daemon-reload']])
        self.assertEqual(sorted(p.name for p in self.m.unit_dir.iterdir()), [])
        self.assertEqual((Path(self.at).is_dir(), list(Path(self.at).iterdir())), (True, []))
        self.assertEqual(self.m.said, [
            'The filesystem %s is no longer mounted at %s, and the unit %s is removed. No data was changed.' % (SPARE, self.at, self.unit),
            'The folder %s is left where it is.' % self.at])
        self.assertEqual(self.m.records.read_bytes(), self.before[0])
        self.keep()
        self.assertEqual(self.unmount(), 0)
        self.assertEqual(self.m.said, ['The filesystem %s has no mount unit of this verb and is not mounted. Nothing was changed.' % SPARE])
        self.assertEqual(self.commands, [])
        self.assertEqual(self.state(), self.before)

    def test_a_disk_that_is_away_can_have_its_unit_removed(self):
        self.m.disks[2]['children'].pop(1)
        self.m.mount_rows.pop()
        self.assertEqual(self.unmount(), 0, self.m.text())
        self.assertEqual(sorted(p.name for p in self.m.unit_dir.iterdir()), [])

    def test_a_mount_this_verb_did_not_make_is_left_alone(self):
        self.refused(self.unmount(identifier=DOCK), 'it is mounted (%s), and not by a unit of this verb: by hand or by /etc/fstab. This verb leaves such a mount alone'
                     % self.m.folders['dock'])
        self.refused(self.unmount(identifier='no'), 'that is not a filesystem identifier')

    def test_a_partition_that_holds_a_dataset_needs_the_option(self):
        self.m.bound.append(dict(dataset='images', filesystemUuid=SPARE))
        self.m.bound.append(dict(dataset='assets', filesystemUuid=SPARE))
        self.refused(self.unmount(), 'it holds: assets, images. What serves them is stopped when it is unmounted, and does not start until it is mounted at %s again. '
                     'To unmount it and let that stop: sudo isle storage partition unmount %s --with-datasets' % (self.at, SPARE))
        self.m.root_user = False
        self.assertEqual(self.unmount('--with-datasets'), 1)
        self.assertTrue(self.m.said[0].endswith('Type: sudo isle storage partition unmount %s --with-datasets' % SPARE), self.m.text())
        self.m.root_user = True
        self.assertEqual(self.unmount('--with-datasets'), 0, self.m.text())
        self.assertEqual(self.m.said[-1], 'Held on it: assets, images. What serves them does not start until it is mounted at %s again.' % self.at)
        self.assertEqual(sorted(p.name for p in self.m.unit_dir.iterdir()), [])

    def test_the_option_changes_nothing_for_a_partition_without_datasets(self):
        self.assertEqual(self.unmount('--with-datasets'), 0, self.m.text())
        self.assertEqual(len(self.m.said), 2)

    def test_a_stop_that_fails_keeps_the_unit(self):
        self.answers['stop'] = (1, 'target is busy\n')
        self.failed(self.unmount(), 'unmount', 'systemctl stop ended with exit 1 (target is busy). The unit %s is kept and the partition may still be mounted. '
                    'What depends on the mount may have been stopped.' % self.unit)
        self.assertEqual(self.verbs(), [['stop', self.unit]])
        self.assertEqual(self.state(), self.before)
        self.answers, self.lets_go = {}, False
        self.failed(self.unmount(), 'unmount', 'it still shows as mounted at %s. The unit %s is kept' % (self.at, self.unit))
        self.assertEqual(self.verbs(), [['stop', self.unit]])
        self.assertEqual(self.state(), self.before)

    def test_a_later_step_that_fails_says_where_it_stands(self):
        self.answers['disable'] = (1, '')
        self.failed(self.unmount(), 'unmount', 'systemctl disable ended with exit 1. The partition is not mounted at %s any more; the unit %s is kept. '
                    'To finish, type the same command.' % (self.at, self.unit))
        self.assertTrue((self.m.unit_dir / self.unit).exists())
        self.answers = {'daemon-reload': (1, '')}
        self.failed(self.unmount(), 'unmount', 'systemctl daemon-reload ended with exit 1. The partition is not mounted and the unit %s is removed; '
                    'systemd has not read that yet' % self.unit)
        self.assertEqual(sorted(p.name for p in self.m.unit_dir.iterdir()), [])

    def test_units_that_do_not_read_as_written_are_refused(self):
        path = self.m.unit_dir / self.unit
        other = self.m.unit_dir / mount_unit_name(str(self.tmp / 'mnt' / 'second'))
        other.write_text(sm.unit_text(SPARE, str(self.tmp / 'mnt' / 'second'), 'ext4'))
        self.keep()
        self.refused(self.unmount(), 'more than one mount unit names it (%s): remove the one that is not wanted by hand' % ', '.join(sorted([self.unit, other.name])))
        other.unlink()
        for text in (sm.unit_text(SPARE, self.at + '-moved', 'ext4'), sm.HEAD % SPARE + '\n[Mount]\n'):
            path.write_text(text)
            self.keep()
            self.refused(self.unmount(), 'the unit %s does not read as this verb wrote it' % self.unit)

    def test_only_a_plain_unit_file_with_this_verbs_first_line_counts(self):
        path = self.m.unit_dir / self.unit
        text = path.read_text()
        path.write_text('\n' + text)                                                  # the line is not the first one
        self.assertEqual(sm.written(self.m, SPARE), [])
        path.write_text(text.replace(SPARE, 'spare-00012', 1))                        # another filesystem whose identifier begins the same
        self.assertEqual(sm.written(self.m, SPARE), [])
        path.unlink()
        (self.tmp / 'real.mount').write_text(text)
        path.symlink_to(self.tmp / 'real.mount')                                      # a link is never followed
        self.assertEqual(sm.written(self.m, SPARE), [])
        path.unlink()
        (self.m.unit_dir / (self.unit[:-len('.mount')] + '.service')).write_text(text)
        self.assertEqual(sm.written(self.m, SPARE), [])
        path.mkdir()
        self.assertEqual(sm.written(self.m, SPARE), [])
        self.m.unit_dir = self.tmp / 'no-such-folder'
        self.assertEqual(sm.written(self.m, SPARE), [])


if __name__ == '__main__':
    unittest.main()
