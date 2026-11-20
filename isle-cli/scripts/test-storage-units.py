"""isle storage units check | apply: temp folders and a recorded systemctl; no real unit is read or written."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import storage_contract as sc
import storage_mount
import storage_units as su

ENTRY = Path(__file__).resolve().parents[1] / 'index.js'
D = 'apt-public-repo'
GATE, SERVICE, TIMER = 'isle-storage-%s.service' % D, 'isle-storage-sync@%s.service' % D, 'isle-storage-sync@%s.timer' % D
BINDING = dict(dataset=D, mountpoint='/mnt/archive', relativePath='isle/datasets/' + D, hostApp='isle-content-host',
               filesystemUuid='fs-1')


class Case(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root, self.units = Path(self.temp.name) / 'state', Path(self.temp.name) / 'units'
        self.units.mkdir()
        self.bind(BINDING)
        self.enabled, self.active, self.calls, self.fail = {GATE, TIMER}, {GATE, TIMER}, [], None
        self.lines = []

    def bind(self, record, dataset=D):
        (self.root / dataset).mkdir(parents=True, exist_ok=True)
        (self.root / dataset / 'binding.json').write_text(json.dumps(record))

    def systemctl(self, args):
        self.calls.append(list(args))
        if args[0] == 'is-enabled':
            return 0 if args[-1] in self.enabled else 1
        if args[0] == 'is-active':
            return 0 if args[-1] in self.active else 3
        if self.fail and args[0] == self.fail:
            return 5
        if args[0] == 'enable':
            self.enabled.add(args[-1])
        return 0

    def install(self):
        for name, text in su.expected(BINDING).items():
            (self.units / name).write_text(text)

    def check(self, dataset=D):
        return su.check(dataset, root=str(self.root), unit_dir=str(self.units), systemctl=self.systemctl)

    def apply(self, euid=0, **kwargs):
        return su.apply(D, root=str(self.root), unit_dir=str(self.units), systemctl=self.systemctl, euid=lambda: euid, **kwargs)

    def main(self, *argv, euid=0):
        code = su.main(list(argv), root=str(self.root), unit_dir=str(self.units), systemctl=self.systemctl,
                       euid=lambda: euid, out=self.lines.append)
        return code, json.loads(self.lines[-1])

    def changes(self):
        return [c for c in self.calls if c[0] not in ('is-enabled', 'is-active')]


class Expected(Case):
    def test_same_text_as_the_migration_writes(self):
        texts = su.expected(BINDING)
        self.assertEqual(list(texts), [GATE, SERVICE, TIMER])
        self.assertEqual(texts[GATE], storage_mount.unit_text(D, '/mnt/archive', '/mnt/archive/isle/datasets/' + D,
                                                              'isle-content-host', 'isle-isle-content-host'))
        self.assertEqual((texts[SERVICE], texts[TIMER]), storage_mount.sync_timer_text(D))

    def test_incomplete_or_unsafe_binding_refused(self):
        for change in (dict(mountpoint=None), dict(hostApp=''), dict(relativePath='../x'), dict(relativePath='/'),
                       dict(mountpoint='relative'), dict(hostApp='bad name')):
            with self.assertRaises(ValueError, msg=change):
                su.expected(dict(BINDING, **change))


class Check(Case):
    def test_installed_units_match(self):
        self.install()
        ok, doc = self.check()
        self.assertTrue(ok)
        self.assertEqual([u['state'] for u in doc['units']], ['matches'] * 3)
        self.assertNotIn('enabled', doc['units'][1])
        self.assertTrue(doc['gateActive'] and doc['timerActive'])
        self.assertEqual(self.changes(), [])

    def test_each_fault_is_named(self):
        self.install()
        (self.units / GATE).write_text(su.expected(BINDING)[GATE].replace('/usr/local/bin/isle', '/usr/bin/isle'))
        (self.units / TIMER).unlink()
        os.unlink(self.units / SERVICE)
        os.symlink('/dev/null', self.units / SERVICE)
        ok, doc = self.check()
        self.assertFalse(ok)
        self.assertEqual([u['state'] for u in doc['units']], ['differs', 'a link, not a file', 'missing'])

    def test_not_enabled_fails_but_inactive_is_only_reported(self):
        self.install()
        self.active.clear()
        ok, doc = self.check()
        self.assertTrue(ok)
        self.assertFalse(doc['gateActive'])
        self.enabled.discard(TIMER)
        ok, doc = self.check()
        self.assertFalse(ok)
        self.assertEqual([u.get('enabled') for u in doc['units']], [True, None, False])

    def test_overrides_fail_the_check(self):
        self.install()
        (self.units / (GATE + '.d')).mkdir()
        (self.units / (GATE + '.d') / 'override.conf').write_text('[Service]\n')
        (self.units / 'isle-storage-sync@.service.d').mkdir()
        (self.units / 'isle-storage-sync@.service.d' / 'x.conf').write_text('[Service]\n')
        ok, doc = self.check()
        self.assertFalse(ok)
        self.assertEqual([len(u['overrides']) for u in doc['units']], [1, 1, 0])

    def test_unbound_and_wrong_names(self):
        ok, doc = self.check('other-set')
        self.assertEqual((ok, doc['bound']), (False, False))
        self.bind(dict(BINDING, dataset='elsewhere'), 'other-set')
        self.assertRaises(ValueError, self.check, 'other-set')
        self.assertRaises(ValueError, self.check, '../x')

    def test_reads_only(self):
        before = sorted(p.name for p in self.units.iterdir())
        self.check()
        self.assertEqual(sorted(p.name for p in self.units.iterdir()), before)
        self.assertEqual(self.changes(), [])


class Apply(Case):
    def test_needs_root_and_writes_nothing_without_it(self):
        self.assertRaises(PermissionError, self.apply, euid=1000)
        self.assertEqual((list(self.units.iterdir()), self.calls), ([], []))

    def test_writes_missing_units_and_enables(self):
        self.enabled.clear()
        ok, doc = self.apply()
        self.assertTrue(ok)
        self.assertEqual(doc['written'], [GATE, SERVICE, TIMER])
        self.assertEqual(self.changes(), [['daemon-reload'], ['enable', GATE], ['enable', '--now', TIMER]])
        self.assertEqual(oct((self.units / GATE).stat().st_mode & 0o777), '0o644')
        self.assertEqual([p.name for p in self.units.iterdir() if '.tmp-' in p.name], [])
        self.assertIn('not restarted', doc['note'])

    def test_second_run_writes_nothing(self):
        self.apply()
        ok, doc = self.apply()
        self.assertTrue(ok)
        self.assertEqual((doc['written'], doc['note']), ([], None))

    def test_never_starts_stops_or_restarts_the_host(self):
        self.apply()
        self.assertFalse([c for c in self.calls if c[0] in ('start', 'stop', 'restart', 'reload', 'disable')])

    def test_replaces_a_link_without_touching_its_target(self):
        target = Path(self.temp.name) / 'elsewhere'
        target.write_text('keep me')
        os.symlink(target, self.units / GATE)
        ok, _ = self.apply()
        self.assertTrue(ok)
        self.assertFalse(os.path.islink(self.units / GATE))
        self.assertEqual(target.read_text(), 'keep me')

    def test_overrides_kept_unless_asked(self):
        folder = self.units / (GATE + '.d')
        folder.mkdir()
        (folder / 'override.conf').write_text('[Service]\n')
        ok, doc = self.apply()
        self.assertFalse(ok)
        self.assertEqual((doc['overridesKept'], doc['overridesRemoved']), ([str(folder / 'override.conf')], []))
        self.assertTrue((folder / 'override.conf').exists())
        ok, doc = self.apply(drop_overrides=True)
        self.assertTrue(ok)
        self.assertEqual(doc['overridesRemoved'], [str(folder / 'override.conf')])
        self.assertFalse(folder.exists())

    def test_folder_in_place_of_a_unit_is_left_alone(self):
        (self.units / GATE).mkdir()
        self.assertRaises(ValueError, self.apply)
        self.assertEqual(self.changes(), [])

    def test_failed_systemctl_is_an_error_and_a_rerun_recovers(self):
        self.enabled.clear()
        self.fail = 'enable'
        self.assertRaises(RuntimeError, self.apply)
        self.fail = None
        ok, doc = self.apply()
        self.assertTrue(ok)
        self.assertEqual(doc['written'], [])

    def test_unbound_dataset_writes_nothing(self):
        (self.root / D / 'binding.json').unlink()
        ok, doc = self.apply()
        self.assertEqual((ok, doc['bound'], list(self.units.iterdir()), self.changes()), (False, False, [], []))


class Main(Case):
    def test_check_all_and_one(self):
        self.install()
        code, data = self.main('check')
        self.assertEqual((code, data['ok'], data['readOnly'], data['operation']), (0, True, True, 'storage.units.check'))
        self.assertEqual([d['dataset'] for d in data['result']['datasets']], [D])
        (self.units / TIMER).unlink()
        code, data = self.main('check', '--dataset', D)
        self.assertEqual((code, data['ok']), (1, False))

    def test_no_bound_dataset_is_an_empty_good_answer(self):
        code, data = su.main(['check'], root=str(self.root / 'absent'), unit_dir=str(self.units), systemctl=self.systemctl,
                             out=self.lines.append), None
        self.assertEqual((code, json.loads(self.lines[-1])['result']), (0, dict(datasets=[])))

    def test_apply_documents(self):
        code, data = self.main('apply', '--dataset', D, euid=1000)
        self.assertEqual((code, data['ok'], data['readOnly']), (1, False, False))
        self.assertIn('sudo isle storage units apply', data['error'])
        code, data = self.main('apply', '--dataset', D, '--overrides', 'remove')
        self.assertEqual((code, data['ok'], len(data['result']['written'])), (0, True, 3))

    def test_through_the_public_dispatch(self):
        env = dict(os.environ, ISLE_STORAGE_UNIT_DIR=str(self.units))
        result = subprocess.run(['node', str(ENTRY), 'storage', 'units', 'check', '--dataset', 'no-such-set'],
                                env=env, capture_output=True, text=True, timeout=20)
        data = json.loads(result.stdout)
        self.assertEqual((result.returncode, data['ok'], data['result']['bound']), (1, False, False))


class AfterUpdate(Case):
    def run_step(self, euid=0):
        return su.after_update(root=str(self.root), unit_dir=str(self.units), systemctl=self.systemctl,
                               euid=lambda: euid, lock_root=str(self.root / 'locks'), stamp='20261120T000000Z')

    def old_gate(self):
        text = su.expected(BINDING)[GATE].replace('/usr/local/bin/isle', '/usr/bin/isle')
        (self.units / GATE).write_text(text)
        return text

    def test_matching_units_are_left_alone(self):
        self.install()
        rows = self.run_step()
        self.assertEqual(rows, [dict(dataset=D, rewritten=[], attention=[])])
        self.assertEqual((self.changes(), su.after_update_lines(rows)), ([], []))
        self.assertFalse((self.root / D / su.REPLACED).exists())

    def test_a_differing_unit_is_rewritten_and_its_old_text_kept(self):
        self.install()
        old = self.old_gate()
        rows = self.run_step()
        kept = self.root / D / su.REPLACED / '20261120T000000Z'
        self.assertEqual(rows, [dict(dataset=D, rewritten=[GATE], attention=[], replacedTextKeptIn=str(kept))])
        self.assertEqual((self.units / GATE).read_text(), su.expected(BINDING)[GATE])
        self.assertEqual(((kept / GATE).read_text(), sorted(os.listdir(kept))), (old, [GATE]))
        self.assertEqual(self.changes(), [['daemon-reload']])  # no enable, no start, no restart
        line, = su.after_update_lines(rows)
        self.assertIn('rewrote ' + GATE, line)
        self.assertIn('nothing was restarted', line)
        self.assertEqual(self.run_step()[0]['rewritten'], [])  # a second run finds nothing to do

    def test_missing_link_not_enabled_and_override_are_named_not_changed(self):
        self.install()
        (self.units / TIMER).unlink()
        os.unlink(self.units / SERVICE)
        os.symlink('/dev/null', self.units / SERVICE)
        self.enabled.clear()
        (self.units / (GATE + '.d')).mkdir()
        (self.units / (GATE + '.d') / 'hand.conf').write_text('[Service]\n')
        rows = self.run_step()
        self.assertEqual(rows[0]['rewritten'], [])
        self.assertEqual(rows[0]['attention'], [GATE + ' is not enabled', GATE + ' has 1 hand override(s)',
                                                SERVICE + ' is a link, not a file', TIMER + ' is missing',
                                                TIMER + ' is not enabled'])
        self.assertEqual(self.changes(), [])
        self.assertTrue(os.path.islink(self.units / SERVICE) and not (self.units / TIMER).exists())
        self.assertIn('see: isle storage units check --dataset ' + D, su.after_update_lines(rows)[0])

    def test_a_held_dataset_is_skipped(self):
        self.install()
        self.old_gate()
        (self.root / 'locks').mkdir()
        with open(self.root / 'locks' / ('app-storage-%s.lock' % D), 'a+') as holder:
            fcntl.flock(holder, fcntl.LOCK_EX)
            rows = self.run_step()
        self.assertEqual((rows[0]['rewritten'], rows[0]['skipped']), ([], 'Another storage operation holds this dataset.'))
        self.assertEqual(self.changes(), [])
        self.assertIn('/usr/bin/isle', (self.units / GATE).read_text())

    def test_one_bad_binding_does_not_stop_the_next_dataset(self):
        self.install()
        self.old_gate()
        self.bind(dict(BINDING, dataset='aaa-broken', hostApp=''), dataset='aaa-broken')
        rows = self.run_step()
        self.assertEqual([r['dataset'] for r in rows], ['aaa-broken', D])
        self.assertIn('hostApp', rows[0]['error'])
        self.assertEqual(rows[1]['rewritten'], [GATE])

    def test_failed_reload_is_reported(self):
        self.install()
        self.old_gate()
        self.fail = 'daemon-reload'
        rows = self.run_step()
        self.assertIn('daemon-reload failed', rows[0]['error'])
        self.assertIn('fault:', su.after_update_lines(rows)[0])

    def test_the_step_always_exits_zero(self):
        self.install()
        self.old_gate()
        for euid, expected in ((1000, 'Storage units were not checked'), (0, 'Storage units of ' + D)):
            self.lines = []
            code = su.main(['after-update'], root=str(self.root), unit_dir=str(self.units), systemctl=self.systemctl,
                           euid=lambda e=euid: e, out=self.lines.append, lock_root=str(self.root / 'locks'))
            self.assertEqual(code, 0)
            self.assertIn(expected, self.lines[0])
        self.lines = []
        self.assertEqual(su.main(['after-update'], root=str(self.root / 'absent'), unit_dir=str(self.units),
                                 systemctl=self.systemctl, euid=lambda: 0, out=self.lines.append,
                                 lock_root=str(self.root / 'locks')), 0)
        self.assertEqual(self.lines, [])

    def test_only_check_and_apply_are_public(self):
        result = subprocess.run(['node', str(ENTRY), 'storage', 'units', 'after-update'], capture_output=True, text=True,
                                timeout=20)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Usage: isle storage units check', result.stderr)


class Request(unittest.TestCase):
    def test_journaled_shape(self):
        self.assertEqual(sc.parse_request(['storage', 'units-apply', '--dataset', D]),
                         dict(verb='units-apply', dataset=D, overrides='keep'))
        parsed = sc.parse_request(['storage', 'units-apply', '--dataset', D, '--overrides', 'remove'])
        self.assertEqual(parsed['overrides'], 'remove')
        self.assertEqual((sc.lock_name(parsed), sc.journal_name(parsed)), ('app-storage-%s.lock' % D, 'storage-' + D))

    def test_refused_shapes(self):
        for args in (['storage', 'units-apply'], ['storage', 'units-apply', '--dataset', '../x'],
                     ['storage', 'units-apply', '--dataset', D, '--overrides', 'wipe'],
                     ['storage', 'units-apply', '--dataset', D, '--force', 'yes'],
                     ['storage', 'units-apply', '--dataset', D, 'extra'],
                     ['storage', 'units-apply', '--overrides', 'keep', '--dataset', D],
                     ['storage', 'units', 'apply', '--dataset', D]):
            with self.assertRaises(ValueError, msg=args):
                sc.parse_request(args)


if __name__ == '__main__':
    unittest.main()
