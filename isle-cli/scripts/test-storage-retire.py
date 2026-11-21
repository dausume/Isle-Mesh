"""isle storage partition retire | device retire.

Fixtures only, the made-up machine of test-storage-roles.py: every "partition" is a temporary folder and the record
is a temporary file.  No real disk, no real record and no network is touched.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import storage_retire
import storage_roles as sr

_spec = importlib.util.spec_from_file_location('storage_roles_fixtures', HERE / 'test-storage-roles.py')
fx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fx)
ARCH, DOCK = fx.ARCH, fx.DOCK


class Case(fx.Case):
    def setUp(self):
        super().setUp()
        self.m.ready()

    def roles(self, *roles, identifier=ARCH):
        self.assertEqual(self.m.call('partition', 'assign', identifier, '--role', ','.join(roles)), 0, self.m.text())

    def retire(self, roles, *options, identifier=ARCH):
        return self.m.call('partition', 'retire', identifier, '--role', roles, *options)

    def state(self):
        return {str(p.relative_to(self.m.tmp)): p.read_bytes() for p in self.m.tmp.rglob('*') if p.is_file() and not p.name.endswith('.lock')}

    def unchanged(self, before):
        self.assertEqual(self.state(), before)


class OneRoleOfSeveral(Case):
    def test_the_marker_and_the_record_lose_it_and_keep_the_others(self):
        self.roles('git', 'backup')
        self.assertEqual(self.retire('git'), 0, self.m.text())
        self.assertIn('Partition sdb1 (%s) no longer carries the role git. No data was moved or removed.' % ARCH, self.m.text())
        self.assertIn('still records it with the roles package-repo, backup', self.m.text())
        marker = json.loads(self.m.marker().read_text())
        self.assertEqual((marker['role'], marker['roles']), ('package-repo', ['package-repo', 'backup']))
        self.assertEqual((marker['assignedOn'], marker['changedOn']), ('test-machine', 'test-machine'))
        self.assertEqual(self.m.record()['partitions'][ARCH], dict(role='package-repo', roles=['package-repo', 'backup'], deviceSerial='S2',
                                                                    assignedAt='2026-01-01T00:00:00+00:00'))
        self.assertEqual(self.m.call('partition', 'check'), 0, self.m.text())
        self.assertEqual((self.m.folders['archive'] / 'kept.txt').read_text(), 'data that must stay\n')

    def test_down_to_one_role_it_is_written_as_one(self):
        self.roles('git')
        self.assertEqual(self.retire('package-repo'), 0, self.m.text())
        self.assertEqual(self.m.record()['partitions'][ARCH], dict(role='git', deviceSerial='S2', assignedAt='2026-01-01T00:00:00+00:00'))
        self.assertNotIn('roles', json.loads(self.m.marker().read_text()))
        self.assertEqual(self.m.call('partition', 'check'), 0)

    def test_several_at_once_and_one_it_never_carried(self):
        self.roles('git', 'blob')
        self.assertEqual(self.retire('git,scratch,blob'), 0, self.m.text())
        self.assertIn('no longer carries the roles git, blob', self.m.text())
        self.assertIn('It did not carry the role scratch.', self.m.text())
        self.assertEqual(sr.roles_of(self.m.record()['partitions'][ARCH]), ['package-repo'])

    def test_a_role_it_does_not_carry_changes_nothing(self):
        before = self.state()
        self.assertEqual(self.retire('git'), 0)
        self.assertIn('does not carry the role git. Nothing was changed.', self.m.text())
        self.unchanged(before)


class TheLastRole(Case):
    def test_data_on_it_stops_it_until_it_is_said_to_stay(self):
        before = self.state()
        self.refused(self.retire('package-repo'), 'would carry no role', 'apt-public-repo',
                     'sudo isle storage partition retire %s --role package-repo --leave-data' % ARCH)
        self.unchanged(before)
        self.assertEqual(self.retire('package-repo', '--leave-data'), 0, self.m.text())
        self.assertIn('This machine no longer records it.', self.m.text())
        self.assertIn('Its marker was removed.', self.m.text())
        self.assertIn('Still on it: apt-public-repo.', self.m.text())
        self.assertFalse(self.m.marker().exists())
        self.assertEqual(self.m.record()['partitions'], {})
        self.assertEqual(sorted(self.m.record()['devices']), ['S2', 'S3'])                # the device stays registered
        self.assertEqual(sorted(p.name for p in self.m.folders['archive'].iterdir()), ['kept.txt'])
        self.assertEqual(self.m.call('partition', 'check', '--table'), 0)
        self.assertIn('No partition of this machine carries a role yet.', self.m.text())

    def test_with_nothing_bound_on_it_no_option_is_needed(self):
        self.roles('assets', identifier=DOCK)
        self.assertEqual(self.retire('assets', identifier=DOCK), 0, self.m.text())
        self.assertFalse(self.m.marker('dock').exists())
        self.assertNotIn(DOCK, self.m.record()['partitions'])

    def test_it_can_be_assigned_again_afterwards(self):
        self.assertEqual(self.retire('package-repo', '--leave-data'), 0)
        self.roles('git')
        self.assertEqual(self.m.call('partition', 'check'), 0)


class DeviceBackups(Case):
    def setUp(self):
        super().setUp()
        self.roles('backup')
        self.kept = self.m.folders['archive'] / storage_retire.BACKUPS / 'release'
        self.kept.mkdir(parents=True)
        (self.kept / 'item.tar').write_bytes(b'a device-backup')

    def test_the_role_backup_is_not_taken_from_under_device_backups(self):
        before = self.state()
        self.refused(self.retire('backup'), 'device-backups of this machine are kept on it', 'no longer lists these', '--role backup --leave-data')
        self.unchanged(before)

    def test_said_to_stay_they_stay_and_the_output_says_where_new_ones_go(self):
        self.assertEqual(self.retire('backup', '--leave-data'), 0, self.m.text())
        self.assertIn('The device-backups stay in %s.' % self.kept.parent, self.m.text())
        self.assertIn('state folder of this machine', self.m.text())
        self.assertEqual((self.kept / 'item.tar').read_bytes(), b'a device-backup')
        self.assertEqual(sr.roles_of(self.m.record()['partitions'][ARCH]), ['package-repo'])

    def test_another_role_of_the_same_partition_is_not_held_by_them(self):
        self.roles('git')
        self.assertEqual(self.retire('git'), 0, self.m.text())

    def test_an_empty_folder_or_none_holds_nothing(self):
        (self.kept / 'item.tar').unlink(); self.kept.rmdir()
        self.assertIsNone(storage_retire.kept_backups(str(self.m.folders['archive'])))
        self.assertEqual(self.retire('backup'), 0, self.m.text())
        self.assertIsNone(storage_retire.kept_backups(str(self.m.folders['dock'])))

    def test_a_folder_that_cannot_be_listed_counts_as_holding_some(self):
        def no(self): raise PermissionError(13, 'Permission denied')
        real, storage_retire.Path.iterdir = storage_retire.Path.iterdir, no
        try: self.assertEqual(storage_retire.kept_backups('/somewhere'), '/somewhere/' + storage_retire.BACKUPS)
        finally: storage_retire.Path.iterdir = real


class MarkerAndRecordApart(Case):
    def theirs(self, **more):
        marker = dict(kind=sr.MARKER_KIND, version=1, filesystemUuid=DOCK, role='assets', deviceSerial='S3',
                      assignedAt='2025-05-05T00:00:00+00:00', assignedOn='another-machine', **more)
        self.m.marker('dock').write_text(json.dumps(marker))

    def test_a_role_only_the_marker_names_is_taken_from_the_marker(self):
        self.theirs()
        record = self.m.records.read_bytes()
        self.assertEqual(self.retire('assets', identifier=DOCK), 0, self.m.text())
        self.assertFalse(self.m.marker('dock').exists())
        self.assertEqual(self.m.records.read_bytes(), record)
        self.assertNotIn('no longer records', self.m.text())

    def test_a_role_that_is_not_typed_is_left_in_the_marker(self):
        self.theirs(roles=['assets', 'scratch'])
        self.roles('assets', 'scratch', 'git', identifier=DOCK)
        self.m.marker('dock').write_text(json.dumps(dict(json.loads(self.m.marker('dock').read_text()), roles=['assets', 'scratch', 'git', 'blob'])))
        self.assertEqual(self.retire('assets,scratch,git', identifier=DOCK), 0, self.m.text())
        self.assertEqual(sr.roles_of(json.loads(self.m.marker('dock').read_text())), ['blob'])
        self.assertNotIn(DOCK, self.m.record()['partitions'])
        self.assertIn('Its marker names the role blob and this machine records no role for it.', self.m.text())

    def test_a_role_only_the_record_names_leaves_the_marker_as_it_is(self):
        self.roles('git')
        marker = json.loads(self.m.marker().read_text())
        self.m.marker().write_text(json.dumps(sr.with_roles(marker, ['package-repo'])))
        raw = self.m.marker().read_bytes()
        self.assertEqual(self.retire('git'), 0, self.m.text())
        self.assertEqual(self.m.marker().read_bytes(), raw)
        self.assertEqual(sr.roles_of(self.m.record()['partitions'][ARCH]), ['package-repo'])
        self.assertEqual(self.m.call('partition', 'check'), 0)

    def test_a_missing_marker_is_said_when_the_record_keeps_a_role(self):
        self.roles('git')
        self.m.marker().unlink()
        self.assertEqual(self.retire('git'), 0, self.m.text())
        self.assertIn('Its marker is gone and this machine records it with the role package-repo.', self.m.text())
        self.assertFalse(self.m.marker().exists())


class TheMarkerOutOfReach(Case):
    def gone(self):
        self.m.disks = [d for d in self.m.disks if d['name'] != 'sdb']
        self.m.mount_rows = [r for r in self.m.mount_rows if r['uuid'] != ARCH]
        self.m.bound = []

    def test_a_partition_that_is_away_needs_record_only(self):
        self.gone()
        before = self.state()
        self.refused(self.retire('package-repo'), 'the partition (%s) cannot have its marker changed: it is not present on this machine' % ARCH,
                     'sudo isle storage partition retire %s --role package-repo --record-only' % ARCH)
        self.unchanged(before)
        marker = self.m.marker().read_bytes()
        self.assertEqual(self.retire('package-repo', '--record-only'), 0, self.m.text())
        self.assertIn('Only the record of this machine was changed', self.m.text())
        self.assertEqual(self.m.record()['partitions'], {})
        self.assertEqual(self.m.marker().read_bytes(), marker)

    def test_not_mounted_read_only_unreadable_and_a_copy_are_each_named(self):
        self.roles('git')
        rows = list(self.m.mount_rows)
        self.m.mount_rows = [r for r in rows if r['uuid'] != ARCH]
        self.refused(self.retire('git'), 'it is not mounted', '--record-only')
        self.m.mount_rows = [dict(r, options='ro,relatime') if r['uuid'] == ARCH else r for r in rows]
        self.refused(self.retire('git'), 'is mounted read-only', '--record-only')
        self.m.mount_rows = rows
        good = self.m.marker().read_bytes()
        self.m.marker().write_text('{not a marker')
        self.refused(self.retire('git'), 'is not a partition marker this CLI understands', '--record-only')
        self.m.marker().write_text(json.dumps(dict(json.loads(good), filesystemUuid='other-0001')))
        self.refused(self.retire('git'), 'names another filesystem (other-0001)', '--record-only')
        raw = self.m.marker().read_bytes()
        self.assertEqual(self.retire('git', '--record-only'), 0, self.m.text())
        self.assertEqual(self.m.marker().read_bytes(), raw)
        self.assertEqual(sr.roles_of(self.m.record()['partitions'][ARCH]), ['package-repo'])

    def test_record_only_never_touches_the_marker_of_a_partition_that_is_there(self):
        self.roles('git')
        raw = self.m.marker().read_bytes()
        self.assertEqual(self.retire('git', '--record-only'), 0, self.m.text())
        self.assertEqual(self.m.marker().read_bytes(), raw)
        self.assertEqual(self.m.call('partition', 'check'), 1)              # the two no longer agree, and check says so
        self.assertEqual(self.retire('scratch', '--record-only'), 0)
        self.assertIn('does not carry the role scratch in the record of this machine. Nothing was changed.', self.m.text())

    def test_the_options_asked_for_are_kept_in_the_command_to_type(self):
        self.gone()
        self.refused(self.retire('package-repo', '--leave-data'), '--role package-repo --record-only --leave-data')


class Refusals(Case):
    def test_each_writes_nothing(self):
        self.roles('git')
        before = self.state()
        for argv, words in ((('nope-0001', 'git'), 'neither records nor sees'), ((ARCH, 'cache'), 'the role must be one of'),
                            ((ARCH, 'git,git'), 'the role must be one of'), (('x', 'git'), 'not a filesystem identifier')):
            self.refused(self.m.call('partition', 'retire', argv[0], '--role', argv[1]), words)
            self.unchanged(before)
        for argv in (('partition', 'retire'), ('partition', 'retire', ARCH), ('partition', 'retire', ARCH, 'git'),
                     ('partition', 'retire', ARCH, '--roles', 'git'), ('partition', 'retire', ARCH, '--role', 'git', '--force'),
                     ('partition', 'retire', ARCH, '--role', 'git', '--leave-data', '--leave-data'),
                     ('partition', 'retire', ARCH, '--role', 'git', '--table'), ('device', 'retire'), ('device', 'retire', 'S3', '--record-only'),
                     ('device', 'retire', 'S3', '--table'), ('device', 'retire', 'S2', 'S3')):
            self.assertEqual(self.m.call(*argv), 2, argv)
            self.assertIn('partition retire <filesystem-identifier> --role', self.m.text())
            self.assertIn('sudo isle storage device retire <serial>', self.m.text())
            self.unchanged(before)

    def test_without_root_it_names_the_command_and_writes_nothing(self):
        self.roles('git')
        before = self.state()
        self.m.root_user = False
        self.assertEqual(self.retire('git'), 1)
        self.assertEqual(self.m.text(), 'This changes the storage records of this machine. Type: sudo isle storage partition retire %s --role git' % ARCH)
        self.assertEqual(self.retire('package-repo,git', '--leave-data'), 1)
        self.assertIn('--role package-repo,git --leave-data', self.m.text())
        self.assertEqual(self.m.call('device', 'retire', 'S3'), 1)
        self.assertEqual(self.m.text(), 'This changes the storage records of this machine. Type: sudo isle storage device retire S3')
        self.unchanged(before)

    def test_a_marker_or_a_record_that_changed_meanwhile_stops_it(self):
        self.roles('git')
        real = self.m.mounts

        def then(change):
            calls = []
            def mounts():
                calls.append(1)
                if len(calls) == 2: change()
                return real()
            self.m.mounts = mounts
        marker = json.loads(self.m.marker().read_text())
        then(lambda: self.m.marker().write_text(json.dumps(sr.with_roles(marker, ['package-repo', 'git', 'blob']))))
        record = self.m.records.read_bytes()
        self.refused(self.retire('git'), 'changed while this command ran')
        self.assertEqual(self.m.records.read_bytes(), record)
        self.assertEqual(sr.roles_of(json.loads(self.m.marker().read_text())), ['package-repo', 'git', 'blob'])
        self.m.mounts = real
        self.roles('git', 'blob')
        self.m.unmounted.add(str(self.m.folders['archive']))
        self.refused(self.retire('git'), 'is no longer mounted at')
        self.m.unmounted.clear()
        real_survey = sr.survey

        def survey(io):
            out = real_survey(io)
            doc = json.loads(self.m.records.read_text())
            doc['partitions'][ARCH] = sr.with_roles(doc['partitions'][ARCH], ['package-repo'])
            self.m.records.write_text(json.dumps(doc))
            return out
        sr.survey = survey
        try: self.refused(self.retire('git'), 'this machine now records it with the role package-repo')
        finally: sr.survey = real_survey
        self.assertEqual(sr.roles_of(json.loads(self.m.marker().read_text())), ['package-repo', 'git', 'blob'])

    def test_a_link_in_place_of_the_marker_is_never_followed(self):
        self.roles('git')
        outside = self.m.tmp / 'outside.json'
        outside.write_bytes(self.m.marker().read_bytes())
        self.m.marker().unlink()
        os.symlink(outside, self.m.marker())
        raw = outside.read_bytes()
        self.refused(self.retire('git'), 'not a plain file')
        self.assertEqual(outside.read_bytes(), raw)
        self.assertTrue(self.m.marker().is_symlink())

    def test_a_damaged_record_stops_both_verbs(self):
        self.m.records.write_text('{')
        self.refused(self.retire('package-repo'), 'are damaged')
        self.refused(self.m.call('device', 'retire', 'S2'), 'are damaged')
        self.assertEqual(self.m.records.read_text(), '{')


class Device(Case):
    def test_a_device_with_no_partition_recorded_on_it_is_taken_out(self):
        self.assertEqual(self.m.call('device', 'retire', 'S3'), 0, self.m.text())
        self.assertEqual(self.m.text(), 'Device S3 (Dock, 2.0 MB, dock) is no longer registered on this machine. Nothing on the disk was changed.')
        self.assertEqual(sorted(self.m.record()['devices']), ['S2'])
        self.assertEqual(sr.roles_of(self.m.record()['partitions'][ARCH]), ['package-repo'])

    def test_one_that_carries_recorded_roles_is_refused_with_the_commands(self):
        self.roles('git')
        before = self.state()
        self.refused(self.m.call('device', 'retire', 'S2'), 'records 1 partition with a role on it',
                     'sudo isle storage partition retire %s --role package-repo,git' % ARCH)
        self.m.root_user = False                 # the reason comes before "type it with sudo"
        self.refused(self.m.call('device', 'retire', 'S2'), 'records 1 partition with a role on it')
        self.m.root_user = True
        self.unchanged(before)
        self.assertEqual(self.retire('package-repo,git', '--leave-data'), 0, self.m.text())
        self.assertEqual(self.m.call('device', 'retire', 'S2'), 0, self.m.text())
        self.assertEqual(sorted(self.m.record()['devices']), ['S3'])

    def test_a_disk_that_is_gone_for_good_can_be_taken_out(self):
        self.m.disks = [d for d in self.m.disks if d['name'] != 'sdc']
        self.assertEqual(self.m.call('device', 'retire', 'S3'), 0, self.m.text())
        self.assertNotIn('S3', self.m.record()['devices'])

    def test_one_that_is_not_registered_changes_nothing(self):
        before = self.state()
        self.assertEqual(self.m.call('device', 'retire', 'S9'), 0)
        self.assertIn('Device S9 is not registered on this machine. Nothing was changed.', self.m.text())
        self.refused(self.m.call('device', 'retire', ''), 'not a hardware serial')
        self.unchanged(before)

    def test_a_partition_recorded_on_it_meanwhile_stops_it(self):
        real = sr.locked

        def locked(io):
            doc = json.loads(self.m.records.read_text())
            doc['partitions'][DOCK] = dict(role='assets', deviceSerial='S3', assignedAt='x')
            self.m.records.write_text(json.dumps(doc))
            return real(io)
        sr.locked = locked
        try: self.refused(self.m.call('device', 'retire', 'S3'), 'sudo isle storage partition retire %s --role assets' % DOCK)
        finally: sr.locked = real
        self.assertIn('S3', self.m.record()['devices'])


if __name__ == '__main__':
    unittest.main()
