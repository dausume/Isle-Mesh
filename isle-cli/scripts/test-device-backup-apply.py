"""isle backup restore <id> --apply, and isle backup before list | drop.

The made-up device of test-device-backup.py with its volumes laid out as Docker lays out a local volume
(<volume>/_data), a table for `docker ps | stop | start`, and an object for the health answer.  No real disk, no
real instance, no real Docker, no network.  tar and SQLite are the only real things run, on temporary folders.
"""
import fcntl
import importlib.util
import inspect
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import device_backup as db
import device_backup_apply as apply
import operations as op


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fx = _load('device_backup_fixtures', 'test-device-backup.py')
dr = _load('device_backup_drill_fixtures', 'test-device-backup-drill.py')
ENTRY = HERE.parent / 'index.js'
OP, OTHER_OP = 'b' * 32, 'c' * 32


class Crash(BaseException):
    """The machine lost power here."""


class Http(fx.Http):
    def get_bytes(self, url, resolve=None, max_bytes=None):
        fx.storage_migrate.check_url(url)
        base, _, path = url.partition('/api/')
        name = base[len('https://api.'):-len('.isle')]
        self.world.asked.append((name, path, resolve))
        if path != 'health' or not self.world.containers['prf-%s-backend' % name]['State']['Running'] or self.world.sick(name):
            raise fx.storage_migrate.HttpError('curl exit 7: no answer')
        return b'{}'


class World(fx.World):
    """The made-up device, with `docker ps | stop | start` and the health answer of each instance."""
    start_limit, stop_grace = 60, 7

    def __init__(self, tmp):
        self.calls, self.asked, self.states, self.refuse, self.after_start, self.ps_fails = [], [], {}, {}, {}, False
        self.sick = lambda name: False
        self.journal = Path(tmp) / 'journal'
        self.journal.mkdir()
        super().__init__(tmp)

    def add_instance(self, name, **kw):
        gate = super().add_instance(name, **kw)
        volume, folder = 'prf-%s_backend-data' % name, self.volumes / name
        (folder / '_data').mkdir()
        os.rename(folder / 'polari.db', folder / '_data' / 'polari.db')
        self.volume_rows[volume]['Mountpoint'] = str(folder / '_data')
        for row in self.containers.values():
            for mount in row['Mounts']:
                if mount.get('Name') == volume: mount['Source'] = str(folder / '_data')
        return gate

    def also_uses(self, container, name, running=True):
        self.containers[container] = dict(State=dict(Running=running), Mounts=[
            dict(Type='volume', Name='prf-%s_backend-data' % name, Driver='local', Source=str(self.data(name)), Destination='/shared')])

    def data(self, name='alpha'): return self.volumes / name / '_data'
    def http(self): return Http(self)
    def operation_dir(self): return str(self.journal)

    def docker(self, args, timeout=60):
        if args[:1] == ['ps']:
            assert args[:4] == ['ps', '--all', '--no-trunc', '--filter'] and args[5:] == ['--format', '{{.Names}}\t{{.State}}'], args
            key, _, volume = args[4].partition('=')
            assert key == 'volume' and volume, args
            if self.ps_fails: return 1, ''
            return 0, ''.join('%s\t%s\n' % (name, self.states.get(name) or ('running' if row['State']['Running'] else 'exited'))
                              for name, row in self.containers.items() if any(m.get('Name') == volume for m in row['Mounts']))
        if args[:1] == ['stop']:
            assert args[:3] == ['stop', '--time', str(self.stop_grace)] and len(args) == 4 and timeout == self.stop_grace + 60, (args, timeout)
            return self._change('stop', args[3], False)
        if args[:1] == ['start']:
            assert len(args) == 2 and timeout == 120, (args, timeout)
            return self._change('start', args[1], True)
        assert timeout == 60, (args, timeout)
        return super().docker(args)

    def _change(self, verb, name, running):
        self.calls.append('%s %s' % (verb, name))
        refuse = self.refuse.get((verb, name))
        if refuse and refuse(): return 1, ''
        self.containers[name]['State']['Running'] = running
        if running and name in self.after_start: self.after_start[name]()
        return 0, ''


def rows_of(path, table='Isle Device'):
    connection = sqlite3.connect('file:%s?mode=ro' % path, uri=True)
    try: return connection.execute('SELECT count(*) FROM "%s"' % table).fetchone()[0]
    finally: connection.close()


def sql(path, statement):
    connection = sqlite3.connect(str(path))
    connection.execute(statement); connection.commit(); connection.close()


class Case(unittest.TestCase):
    """A device with one instance, a device-backup of it that passed a drill, and newer data since then."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.w = World(self.dir.name)
        self.build()
        self.assertEqual(self.w.run('create', '--yes'), 0, self.w.text())
        self.ident = max(self.w.kept())
        self.assertEqual(self.w.run('restore', self.ident, '--drill'), 0, self.w.text())
        self.later()
        self.w.calls, self.w.asked = [], []
        self.gate_calls = {api: list(gate.calls) for api, gate in self.w.gates.items()}

    def build(self):
        dr.database(self.w.data() / 'polari.db', rows=3)
        (self.w.data() / 'files').mkdir()
        (self.w.data() / 'files' / 'kept.txt').write_text('in the device-backup')
        os.chmod(self.w.data(), 0o750)

    def later(self, name='alpha'):
        """What the instance wrote after the device-backup was taken."""
        dr.database(self.w.data(name) / 'polari.db', rows=7)
        (self.w.data(name) / 'later.txt').write_text('written after the device-backup')

    def run_apply(self, *more, yes=True):
        return self.w.run('restore', self.ident, '--apply', *((['--yes'] if yes else []) + list(more)))

    def volume(self, name='alpha'): return self.w.volumes / name
    def beside(self, name='alpha'): return sorted(p.name for p in self.volume(name).iterdir() if p.name != '_data')
    def tag(self, operation=OP): return '%s-%s' % (self.ident, operation[:8])
    def records(self): return sorted(p.name for p in (self.w.state / apply.RECORDS).glob('*'))

    def is_restored(self, name='alpha'):
        self.assertEqual(rows_of(self.w.data(name) / 'polari.db'), 3)
        self.assertFalse((self.w.data(name) / 'later.txt').exists())

    def is_as_it_was(self, name='alpha'):
        self.assertEqual(rows_of(self.w.data(name) / 'polari.db'), 7)
        self.assertEqual((self.w.data(name) / 'later.txt').read_text(), 'written after the device-backup')
        self.assertEqual(self.beside(name), [])
        self.assertEqual(self.records(), [])

    def gates_untouched(self):
        self.assertEqual({api: list(gate.calls) for api, gate in self.w.gates.items()}, self.gate_calls)


class PuttingBack(Case):
    def test_it_puts_the_data_back_and_keeps_what_the_instance_had(self):
        self.assertEqual(self.run_apply(), 0, self.w.text())
        self.is_restored()
        self.assertEqual((self.w.data() / 'files' / 'kept.txt').read_text(), 'in the device-backup')
        self.assertEqual(os.stat(self.w.data()).st_mode & 0o777, 0o750)             # the folder itself as the archive holds it
        self.assertEqual(self.beside(), ['before-' + self.tag()])                   # nothing else is left beside the data
        kept = self.volume() / ('before-' + self.tag())
        self.assertEqual(rows_of(kept / 'polari.db'), 7)                            # what it had is whole, beside it
        self.assertEqual((kept / 'later.txt').read_text(), 'written after the device-backup')
        self.assertEqual(self.w.calls, ['stop prf-alpha-backend', 'start prf-alpha-backend'])
        self.assertEqual(self.w.asked, [('alpha', 'health', None)])
        self.assertEqual(self.records(), [])
        self.gates_untouched()                                                      # a restore raises no write gate
        text = self.w.text()
        for part in ('Device-backup %s of %s' % (self.ident, fx.DEVICE), 'polari-alpha', 'stops and starts: prf-alpha-backend',
                     'put back; the instance answers and its tables are those of the device-backup',
                     'the data it had is kept in %s' % kept, 'Done. What was kept from before: sudo isle backup before list'):
            self.assertIn(part, text)
        self.assertNotIn('NOT', text)
        result = json.loads((self.w.journal / 'device-backup-restore.json').read_text())
        self.assertEqual((result['id'], result['notPutBack'], [row['name'] for row in result['putBack']]), (self.ident, [], ['polari-alpha']))
        self.assertEqual(result['putBack'][0]['started'], ['prf-alpha-backend'])

    def test_without_yes_it_says_what_it_would_do_and_changes_nothing(self):
        before = dr.snapshot(self.dir.name)
        self.w.op = None                                                            # and it is not journaled
        self.assertEqual(self.run_apply(yes=False), 0, self.w.text())
        self.assertEqual(dr.snapshot(self.dir.name), before)
        self.assertEqual((self.w.calls, self.w.asked), ([], []))
        for part in ('This would replace the data of polari-alpha with that of the device-backup',
                     'Nothing was changed. To do it: sudo isle backup restore %s --apply --yes' % self.ident):
            self.assertIn(part, self.w.text())
        self.assertEqual(self.run_apply('--item', 'polari-alpha', yes=False), 0)
        self.assertIn('sudo isle backup restore %s --apply --yes --item polari-alpha' % self.ident, self.w.text())

    def test_every_container_on_the_volume_is_stopped_and_only_those_are_started(self):
        self.w.also_uses('prf-alpha-worker', 'alpha')
        self.w.also_uses('prf-alpha-once', 'alpha', running=False)
        self.assertEqual(self.run_apply(), 0, self.w.text())
        self.assertEqual(self.w.calls, ['stop prf-alpha-backend', 'stop prf-alpha-worker', 'start prf-alpha-worker', 'start prf-alpha-backend'])
        self.assertFalse(self.w.containers['prf-alpha-once']['State']['Running'])
        self.is_restored()

    def test_an_instance_that_is_not_running_is_put_back_and_not_started(self):
        self.w.containers['prf-alpha-backend']['State']['Running'] = False
        self.assertEqual(self.run_apply(), 0, self.w.text())
        self.is_restored()
        self.assertEqual((self.w.calls, self.w.asked), ([], []))
        self.assertIn('no container that uses it is running; nothing is started', self.w.text())
        self.assertIn('put back; equal to its archive, nothing was started', self.w.text())
        self.assertFalse(list(self.w.data().glob('*-shm')) + list(self.w.data().glob('*-wal')))     # and no database of it was opened

    def test_a_count_that_moved_is_told_and_does_not_fail(self):
        self.w.after_start['prf-alpha-backend'] = lambda: sql(self.w.data() / 'polari.db', "INSERT INTO \"Isle Device\" (name) VALUES ('on start')")
        self.assertEqual(self.run_apply(), 0, self.w.text())
        self.assertIn('polari.db: the table Isle Device holds 4 rows; the device-backup holds 3', self.w.text())
        self.assertEqual(rows_of(self.w.data() / 'polari.db'), 4)

    def test_the_same_operation_run_again_leaves_what_it_put_back(self):
        self.assertEqual(self.run_apply(), 0, self.w.text())
        self.w.calls = []
        self.assertEqual(self.run_apply(), 0, self.w.text())
        self.assertIn('was put back by this operation before it stopped; left as it is', self.w.text())
        self.assertEqual(self.w.calls, [])
        self.assertEqual(self.beside(), ['before-' + self.tag()])

    def test_the_next_restore_removes_what_the_one_before_kept(self):
        self.assertEqual(self.run_apply(), 0, self.w.text())
        self.w.op = OTHER_OP
        self.assertEqual(self.run_apply(), 0, self.w.text())
        self.assertEqual(self.beside(), ['before-' + self.tag(OTHER_OP)])
        self.assertIn('removed what was kept from before an earlier restore: before-' + self.tag(), self.w.text())
        self.is_restored()


class Refused(Case):
    def refused(self, part, code=1, argv=(), yes=True):
        before = dr.snapshot(self.dir.name)
        self.assertEqual(self.run_apply(*argv, yes=yes), code, self.w.text())
        self.assertIn(part, self.w.text())
        self.assertEqual(dr.snapshot(self.dir.name), before)                        # nothing on the device changed
        self.assertEqual(self.w.calls, [])                                          # and no container was stopped or started
        self.gates_untouched()

    def folder(self): return self.w.base() / self.ident

    def test_usage(self):
        for argv in (['--item'], ['--item', 'Bad Name'], ['--item', '../x'], ['--yes'], ['x'], ['--item', 'polari-alpha', '--yes'],
                     ['--item', 'polari-alpha', 'polari-alpha']):
            self.refused('Usage', code=2, argv=argv)
        self.assertEqual(self.w.run('restore', '../' + self.ident, '--apply', '--yes'), 2)

    def test_not_root_and_not_through_the_journal(self):
        self.w.root = False
        self.refused('Refused: a device-backup can be read by root only')
        self.w.root, self.w.op = True, None
        self.refused('Refused: data is put back through the operation journal. Type: sudo isle backup restore %s --apply --yes' % self.ident)

    def test_a_device_backup_that_is_not_there_or_not_whole(self):
        self.assertEqual(self.w.run('restore', '20200101T000000Z-00000000', '--apply', '--yes'), 1)
        self.assertIn('Refused: no device-backup 20200101T000000Z-00000000', self.w.text())
        manifest = self.folder() / db.MANIFEST
        text = manifest.read_text()
        manifest.write_text(json.dumps(dict(json.loads(text), complete=False)))
        self.refused('is incomplete: it is not offered for a restore. Nothing was changed.')
        manifest.write_text('{')
        self.refused('is damaged: it is not offered for a restore. Nothing was changed.')
        manifest.write_text(text)
        archive = next(self.folder().glob('*polari-alpha.tar.gz'))
        with open(archive, 'ab') as out: out.write(b'x')
        self.refused('DOES NOT MATCH its record. Nothing was changed.')

    def test_no_passed_drill_of_these_archives_with_this_cli(self):
        record = self.folder() / db.DRILL
        text = record.read_text()
        record.write_text(json.dumps(dict(json.loads(text), passed=False)))
        self.refused('has no passed drill of these archives (the last one failed). First: sudo isle backup restore %s --drill' % self.ident)
        record.unlink()
        self.refused('has no passed drill of these archives (none is recorded)')
        record.write_text(text)
        self.w.versions = lambda: {'isle-mesh-cli': '9.9.9'}
        self.refused('passed with another version of the isle CLI than this machine runs now (9.9.9)')
        self.w.versions = lambda: {}
        self.refused('than this machine runs now (not known)')

    def test_items_that_cannot_be_put_back(self):
        self.refused("Refused: isle-settings is one of the isle's own folders. Putting those back is not built", argv=['--item', 'isle-settings'])
        self.refused("Refused: isle-state is one of the isle's own folders", argv=['--item', 'polari-alpha', '--item', 'isle-state'])
        self.refused('holds no item polari-beta. It holds: isle-settings, isle-state, polari-alpha', argv=['--item', 'polari-beta'])
        self.refused('Refused: an item is named twice', argv=['--item', 'polari-alpha', '--item', 'polari-alpha'])

    def test_a_machine_that_is_not_as_the_device_backup_needs_it(self):
        row = self.w.containers.pop('prf-alpha-backend')
        volume = self.w.volume_rows.pop('prf-alpha_backend-data')
        self.refused('Refused: the instance of polari-alpha is not deployed on this machine now')
        self.w.containers['prf-alpha-backend'], self.w.volume_rows['prf-alpha_backend-data'] = row, volume
        mount = [m for m in row['Mounts'] if m.get('Name')][0]
        mount['Name'] = 'another-volume'
        self.refused('Refused: polari-alpha uses the volume another-volume now; the device-backup holds the volume prf-alpha_backend-data')
        mount['Name'], mount['Source'] = 'prf-alpha_backend-data', str(self.volume())
        self.refused('Refused: the data of polari-alpha is not a plain folder of a local Docker volume')
        mount['Source'] = str(self.w.data())
        self.w.ps_fails = True
        self.refused('Refused: Docker did not say which containers use the volume of polari-alpha')
        self.w.ps_fails = False
        self.w.also_uses('prf-alpha-worker', 'alpha')
        self.w.states['prf-alpha-worker'] = 'paused'
        self.refused('is neither running nor stopped: prf-alpha-worker (paused)')
        del self.w.containers['prf-alpha-worker']
        record = self.w.polari_dir / 'alpha' / 'deployment.json'
        record.write_text(json.dumps(dict(json.loads(record.read_text()), backend_url='')))
        self.refused('Refused: the instance of polari-alpha is running and its record names no address to ask for its health')

    def test_too_little_room_for_a_second_copy(self):
        self.w.free_bytes = 100
        self.refused('Refused: a second copy of the data needs 1 MiB beside the data of polari-alpha')

    def test_no_data_of_an_instance_in_it(self):
        manifest = self.folder() / db.MANIFEST
        doc = json.loads(manifest.read_text())
        doc['items'] = [item for item in doc['items'] if item['kind'] != 'polari-volume']
        manifest.write_text(json.dumps(doc))
        (self.folder() / db.DRILL).write_text(json.dumps(dict(json.loads((self.folder() / db.DRILL).read_text()),
                                                               archives={item['file']: item['sha256'] for item in doc['items']})))
        self.refused('holds the data of no Polari instance; there is nothing this can put back')


class Failing(Case):
    """Whatever goes wrong, the instance ends on the data it had."""

    def not_put_back(self, part, calls):
        self.assertEqual(self.run_apply(), 1, self.w.text())
        self.assertIn('NOT PUT BACK: ' + part, self.w.text())
        self.assertIn('Not put back: polari-alpha.', self.w.text())
        self.is_as_it_was()
        self.assertEqual(self.w.calls, calls)
        self.assertTrue(self.w.containers['prf-alpha-backend']['State']['Running'])
        self.gates_untouched()

    def restored_now(self): return rows_of(self.w.data() / 'polari.db') == 3

    def test_an_archive_that_unpacks_wrong_is_never_moved_into_place(self):
        real = self.w.unpack
        def unpack(archive, target):
            real(archive, target); (Path(target) / 'files' / 'kept.txt').write_text('not what the archive holds')
        self.w.unpack = unpack
        self.not_put_back('what was unpacked is not what the archive holds (files/kept.txt: its bytes are not those of the archive)', [])
        self.assertIn('polari-alpha has the data it had.', self.w.text())

    def test_an_archive_that_cannot_be_unpacked(self):
        def unpack(archive, target): raise db.ItemFailed('tar ended with exit 2')
        self.w.unpack = unpack
        self.not_put_back('its archive could not be unpacked beside the data (tar ended with exit 2)', [])

    def test_a_container_that_does_not_stop(self):
        self.w.also_uses('prf-alpha-worker', 'alpha')
        self.w.refuse[('stop', 'prf-alpha-worker')] = lambda: True
        self.not_put_back('the container prf-alpha-worker could not be stopped. polari-alpha has the data it had and is running on it',
                          ['stop prf-alpha-backend', 'stop prf-alpha-worker', 'start prf-alpha-worker', 'start prf-alpha-backend'])

    def test_a_container_that_does_not_start_on_the_data_of_the_device_backup(self):
        self.w.refuse[('start', 'prf-alpha-backend')] = self.restored_now
        self.not_put_back('on the data of the device-backup these containers did not start: prf-alpha-backend',
                          ['stop prf-alpha-backend', 'start prf-alpha-backend', 'stop prf-alpha-backend', 'start prf-alpha-backend'])

    def test_no_health_answer_on_the_data_of_the_device_backup(self):
        self.w.sick = lambda name: self.restored_now()
        self.not_put_back('on the data of the device-backup the instance gave no health answer in 60 seconds. '
                          'polari-alpha has the data it had and is running on it',
                          ['stop prf-alpha-backend', 'start prf-alpha-backend', 'stop prf-alpha-backend', 'start prf-alpha-backend'])
        self.assertGreater(len(self.w.asked), 10)                                   # it was asked until the time was up

    def test_tables_that_are_gone_or_emptied_after_the_start(self):
        database = self.w.data() / 'polari.db'
        for change, part in ((lambda: sql(database, 'DELETE FROM "Isle Device"'), 'polari.db: the table Isle Device held 3 rows and holds none'),
                             (lambda: sql(database, 'DROP TABLE empty_one'), 'polari.db: the table empty_one is gone'),
                             (lambda: database.unlink(), 'polari.db: the database is no longer there'),
                             (lambda: database.write_bytes(b'SQLite format 3\x00' + b'\x07' * 4000), 'polari.db: the database is not whole')):
            with self.subTest(part):
                self.w.calls, self.w.asked = [], []
                self.w.after_start['prf-alpha-backend'] = lambda: change() if self.restored_now_or_gone() else None
                self.not_put_back('after its start the data is not that of the device-backup (' + part,
                                  ['stop prf-alpha-backend', 'start prf-alpha-backend', 'stop prf-alpha-backend', 'start prf-alpha-backend'])

    def restored_now_or_gone(self):
        try: return self.restored_now()
        except sqlite3.Error: return False

    def test_no_health_answer_on_the_data_it_had_either_is_said(self):
        self.w.sick = lambda name: True
        self.assertEqual(self.run_apply(), 1, self.w.text())
        self.assertIn('It has the data it had and its containers are started, but it gave no health answer in 60 seconds', self.w.text())
        self.is_as_it_was()

    def test_a_container_that_does_not_start_again_on_the_data_it_had_stops_everything(self):
        self.w.refuse[('start', 'prf-alpha-backend')] = lambda: True
        self.assertEqual(self.run_apply(), 1, self.w.text())
        self.assertIn('STOPPED: on the data of the device-backup these containers did not start: prf-alpha-backend. It has the data it had, '
                      'and these containers did not start: prf-alpha-backend. The record is', self.w.text())
        self.assertEqual(rows_of(self.w.data() / 'polari.db'), 7)                   # the data is the data it had
        self.assertEqual(self.records(), ['polari-alpha.json'])                     # and the record stays for the next run
        self.assertEqual(self.w.run('before', 'list'), 0)
        self.assertIn('none', self.w.text())
        del self.w.refuse[('start', 'prf-alpha-backend')]
        self.w.calls = []
        self.assertEqual(self.run_apply(), 0, self.w.text())                        # the next run starts it, then puts the data back
        self.assertIn('An earlier restore of polari-alpha did not finish', self.w.text())
        self.assertEqual(self.w.calls, ['start prf-alpha-backend', 'stop prf-alpha-backend', 'start prf-alpha-backend'])
        self.is_restored()


class Interrupted(Case):
    """The machine stops in the middle; the next run first puts the instance back as it was."""

    def crash_at(self, point):
        real_unpack, real_rename, renames = self.w.unpack, os.rename, []
        def unpack(archive, target):
            real_unpack(archive, target)
            raise Crash()
        def rename(source, target):
            renames.append(Path(target).name)
            if len(renames) == (2 if point == 'between the renames' else 3): raise Crash()
            real_rename(source, target)
        def start():
            raise Crash()
        def stop():
            self.w.containers['prf-alpha-backend']['State']['Running'] = False
            raise Crash()
        if point == 'unpacked': self.w.unpack = unpack
        elif point == 'stopped': self.w.refuse[('stop', 'prf-alpha-backend')] = stop
        elif point == 'started': self.w.after_start['prf-alpha-backend'] = start
        else: apply.os.rename = rename
        try:
            with self.assertRaises(Crash): self.run_apply()
        finally:
            self.w.unpack, apply.os.rename = real_unpack, real_rename
            self.w.after_start.clear(); self.w.refuse.clear()

    def test_wherever_it_stopped_the_next_run_puts_it_back_as_it_was_first(self):
        for point, beside, running in (('unpacked', ['restore-'], True), ('stopped', ['restore-'], False),
                                       ('between the renames', ['before-', 'restore-'], False), ('started', ['before-'], True)):
            with self.subTest(point):
                self.setUp()
                self.crash_at(point)
                self.assertEqual(self.beside(), [name + self.tag() for name in beside])
                self.assertEqual(self.records(), ['polari-alpha.json'])
                self.assertEqual(self.w.containers['prf-alpha-backend']['State']['Running'], running)
                self.assertEqual(self.w.run('before', 'drop', 'polari-alpha', '--yes'), 1)      # what is kept is what it goes back to
                self.assertIn('Refused: a restore of polari-alpha did not finish', self.w.text())
                kept = dr.snapshot(self.dir.name)
                self.assertEqual(self.run_apply(yes=False), 1, self.w.text())       # it cannot say what it would do, and says why
                self.assertIn('An earlier restore did not finish (polari-alpha). What this would do can be said only once its data is back '
                              'as it was: sudo isle backup before undo --yes. Nothing was changed.', self.w.text())
                self.assertEqual(dr.snapshot(self.dir.name), kept)
                self.w.unpack = lambda archive, target: (_ for _ in ()).throw(db.ItemFailed('no tar today'))
                self.assertEqual(self.run_apply(), 1, self.w.text())                # this run puts nothing back, and undoes the old one first
                self.assertIn('An earlier restore of polari-alpha did not finish (device-backup %s). Putting its data back as it was.' % self.ident,
                              self.w.text())
                self.assertIn('  polari-alpha has the data it had.', self.w.text())
                self.is_as_it_was()
                self.assertTrue(self.w.containers['prf-alpha-backend']['State']['Running'])

    def test_the_retry_of_the_operation_then_puts_it_back(self):
        self.crash_at('started')
        self.w.calls = []
        self.assertEqual(self.run_apply(), 0, self.w.text())
        self.assertEqual(self.w.calls, ['stop prf-alpha-backend', 'start prf-alpha-backend', 'stop prf-alpha-backend', 'start prf-alpha-backend'])
        self.is_restored()
        self.assertEqual(self.beside(), ['before-' + self.tag()])
        self.assertEqual(rows_of(self.volume() / ('before-' + self.tag()) / 'polari.db'), 7)

    def test_before_undo_puts_it_back_without_a_new_restore(self):
        self.assertEqual(self.w.run('before', 'undo', '--yes'), 0)
        self.assertIn('No restore is unfinished. Nothing was changed.', self.w.text())
        for argv in (['undo'], ['undo', '--yes', 'x'], ['undo', 'polari-alpha', '--yes']):
            self.assertEqual(self.w.run('before', *argv), 2, argv)
        for point in ('unpacked', 'stopped', 'between the renames', 'started'):
            with self.subTest(point):
                self.setUp()
                self.crash_at(point)
                kept, self.w.calls, self.w.op = dr.snapshot(self.dir.name), [], None        # it needs no journal: it only goes back
                self.w.root = False
                self.assertEqual(self.w.run('before', 'undo', '--yes'), 1)
                self.assertIn('Refused: this stops and starts containers', self.w.text())
                self.w.root = True
                self.w.lock_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self.w.lock_file, 'a+') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.assertEqual(self.w.run('before', 'undo', '--yes'), 1)
                    self.assertIn('is running now; nothing was changed', self.w.text())
                self.assertEqual((dr.snapshot(self.dir.name), self.w.calls), (kept, []))
                self.assertEqual(self.w.run('before', 'undo', '--yes'), 0, self.w.text())
                self.assertIn('  polari-alpha has the data it had.', self.w.text())
                self.is_as_it_was()
                self.assertTrue(self.w.containers['prf-alpha-backend']['State']['Running'])
                self.assertEqual(self.w.asked, [])
        self.w.op = OP
        self.crash_at('started')
        self.w.refuse[('stop', 'prf-alpha-backend')] = lambda: True
        self.assertEqual(self.w.run('before', 'undo', '--yes'), 1)
        self.assertIn('STOPPED: polari-alpha could not be put back as it was: the container prf-alpha-backend could not be stopped', self.w.text())
        self.assertEqual(self.records(), ['polari-alpha.json'])

    def test_a_record_that_cannot_be_read_stops_before_anything(self):
        for text in ('{', json.dumps(dict(kind=apply.RECORD_KIND, version=1, item='polari-alpha', volumeFolder='relative', new='restore-x',
                                          before='before-x', id='x', stopped=[])),
                     json.dumps(dict(kind=apply.RECORD_KIND, version=1, item='polari-alpha', volumeFolder=str(self.volume()), new='restore-x',
                                     before='../_data', id='x', stopped=[]))):
            (self.w.state / apply.RECORDS).mkdir(exist_ok=True)
            (self.w.state / apply.RECORDS / 'polari-alpha.json').write_text(text)
            before = dr.snapshot(self.dir.name)
            self.assertEqual(self.run_apply(), 1, self.w.text())
            self.assertIn('STOPPED: the record', self.w.text())
            self.assertIn('cannot be read. Nothing was changed', self.w.text())
            self.assertEqual(dr.snapshot(self.dir.name), before)
            self.assertEqual(self.w.calls, [])


class TwoInstances(Case):
    def build(self):
        super().build()
        self.w.add_instance('beta')
        dr.database(self.w.data('beta') / 'polari.db', rows=3)

    def later(self, name='alpha'):
        super().later('alpha'); super().later('beta')

    def test_the_second_fails_the_first_stays_put_back_and_the_retry_finishes(self):
        self.w.sick = lambda name: name == 'beta' and rows_of(self.w.data('beta') / 'polari.db') == 3
        self.assertEqual(self.run_apply(), 1, self.w.text())
        self.assertIn('Not put back: polari-beta. Put back before it and left so: polari-alpha.', self.w.text())
        self.is_restored('alpha')
        self.assertEqual(rows_of(self.w.data('beta') / 'polari.db'), 7)
        self.assertEqual(self.beside('beta'), [])
        result = json.loads((self.w.journal / 'device-backup-restore.json').read_text())
        self.assertEqual(([row['name'] for row in result['putBack']], result['notPutBack']), (['polari-alpha'], ['polari-beta']))
        self.w.sick, self.w.calls = (lambda name: False), []
        self.assertEqual(self.run_apply(), 0, self.w.text())
        self.assertIn('was put back by this operation before it stopped; left as it is', self.w.text())
        self.assertEqual(self.w.calls, ['stop prf-beta-backend', 'start prf-beta-backend'])
        self.is_restored('alpha'); self.is_restored('beta')

    def test_one_item_named_is_the_only_one_touched(self):
        self.assertEqual(self.run_apply('--item', 'polari-beta'), 0, self.w.text())
        self.is_restored('beta')
        self.is_as_it_was('alpha')
        self.assertEqual(self.w.calls, ['stop prf-beta-backend', 'start prf-beta-backend'])


class Before(Case):
    def test_list_and_drop(self):
        self.assertEqual(self.w.run('before'), 0)
        self.assertIn('none', self.w.text())
        self.assertEqual(self.run_apply(), 0, self.w.text())
        kept = self.volume() / ('before-' + self.tag())
        self.assertEqual(self.w.run('before', 'list'), 0)
        self.assertIn(str(kept), self.w.text())
        self.assertNotIn('under way', self.w.text())
        self.assertEqual(self.w.run('before', 'list', '--json'), 0)
        self.assertEqual(json.loads(self.w.text()), dict(keptBefore=[dict(item='polari-alpha', path=str(kept), underWay=False)]))
        for argv in (['drop'], ['drop', 'polari-alpha'], ['drop', 'polari-alpha', '--yes', 'x'], ['drop', '../x', '--yes'], ['remove', 'x', '--yes']):
            self.assertEqual(self.w.run('before', *argv), 2, argv)
        self.w.root = False
        for argv in (['list'], ['drop', 'polari-alpha', '--yes']):
            self.assertEqual(self.w.run('before', *argv), 1)
            self.assertIn('Refused', self.w.text())
        self.w.root = True
        self.w.lock_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.w.lock_file, 'a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.w.run('before', 'drop', 'polari-alpha', '--yes'), 1)
            self.assertIn('is running now; nothing was removed', self.w.text())
        self.assertTrue(kept.is_dir())
        self.assertEqual(self.w.run('before', 'drop', 'polari-beta', '--yes'), 1)
        self.assertIn('Refused: nothing is kept from before a restore of polari-beta', self.w.text())
        self.assertEqual(self.w.run('before', 'drop', 'polari-alpha', '--yes'), 0, self.w.text())
        self.assertIn('Removed %s.' % kept, self.w.text())
        self.assertEqual(self.beside(), [])
        self.is_restored()                                                          # the live data is not what was removed
        self.assertEqual(self.w.run('before', 'drop', 'polari-alpha', '--yes'), 1)

    def test_a_device_backup_taken_after_a_restore_does_not_hold_what_was_kept(self):
        self.assertEqual(self.run_apply(), 0, self.w.text())
        self.w.clock = self.w.clock + db.timedelta(hours=1)
        self.assertEqual(self.w.run('create', '--yes'), 0, self.w.text())
        newest = max(self.w.kept())
        listing = db.drill.contents(next((self.w.base() / newest).glob('*polari-alpha.tar.gz')))
        self.assertIn('polari.db', listing)
        self.assertFalse([path for path in listing if 'before-' in path or 'later.txt' in path])


class Wiring(unittest.TestCase):
    def test_the_journal_takes_the_request_under_the_device_backup_lock(self):
        ident = '20261122T101500Z-0a1b2c3d'
        for good in (['backup', 'restore', ident, '--apply', '--yes'], ['backup', 'restore', ident, '--apply', '--yes', '--item', 'polari-alpha'],
                     ['backup', 'restore', ident, '--apply', '--yes', '--item', 'polari-alpha', '--item', 'polari-beta']):
            self.assertEqual(op.request(good), good)
            self.assertEqual(op._names(good), ('app-device-backup.lock', 'device-backup-restore'))
        for bad in (['backup', 'restore', ident, '--apply'], ['backup', 'restore', ident, '--drill'], ['backup', 'restore', ident, '--apply', '--yes', '--item'],
                    ['backup', 'restore', '../' + ident, '--apply', '--yes'], ['backup', 'restore', ident, '--apply', '--yes', '--item', 'Bad Name'],
                    ['backup', 'restore', ident, '--apply', '--yes', '--force'], ['backup', 'restore', ident, '--yes', '--apply'],
                    ['backup', 'before', 'drop', 'polari-alpha', '--yes']):
            with self.assertRaises(ValueError, msg=bad): op.request(bad)
            self.assertIsNone(op._backup_names(bad))
        self.assertEqual(op._names(['backup', 'create', '--yes']), ('app-device-backup.lock', 'device-backup'))

    def test_the_wrapper_sends_only_the_journaled_form_through_the_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            scripts = Path(tmp) / 'scripts'
            scripts.mkdir()
            (scripts / 'backup.sh').write_text((HERE / 'backup.sh').read_text())
            for name in ('operations.py', 'device_backup.py'):
                (scripts / name).write_text('import sys\nprint(%r, *sys.argv[1:])\n' % name)
            def sent(*argv, operation=None):
                env = dict(os.environ, **({'ISLE_OPERATION_ID': operation} if operation else {}))
                env.pop('ISLE_OPERATION_ID', None) if not operation else None
                return subprocess.run(['bash', str(scripts / 'backup.sh'), *argv], capture_output=True, text=True, env=env, timeout=60).stdout.strip()
            ident = '20261122T101500Z-0a1b2c3d'
            self.assertEqual(sent('restore', ident, '--apply', '--yes'), 'operations.py run backup restore %s --apply --yes' % ident)
            self.assertEqual(sent('restore', ident, '--apply', '--yes', '--item', 'polari-alpha'),
                             'operations.py run backup restore %s --apply --yes --item polari-alpha' % ident)
            self.assertEqual(sent('restore', ident, '--apply', '--yes', operation='a' * 32), 'device_backup.py restore %s --apply --yes' % ident)
            self.assertEqual(sent('restore', ident, '--apply'), 'device_backup.py restore %s --apply' % ident)
            self.assertEqual(sent('restore', ident, '--drill'), 'device_backup.py restore %s --drill' % ident)
            self.assertEqual(sent('before', 'drop', 'polari-alpha', '--yes'), 'device_backup.py before drop polari-alpha --yes')
            self.assertEqual(sent('before', 'undo', '--yes'), 'device_backup.py before undo --yes')
            self.assertEqual(sent('create', '--yes'), 'operations.py run backup create --yes')

    def test_the_real_docker_call_takes_a_time_limit_and_the_command_table_names_the_verbs(self):
        self.assertEqual(inspect.signature(db.Deps.docker).parameters['timeout'].default, 60)
        self.assertGreater(db.Deps.start_limit, db.Deps.stop_grace)
        done = subprocess.run(['node', str(ENTRY), 'backup', 'help'], capture_output=True, text=True, timeout=60)
        for part in ('restore <id> --apply [--yes] [--item <name>]', 'before list [--json]', 'before drop <item> --yes', 'before undo --yes'):
            self.assertIn(part, done.stdout)
        self.assertIn('--apply', (HERE.parent / 'index.js').read_text().split("backup: {")[1].split('\n')[0])
        text = (HERE / 'device_backup_apply.py').read_text()
        self.assertLess(len(text.splitlines()), 460)
        self.assertNotRegex(text, r'/mnt/|/home/|/media/|/var/lib/docker')         # no machine's paths, names or addresses in the code
        self.assertNotRegex(text, r'\b(?:\d{1,3}\.){3}\d{1,3}\b')


if __name__ == '__main__':
    unittest.main()
