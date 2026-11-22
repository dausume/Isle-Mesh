"""isle backup restore <id> --drill.

The made-up device of test-device-backup.py: temporary folders, a table for Docker, an object for the write gate.
No real disk, no real instance, no network.  tar and SQLite are the only real things run, on those temporary folders.
"""
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import device_backup as db
import device_backup_drill as drill

_spec = importlib.util.spec_from_file_location('device_backup_fixtures', HERE / 'test-device-backup.py')
fx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fx)
ENTRY = HERE.parent / 'index.js'
API = 'https://api.alpha.isle'


def database(path, rows=3):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists(): path.unlink()
    connection = sqlite3.connect(str(path))
    connection.execute('CREATE TABLE "Isle Device" (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)')      # makes SQLite keep a table of its own
    connection.execute('CREATE TABLE empty_one (id INTEGER)')
    connection.executemany('INSERT INTO "Isle Device" (name) VALUES (?)', [('row %d' % n,) for n in range(rows)])
    connection.commit(); connection.close()


def snapshot(root, without=()):
    out = {}
    for path in sorted(Path(root).rglob('*')):
        if any(part in without for part in path.parts): continue
        found = path.lstat()
        out[str(path)] = (found.st_mode, found.st_size, os.readlink(path) if path.is_symlink() else path.read_bytes() if path.is_file() else None)
    return out


class Drill(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.w = fx.World(self.dir.name)
        self.volume = self.w.volumes / 'alpha'
        database(self.volume / 'polari.db')
        (self.w.tmp / 'outside').write_text('not part of any device-backup')
        os.symlink(str(self.w.tmp / 'outside'), self.w.settings / 'link-out')
        os.link(self.w.settings / 'device-name', self.w.settings / 'device-name-again')
        os.chmod(self.w.settings / 'ca' / 'isle-root.key', 0o600)
        os.chmod(self.w.settings / 'apps' / 'one' / 'release.json', 0o666)      # wider than a plain unpack would give it

    def make(self):
        self.assertEqual(self.w.run('create', '--yes'), 0, self.w.text())
        return max(self.w.kept())

    def record(self, ident): return json.loads((self.w.base() / ident / db.DRILL).read_text())

    def spoil(self, change):
        """Unpack for real, then change the scratch copy as a faulty restore would."""
        real = self.w.unpack
        def unpack(archive, target):
            real(archive, target)
            change(Path(archive).name, Path(target))
        self.w.unpack = unpack

    # ---- a drill that passes

    def test_a_drill_unpacks_compares_and_leaves_the_machine_as_it_was(self):
        ident = self.make()
        gate_calls, archived = list(self.w.gates[API].calls), len(self.w.archived)
        before = snapshot(self.dir.name, without=('locks',))
        self.assertEqual(self.w.run('restore', ident, '--drill'), 0, self.w.text())
        after = snapshot(self.dir.name, without=('locks',))
        record_path = str(self.w.base() / ident / db.DRILL)
        self.assertEqual(set(after) - set(before), {record_path})                # the record of the drill is the one new thing
        self.assertEqual({k: v for k, v in after.items() if k != record_path}, before)
        self.assertEqual(self.w.gates[API].calls, gate_calls)                    # a drill never touches a write gate
        self.assertEqual(len(self.w.archived), archived)                         # and copies nothing of the machine again
        text = self.w.text()
        for part in ('Drill of device-backup %s of %s' % (ident, fx.DEVICE), 'isle-settings', 'isle-state', 'polari-alpha',
                     'unpacked and equal to its archive', '1 database opened whole, 3 rows', 'The drill passed.',
                     'Nothing on this machine was changed', 'is removed'):
            self.assertIn(part, text)
        self.assertNotIn('NOT', text)
        doc = self.record(ident)
        self.assertEqual((doc['kind'], doc['version'], doc['id'], doc['device'], doc['passed']), (db.DRILL_KIND, 1, ident, fx.DEVICE, True))
        self.assertEqual(doc['cliVersions'], {'isle-mesh-cli': '0.0.0+test'})
        self.assertEqual([row['name'] for row in doc['items']], ['isle-settings', 'isle-state', 'polari-alpha'])
        self.assertTrue(all(row['wrong'] == [] and row['wrongCount'] == 0 for row in doc['items']))
        volume = doc['items'][2]
        self.assertEqual((volume['files'], volume['bytes']), (1, (self.volume / 'polari.db').stat().st_size))
        self.assertEqual(volume['databases'], [dict(path='polari.db', integrity='ok', tables={'Isle Device': 3, 'empty_one': 0})])
        self.assertNotIn('databases', doc['items'][0])                           # only an instance's data is opened as databases
        self.assertEqual(os.stat(record_path).st_mode & 0o777, 0o600)
        manifest = json.loads((self.w.base() / ident / db.MANIFEST).read_text())
        self.assertEqual(doc['archives'], {item['file']: item['sha256'] for item in manifest['items']})

    def test_links_are_unpacked_as_links_and_what_they_point_at_is_not_touched(self):
        ident, seen = self.make(), {}
        def look(name, target):
            if 'isle-settings' in name:
                seen.update(link=os.readlink(target / 'link-out'), same=os.path.samefile(target / 'device-name', target / 'device-name-again'),
                            key=(target / 'ca' / 'isle-root.key').stat().st_mode & 0o777, where=target,
                            scratch=target.parent.stat().st_mode & 0o777)
        self.spoil(look)
        self.assertEqual(self.w.run('restore', ident, '--drill'), 0, self.w.text())
        self.assertEqual((seen['link'], seen['same'], seen['key']), (str(self.w.tmp / 'outside'), True, 0o600))
        self.assertEqual(seen['scratch'], 0o700)                                              # nobody else can look into it
        self.assertEqual(seen['where'].parent, self.w.base() / (db.SCRATCH + ident))          # beside the device-backup, on its home
        self.assertFalse(seen['where'].parent.exists())                                       # and gone afterwards
        self.assertEqual((self.w.tmp / 'outside').read_text(), 'not part of any device-backup')

    def test_a_drill_on_the_partition_with_the_role_backup(self):
        w = fx.World(tempfile.mkdtemp(dir=self.dir.name), backup_partition=True)
        self.assertEqual(w.run('create', '--yes'), 0, w.text())
        ident = max(w.kept())
        self.assertEqual(w.run('restore', ident, '--drill'), 0, w.text())
        self.assertTrue((w.machine.folders['first'] / db.FOLDER / fx.DEVICE / ident / db.DRILL).is_file())
        self.assertEqual([p.name for p in (w.machine.folders['first'] / db.FOLDER / fx.DEVICE).iterdir()], [ident])

    def test_an_instance_with_no_database_file_is_said_and_passes(self):
        (self.volume / 'polari.db').write_text('plain words, no database')
        ident = self.make()
        self.assertEqual(self.w.run('restore', ident, '--drill'), 0, self.w.text())
        self.assertIn('no database in it', self.w.text())
        self.assertEqual(self.record(ident)['items'][2]['databases'], [])

    # ---- a drill that fails

    def failing(self, change, *words):
        ident = self.make()
        self.spoil(change)
        self.assertEqual(self.w.run('restore', ident, '--drill'), 1, self.w.text())
        text = self.w.text()
        for word in words + ('NOT what the archive holds', 'The drill FAILED.'): self.assertIn(word, text)
        self.assertNotIn('The drill passed', text)
        self.assertFalse((self.w.base() / (db.SCRATCH + ident)).exists())
        doc = self.record(ident)
        self.assertFalse(doc['passed'])
        return ident, doc

    def test_a_file_unpacked_with_other_bytes_fails_the_drill(self):
        def change(name, target):
            if 'isle-settings' in name: (target / 'ca' / 'isle-root.key').write_text('the CA kex')      # same length
        ident, doc = self.failing(change, 'ca/isle-root.key: its bytes are not those of the archive')
        self.assertEqual([row['wrongCount'] for row in doc['items']], [1, 0, 0])
        self.assertEqual(self.w.run('list'), 0)
        self.assertIn('drill: failed', self.w.text())

    def test_a_shorter_file_fails_the_drill(self):
        def change(name, target):
            if 'isle-state' in name: (target / 'desired-release.json').write_text('{')
        self.failing(change, 'desired-release.json: its bytes are not those of the archive')

    def test_a_file_that_was_not_unpacked_and_one_that_was_added_fail_the_drill(self):
        def change(name, target):
            if 'isle-settings' in name:
                (target / 'device-name').unlink(); (target / 'apps' / 'extra').write_text('x'); (target / 'apps' / 'extra-folder').mkdir()
        ident, doc = self.failing(change, 'device-name: in the archive, not unpacked', 'apps/extra: unpacked, not in the archive',
                                  'apps/extra-folder: unpacked, not in the archive')
        self.assertGreaterEqual(doc['items'][0]['wrongCount'], 3)

    def test_other_permissions_fail_the_drill(self):
        def change(name, target):
            if 'isle-settings' in name: os.chmod(target / 'ca' / 'isle-root.key', 0o644)
        self.failing(change, 'ca/isle-root.key: its permissions are 644, the archive holds 600')

    def test_a_link_that_points_elsewhere_and_a_file_in_place_of_a_folder_fail_the_drill(self):
        def change(name, target):
            if 'isle-settings' in name:
                (target / 'link-out').unlink(); os.symlink('/somewhere/else', target / 'link-out')
                (target / 'device-name-again').unlink(); (target / 'device-name-again').write_text(fx.DEVICE)
                (target / 'apps' / 'one' / 'release.json').unlink(); (target / 'apps' / 'one').rmdir(); (target / 'apps' / 'one').write_text('x')
        self.failing(change, 'link-out: the link points somewhere else', 'no longer the same file as',
                     'apps/one: unpacked as a file, the archive holds a folder')

    def test_another_owner_fails_the_drill(self):
        rows = drill.contents(self.archive_of(self.make(), 'isle-state'))
        with tempfile.TemporaryDirectory(dir=self.dir.name) as target:
            self.w.unpack(self.archive_of(max(self.w.kept()), 'isle-state'), target)
            self.assertEqual(drill.compare(target, rows), [])
            row = rows['desired-release.json']
            for key in ('uid', 'gid'):
                row[key] += 1
                self.assertEqual(drill.compare(target, rows), ['desired-release.json: its owner is %d:%d, the archive holds %d:%d'
                                                               % (os.getuid(), os.stat(target).st_gid, row['uid'], row['gid'])])
                row[key] -= 1

    def archive_of(self, ident, name):
        manifest = json.loads((self.w.base() / ident / db.MANIFEST).read_text())
        return self.w.base() / ident / [item['file'] for item in manifest['items'] if item['name'] == name][0]

    def test_a_database_that_is_not_whole_fails_the_drill(self):
        database(self.volume / 'polari.db', rows=4000)
        raw = bytearray((self.volume / 'polari.db').read_bytes())
        raw[4096:8192] = b'\xff' * 4096                # a page of the table is overwritten; the file's first page still says SQLite
        (self.volume / 'polari.db').write_bytes(bytes(raw))
        ident = self.make()                             # the device-backup holds it as it lies: complete, and every file matches
        self.assertEqual(self.w.run('verify', ident), 0)
        self.assertEqual(self.w.run('restore', ident, '--drill'), 1, self.w.text())
        self.assertIn('polari.db: the database is not whole', self.w.text())
        self.assertIn('The drill FAILED.', self.w.text())
        doc = self.record(ident)
        self.assertNotEqual(doc['items'][2]['databases'][0]['integrity'], 'ok')
        self.assertFalse(doc['passed'])

    def test_a_database_that_opens_but_does_not_agree_with_itself_fails_the_drill(self):
        path = self.volume / 'polari.db'
        path.unlink()
        connection = sqlite3.connect(str(path))
        connection.execute('CREATE TABLE t (name TEXT)'); connection.execute('CREATE INDEX t_name ON t (name)')
        connection.executemany('INSERT INTO t VALUES (?)', [('entry-number-%04d' % n,) for n in range(50)])
        connection.commit(); connection.close()
        raw = bytearray(path.read_bytes())
        self.assertEqual(raw.count(b'entry-number-0007'), 2)              # once in the table, once in its index
        at = raw.find(b'entry-number-0007'); raw[at:at + 17] = b'entry-number-9997'
        path.write_bytes(bytes(raw))                                     # every row can still be read and counted
        ident = self.make()
        self.assertEqual(self.w.run('restore', ident, '--drill'), 1, self.w.text())
        self.assertIn('polari.db: the database is not whole (', self.w.text())
        self.assertIn('t_name', self.w.text())
        found = self.record(ident)['items'][2]['databases'][0]
        self.assertEqual(found['tables'], {'t': 50})
        self.assertIn('t_name', found['integrity'])
        self.assertFalse(self.record(ident)['passed'])

    def test_tar_that_fails_fails_the_item_and_the_scratch_copy_is_removed(self):
        ident = self.make()
        def unpack(archive, target):
            (Path(target) / 'half').write_text('x')
            raise db.ItemFailed('tar ended with exit 2: no room')
        self.w.unpack = unpack
        self.assertEqual(self.w.run('restore', ident, '--drill'), 1)
        self.assertIn('tar ended with exit 2: no room', self.w.text())
        self.assertFalse((self.w.base() / (db.SCRATCH + ident)).exists())
        self.assertEqual([row['wrongCount'] for row in self.record(ident)['items']], [1, 1, 1])

    def test_the_real_unpack_reports_a_tar_that_fails(self):
        target = self.w.tmp / 'empty'; target.mkdir()
        (self.w.tmp / 'not-an-archive.tar.gz').write_text('no')
        with self.assertRaises(db.ItemFailed) as caught: db.Deps().unpack(self.w.tmp / 'not-an-archive.tar.gz', target)
        self.assertIn('tar ended with exit', str(caught.exception))
        self.assertEqual(list(target.iterdir()), [])

    def test_only_the_first_lines_are_printed_and_the_count_is_kept(self):
        for n in range(130): (self.w.settings / 'apps' / ('file-%03d' % n)).write_text('x')
        def change(name, target):
            if 'isle-settings' in name:
                for n in range(130): (target / 'apps' / ('file-%03d' % n)).unlink()
        ident, doc = self.failing(change, 'and 120 more')
        self.assertEqual((doc['items'][0]['wrongCount'], len(doc['items'][0]['wrong'])), (130, 100))
        self.assertEqual(len([line for line in self.w.said if 'in the archive, not unpacked' in line]), drill.SHOWN)

    # ---- what is refused before anything is unpacked

    def never(self):
        def unpack(archive, target): raise AssertionError('unpacked')
        self.w.unpack = unpack

    def test_a_device_backup_that_no_longer_matches_its_record_is_not_unpacked(self):
        ident = self.make()
        target = self.archive_of(ident, 'isle-state')
        raw = bytearray(target.read_bytes()); raw[-1] ^= 1; target.write_bytes(bytes(raw))
        self.never()
        self.assertEqual(self.w.run('restore', ident, '--drill'), 1)
        self.assertIn('DOES NOT MATCH its record. Nothing was unpacked.', self.w.text())
        self.assertFalse((self.w.base() / ident / db.DRILL).exists())

    def test_an_incomplete_a_damaged_and_an_unknown_one_are_not_drilled(self):
        ident = self.make()
        self.never()
        path = self.w.base() / ident / db.MANIFEST
        good = path.read_text()
        doc = json.loads(good); doc['complete'] = False; path.write_text(json.dumps(doc))
        self.assertEqual(self.w.run('restore', ident, '--drill'), 1)
        self.assertIn('is incomplete: it is not offered for a restore, and not for a drill', self.w.text())
        path.write_text('{not json')
        self.assertEqual(self.w.run('restore', ident, '--drill'), 1)
        self.assertIn('is damaged', self.w.text())
        doc = json.loads(good); doc['device'] = 'another-device'; path.write_text(json.dumps(doc))
        self.assertEqual(self.w.run('restore', ident, '--drill'), 1)
        self.assertIn('is damaged', self.w.text())          # one that names another device is not this device's
        self.assertEqual(self.w.run('restore', '20200101T000000Z-00000000', '--drill'), 1)
        self.assertIn('Refused: no device-backup', self.w.text())
        self.assertFalse((self.w.base() / ident / db.DRILL).exists())

    def test_usage_root_and_apply(self):
        ident = self.make()
        self.never()
        for argv in (['restore'], ['restore', ident], ['restore', ident, '--drill', 'x'], ['restore', '../' + ident, '--drill'],
                     ['restore', ident, '--yes'], ['restore', '--drill', ident]):
            self.assertEqual(self.w.run(*argv), 2, argv)
            self.assertIn('Usage', self.w.text())
        self.assertEqual(self.w.run('restore', ident, '--apply'), 1)        # putting back needs a passed drill first
        self.assertIn('has no passed drill of these archives (none is recorded)', self.w.text())
        self.w.root = False
        self.assertEqual(self.w.run('restore', ident, '--drill'), 1)
        self.assertIn('Refused: a device-backup can be read by root only. Type: sudo isle backup restore %s --drill' % ident, self.w.text())
        self.assertFalse((self.w.base() / ident / db.DRILL).exists())

    def test_too_little_room_is_refused_before_anything_is_unpacked(self):
        ident = self.make()
        self.never()
        need = sum(drill.totals(drill.contents(self.archive_of(ident, name)))[2] for name in ('isle-settings', 'isle-state', 'polari-alpha'))
        self.w.free_bytes = need + self.w.reserve - 1
        self.assertEqual(self.w.run('restore', ident, '--drill'), 1)
        self.assertIn('Refused: the scratch copy needs', self.w.text())
        self.assertEqual(sorted(p.name for p in self.w.base().iterdir()), [ident])
        self.assertFalse((self.w.base() / ident / db.DRILL).exists())
        self.w.unpack = lambda archive, target: db.Deps.unpack(self.w, archive, target)
        self.w.free_bytes = need + self.w.reserve
        self.assertEqual(self.w.run('restore', ident, '--drill'), 0, self.w.text())

    def test_a_drill_is_refused_while_a_device_backup_runs(self):
        ident = self.make()
        self.never()
        self.w.lock_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.w.lock_file, 'a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.w.run('restore', ident, '--drill'), 1)
        self.assertIn('a device-backup or another drill is running now; nothing was unpacked', self.w.text())

    def test_a_scratch_folder_left_by_an_interrupted_drill_is_replaced(self):
        ident = self.make()
        left = self.w.base() / (db.SCRATCH + ident)
        (left / '01-isle-settings').mkdir(parents=True); (left / '01-isle-settings' / 'old').write_text('from before')
        self.assertEqual(self.w.run('restore', ident, '--drill'), 0, self.w.text())
        self.assertFalse(left.exists())
        self.assertEqual(self.w.kept(), {ident: 'release'})          # a scratch folder is never counted as a device-backup

    def test_something_else_in_the_place_of_the_scratch_folder_is_refused_and_left(self):
        ident = self.make()
        self.never()
        elsewhere = self.w.tmp / 'elsewhere'; elsewhere.mkdir(); (elsewhere / 'keep').write_text('kept')
        left = self.w.base() / (db.SCRATCH + ident)
        os.symlink(str(elsewhere), left)
        self.assertEqual(self.w.run('restore', ident, '--drill'), 1)
        self.assertIn('is in the way and is not a scratch folder of a drill', self.w.text())
        self.assertTrue(left.is_symlink()); self.assertEqual((elsewhere / 'keep').read_text(), 'kept')
        left.unlink(); left.write_text('a file')
        self.assertEqual(self.w.run('restore', ident, '--drill'), 1)
        self.assertEqual(left.read_text(), 'a file')

    def test_a_scratch_copy_that_cannot_be_removed_is_said(self):
        ident = self.make()
        real = db.shutil.rmtree
        self.addCleanup(setattr, db.shutil, 'rmtree', real)
        db.shutil.rmtree = lambda path, ignore_errors=False: None
        self.assertEqual(self.w.run('restore', ident, '--drill'), 0, self.w.text())
        self.assertIn('the scratch copy COULD NOT BE REMOVED (%s)' % (self.w.base() / (db.SCRATCH + ident)), self.w.text())
        db.shutil.rmtree = real

    # ---- the record of a drill, and the list

    def test_the_list_shows_the_drill_and_a_record_of_other_archives_does_not_count(self):
        ident = self.make()
        self.assertEqual(self.w.run('list', '--json'), 0)
        self.assertEqual([row['drill'] for row in json.loads(self.w.text())['deviceBackups']], ['none'])
        self.assertEqual(self.w.run('restore', ident, '--drill'), 0, self.w.text())
        self.assertEqual(self.w.run('list'), 0)
        self.assertIn('drill: passed', self.w.text())
        folder = self.w.base() / ident
        manifest = json.loads((folder / db.MANIFEST).read_text())
        found = db.drilled(folder, manifest)
        self.assertEqual((found['state'], found['cliVersions']), ('passed', {'isle-mesh-cli': '0.0.0+test'}))
        good = (folder / db.DRILL).read_text()
        for change, state in ((lambda d: d['archives'].update({manifest['items'][0]['file']: '0' * 64}), 'none'),
                              (lambda d: d.update(id='20200101T000000Z-00000000'), 'unreadable'),
                              (lambda d: d.update(kind='something-else'), 'unreadable'), (lambda d: d.update(version=2), 'unreadable'),
                              (lambda d: d.update(at='yesterday'), 'unreadable'), (lambda d: d.pop('passed'), 'unreadable'),
                              (lambda d: d.update(passed='yes'), 'failed'), (lambda d: d.update(passed=False), 'failed')):
            doc = json.loads(good); change(doc); (folder / db.DRILL).write_text(json.dumps(doc))
            self.assertEqual(db.drilled(folder, manifest)['state'], state)
        (folder / db.DRILL).write_text('{not json')
        self.assertEqual(db.drilled(folder, manifest)['state'], 'unreadable')
        self.assertEqual(self.w.run('verify', ident), 0)             # the record of a drill is not a file of the device-backup
        doc = json.loads((folder / db.MANIFEST).read_text()); doc['complete'] = False; (folder / db.MANIFEST).write_text(json.dumps(doc))
        self.assertEqual(self.w.run('list', '--json'), 0)
        self.assertEqual([row['drill'] for row in json.loads(self.w.text())['deviceBackups']], [None])

    def test_the_next_device_backup_starts_with_no_drill(self):
        first = self.make()
        self.assertEqual(self.w.run('restore', first, '--drill'), 0, self.w.text())
        self.w.clock = self.w.clock + fx.timedelta(days=1)
        second = self.make()
        self.assertEqual(self.w.run('list', '--json'), 0)
        self.assertEqual({row['id']: row['drill'] for row in json.loads(self.w.text())['deviceBackups']}, {first: 'passed', second: 'none'})

    # ---- the comparing code by itself

    def test_an_archive_that_would_write_outside_the_folder_is_refused_whole(self):
        for name, link in (('../outside', None), ('/etc/outside', None), ('a/../../outside', None), ('inside', '../../outside')):
            path = self.w.tmp / 'crafted.tar.gz'
            with tarfile.open(path, 'w:gz') as tar:
                member = tarfile.TarInfo(name)
                if link: member.type, member.linkname = tarfile.LNKTYPE, link
                else: member.size = 1
                tar.addfile(member, None if link else io.BytesIO(b'x'))
            with self.assertRaises(drill.DrillFailed) as caught: drill.contents(path)
            self.assertIn('outside the folder', str(caught.exception))
        path.write_text('not an archive')
        with self.assertRaises(drill.DrillFailed) as caught: drill.contents(path)
        self.assertIn('cannot be read', str(caught.exception))

    def test_a_crafted_archive_is_never_unpacked_by_the_verb(self):
        ident = self.make()
        self.never()
        target = self.archive_of(ident, 'isle-state')
        with tarfile.open(target, 'w:gz') as tar:
            member = tarfile.TarInfo('../outside'); member.size = 1
            tar.addfile(member, io.BytesIO(b'x'))
        path = self.w.base() / ident / db.MANIFEST
        doc = json.loads(path.read_text())
        row = [item for item in doc['items'] if item['name'] == 'isle-state'][0]
        row.update(sizeBytes=target.stat().st_size, sha256=db.hashlib.sha256(target.read_bytes()).hexdigest())
        path.write_text(json.dumps(doc))
        self.assertEqual(self.w.run('restore', ident, '--drill'), 1)
        self.assertIn('would be written outside the folder', self.w.text())
        self.assertIn('Nothing was unpacked', self.w.text())
        self.assertFalse((self.w.tmp / 'outside-x').exists())

    def test_contents_and_totals(self):
        rows = drill.contents(self.archive_of(self.make(), 'isle-settings'))
        self.assertEqual(rows['']['type'], 'folder')
        self.assertEqual(rows['link-out'], dict(type='link', target=str(self.w.tmp / 'outside'), mode=rows['link-out']['mode'],
                                               uid=os.getuid(), gid=rows['link-out']['gid']))
        same = sorted(rows[name]['type'] for name in ('device-name', 'device-name-again'))
        self.assertEqual(same, ['file', 'same-file'])
        self.assertEqual(rows['ca/isle-root.key']['sha256'], db.hashlib.sha256(b'the CA key').hexdigest())
        self.assertEqual(rows['ca/isle-root.key']['mode'], 0o600)
        self.assertNotIn('apt/repo/pool/big.deb', rows)
        entries, files, size = drill.totals(rows)
        self.assertEqual(files, len([r for r in rows.values() if r['type'] == 'file']))
        self.assertEqual(size, sum(r['size'] for r in rows.values() if r['type'] == 'file'))
        self.assertEqual(entries, len(rows))

    def test_databases_are_found_by_what_they_are_not_by_their_name(self):
        folder = self.w.tmp / 'data'
        database(folder / 'deep' / 'any-name.bin', rows=2)
        (folder / 'looks.db').write_text('words')
        (folder / 'empty.db').write_bytes(b'')
        os.symlink(str(folder / 'deep' / 'any-name.bin'), folder / 'a-link.db')
        self.assertEqual(drill.databases(folder), [dict(path='deep/any-name.bin', integrity='ok', tables={'Isle Device': 2, 'empty_one': 0})])
        (folder / 'cut.db').write_bytes((folder / 'deep' / 'any-name.bin').read_bytes()[:100])
        found = {row['path']: row for row in drill.databases(folder)}
        self.assertNotEqual(found['cut.db']['integrity'], 'ok')
        self.assertEqual(found['cut.db']['tables'], {})

    def test_the_verb_is_reachable_through_the_cli_and_no_place_is_written_into_the_code(self):
        done = subprocess.run(['node', str(ENTRY), 'backup', 'restore'], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn('sudo isle backup restore <id> --drill', done.stdout)
        done = subprocess.run(['node', str(ENTRY), 'backup', 'help'], capture_output=True, text=True, timeout=60)
        self.assertIn('restore <id> --drill', done.stdout)
        self.assertIn('restore <id> --drill', (HERE / 'backup.sh').read_text())
        text = (HERE / 'device_backup_drill.py').read_text()
        self.assertNotRegex(text, r'/mnt/|/home/|/media/|/etc/|/var/')
        self.assertNotRegex(text, r'\b(?:\d{1,3}\.){3}\d{1,3}\b')


if __name__ == '__main__':
    unittest.main()
