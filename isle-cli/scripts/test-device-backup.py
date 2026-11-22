"""isle backup plan | create | list | verify | release-hold.

Fixtures only: the settings folder, the state folder, every "volume" and every "partition" are temporary folders,
the block device and mount reports are made up, Docker is a table and the write gate is an object in this file.
No real disk, no real instance, no network.  tar is the only real program run, on those temporary folders.
"""
from datetime import datetime, timedelta, timezone
import fcntl
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import device_backup as db
import device_backup_items as dbi
import storage_migrate
import storage_roles as sr

ENTRY = Path(__file__).resolve().parents[1] / 'index.js'
DEVICE = 'test-device'
START = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
BACKUP_FS, OTHER_FS = 'bkup-0001', 'othr-0001'


class Machine(sr.Io):
    """A made-up machine: a system disk and a fixed disk with two partitions, each a temporary folder."""

    def __init__(self, tmp):
        self.records = tmp / 'etc-storage' / 'device-records.json'
        self.folders = {name: tmp / name for name in ('system', 'first', 'second')}
        for folder in self.folders.values(): folder.mkdir()
        self.disks = [
            dict(name='sda', type='disk', size=128000, serial='S1', model='System', children=[
                dict(name='sda2', type='part', size=127000, fstype='ext4', uuid='root-0001', mountpoints=['/'])]),
            dict(name='sdb', type='disk', size=500000, serial='S2', model='Fixed', children=[
                dict(name='sdb1', type='part', size=250000, fstype='ext4', uuid=BACKUP_FS, mountpoints=[str(self.folders['first'])]),
                dict(name='sdb2', type='part', size=250000, fstype='ext4', uuid=OTHER_FS, mountpoints=[str(self.folders['second'])])])]
        self.mount_rows = [dict(target=str(self.folders['system']), fsroot='/', uuid='root-0001', options='rw'),
                           dict(target=str(self.folders['first']), fsroot='/', uuid=BACKUP_FS, options='rw,relatime'),
                           dict(target=str(self.folders['second']), fsroot='/', uuid=OTHER_FS, options='rw')]
        self.said = []

    def tree(self): return dict(blockdevices=self.disks)
    def datasets(self): return []
    def usage(self, path): return 1000, 400
    def is_root(self): return True
    def which(self, program): return None       # a machine with no program on its path; never the real one
    def is_mount(self, path): return True
    def now(self): return '2026-01-01T00:00:00+00:00'
    def say(self, line=''): self.said.append(line)
    def machine(self): return DEVICE
    def mounts(self): return list(self.mount_rows)


class GateServer:
    """The write gate of one made-up instance, with the answers of polariApiServer/quiesce.py."""

    def __init__(self):
        self.up, self.name, self.receipt, self.calls = False, '', {}, []
        self.flush_after, self.flush_error, self.down = 1, None, False

    def get(self, path):
        self.calls.append('status')
        if self.down: raise storage_migrate.HttpError('curl exit 7: no answer')
        if self.up and self.receipt.get('phase') == 'flushing':
            self.receipt['polls'] = self.receipt.get('polls', 0) + 1
            if self.flush_after is not None and self.receipt['polls'] >= self.flush_after:
                self.receipt = dict(phase='flush-failed', persisted=False, error=self.flush_error) if self.flush_error else \
                    dict(phase='done', persisted=True, flushSeconds=0.5, error=None)
        return dict(ok=True, quiesced=self.up, reason='', moveName=self.name, engagedAt=None, receipt=dict(self.receipt))

    def post(self, path, payload):
        if self.down: raise storage_migrate.HttpError('curl exit 7: no answer')
        if path.endswith('/release'):
            self.calls.append('release'); self.up, self.name, self.receipt = False, '', {}
            return 200, '{}'
        self.calls.append('raise ' + payload['moveName'])
        if self.up: return (200, '{}') if payload['moveName'] == self.name else (409, '{}')
        self.up, self.name, self.receipt = True, payload['moveName'], dict(phase='flushing')
        return 200, '{}'


class Http:
    def __init__(self, world): self.world = world

    def _server(self, url, resolve):
        storage_migrate.check_url(url)
        self.world.routes.append(resolve)
        base, _, path = url.partition('/api/')
        if resolve is None and base in self.world.unresolved: raise storage_migrate.HttpError('curl exit 6: name not found')
        return self.world.gates[base], '/api/' + path

    def get_json(self, url, resolve=None):
        server, path = self._server(url, resolve)
        return server.get(path)

    def post_json(self, url, payload, token=None, resolve=None):
        server, path = self._server(url, resolve)
        return server.post(path, payload)


class World(db.Deps):
    """A made-up device: one instance record with a running backend and a data volume."""
    hold_limit, poll_interval, reserve = 60, 5, 1024

    def __init__(self, tmp, backup_partition=False):
        self.tmp = Path(tmp)
        self.settings, self.state, self.volumes = self.tmp / 'settings', self.tmp / 'state', self.tmp / 'volumes'
        self.polari_dir, self.lock_file = self.settings / 'polari', self.tmp / 'locks' / 'app-device-backup.lock'
        for path, text in (('device-name', DEVICE), ('ca/isle-root.key', 'the CA key'), ('apt/gnupg/key', 'signing key'),
                           ('apt/repo/pool/big.deb', 'x' * 4000), ('apps/one/release.json', '{}')):
            self._put(self.settings / path, text)
        for path, text in (('desired-release.json', '{}'), ('artifacts/a/blob', 'y' * 3000), ('operations/op/status.json', '{}'),
                           ('hotfix/receipt.json', '{}')):
            self._put(self.state / path, text)
        self.machine = Machine(self.tmp)
        self.said, self.root, self.clock, self.ticks, self.free_bytes, self.op = [], True, START, 0, 10 ** 9, 'b' * 32
        self.containers, self.volume_rows, self.gates, self.unresolved, self.routes, self.archived = {}, {}, {}, set(), [], []
        self.spoil = None
        self.add_instance('alpha')
        self.volume_rows['some-other-stack_db'] = dict(Driver='local', Mountpoint=str(self.volumes / 'unrelated'))
        self.volume_rows['a' * 64] = dict(Driver='local', Mountpoint=str(self.volumes / 'anonymous'))
        if backup_partition: self.assign(BACKUP_FS, 'backup')

    @staticmethod
    def _put(path, text):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text)

    def add_instance(self, name, role='backend', running=True, api=None, record=True, container=None, driver='local'):
        api = api or 'https://api.%s.isle' % name
        volume, folder = 'prf-%s_backend-data' % name, self.volumes / name
        self._put(folder / 'polari.db', 'rows of ' + name)
        if record: self._put(self.polari_dir / name / 'deployment.json', json.dumps(dict(name=name, role=role, backend_url=api, modules='islemesh')))
        self.volume_rows[volume] = dict(Driver=driver, Mountpoint=str(folder))
        self.containers[container or 'prf-%s-backend' % name] = dict(State=dict(Running=running), Mounts=[
            dict(Type='bind', Source='/somewhere', Destination='/etc/trust'),
            dict(Type='volume', Name=volume, Driver=driver, Source=str(folder), Destination='/data')])
        self.gates[api] = GateServer()
        return self.gates[api]

    def assign(self, identifier, role):
        if sr.register(self.machine, 'S2', 'fixed') not in (0, None) and not self.machine.records.exists(): raise AssertionError(self.machine.said)
        if sr.assign(self.machine, identifier, role) != 0: raise AssertionError(self.machine.said)

    def say(self, line=''): self.said.append(line)
    def is_root(self): return self.root
    def now(self): return self.clock
    def time(self): return self.ticks
    def sleep(self, seconds): self.ticks += seconds
    def operation(self): return self.op
    def operation_dir(self): return None
    def free(self, path): return self.free_bytes
    def device(self): return DEVICE
    def roles_io(self): return self.machine
    def http(self): return Http(self)
    def versions(self): return {'isle-mesh-cli': '0.0.0+test'}

    def docker(self, args):
        if args[:2] == ['container', 'inspect']: return (0, json.dumps([self.containers[args[2]]])) if args[2] in self.containers else (1, '[]')
        if args[:2] == ['volume', 'inspect']: return (0, json.dumps([self.volume_rows[args[2]]])) if args[2] in self.volume_rows else (1, '[]')
        if args[:2] == ['volume', 'ls']: return 0, '\n'.join(self.volume_rows) + '\n'
        raise AssertionError(args)

    def archive(self, source, left_out, target):
        self.archived.append((Path(source).name, {api: gate.up for api, gate in self.gates.items()}))
        done = super().archive(source, left_out, target)
        if self.spoil: return self.spoil(done, target)
        return done

    def run(self, *argv):
        self.said = []
        return db.main(list(argv), self)

    def base(self): return Path(db.home(self)['path']) / DEVICE
    def kept(self): return {doc['id']: doc['slot'] for _, doc, state in db.held(self.base(), DEVICE) if state == 'complete'}
    def text(self): return '\n'.join(self.said)


def names_in(archive):
    with tarfile.open(archive) as tar: return set(tar.getnames())


class DeviceBackup(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.w = World(self.dir.name)

    def make(self, world=None, hours=0, days=0):
        world = world or self.w
        world.clock = world.clock + timedelta(hours=hours, days=days)
        code = world.run('create', '--yes')
        self.assertEqual(code, 0, world.text())
        return max(world.kept())

    # ---- plan

    def test_plan_lists_everything_and_writes_nothing(self):
        before = sorted(str(p) for p in Path(self.dir.name).rglob('*'))
        self.assertEqual(self.w.run('plan'), 0)
        text = self.w.text()
        for part in ('Device-backup of ' + DEVICE, 'isle-settings', 'isle-state', 'polari-alpha', 'behind the write gate of instance alpha',
                     'left out: apt/repo', 'left out: artifacts', 'left out: operations', 'not covered: volume some-other-stack_db',
                     'no partition with the role backup', 'Ready.', 'Kept now: none'):
            self.assertIn(part, text)
        self.assertNotIn('a' * 64, text)                 # an anonymous volume is not named
        self.assertEqual(before, sorted(str(p) for p in Path(self.dir.name).rglob('*')))
        self.assertEqual(self.w.gates['https://api.alpha.isle'].calls, [])     # a plan never touches the write gate
        self.assertEqual(self.w.run('plan', '--json'), 0)
        report = json.loads(self.w.text())
        self.assertEqual([i['name'] for i in report['items']], ['isle-settings', 'isle-state', 'polari-alpha'])
        self.assertTrue(report['ready'] and report['home']['kind'] == 'local' and report['held'] == [])
        self.w.free_bytes = 10
        self.w.run('plan'); self.assertIn('NOT ready.', self.w.text())

    # ---- create

    def test_create_writes_one_complete_device_backup(self):
        ident = self.make()
        folder = self.w.base() / ident
        doc = json.loads((folder / db.MANIFEST).read_text())
        self.assertTrue(doc['complete'] and doc['slot'] == 'release' and doc['device'] == DEVICE and doc['kind'] == db.KIND)
        self.assertEqual([i['name'] for i in doc['items']], ['isle-settings', 'isle-state', 'polari-alpha'])
        self.assertEqual(doc['instances'], ['alpha'])
        self.assertEqual(doc['home'], dict(kind='local', filesystemUuid=None))
        self.assertEqual([r['name'] for r in doc['notCovered']], ['volume some-other-stack_db'])
        self.assertEqual(db.reread(folder, doc), [])
        for item in doc['items']:
            self.assertEqual(stat.S_IMODE((folder / item['file']).stat().st_mode), 0o600)
            self.assertRegex(item['sha256'], r'^[0-9a-f]{64}$')
        self.assertEqual(stat.S_IMODE((folder / db.MANIFEST).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        settings, state, volume = (names_in(folder / i['file']) for i in doc['items'])
        self.assertTrue({'./ca/isle-root.key', './apt/gnupg/key', './device-name', './polari/alpha/deployment.json'} <= settings)
        self.assertFalse(any(n.startswith('./apt/repo') for n in settings))
        self.assertTrue({'./desired-release.json', './hotfix/receipt.json'} <= state)
        self.assertFalse(any(n.startswith(('./artifacts', './operations', './' + db.FOLDER)) for n in state))
        self.assertIn('./polari.db', volume)
        self.assertEqual(doc['items'][2]['source'], 'prf-alpha_backend-data')
        self.assertEqual(doc['items'][2]['hold'], dict(name=db.HOLD_PREFIX + 'b' * 32, persisted=True, flushSeconds=0.5))
        self.assertEqual(doc['operationId'], 'b' * 32)

    def test_the_volume_is_copied_only_while_the_gate_is_up_and_the_gate_ends_down(self):
        gate = self.w.gates['https://api.alpha.isle']
        gate.flush_after = 3
        self.make()
        states = dict(self.w.archived)
        self.assertEqual(states['alpha'], {'https://api.alpha.isle': True})
        self.assertEqual(states['settings'], {'https://api.alpha.isle': False})
        self.assertFalse(gate.up)
        self.assertEqual(gate.calls[0], 'status')
        self.assertTrue(gate.calls[1].startswith('raise ' + db.HOLD_PREFIX))
        self.assertEqual(gate.calls[-1], 'release')
        self.assertEqual(self.w.ticks, 10)                # two waits of poll_interval before the third status said flushed

    def test_an_instance_that_is_not_running_is_copied_without_a_gate(self):
        self.w.containers['prf-alpha-backend']['State']['Running'] = False
        ident = self.make()
        doc = json.loads((self.w.base() / ident / db.MANIFEST).read_text())
        self.assertIn('not running', doc['items'][2]['method'])
        self.assertNotIn('hold', doc['items'][2])
        self.assertEqual(self.w.gates['https://api.alpha.isle'].calls, [])

    def test_a_stateless_engine_counts_as_flushed(self):
        gate = self.w.gates['https://api.alpha.isle']
        gate.flush_error = 'no persistence layer to flush (stateless)'
        ident = self.make()
        self.assertFalse(json.loads((self.w.base() / ident / db.MANIFEST).read_text())['items'][2]['hold']['persisted'])

    def test_which_instances_are_taken(self):
        self.w.add_instance('front', role='frontend')                          # holds no data: not an item, not a complaint
        self.w.add_instance('isle', record=False, container=dbi.ISLE_BACKEND, api=dbi.ISLE_API)   # the isle's own instance
        self.w._put(self.w.polari_dir / 'broken' / 'deployment.json', '{not json')
        self.w._put(self.w.polari_dir / 'gone' / 'deployment.json', json.dumps(dict(role='backend', backend_url='https://api.gone.isle')))
        self.w.add_instance('remote', driver='nfs')
        items, missing = dbi.listing(self.w)
        self.assertEqual([i['name'] for i in items], ['isle-settings', 'isle-state', 'polari-isle', 'polari-alpha'])
        why = {row['name']: row['why'] for row in missing}
        self.assertIn('cannot be read', why['instance broken'])
        self.assertIn('no container prf-gone-backend and no data volume', why['instance gone'])
        self.assertIn('not a local Docker volume', why['instance remote'])
        self.assertNotIn('instance front', why)
        self.assertIn('volume prf-front_backend-data', why)                    # its volume exists and nothing covers it: said, not dropped

    def test_a_removed_container_still_has_its_volume_copied(self):
        del self.w.containers['prf-alpha-backend']
        items, missing = dbi.listing(self.w)
        self.assertEqual((items[2]['name'], items[2]['running'], items[2]['volume']), ('polari-alpha', False, 'prf-alpha_backend-data'))

    def test_the_isles_own_instance_is_asked_on_this_machine_when_its_name_does_not_resolve(self):
        gate = self.w.add_instance('isle', record=False, container=dbi.ISLE_BACKEND, api=dbi.ISLE_API)
        self.w.unresolved.add(dbi.ISLE_API)
        self.make()
        self.assertIn(dbi.ISLE_API_LOCAL, self.w.routes)
        self.assertEqual(gate.calls[-1], 'release')
        self.assertFalse(gate.up)

    # ---- the write gate refuses or fails

    def test_a_gate_held_for_something_else_is_not_touched(self):
        earlier = self.make()
        gate = self.w.gates['https://api.alpha.isle']
        gate.up, gate.name, gate.calls = True, 'relocation-7', []
        self.w.clock += timedelta(days=1)
        self.assertEqual(self.w.run('create', '--yes'), 1)
        self.assertIn('already held for something else (relocation-7)', self.w.text())
        self.assertIn('NOT TAKEN', self.w.text())
        self.assertEqual(gate.calls, ['status', 'status'])           # asked before and after; never raised, never lowered
        self.assertTrue(gate.up and gate.name == 'relocation-7')
        self.assertEqual(self.w.kept(), {earlier: 'release'})        # the earlier one is untouched; the new one is not counted
        states = [state for _, _, state in db.held(self.w.base())]
        self.assertEqual(sorted(states), ['complete', 'incomplete'])

    def test_a_gate_left_up_by_an_interrupted_device_backup_is_taken_over(self):
        gate = self.w.gates['https://api.alpha.isle']
        gate.up, gate.name, gate.receipt = True, db.HOLD_PREFIX + 'an-earlier-run', dict(phase='done', persisted=True)
        self.make()
        self.assertEqual(gate.calls[:3], ['status', 'release', gate.calls[2]])
        self.assertTrue(gate.calls[2].startswith('raise ' + db.HOLD_PREFIX) and 'an-earlier-run' not in gate.calls[2])
        self.assertFalse(gate.up)

    def test_a_retry_of_the_same_operation_resumes_its_own_hold(self):
        gate = self.w.gates['https://api.alpha.isle']
        self.w.op = 'c' * 32
        gate.up, gate.name, gate.receipt = True, db.HOLD_PREFIX + 'c' * 32, dict(phase='done', persisted=True)
        ident = self.make()
        self.assertTrue(ident.endswith('-cccccccc'))
        self.assertNotIn('release', gate.calls[:-1])
        self.assertFalse(gate.up)

    def test_a_flush_that_fails_or_never_ends_fails_the_item_and_lowers_the_gate(self):
        for setup, words in ((dict(flush_error='disk full'), 'could not flush its data: disk full'),
                             (dict(flush_after=None), 'had not flushed its data after 1 minutes')):
            with self.subTest(words=words):
                world = World(tempfile.mkdtemp(dir=self.dir.name))
                gate = world.gates['https://api.alpha.isle']
                for key, value in setup.items(): setattr(gate, key, value)
                self.assertEqual(world.run('create', '--yes'), 1)
                self.assertIn(words, world.text())
                self.assertFalse(gate.up)
                self.assertEqual(gate.calls[-1], 'release')
                self.assertEqual(world.kept(), {})
                self.assertNotIn('alpha', [name for name, _ in world.archived])     # nothing was copied from it

    def test_an_instance_that_does_not_answer_fails_the_item(self):
        self.w.gates['https://api.alpha.isle'].down = True
        self.assertEqual(self.w.run('create', '--yes'), 1)
        self.assertIn('did not answer at its API', self.w.text())
        self.assertIn('Its write gate may still be up', self.w.text())
        self.assertIn('sudo isle backup release-hold', self.w.text())
        self.assertEqual(self.w.kept(), {})

    def test_a_backend_address_outside_the_isle_is_never_asked(self):
        self.w.add_instance('beta', api='https://example.com')
        self.assertEqual(self.w.run('create', '--yes'), 1)
        self.assertIn('polari-beta', self.w.text())
        self.assertIn('NOT TAKEN', self.w.text())
        self.assertEqual(self.w.gates['https://example.com'].calls, [])

    def test_a_file_that_changes_behind_the_gate_fails_the_item(self):
        self.w.spoil = lambda done, target: (done[0], done[1], True) if 'alpha' in str(target) else done
        self.assertEqual(self.w.run('create', '--yes'), 1)
        self.assertIn('changed while it was copied behind the write gate', self.w.text())
        self.assertFalse(self.w.gates['https://api.alpha.isle'].up)

    # ---- complete only when read back

    def test_a_file_that_reads_back_differently_leaves_it_incomplete_and_rotates_nothing(self):
        earlier = self.make()
        def spoil(done, target):
            if 'isle-state' in str(target): Path(target).write_bytes(b'not what was written')
            return done
        self.w.spoil = spoil
        self.w.clock += timedelta(days=1)
        self.assertEqual(self.w.run('create', '--yes'), 1)
        self.assertIn('re-read: isle-state', self.w.text())
        self.assertIn('incomplete', self.w.text())
        self.assertEqual(self.w.kept(), {earlier: 'release'})
        self.assertFalse(self.w.gates['https://api.alpha.isle'].up)

    def test_too_little_room_is_refused_before_anything_is_written(self):
        self.w.free_bytes = 500
        self.assertEqual(self.w.run('create', '--yes'), 1)
        self.assertIn('Refused:', self.w.text())
        self.assertIn('MiB are needed', self.w.text())
        self.assertFalse((self.w.state / db.FOLDER).exists())
        self.assertEqual(self.w.gates['https://api.alpha.isle'].calls, [])

    def test_create_needs_root_and_the_word_yes(self):
        self.w.root = False
        self.assertEqual(self.w.run('create', '--yes'), 1)
        self.assertIn('sudo isle backup create --yes', self.w.text())
        self.w.root, self.w.op = True, None            # not through the journal: no lock, so nothing is taken
        self.assertEqual(self.w.run('create', '--yes'), 1)
        self.assertIn('through the operation journal', self.w.text())
        self.w.op = 'b' * 32
        for argv in (['create'], ['create', '--yes', '--slot', 'long-term'], ['create', '--force']):
            self.assertEqual(self.w.run(*argv), 2, argv)
        self.assertFalse(self.w.base().exists())

    # ---- two are kept

    def test_the_two_slot_rule_over_four_releases(self):
        first = self.make()
        self.assertEqual(self.w.kept(), {first: 'release'})
        second = self.make(days=1)                     # no long-term one yet: the previous one becomes it
        self.assertEqual(self.w.kept(), {first: 'long-term', second: 'release'})
        third = self.make(days=2)                      # the long-term one is 3 days old: the previous one is deleted
        self.assertEqual(self.w.kept(), {first: 'long-term', third: 'release'})
        self.assertIn('Deleted %s (release)' % second, self.w.text())
        fourth = self.make(days=4)                     # the long-term one is 7 days old: the previous one replaces it
        self.assertEqual(self.w.kept(), {third: 'long-term', fourth: 'release'})
        self.assertIn('The previous one, %s, is now the long-term one.' % third, self.w.text())
        self.assertEqual(sorted(p.name for p in self.w.base().iterdir()), [third, fourth])
        promoted = json.loads((self.w.base() / third / db.MANIFEST).read_text())
        self.assertEqual(db.reread(self.w.base() / third, promoted), [])       # changing the slot leaves the files as they were

    def test_rotation_table(self):
        def doc(ident, slot, days): return dict(id=ident, slot=slot, createdAt=(START - timedelta(days=days)).isoformat())
        self.assertEqual(db.rotation([], START), (None, []))
        self.assertEqual(db.rotation([doc('r', 'release', 1)], START), ('r', []))
        self.assertEqual(db.rotation([doc('l', 'long-term', 3)], START), (None, []))
        self.assertEqual(db.rotation([doc('r', 'release', 1), doc('l', 'long-term', 6.99)], START), (None, ['r']))
        self.assertEqual(db.rotation([doc('r', 'release', 1), doc('l', 'long-term', 7)], START), ('r', ['l']))
        keep, delete = db.rotation([doc('r1', 'release', 2), doc('r2', 'release', 1), doc('l1', 'long-term', 30), doc('l2', 'long-term', 9)], START)
        self.assertEqual((keep, sorted(delete)), ('r2', ['l1', 'l2', 'r1']))    # leftovers of an interrupted run never grow the count

    def test_an_incomplete_one_is_removed_by_the_next_create_and_a_damaged_one_is_left(self):
        first = self.make()
        base = self.w.base()
        half, damaged = base / '20251231T000000Z-00000001', base / '20251230T000000Z-00000002'
        for folder in (half, damaged): folder.mkdir()
        (half / '01-isle-settings.tar.gz').write_bytes(b'half')
        (damaged / db.MANIFEST).write_text('{not json')
        self.assertEqual(sorted(s for _, _, s in db.held(base)), ['complete', 'damaged', 'incomplete'])
        second = self.make(days=1)
        self.assertFalse(half.exists())
        self.assertTrue(damaged.exists())
        self.assertEqual(self.w.kept(), {first: 'long-term', second: 'release'})
        other = base / '20251229T000000Z-00000003'      # a device-backup of another device is never counted as this one's
        other.mkdir()
        (other / db.MANIFEST).write_text(json.dumps(dict(kind=db.KIND, version=1, id=other.name, slot='release', device='another',
                                                         createdAt=START.isoformat(), items=[], complete=True)))
        self.make(days=1)
        self.assertTrue(other.exists())

    # ---- where they live

    def test_the_partition_with_the_role_backup_is_the_home(self):
        world = World(tempfile.mkdtemp(dir=self.dir.name), backup_partition=True)
        where = db.home(world)
        self.assertEqual(where, dict(kind='partition', path=str(world.machine.folders['first'] / db.FOLDER), filesystemUuid=BACKUP_FS))
        ident = self.make(world)
        folder = world.machine.folders['first'] / db.FOLDER / DEVICE / ident
        self.assertTrue((folder / db.MANIFEST).is_file())
        self.assertEqual(json.loads((folder / db.MANIFEST).read_text())['home'], dict(kind='partition', filesystemUuid=BACKUP_FS))
        self.assertFalse((world.state / db.FOLDER).exists())
        self.assertTrue((world.machine.folders['first'] / sr.MARKER).is_file())        # the marker is beside it, untouched

    def test_a_backup_partition_that_is_not_usable_is_refused_and_never_replaced_by_the_local_folder(self):
        cases = []
        away = World(tempfile.mkdtemp(dir=self.dir.name), backup_partition=True)
        away.machine.mount_rows = [m for m in away.machine.mount_rows if m['uuid'] != BACKUP_FS]
        away.machine.disks[1]['children'][0]['mountpoints'] = [None]
        cases.append((away, 'is not mounted'))
        read_only = World(tempfile.mkdtemp(dir=self.dir.name), backup_partition=True)
        read_only.machine.mount_rows[1]['options'] = 'ro,relatime'
        cases.append((read_only, 'mounted read-only'))
        unmarked = World(tempfile.mkdtemp(dir=self.dir.name), backup_partition=True)
        (unmarked.machine.folders['first'] / sr.MARKER).unlink()
        cases.append((unmarked, 'marker missing'))
        two = World(tempfile.mkdtemp(dir=self.dir.name), backup_partition=True)
        two.assign(OTHER_FS, 'backup')
        cases.append((two, 'records 2 partitions with the role backup'))
        damaged = World(tempfile.mkdtemp(dir=self.dir.name), backup_partition=True)
        damaged.machine.records.write_text('{not json')
        cases.append((damaged, 'could not be read'))
        for world, words in cases:
            with self.subTest(words=words):
                self.assertEqual(world.run('create', '--yes'), 1)
                self.assertIn('Refused:', world.text())
                self.assertIn(words, world.text())
                self.assertFalse((world.state / db.FOLDER).exists())
                self.assertEqual(world.gates['https://api.alpha.isle'].calls, [])
                self.assertEqual(world.run('plan'), 1)

    def test_a_partition_with_another_role_is_not_a_home(self):
        self.w.assign(OTHER_FS, 'git')
        self.assertEqual(db.home(self.w)['kind'], 'local')

    def test_a_partition_that_carries_backup_beside_another_role_is_the_home(self):
        self.w.assign(OTHER_FS, 'git')
        self.w.assign(OTHER_FS, 'backup')
        self.assertEqual((db.home(self.w)['kind'], db.home(self.w)['filesystemUuid']), ('partition', OTHER_FS))
        both = World(tempfile.mkdtemp(dir=self.dir.name))
        both.assign(BACKUP_FS, 'registry,backup')
        self.assertEqual((db.home(both)['kind'], db.home(both)['filesystemUuid']), ('partition', BACKUP_FS))

    # ---- list, verify, release-hold

    def test_list_and_verify(self):
        self.assertEqual(self.w.run('list'), 0)
        self.assertIn('none', self.w.text())
        first, second = self.make(), self.make(days=1)
        self.assertEqual(self.w.run('list'), 0)
        lines = [line for line in self.said_lines() if line.startswith('  2026')]
        self.assertEqual([(l.split()[0], l.split()[1], l.split()[2]) for l in lines], [(first, 'long-term', 'complete'), (second, 'release', 'complete')])
        self.assertEqual(self.w.run('list', '--json'), 0)
        self.assertEqual([r['slot'] for r in json.loads(self.w.text())['deviceBackups']], ['long-term', 'release'])
        self.assertEqual(self.w.run('verify', second), 0)
        self.assertIn('every file matches its record (3 files)', self.w.text())
        doc = json.loads((self.w.base() / second / db.MANIFEST).read_text())
        target = self.w.base() / second / doc['items'][1]['file']
        raw = bytearray(target.read_bytes()); raw[-1] ^= 1; target.write_bytes(bytes(raw))
        self.assertEqual(self.w.run('verify', second), 1)
        self.assertIn('isle-state', self.w.text())
        self.assertIn('DOES NOT MATCH', self.w.text())
        (self.w.base() / first / doc['items'][0]['file'].replace(second, first)).unlink()
        self.assertEqual(self.w.run('verify', first), 1)
        self.assertIn('cannot be read', self.w.text())
        for argv in (['verify'], ['verify', '../' + first], ['verify', 'x'], ['list', '--all'], ['nonsense']):
            self.assertEqual(self.w.run(*argv), 2, argv)
        self.assertEqual(self.w.run('verify', '20200101T000000Z-00000000'), 1)
        self.assertIn('no device-backup', self.w.text())

    def said_lines(self): return list(self.w.said)

    def test_verify_does_not_offer_an_incomplete_one(self):
        ident = self.make()
        path = self.w.base() / ident / db.MANIFEST
        doc = json.loads(path.read_text()); doc['complete'] = False; path.write_text(json.dumps(doc))
        self.assertEqual(self.w.run('verify', ident), 1)
        self.assertIn('is incomplete: it is not offered for a restore', self.w.text())

    def test_release_hold_lowers_only_a_device_backup_hold(self):
        gate = self.w.gates['https://api.alpha.isle']
        self.w.add_instance('stopped', running=False)
        self.assertEqual(self.w.run('release-hold'), 0)
        self.assertIn('alpha: no write gate of a device-backup is up.', self.w.text())
        self.assertIn('stopped: not running, nothing held.', self.w.text())
        gate.up, gate.name = True, 'relocation-7'
        self.assertEqual(self.w.run('release-hold'), 0)
        self.assertTrue(gate.up)
        gate.name = db.HOLD_PREFIX + 'old'
        self.assertEqual(self.w.run('release-hold'), 0)
        self.assertFalse(gate.up)
        self.assertIn('lowered the write gate left by %sold' % db.HOLD_PREFIX, self.w.text())
        gate.down = True
        self.assertEqual(self.w.run('release-hold'), 1)
        self.w.root = False
        self.assertEqual(self.w.run('release-hold'), 1)
        self.assertIn('sudo isle backup release-hold', self.w.text())
        self.assertEqual(self.w.run('release-hold', 'x'), 2)

    def test_release_hold_refuses_while_a_device_backup_runs(self):
        gate = self.w.gates['https://api.alpha.isle']
        gate.up, gate.name = True, db.HOLD_PREFIX + 'running'
        self.w.lock_file.parent.mkdir(parents=True)
        with open(self.w.lock_file, 'a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.w.run('release-hold'), 1)
        self.assertIn('a device-backup is running now', self.w.text())
        self.assertTrue(gate.up)

    # ---- the pieces around it

    def test_measure_counts_files_and_skips_what_is_left_out(self):
        self.assertEqual(dbi.measure(self.w.settings, ['apt/repo'])[1], 0)
        with_repo, without = dbi.measure(self.w.settings)[0], dbi.measure(self.w.settings, ['apt/repo'])[0]
        self.assertEqual(with_repo - without, 4000)
        self.assertEqual(dbi.measure(self.w.tmp / 'not-there'), (0, 1))

    def test_the_real_post_takes_the_same_route_as_the_real_get(self):
        seen = []
        def run(argv, **kw):
            seen.append(argv)
            return subprocess.CompletedProcess(argv, 0, b'{}\n200', b'')
        http = storage_migrate.CurlHttp(ca='/nowhere/ca.crt', run=run)
        self.assertEqual(http.post_json(dbi.ISLE_API + '/api/quiesce', {}, resolve=dbi.ISLE_API_LOCAL)[0], 200)
        argv = seen[0]
        self.assertEqual(argv[argv.index('--resolve') + 1], 'api.polari.isle:443:127.0.0.1')
        self.assertIn('--cacert', argv)
        self.assertFalse({'-k', '--insecure'} & set(argv))
        with self.assertRaises(ValueError): http.post_json('https://10.0.0.1/api/quiesce', {})
        with self.assertRaises(ValueError): http.post_json(dbi.ISLE_API + '/x', {}, resolve=('other.isle', '127.0.0.1'))

    def test_the_verb_is_reachable_through_the_cli(self):
        done = subprocess.run(['node', str(ENTRY), 'backup', 'help'], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('isle backup: device-backups of this machine', done.stdout)
        text = (Path(__file__).resolve().parent / 'backup.sh').read_text()
        self.assertIn('operations.py" run backup "$@"', text)          # create goes through the journal, never straight to the code

    def test_no_machine_name_address_or_mount_point_is_written_into_the_code(self):
        for name in ('device_backup.py', 'device_backup_items.py', 'backup.sh'):
            text = (Path(__file__).resolve().parent / name).read_text()
            self.assertNotRegex(text, r'/mnt/|/home/|/media/')
            self.assertNotRegex(text, r'\b(?:\d{1,3}\.){3}\d{1,3}\b(?<!127\.0\.0\.1)')


if __name__ == '__main__':
    unittest.main()
