"""isle storage slot list | check | declare | bind | unbind.

Fixtures only, the made-up machine of test-storage-roles.py: every "partition" is a temporary folder, the apps folder
and the record are temporary too, and a change of owner is only written down.  No real disk, no real app, no real
record and no network is touched.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import storage_kinds
import storage_roles as sr
import storage_slots as ss

_spec = importlib.util.spec_from_file_location('storage_roles_fixtures', HERE / 'test-storage-roles.py')
fx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fx)
ARCH, DOCK, SPARE, ENTRY = fx.ARCH, fx.DOCK, fx.SPARE, fx.ENTRY

PROJECTS = dict(name='projects', role='assets', target='/home/app/projects', writable=True, owner='1000:1001', sizeBytes=300)
LIBRARY = dict(name='library', role='assets', target='/usr/share/app/library')
FULL = dict(name='projects', role='assets', kind='file-pool', target='/home/app/projects', service=None, writable=True, owner='1000:1001',
            distribution='pooled', association='app', copies=1, lifetime='until-removed', sizeBytes=300)


class Case(fx.Case):
    """The made-up machine with the dock partition carrying `assets` and `scratch`, and the app `kicad` declaring two slots."""

    def setUp(self):
        super().setUp()
        self.m.ready()
        self.assertEqual(self.m.call('partition', 'assign', DOCK, '--role', 'assets,scratch'), 0, self.m.text())
        self.dock = self.m.folders['dock']
        self.folder = self.dock / 'isle-slots' / 'kicad' / 'projects'
        self.release = self.m.tmp / 'release.json'
        self.declare()

    def declare(self, *slots, app='kicad', code=0):
        self.release.write_text(json.dumps(dict(schemaVersion=1, name=app, storageSlots=list(slots or (PROJECTS, LIBRARY)))))
        self.assertEqual(self.m.call('slot', 'declare', app, '--from', str(self.release)), code, self.m.text())

    def bind(self, slot='projects', identifier=DOCK, app='kicad'):
        return self.m.call('slot', 'bind', app, slot, '--partition', identifier)

    def rows(self, *argv):
        code = self.m.call('slot', *(argv or ('list',)))
        return code, json.loads(self.m.said[0])

    def state(self, slot='projects'):
        return [r['state'] for r in self.rows()[1]['result']['slots'] if r['slot'] == slot][0]

    def everything(self):
        return self.m.records.read_bytes(), self.m.listing(), list(self.m.chowned)

    def edit_record(self, change):
        doc = self.m.record()
        change(doc)
        self.m.records.write_text(json.dumps(doc))


class Declared(unittest.TestCase):
    def test_every_field_is_filled_in(self):
        self.assertEqual(ss.declared([PROJECTS]), [FULL])
        self.assertEqual(ss.declared([dict(name='cache', role='scratch', target='/var/cache/app/')]), [dict(
            name='cache', role='scratch', kind='plain-folder', target='/var/cache/app', service=None, writable=False, owner=None,
            distribution='kept', association='machine', copies=1, lifetime='until-removed', sizeBytes=None)])
        full = dict(PROJECTS, service='web_1', distribution='taken', association='isle', copies=2, lifetime='scratch')
        self.assertEqual(ss.declared([full]), [dict(FULL, service='web_1', distribution='taken', association='isle', copies=2, lifetime='scratch')])
        self.assertEqual(ss.declared([dict(PROJECTS, service=None, owner=None)])[0]['owner'], None)       # a null is a field left out
        self.assertEqual(ss.declared([]), [])

    def test_the_defaults_come_from_what_the_role_allows(self):
        for role in sr.ROLES:
            slot = ss.declared([dict(name='a', role=role, target='/data')])[0]
            allows = storage_kinds.of(role)
            self.assertEqual(slot['kind'], allows['kind'])
            self.assertEqual(slot['distribution'], allows['distribution'][0])
            self.assertEqual(slot['association'], 'app' if 'app' in allows['association'] else allows['association'][0])

    def test_what_is_wrong_is_said(self):
        for bad, word in (
                (dict(PROJECTS), 'are a list'), ('projects', 'are a list'), (None, 'are a list'),
                (['projects'], 'has only'), ([dict(PROJECTS, mode='0777')], 'has only'),
                ([dict(PROJECTS, name='Projects')], 'the name'), ([dict(PROJECTS, name='-a')], 'the name'), ([dict(PROJECTS, name='a' * 65)], 'the name'),
                ([dict(PROJECTS, name='a/b')], 'the name'), ([dict(PROJECTS, name=None)], 'the name'), ([dict(PROJECTS, name='a\n')], 'the name'),
                ([dict(PROJECTS, role='attic')], 'needs a role'), ([dict(name='a', target='/data')], 'needs a role'), ([dict(PROJECTS, role=['assets'])], 'needs a role'),
                ([dict(name='a', role='assets')], 'needs a target'), ([dict(PROJECTS, target='data')], 'needs a target'),
                ([dict(PROJECTS, target='/')], 'needs a target'), ([dict(PROJECTS, target='/a/../b')], 'needs a target'),
                ([dict(PROJECTS, target='/a//b')], 'needs a target'), ([dict(PROJECTS, target='/a/./b')], 'needs a target'),
                ([dict(PROJECTS, target='/a b')], 'needs a target'), ([dict(PROJECTS, target=7)], 'needs a target'),
                ([dict(PROJECTS, service='a b')], 'a service'), ([dict(PROJECTS, service='')], 'a service'), ([dict(PROJECTS, service=3)], 'a service'),
                ([dict(PROJECTS, writable='yes')], 'true or false'), ([dict(PROJECTS, writable=1)], 'true or false'),
                ([dict(PROJECTS, owner='root')], 'two numbers'), ([dict(PROJECTS, owner='01:1')], 'two numbers'), ([dict(PROJECTS, owner=1000)], 'two numbers'),
                ([dict(PROJECTS, owner='1:1\n')], 'two numbers'),
                ([dict(PROJECTS, distribution='served')], 'allows the distribution: pooled, cached, taken'),
                ([dict(PROJECTS, association='machine')], 'allows the association: app, isle'),
                ([dict(PROJECTS, copies=3)], '1 or 2'), ([dict(PROJECTS, copies=0)], '1 or 2'), ([dict(PROJECTS, copies=True)], '1 or 2'), ([dict(PROJECTS, copies='2')], '1 or 2'),
                ([dict(PROJECTS, lifetime='forever')], 'lifetime is one of'), ([dict(PROJECTS, lifetime=['release'])], 'lifetime is one of'),
                ([dict(PROJECTS, sizeBytes=0)], 'above 0'), ([dict(PROJECTS, sizeBytes=1.5)], 'above 0'), ([dict(PROJECTS, sizeBytes=True)], 'above 0'),
                ([PROJECTS, dict(LIBRARY, name='projects')], 'two storage slots have the name'),
                ([PROJECTS, dict(LIBRARY, target='/home/app/projects/')], 'two storage slots have the target')):
            with self.assertRaises(ValueError, msg=bad) as caught: ss.declared(bad)
            self.assertIn(word, str(caught.exception), bad)

    def test_a_null_is_a_field_left_out(self):
        nulls = dict(PROJECTS, service=None, writable=None, owner=None, distribution=None, association=None, copies=None, lifetime=None, sizeBytes=None)
        self.assertEqual(ss.declared([nulls]), [dict(FULL, writable=False, owner=None, sizeBytes=None)])

    def test_the_same_target_in_two_services_is_two_slots(self):
        both = ss.declared([dict(PROJECTS, service='a'), dict(LIBRARY, target=PROJECTS['target'], service='b')])
        self.assertEqual([s['service'] for s in both], ['a', 'b'])

    def test_the_lifetimes_and_the_copies(self):
        self.assertEqual(list(ss.LIFETIMES), ['until-removed', 'release', 'scratch'])
        self.assertEqual(ss.COPIES, (1, 2))


class Declare(Case):
    def test_the_declaration_is_kept_with_the_app(self):
        path = self.m.apps_dir / 'kicad' / 'storage-slots.json'
        self.assertEqual(json.loads(path.read_text()), dict(kind='isle.storage.slots', version=1, app='kicad', slots=[PROJECTS, LIBRARY]))
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)
        self.assertIn('The app kicad declares 2 storage slots: projects (role assets), library (role assets).', self.m.said)
        self.assertEqual(self.m.said[-1], 'Nothing was bound, made or started. See: isle storage slot list --table')
        self.assertNotIn('slots', self.m.record())
        self.assertFalse((self.dock / 'isle-slots').exists())
        self.assertEqual(ss.read_declaration(self.m, 'kicad'), (ss.declared([PROJECTS, LIBRARY]), None))
        self.assertEqual(ss.apps(self.m), ['kicad'])

    def test_the_same_again_changes_nothing(self):
        before = self.everything()
        os.utime(self.m.apps_dir / 'kicad' / 'storage-slots.json', (1, 1))
        self.m.root_user = False                                                      # and it needs no root to say so
        self.declare()
        self.assertEqual(self.m.said, ['The app kicad already declares these storage slots. Nothing was changed.'])
        self.assertEqual(self.everything(), before)
        self.assertEqual(os.stat(self.m.apps_dir / 'kicad' / 'storage-slots.json').st_mtime, 1)

    def test_one_slot_and_none(self):
        self.declare(LIBRARY, app='viewer')
        self.assertIn('The app viewer declares 1 storage slot: library (role assets).', self.m.said)
        self.release.write_text(json.dumps(dict(storageSlots=[])))
        self.assertEqual(self.m.call('slot', 'declare', 'viewer', '--from', str(self.release)), 0)
        self.assertIn('The app viewer declares 0 storage slots: none.', self.m.said)
        self.assertEqual(ss.read_declaration(self.m, 'viewer'), ([], None))

    def test_without_root_it_says_what_to_type(self):
        self.m.root_user = False
        before = self.everything()
        self.declare(LIBRARY, code=1)
        self.assertEqual(self.m.said, ['This writes what the app kicad declares (%s). Type: sudo isle storage slot declare kicad --from %s' % (
            self.m.apps_dir / 'kicad' / 'storage-slots.json', self.release)])
        self.assertEqual(self.everything(), before)

    def test_what_cannot_be_declared(self):
        before = self.everything()
        self.refused(self.m.call('slot', 'declare', 'Kicad', '--from', str(self.release)), 'not the name of an app')
        self.refused(self.m.call('slot', 'declare', 'a/b', '--from', str(self.release)), 'not the name of an app')
        self.refused(self.m.call('slot', 'declare', 'kicad', '--from', str(self.m.tmp / 'none.json')), 'cannot be read')
        self.release.write_text('{not json')
        self.refused(self.m.call('slot', 'declare', 'kicad', '--from', str(self.release)), 'is not JSON')
        for doc in ([], dict(name='kicad'), dict(slots=[PROJECTS]), dict(storage=[PROJECTS]), 'the storage of it'):   # `storage` is another thing: the dataset bind of a hosting app
            self.release.write_text(json.dumps(doc))
            self.refused(self.m.call('slot', 'declare', 'kicad', '--from', str(self.release)), 'has no list `storageSlots`')
        self.release.write_text(json.dumps(dict(storageSlots=[dict(PROJECTS, role='attic')])))
        self.refused(self.m.call('slot', 'declare', 'kicad', '--from', str(self.release)), 'needs a role')
        self.m.root_user = False                                                     # what is wrong is said before the word about sudo
        self.refused(self.m.call('slot', 'declare', 'kicad', '--from', str(self.release)), 'needs a role')
        self.assertEqual(self.everything(), before)

    def test_a_bound_slot_is_not_dropped_and_does_not_change_its_role(self):
        self.assertEqual(self.bind(), 0, self.m.text())
        before = self.everything()
        self.declare(LIBRARY, code=1)
        self.refused(1, 'its slot projects is bound to the partition dock-0001, and this release does not declare it',
                     'First: sudo isle storage slot unbind kicad projects')
        self.declare(dict(PROJECTS, role='scratch'), LIBRARY, code=1)
        self.refused(1, 'bound with the role assets, and this release declares it with the role scratch', 'First: sudo isle storage slot unbind kicad projects')
        self.assertEqual(self.everything(), before)
        self.declare(dict(PROJECTS, target='/srv/projects'), LIBRARY)                # anything else of a bound slot may change
        self.assertEqual(ss.read_declaration(self.m, 'kicad')[0][0]['target'], '/srv/projects')
        self.declare(LIBRARY, app='viewer')                                           # and another app's slots are its own
        self.assertEqual(self.m.call('slot', 'unbind', 'kicad', 'projects'), 0)
        self.declare(LIBRARY)

    def test_a_link_is_not_written_through(self):
        path = self.m.apps_dir / 'kicad' / 'storage-slots.json'
        elsewhere = self.m.tmp / 'elsewhere.json'
        elsewhere.write_text('kept\n')
        path.unlink(); path.symlink_to(elsewhere)
        self.declare(LIBRARY, code=1)
        self.refused(1, 'is a link')
        self.assertEqual(elsewhere.read_text(), 'kept\n')

    def test_a_declaration_that_cannot_be_read(self):
        path = self.m.apps_dir / 'kicad' / 'storage-slots.json'
        good = json.loads(path.read_text())
        for doc in ('{not json', json.dumps([]), json.dumps(dict(good, version=2)), json.dumps(dict(good, app='other')),
                    json.dumps(dict(good, kind='other')), json.dumps(dict(good, slots=[dict(PROJECTS, role='attic')])),
                    json.dumps({k: v for k, v in good.items() if k != 'slots'})):
            path.write_text(doc)
            slots, problem = ss.read_declaration(self.m, 'kicad')
            self.assertIsNone(slots, doc)
            self.assertIn('is not one this CLI understands', problem)
        path.unlink(); path.mkdir()
        self.assertIn('cannot be read', ss.read_declaration(self.m, 'kicad')[1])
        self.assertEqual(ss.read_declaration(self.m, 'no-such-app'), ([], None))

    def test_only_apps_with_a_declaration_are_looked_at(self):
        (self.m.apps_dir / 'plain').mkdir()
        (self.m.apps_dir / 'Not-An-App').mkdir()
        (self.m.apps_dir / 'Not-An-App' / 'storage-slots.json').write_text('{}')
        (self.m.apps_dir / 'a-file').write_text('x')
        self.assertEqual(ss.apps(self.m), ['kicad'])
        shutil.rmtree(self.m.apps_dir)
        self.assertEqual(ss.apps(self.m), [])


class Bind(Case):
    def test_bind_makes_the_folder_and_records_the_binding(self):
        partitions = self.m.record()['partitions']
        self.assertEqual(self.bind(), 0, self.m.text())
        self.assertEqual(self.m.said, [
            'Slot projects of kicad is bound to partition sdc1 (dock-0001), under its role assets.',
            'Its data is the folder %s, made now and owned by 1000:1001.' % self.folder,
            'The app sees it at /home/app/projects, and writes to it.',
            'Nothing was started or stopped.'])
        self.assertTrue(self.folder.is_dir())
        self.assertEqual([os.stat(p).st_mode & 0o777 for p in (self.folder, self.folder.parent, self.folder.parent.parent)], [0o755] * 3)
        self.assertEqual(self.m.chowned, [(1000, 1001)])
        record = self.m.record()
        self.assertEqual(record['slots'], {'kicad/projects': dict(role='assets', filesystemUuid=DOCK, boundAt='2026-01-01T00:00:00+00:00')})
        self.assertEqual(record['partitions'], partitions)                            # the roles are not touched
        self.assertEqual(sorted(record), ['devices', 'partitions', 'slots', 'version'])
        self.assertEqual(self.state(), 'ok')
        self.assertEqual(self.m.call('partition', 'check'), 0, self.m.text())         # a record with slots is still a good record

    def test_a_slot_the_app_only_reads_in_a_service_with_two_copies(self):
        self.declare(dict(LIBRARY, service='viewer', copies=2))
        self.assertEqual(self.bind('library'), 0, self.m.text())
        self.assertEqual(self.m.said[1:], [
            'Its data is the folder %s, made now.' % (self.folder.parent / 'library'),
            'The app sees it at /usr/share/app/library in its service viewer, and only reads it.',
            'The release asks for 2 copies. One is bound here; nothing keeps a second copy yet.',
            'Nothing was started or stopped.'])
        self.assertEqual(self.m.chowned, [])

    def test_a_folder_that_is_there_is_kept_as_it_is(self):
        self.folder.mkdir(parents=True)
        self.folder.chmod(0o700)
        (self.folder / 'board.kicad_pcb').write_text('a board\n')
        self.assertEqual(self.bind(), 0, self.m.text())
        self.assertEqual(self.m.said[1], 'Its data is the folder %s. It was there already: what is in it and its owner were kept.' % self.folder)
        self.assertEqual((self.folder / 'board.kicad_pcb').read_text(), 'a board\n')
        self.assertEqual(os.stat(self.folder).st_mode & 0o777, 0o700)
        self.assertEqual(self.m.chowned, [])

    def test_the_same_again_changes_nothing(self):
        self.assertEqual(self.bind(), 0)
        self.m.root_user = False
        before = self.everything()
        self.assertEqual(self.bind(), 0)
        self.assertEqual(self.m.said, ['Slot projects of kicad is bound to partition sdc1 (dock-0001) already. Nothing was changed.'])
        self.assertEqual(self.everything(), before)

    def test_a_bound_slot_whose_folder_is_gone_is_made_again_and_keeps_its_date(self):
        self.assertEqual(self.bind(), 0)
        shutil.rmtree(self.dock / 'isle-slots')
        self.assertEqual(self.state(), 'folder missing')
        self.m.now = lambda: '2027-01-01T00:00:00+00:00'
        self.assertEqual(self.bind(), 0, self.m.text())
        self.assertTrue(self.folder.is_dir())
        self.assertEqual(self.m.record()['slots']['kicad/projects']['boundAt'], '2026-01-01T00:00:00+00:00')

    def test_without_root_it_says_what_to_type(self):
        self.m.root_user = False
        before = self.everything()
        self.assertEqual(self.bind(), 1)
        self.assertEqual(self.m.said, ['This makes the folder %s and changes the storage records of this machine. '
                                       'Type: sudo isle storage slot bind kicad projects --partition dock-0001' % self.folder])
        self.assertEqual(self.everything(), before)

    def test_what_cannot_be_bound(self):
        before = self.everything()
        self.refused(self.bind(app='Kicad'), 'not an app and a storage slot of it')
        self.refused(self.bind(slot='Projects'), 'not an app and a storage slot of it')
        self.refused(self.bind(identifier='a b'), 'not a filesystem identifier')
        self.refused(self.bind(app='viewer'), 'the app viewer declares no storage slot projects')
        self.refused(self.bind(slot='boards'), 'the app kicad declares no storage slot boards')
        self.refused(self.bind(identifier='gone-0001'), 'no partition with the identifier gone-0001 is present')
        self.refused(self.bind(identifier=ARCH), 'partition sdb1 does not carry the role assets on this machine', 'It is carried by: dock-0001')
        self.refused(self.bind(identifier=SPARE), 'partition sdc2 does not carry the role assets', 'It is carried by: dock-0001')
        self.assertEqual(self.everything(), before)

    def test_no_partition_carries_the_role(self):
        self.declare(dict(name='boards', role='blob', target='/boards'))
        self.refused(self.bind(slot='boards', identifier=ARCH), 'No partition of this machine carries it. To give it: sudo isle storage partition assign arch-0001 --role blob')

    def test_a_partition_that_needs_attention_takes_no_slot(self):
        before = self.everything()
        self.m.marker('dock').rename(self.dock / 'put-aside')
        self.refused(self.bind(), 'partition sdc1 needs attention first (marker missing)', 'isle storage partition check --table')
        (self.dock / 'put-aside').rename(self.m.marker('dock'))
        self.m.disks[1]['children'].append(dict(name='sdb2', type='part', size=1, fstype='ext4', uuid=DOCK, mountpoints=[None]))
        self.refused(self.bind(), 'two partitions report the identifier dock-0001')
        self.m.disks[1]['children'].pop()
        self.m.mount_rows[2]['options'] = 'ro,relatime'
        self.refused(self.bind(), 'partition sdc1 is mounted read-only, and the app writes to this slot')
        self.assertEqual(self.everything(), before)
        self.assertEqual(self.bind('library'), 0, self.m.text())                      # a slot that is only read may sit on it

    def test_a_partition_that_is_away_takes_no_slot(self):
        self.m.disks[2]['children'][0]['mountpoints'] = [None]
        del self.m.mount_rows[2]
        self.refused(self.bind(), 'partition sdc1 needs attention first (not mounted)')
        self.assertFalse((self.dock / 'isle-slots').exists())

    def test_the_size_it_asks_for_must_be_free(self):
        self.declare(dict(PROJECTS, sizeBytes=401), LIBRARY)
        before = self.everything()
        self.refused(self.bind(), 'the slot asks for 401 B and partition sdc1 has 400 B free')
        self.assertEqual(self.everything(), before)
        self.folder.mkdir(parents=True)                                               # data that is there already is not counted twice
        self.assertEqual(self.bind(), 0, self.m.text())
        self.declare(PROJECTS, dict(LIBRARY, sizeBytes=400))
        self.assertEqual(self.bind('library'), 0, self.m.text())

    def test_bound_elsewhere_is_unbound_first(self):
        self.assertEqual(self.m.call('partition', 'assign', ARCH, '--role', 'package-repo,assets'), 0, self.m.text())
        self.assertEqual(self.bind(), 0)
        before = self.everything()
        self.refused(self.bind(identifier=ARCH), 'it is bound to the partition dock-0001 already', 'first: sudo isle storage slot unbind kicad projects',
                     'its data stays on that partition; no verb moves it yet')
        self.assertEqual(self.everything(), before)
        self.assertEqual(self.m.call('slot', 'unbind', 'kicad', 'projects'), 0)
        self.assertEqual(self.bind(identifier=ARCH), 0, self.m.text())
        self.assertEqual(self.m.record()['slots']['kicad/projects']['filesystemUuid'], ARCH)
        self.assertTrue(self.folder.is_dir())                                         # what was on the first partition is still there

    def test_bound_with_another_role_is_unbound_first(self):
        self.assertEqual(self.bind(), 0)
        (self.m.apps_dir / 'kicad' / 'storage-slots.json').write_text(json.dumps(dict(
            kind='isle.storage.slots', version=1, app='kicad', slots=[dict(PROJECTS, role='scratch')])))
        self.assertEqual(self.state(), 'declared with another role')
        self.refused(self.bind(), 'it is bound with the role assets and declared with the role scratch', 'First: sudo isle storage slot unbind kicad projects')

    def test_never_through_a_link(self):
        outside = self.m.tmp / 'outside'
        outside.mkdir()
        for link in (self.dock / 'isle-slots', self.dock / 'isle-slots' / 'kicad', self.folder):
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(outside, target_is_directory=True)
            before = self.everything()
            self.refused(self.bind())
            self.assertEqual(self.everything(), before, link)
            self.assertEqual(list(outside.iterdir()), [], link)
            link.unlink()
        self.folder.parent.mkdir(parents=True, exist_ok=True)
        self.folder.write_text('a file where the folder goes\n')
        self.refused(self.bind(), 'is there and is not a folder of the partition itself')

    def test_a_link_put_there_after_binding_is_a_missing_folder(self):
        self.assertEqual(self.bind(), 0)
        outside = self.m.tmp / 'outside'
        outside.mkdir()
        self.folder.rmdir(); self.folder.symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.state(), 'folder missing')
        with self.assertRaises(ValueError): ss.binds(self.m, 'kicad')

    def test_the_partition_is_looked_at_again_under_the_lock(self):
        real = self.m.mounts
        calls = []

        def mounts():
            calls.append(1)
            return real() if len(calls) == 1 else [m for m in real() if m['uuid'] != DOCK]
        self.m.mounts = mounts
        self.refused(self.bind(), 'partition sdc1 is no longer mounted at')
        self.assertNotIn('slots', self.m.record())
        self.assertFalse((self.dock / 'isle-slots').exists())
        calls.clear()
        elsewhere = self.m.tmp / 'elsewhere'
        elsewhere.mkdir()
        self.m.mounts = lambda: mounts() if len(calls) == 0 else [dict(m, target=str(elsewhere)) if m['uuid'] == DOCK else m for m in real()]
        self.refused(self.bind(), 'partition sdc1 is no longer mounted at %s' % self.dock)      # mounted on another folder meanwhile
        self.assertEqual(list(elsewhere.iterdir()), [])
        self.m.mounts = real
        self.m.unmounted.add(str(self.dock))
        self.refused(self.bind(), 'is no longer mounted at')
        self.assertFalse((self.dock / 'isle-slots').exists())

    def test_a_record_that_changed_meanwhile(self):
        real = sr.survey

        def survey(io):
            found = real(io)
            self.edit_record(lambda doc: doc.update(slots={'kicad/projects': dict(role='assets', filesystemUuid=DOCK, boundAt='x')}))
            return found
        sr.survey = survey
        try: self.refused(self.bind(), 'the binding of this slot changed while this command ran')
        finally: sr.survey = real
        self.assertFalse((self.dock / 'isle-slots').exists())

    def test_a_role_taken_meanwhile(self):
        real = sr.survey

        def survey(io):
            found = real(io)
            self.edit_record(lambda doc: doc['partitions'][DOCK].update(role='scratch', roles=['scratch']))
            return found
        sr.survey = survey
        try: self.refused(self.bind(), 'this machine no longer records partition sdc1 with the role assets')
        finally: sr.survey = real
        self.assertFalse((self.dock / 'isle-slots').exists())

    def test_damaged_slots_in_the_record_stop_every_verb(self):
        for bad in ([], {'kicad': dict(role='assets', filesystemUuid=DOCK)}, {'kicad/projects/x': dict(role='assets', filesystemUuid=DOCK)},
                    {'kicad/projects': dict(role='attic', filesystemUuid=DOCK)}, {'kicad/projects': dict(role='assets')},
                    {'kicad/projects': dict(role='assets', filesystemUuid='a b')}, {'Kicad/projects': dict(role='assets', filesystemUuid=DOCK)},
                    {'kicad/Projects': dict(role='assets', filesystemUuid=DOCK)}, {'kicad/projects': 'dock-0001'}, {7: 1}):
            self.edit_record(lambda doc: doc.update(slots=bad))
            text = self.m.records.read_text()
            self.refused(self.bind(), 'the storage slots in the records of this machine are damaged')
            self.refused(self.m.call('slot', 'unbind', 'kicad', 'projects'), 'are damaged')
            self.refused(self.m.call('partition', 'retire', DOCK, '--role', 'scratch'), 'are damaged')
            code, doc = self.rows()
            self.assertEqual((code, doc['ok']), (1, False), bad)
            self.assertIn('are damaged', doc['error'])
            self.assertEqual(self.m.records.read_text(), text)
        self.assertFalse((self.dock / 'isle-slots').exists())

    def test_a_folder_that_cannot_be_made_is_a_refusal(self):
        def fail(*a, **k): raise PermissionError(13, 'Permission denied')
        self.m.chown = fail
        self.refused(self.bind(), 'Permission denied')
        self.assertNotIn('slots', self.m.record())                                    # nothing is recorded for a folder that is not right
        self.assertFalse((self.dock / 'isle-slots').exists())                         # and the folders made for it are taken away again
        (self.folder.parent / 'library').mkdir(parents=True)
        self.refused(self.bind(), 'Permission denied')
        self.assertEqual(sorted(p.name for p in self.folder.parent.iterdir()), ['library'])      # what was there before is kept

    def test_the_folders_are_made_with_the_same_rights_whatever_the_mask(self):
        old = os.umask(0o077)
        try: self.assertEqual(self.bind(), 0, self.m.text())
        finally: os.umask(old)
        self.assertEqual([os.stat(p).st_mode & 0o777 for p in (self.folder, self.folder.parent, self.folder.parent.parent)], [0o755] * 3)


class Unbind(Case):
    def test_unbind_takes_the_binding_and_leaves_the_data(self):
        self.assertEqual(self.bind(), 0)
        (self.folder / 'board.kicad_pcb').write_text('a board\n')
        self.assertEqual(self.bind('library'), 0)
        self.assertEqual(self.m.call('slot', 'unbind', 'kicad', 'projects'), 0)
        self.assertEqual(self.m.said, [
            'Slot projects of kicad is no longer bound to the partition dock-0001. No data was moved or removed.',
            'Its folder isle-slots/kicad/projects on that partition is left where it is.',
            'An app that is running keeps the folder until it is started again. `isle storage slot check --app kicad` fails while the slot is declared and not bound.'])
        self.assertEqual((self.folder / 'board.kicad_pcb').read_text(), 'a board\n')
        self.assertEqual(list(self.m.record()['slots']), ['kicad/library'])
        self.assertEqual(self.state(), 'not bound')
        self.assertEqual(self.m.call('slot', 'unbind', 'kicad', 'library'), 0)
        self.assertEqual(sorted(self.m.record()), ['devices', 'partitions', 'version'])    # as a CLI that knows no slots wrote it

    def test_not_bound_changes_nothing(self):
        before = self.everything()
        self.m.root_user = False
        self.assertEqual(self.m.call('slot', 'unbind', 'kicad', 'projects'), 0)
        self.assertEqual(self.m.said, ['Slot projects of kicad is not bound on this machine. Nothing was changed.'])
        self.assertEqual(self.everything(), before)
        self.refused(self.m.call('slot', 'unbind', 'kicad', 'a/b'), 'not an app and a storage slot of it')

    def test_without_root_it_says_what_to_type(self):
        self.assertEqual(self.bind(), 0)
        self.m.root_user = False
        before = self.everything()
        self.assertEqual(self.m.call('slot', 'unbind', 'kicad', 'projects'), 1)
        self.assertEqual(self.m.said, ['This changes the storage records of this machine. Type: sudo isle storage slot unbind kicad projects'])
        self.assertEqual(self.everything(), before)

    def test_a_slot_whose_partition_is_away_or_whose_app_is_gone_can_be_unbound(self):
        self.assertEqual(self.bind(), 0)
        del self.m.disks[2]
        shutil.rmtree(self.m.apps_dir)
        self.assertEqual(self.m.call('slot', 'unbind', 'kicad', 'projects'), 0, self.m.text())
        self.assertNotIn('slots', self.m.record())


class States(Case):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.bind(), 0)

    def test_the_row_of_a_bound_slot(self):
        code, doc = self.rows()
        self.assertEqual((code, doc['ok'], doc['operation'], doc['readOnly']), (0, True, 'storage.slot.list', True))
        self.assertEqual(doc['result']['slots'][0], dict(
            {k: v for k, v in FULL.items() if k != 'name'}, app='kicad', slot='projects', filesystemUuid=DOCK, folder=str(self.folder), state='ok', problem=None))
        self.assertEqual(doc['result']['slots'][1]['state'], 'not bound')
        self.assertEqual((doc['result']['slots'][1]['folder'], doc['result']['slots'][1]['filesystemUuid']), (None, None))
        self.assertEqual(doc['result']['needAttention'], ['kicad/library'])

    def test_partition_away(self):
        self.m.disks[2]['children'][0]['mountpoints'] = [None]
        row = self.m.mount_rows.pop(2)
        self.assertEqual(self.state(), 'partition away')
        self.m.mount_rows.append(dict(row, fsroot='/sub'))                           # a part of it mounted is not the partition
        self.assertEqual(self.state(), 'partition away')
        del self.m.disks[2]
        self.assertEqual(self.state(), 'partition away')

    def test_role_taken_away(self):
        self.edit_record(lambda doc: doc['partitions'][DOCK].update(role='scratch', roles=['scratch']))
        self.assertEqual(self.state(), 'role taken away')
        self.edit_record(lambda doc: doc['partitions'].pop(DOCK))
        self.assertEqual(self.state(), 'role taken away')

    def test_partition_needs_attention(self):
        self.m.marker('dock').unlink()
        self.assertEqual(self.state(), 'partition needs attention')

    def test_two_partitions_with_one_identifier(self):
        self.m.disks[2]['children'].append(dict(name='sdc2', type='part', size=1, fstype='ext4', uuid=DOCK, mountpoints=[None]))
        self.assertEqual([(p['name'], p['roleState']) for p in sr.survey(self.m)[2] if p['filesystemUuid'] == DOCK], [('sdc1', 'ok'), ('sdc2', 'ok')])
        self.assertEqual(self.state(), 'partition needs attention')

    def test_folder_missing(self):
        self.folder.rmdir()
        self.assertEqual(self.state(), 'folder missing')
        self.folder.write_text('a file\n')
        self.assertEqual(self.state(), 'folder missing')

    def test_mounted_read_only_matters_to_a_slot_that_is_written(self):
        self.assertEqual(self.bind('library'), 0)
        self.m.mount_rows[2]['options'] = 'ro'
        self.assertEqual((self.state(), self.state('library')), ('mounted read-only', 'ok'))

    def test_not_declared(self):
        (self.m.apps_dir / 'kicad' / 'storage-slots.json').unlink()
        code, doc = self.rows()
        self.assertEqual(doc['result']['slots'], [dict(
            app='kicad', slot='projects', role='assets', kind='file-pool', target=None, service=None, writable=None, owner=None, distribution=None,
            association=None, copies=None, lifetime=None, sizeBytes=None, filesystemUuid=DOCK, folder=None, state='not declared', problem=None)])
        self.declare(LIBRARY, app='viewer')
        self.declare(LIBRARY, code=1)                                                 # and it cannot be dropped by a release while it is bound

    def test_a_declaration_that_cannot_be_read(self):
        path = self.m.apps_dir / 'kicad' / 'storage-slots.json'
        path.write_text('{not json')
        code, doc = self.rows()
        self.assertEqual([(r['slot'], r['state']) for r in doc['result']['slots']], [(None, 'declaration cannot be read'), ('projects', 'declaration cannot be read')])
        self.assertIn('is not one this CLI understands', doc['result']['slots'][0]['problem'])
        self.assertEqual(doc['result']['needAttention'], ['kicad/', 'kicad/projects'])
        self.assertEqual(self.m.call('slot', 'check', '--table'), 1)
        self.assertIn('The app kicad: its declaration %s is not one this CLI understands.' % path, self.m.said)
        self.refused(self.bind(), 'the app kicad: its declaration')
        with self.assertRaises(ValueError) as caught: ss.binds(self.m, 'kicad')
        self.assertIn('is not one this CLI understands', str(caught.exception))


class Report(Case):
    def test_list_shows_and_check_judges(self):
        code, doc = self.rows('list')
        self.assertEqual((code, doc['ok'], [r['state'] for r in doc['result']['slots']]), (0, True, ['not bound', 'not bound']))
        code, doc = self.rows('check')
        self.assertEqual((code, doc['ok'], doc['operation'], doc['result']['needAttention']), (1, False, 'storage.slot.check', ['kicad/projects', 'kicad/library']))
        self.assertEqual(self.bind(), 0)
        self.assertEqual(self.rows('check')[0], 1)
        self.assertEqual(self.bind('library'), 0)
        code, doc = self.rows('check')
        self.assertEqual((code, doc['ok'], doc['result']['needAttention']), (0, True, []))

    def test_check_for_one_app(self):
        self.declare(LIBRARY, app='viewer')
        self.assertEqual(self.bind('library', app='viewer'), 0, self.m.text())
        code, doc = self.rows('check', '--app', 'viewer')
        self.assertEqual((code, [(r['app'], r['slot']) for r in doc['result']['slots']]), (0, [('viewer', 'library')]))
        self.assertEqual(self.rows('check', '--app', 'kicad')[0], 1)
        code, doc = self.rows('check', '--app', 'no-slots')                           # an app that declares none has nothing in the way
        self.assertEqual((code, doc['result']['slots']), (0, []))
        self.assertEqual(self.m.call('slot', 'check', '--app', 'no-slots', '--table'), 0)
        self.assertEqual(self.m.said, ['The app no-slots declares no storage slot.', 'Nothing needs attention.'])

    def test_the_table(self):
        self.assertEqual(self.bind(), 0)
        self.assertEqual(self.m.call('slot', 'list', '--table'), 0)
        lines = self.m.said[0].splitlines()
        self.assertRegex(lines[0], r'^APP\s+SLOT\s+ROLE\s+KIND\s+THE APP SEES IT AT\s+WRITES\s+COPIES\s+PARTITION\s+STATE$')
        self.assertRegex(lines[1], r'^kicad\s+projects\s+assets\s+file-pool\s+/home/app/projects\s+yes\s+1\s+dock-0001\s+ok$')
        self.assertRegex(lines[2], r'^kicad\s+library\s+assets\s+file-pool\s+/usr/share/app/library\s+no\s+1\s+-\s+not bound$')
        self.assertEqual(self.m.said[1:], ['Slot library of kicad is not bound. Type: sudo isle storage slot bind kicad library --partition <filesystem-identifier>'
                                           '   (those with its role: isle storage role show assets --table)'])
        self.assertEqual(self.m.call('slot', 'check', '--table'), 1)
        self.assertEqual(self.m.said[-1], '1 of 2 need attention.')
        self.assertEqual(self.bind('library'), 0)
        self.assertEqual(self.m.call('slot', 'check', '--table'), 0)
        self.assertEqual(self.m.said[-1], 'Nothing needs attention.')

    def test_the_table_says_how_to_bind_only_of_a_slot_that_is_not_bound(self):
        self.assertEqual(self.bind(), 0)
        self.assertEqual(self.bind('library'), 0)
        self.m.marker('dock').unlink()
        self.assertEqual(self.m.call('slot', 'check', '--table'), 1)
        self.assertRegex(self.m.said[0], r'dock-0001\s+partition needs attention\n.*dock-0001\s+partition needs attention$')
        self.assertEqual(self.m.said[1:], ['2 of 2 need attention.'])

    def test_a_machine_without_slots(self):
        shutil.rmtree(self.m.apps_dir)
        self.assertEqual(self.m.call('slot', 'list', '--table'), 0)
        self.assertEqual(self.m.said, ['No app of this machine declares a storage slot.'])
        self.assertEqual(self.m.call('slot', 'check', '--table'), 0)
        self.assertEqual(self.rows('check'), (0, dict(ok=True, evaluatedAt='2026-01-01T00:00:00+00:00', operation='storage.slot.check', readOnly=True,
                                                      result=dict(slots=[], needAttention=[], unitsOutOfDate=[]))))

    def test_nothing_is_changed_by_looking(self):
        self.assertEqual(self.bind(), 0)
        self.m.root_user = False
        before = self.everything()
        for argv in (('list',), ('list', '--table'), ('check',), ('check', '--table'), ('check', '--app', 'kicad')):
            self.m.call('slot', *argv)
        self.assertEqual(self.everything(), before)

    def test_usage(self):
        for argv in ((), ('show',), ('list', 'kicad'), ('list', '--app', 'kicad'), ('check', '--app'), ('check', '--app', 'Kicad'), ('check', 'kicad', 'kicad'), ('check', '--app', 'kicad', 'x'),
                     ('declare', 'kicad'), ('declare', 'kicad', '--file', 'x'), ('bind', 'kicad', 'projects'), ('bind', 'kicad', 'projects', '--to', DOCK),
                     ('bind', 'kicad', 'projects', '--partition'), ('unbind', 'kicad'), ('unbind', 'kicad', 'projects', 'x')):
            self.assertEqual(self.m.call('slot', *argv), 2, argv)
            self.assertIn('sudo isle storage slot bind <app> <slot> --partition <filesystem-identifier>', self.m.text())
            self.assertIn('isle storage slot check [--app <app>] [--table]', self.m.text())
            self.assertIn('sudo isle storage slot declare <app> --from <release.json>', self.m.text())


class WhatAStartGives(Case):
    def test_the_binds_of_an_app(self):
        self.declare(PROJECTS, dict(LIBRARY, service='viewer'))
        self.assertEqual(self.bind(), 0)
        with self.assertRaises(ValueError) as caught: ss.binds(self.m, 'kicad')      # one slot not bound: the app is not to be started
        self.assertIn('the storage slot library of kicad cannot be used: not bound', str(caught.exception))
        self.assertIn('isle storage slot check --app kicad --table', str(caught.exception))
        self.assertEqual(self.bind('library'), 0)
        self.assertEqual(ss.binds(self.m, 'kicad'), [
            dict(service=None, source=str(self.folder), target='/home/app/projects', readOnly=False),
            dict(service='viewer', source=str(self.folder.parent / 'library'), target='/usr/share/app/library', readOnly=True)])
        self.assertEqual(ss.binds(self.m, 'no-slots'), [])
        self.m.disks[2]['children'][0]['mountpoints'] = [None]
        del self.m.mount_rows[2]
        with self.assertRaises(ValueError) as caught: ss.binds(self.m, 'kicad')
        self.assertIn('partition away', str(caught.exception))


class OtherVerbs(Case):
    def test_a_role_with_a_slot_bound_under_it_is_not_retired(self):
        self.assertEqual(self.bind(), 0)
        self.assertEqual(self.bind('library'), 0)
        before = self.everything()
        for more in ((), ('--record-only',), ('--leave-data',)):
            self.refused(self.m.call('partition', 'retire', DOCK, '--role', 'assets', *more),
                         'storage slots of apps are bound to it under the role assets: kicad/library, kicad/projects',
                         'First, for each: sudo isle storage slot unbind <app> <slot>')
        self.refused(self.m.call('partition', 'retire', DOCK, '--role', 'scratch,assets'), 'under the role assets: kicad/library, kicad/projects')
        self.assertEqual(self.everything(), before)
        self.assertEqual(self.m.call('partition', 'retire', DOCK, '--role', 'scratch'), 0, self.m.text())     # a role no slot is under goes
        self.assertEqual(self.m.call('slot', 'unbind', 'kicad', 'projects'), 0)
        self.assertEqual(self.m.call('slot', 'unbind', 'kicad', 'library'), 0)
        self.assertEqual(self.m.call('partition', 'retire', DOCK, '--role', 'assets'), 0, self.m.text())
        self.assertTrue(self.folder.is_dir())                                         # and the data of the slots is still on the partition

    def test_slots_bound_to_one_partition(self):
        self.assertEqual(self.bind(), 0)
        records = self.m.record()
        self.assertEqual(ss.bound_to(records, DOCK), ['kicad/projects'])
        self.assertEqual(ss.bound_to(records, DOCK, ['assets']), ['kicad/projects'])
        self.assertEqual(ss.bound_to(records, DOCK, ['scratch']), [])
        self.assertEqual(ss.bound_to(records, ARCH), [])
        self.assertEqual(ss.bound_to(dict(version=1, devices={}, partitions={}), DOCK), [])

    def test_assign_keeps_the_slots_of_the_record(self):
        self.assertEqual(self.bind(), 0)
        slots = self.m.record()['slots']
        self.assertEqual(self.m.call('partition', 'assign', ARCH, '--role', 'package-repo,git'), 0, self.m.text())
        self.assertEqual(self.m.call('device', 'register', 'S1', '--class', 'fixed'), 0, self.m.text())
        self.assertEqual(self.m.record()['slots'], slots)

    def test_through_the_public_dispatch(self):
        result = subprocess.run(['node', str(ENTRY), 'storage', 'slot'], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 2)                                        # the usage: it reads nothing of the machine
        self.assertIn('sudo isle storage slot bind <app> <slot> --partition <filesystem-identifier>', result.stdout)

    def test_the_real_change_of_owner_is_a_change_of_owner(self):
        with mock.patch('os.chown') as chown: sr.Io().chown(7, 12, 34)                 # the real one is never reached
        chown.assert_called_once_with(7, 12, 34)
        self.assertEqual(sr.Io.apps_dir, '/etc/isle-mesh/apps')


if __name__ == '__main__':
    unittest.main()
