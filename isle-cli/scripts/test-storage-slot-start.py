#!/usr/bin/env python3
"""What the start of an app does with its storage slots: the overlay, the check before a deploy, an install, a restart.

Fixtures only, the made-up machine of test-storage-slots.py: every "partition" is a temporary folder, and the apps
folder and the record are temporary too.  The deploy script is never run whole: only its slot lines, with a made-up
check.  No real disk, no real app, no container and no network is touched.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import app_overlay
import catalog_artifacts
import runtime_reconcile
import storage_roles as sr
import storage_slot_start as start
import storage_slots as ss

_spec = importlib.util.spec_from_file_location('storage_slots_fixtures', HERE / 'test-storage-slots.py')
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)
PROJECTS, LIBRARY, DOCK = base.PROJECTS, base.LIBRARY, base.DOCK

DEPLOY = (HERE / 'app-deploy.sh').read_text()
COMPOSE = {'services': {'app': {'image': 'x'}, 'viewer': {'image': 'y', 'volumes': ['kept:/var/kept']}}}
VIEWED = dict(LIBRARY, service='viewer')


def volume(source, target, read_only):
    return {'type': 'bind', 'source': str(source), 'target': target, 'read_only': read_only, 'bind': {'create_host_path': False}}


class Case(base.Case):
    """The app `kicad` declares `projects` for its first service and `library` for its service `viewer`; both bound."""

    def setUp(self):
        super().setUp()
        self.declare(PROJECTS, VIEWED)
        self.assertEqual(self.bind(), 0, self.m.text())
        self.assertEqual(self.bind('library'), 0, self.m.text())
        self.library = self.folder.parent / 'library'
        self.app = Path(self.m.apps_dir) / 'kicad'

    def overlay(self):
        return app_overlay.build(COMPOSE, 'app', None, ss.binds(self.m, 'kicad'))

    def deployed(self, overlay=None):
        (self.app / start.OVERLAY).write_text(yaml.safe_dump(overlay or self.overlay()))


class Overlay(Case):
    def test_each_slot_is_given_to_its_service(self):
        overlay = self.overlay()
        self.assertEqual(overlay['services']['app'], {
            'networks': ['isle-agent-net'], 'volumes': [volume(self.folder, '/home/app/projects', False)]})
        self.assertEqual(overlay['services']['viewer']['volumes'], [volume(self.library, '/usr/share/app/library', True)])
        self.assertNotIn('environment', overlay['services']['app'])          # that is the dataset bind's, not a slot's
        self.assertIn('create_host_path: false', yaml.safe_dump(overlay))
        self.assertEqual(app_overlay.build(COMPOSE, 'app'), app_overlay.build(COMPOSE, 'app', None, []))

    def test_a_slot_beside_the_dataset_bind(self):
        overlay = app_overlay.build(COMPOSE, 'app', '/mnt/disk/data', ss.binds(self.m, 'kicad'))
        self.assertEqual(overlay['services']['app']['volumes'], [
            volume('/mnt/disk/data', '/data', True), volume(self.folder, '/home/app/projects', False)])
        self.assertEqual(overlay['services']['app']['environment'], {'ISLE_DATASET_PATH': '/mnt/disk/data'})

    def test_what_is_refused(self):
        slot = dict(service=None, source='/p/isle-slots/a/b', target='/work', readOnly=True)
        for service, bind_path, slots, words in (
                ('app', None, [dict(slot, service='editor')], "for the service 'editor', which is not in the compose file"),
                (None, None, [slot], 'which is not in the compose file'),                       # no first service was named
                ('app', None, [slot, dict(slot, service='app', source='/p/isle-slots/a/c')], 'Two folders would be given to the service app at /work'),
                ('app', '/mnt/disk/data', [dict(slot, target='/data')], 'Two folders would be given to the service app at /data')):
            with self.assertRaises(ValueError, msg=words) as caught: app_overlay.build(COMPOSE, service, bind_path, slots)
            self.assertIn(words, str(caught.exception))
        self.assertEqual(len(app_overlay.build(COMPOSE, 'app', None, [slot, dict(slot, service='viewer')])['services']), 2)   # the same place in two services

    def cli(self, *argv):
        with tempfile.TemporaryDirectory() as temp:
            compose = Path(temp) / 'c.yml'
            compose.write_text(yaml.safe_dump(COMPOSE))
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = app_overlay.main([str(compose), *argv])
            return code, out.getvalue(), err.getvalue()

    def test_the_command_reads_the_slots_of_the_app_it_is_told(self):
        with mock.patch.object(sr, 'Io', return_value=self.m):
            code, out, err = self.cli('--service', 'app', '--slots-of', 'kicad')
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(yaml.safe_load(out), self.overlay())
        with mock.patch.object(sr, 'Io', side_effect=AssertionError('the records are not read for an app without slots')):
            code, out, err = self.cli('--service', 'app')
        self.assertEqual((code, yaml.safe_load(out)), (0, app_overlay.build(COMPOSE, 'app')))

    def test_the_command_writes_no_overlay_for_a_slot_that_cannot_be_given(self):
        self.assertEqual(self.m.call('slot', 'unbind', 'kicad', 'library'), 0, self.m.text())
        with mock.patch.object(sr, 'Io', return_value=self.m):
            code, out, err = self.cli('--service', 'app', '--slots-of', 'kicad')
        self.assertEqual((code, out), (1, ''))
        self.assertIn('the storage slot library of kicad cannot be used: not bound', err)


class DeployScript(unittest.TestCase):
    """Only the slot lines of app-deploy.sh, run with a made-up `isle storage slot check`."""
    MARK = '# ---- storage slots:'

    def block(self, declared, code=0, link=False):
        lines = DEPLOY.split(self.MARK, 1)[1].split('\necho "deploying', 1)[0]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'apps' / 'kicad').mkdir(parents=True)
            (root / 'scripts').mkdir()
            if declared: (root / 'apps' / 'kicad' / 'storage-slots.json').write_text('{}')
            if link: os.symlink(root / 'nowhere', root / 'apps' / 'kicad' / 'storage-slots.json')
            (root / 'scripts' / 'storage_roles.py').write_text(
                'import sys\nopen(%r, "a").write(" ".join(sys.argv[1:]) + "\\n")\nprint("the table: library is not bound")\nsys.exit(%d)\n'
                % (str(root / 'calls'), code))
            r = subprocess.run(['bash', '-c', 'set -euo pipefail\ndie(){ echo "[FAIL] $*"; exit 1; }\nAPPS_DIR=%s; SCRIPT_DIR=%s; NAME=kicad\n# %s\n'
                                'echo "SLOTS=$SLOTS"' % (root / 'apps', root / 'scripts', lines)], capture_output=True, text=True, timeout=30)
            calls = (root / 'calls').read_text().splitlines() if (root / 'calls').exists() else []
            return r.returncode, r.stdout, calls

    def test_an_app_without_slots_is_not_checked(self):
        self.assertEqual(self.block(False), (0, 'SLOTS=0\n', []))

    def test_an_app_with_slots_is_checked_first(self):
        self.assertEqual(self.block(True), (0, 'SLOTS=1\n', ['slot check --app kicad --table']))
        self.assertEqual(self.block(False, link=True)[1:], ('SLOTS=1\n', ['slot check --app kicad --table']))   # a declaration that is a link is still one

    def test_a_slot_that_is_not_ready_stops_the_deploy(self):
        code, out, calls = self.block(True, code=1)
        self.assertEqual(code, 1)
        self.assertIn('[FAIL] the storage slots of kicad are not ready; refusing to start it: the table: library is not bound', out)
        self.assertNotIn('SLOTS=', out)
        self.assertEqual(calls, ['slot check --app kicad --table'] * 2)

    def test_the_check_comes_before_anything_is_written_or_started(self):
        check = DEPLOY.index(self.MARK)
        self.assertEqual(DEPLOY.count(self.MARK), 1)
        for later in ('sudo mkdir -p "$APPS_DIR/$NAME"', 'docker compose -p "isle-$NAME"', 'app_overlay.py" "$COMPOSE"'):
            self.assertLess(check, DEPLOY.index(later), later)
        self.assertEqual(subprocess.run(['bash', '-n', str(HERE / 'app-deploy.sh')]).returncode, 0)

    def arguments(self, slots, bind_path):
        lines = DEPLOY.split('OVERLAY_ARGS=()\n', 1)[1].split('python3 "$SCRIPT_DIR/app_overlay.py"', 1)[0]
        r = subprocess.run(['bash', '-c', 'set -euo pipefail\nSLOTS=%s; BIND_PATH=%s; SERVICE=app; NAME=kicad; OVERLAY_ARGS=()\n%s'
                            'printf "%%s " "${OVERLAY_ARGS[@]+"${OVERLAY_ARGS[@]}"}"' % (slots, bind_path, lines)],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_the_overlay_is_told_whose_slots_to_give(self):
        self.assertEqual(self.arguments(0, "''"), '')
        self.assertEqual(self.arguments(1, "''"), '--service app --slots-of kicad')
        self.assertEqual(self.arguments(0, '/mnt/d'), '--service app --bind-path /mnt/d')
        self.assertEqual(self.arguments(1, '/mnt/d'), '--service app --bind-path /mnt/d --service app --slots-of kicad')


class Install(Case):
    def kept(self, app='kicad'):
        return json.loads((Path(self.m.apps_dir) / app / ss.FILE).read_text())['slots']

    def test_an_app_without_slots_is_left_alone(self):
        before = self.everything()
        for nothing in (None, []): self.assertEqual(start.at_install(self.m, 'plain', nothing), [])
        self.assertEqual(self.everything(), before)                              # not even the lock of the records

    def test_what_the_release_declares_is_kept_and_given(self):
        self.assertEqual(start.at_install(self.m, 'kicad', [PROJECTS, VIEWED]), ss.binds(self.m, 'kicad'))
        self.assertEqual(len(ss.binds(self.m, 'kicad')), 2)

    def test_a_slot_that_is_not_bound_stops_the_install_and_its_declaration_is_kept(self):
        with self.assertRaises(ValueError) as caught: start.at_install(self.m, 'freecad', [PROJECTS])
        self.assertIn('the storage slot projects of freecad cannot be used: not bound', str(caught.exception))
        self.assertIn('isle storage slot check --app freecad --table', str(caught.exception))
        self.assertIn('What the release declares is kept, and nothing was deployed', str(caught.exception))
        self.assertEqual(self.kept('freecad'), [PROJECTS])                       # so the slot can be bound, and the install typed again
        self.assertEqual(self.bind(app='freecad'), 0, self.m.text())
        self.assertEqual(start.at_install(self.m, 'freecad', [PROJECTS]), [
            dict(service=None, source=str(self.dock / 'isle-slots' / 'freecad' / 'projects'), target='/home/app/projects', readOnly=False)])

    def test_a_release_that_drops_or_changes_a_bound_slot_is_refused(self):
        before = self.everything()
        for listed, words in (([PROJECTS], 'this release does not declare it'), (None, 'this release does not declare it'),
                              ([], 'this release does not declare it'),
                              ([PROJECTS, dict(VIEWED, role='scratch')], 'this release declares it with the role scratch')):
            with self.assertRaises(ValueError, msg=listed) as caught: start.at_install(self.m, 'kicad', listed)
            self.assertIn(words, str(caught.exception))
            self.assertNotIn('is kept', str(caught.exception))
        with self.assertRaises(ValueError): start.at_install(self.m, 'kicad', [dict(PROJECTS, role='attic')])
        with self.assertRaises(ValueError): start.at_install(self.m, 'kicad', 'projects')
        self.assertEqual(self.everything(), before)

    def test_a_release_that_no_longer_declares_slots(self):
        for slot in ('projects', 'library'): self.assertEqual(self.m.call('slot', 'unbind', 'kicad', slot), 0, self.m.text())
        self.assertEqual(start.at_install(self.m, 'kicad', None), [])
        self.assertEqual(self.kept(), [])

    def test_the_install_command_uses_the_records_of_this_machine(self):
        with mock.patch.object(sr, 'Io', return_value=self.m):
            self.assertEqual(catalog_artifacts.declare_slots('kicad', [PROJECTS, VIEWED]), ss.binds(self.m, 'kicad'))
            with self.assertRaisesRegex(ValueError, 'not bound'): catalog_artifacts.declare_slots('freecad', [PROJECTS])


class Restart(Case):
    def test_the_slot_folders_an_overlay_gives(self):
        overlay = self.overlay()
        overlay['services']['app']['volumes'] += [volume('/mnt/disk/data', '/data', True), 'named:/var/named',
                                                  volume('/mnt/disk/isle-slots-old/data', '/old', True),   # a folder that only has the words in its name
                                                  {'type': 'volume', 'source': 'x/isle-slots/y', 'target': '/v'}]
        overlay['services']['idle'] = None
        self.assertEqual(start.given(overlay), {(str(self.folder), '/home/app/projects', False),
                                                (str(self.library), '/usr/share/app/library', True)})
        for nothing in (None, {}, {'services': None}, {'services': {'app': {'volumes': None}}}): self.assertEqual(start.given(nothing), set())

    def test_an_app_as_it_was_deployed_may_start(self):
        self.deployed()
        self.assertIsNone(start.gate(self.app, self.m))
        self.assertIsNone(start.gate(str(self.app) + '/', self.m))

    def test_only_an_app_of_this_machine_with_slots_is_looked_at(self):
        elsewhere = self.m.tmp / 'elsewhere' / 'kicad'
        elsewhere.mkdir(parents=True)
        (elsewhere / ss.FILE).write_text('{}')
        self.assertIsNone(start.gate(elsewhere, self.m))                        # a compose project that is not an isle app
        plain = Path(self.m.apps_dir) / 'plain'
        plain.mkdir()
        self.assertIsNone(start.gate(plain, self.m))                            # an isle app that declares no slots

    def test_a_slot_that_cannot_be_given_stops_the_start(self):
        self.deployed()
        self.assertEqual(self.m.call('slot', 'unbind', 'kicad', 'library'), 0, self.m.text())
        self.assertIn('the storage slot library of kicad cannot be used: not bound', start.gate(self.app, self.m))
        self.assertEqual(self.bind('library'), 0, self.m.text())
        self.assertIsNone(start.gate(self.app, self.m))
        self.m.disks[2]['children'][0]['mountpoints'] = [None]
        del self.m.mount_rows[2]
        self.assertIn('partition away', start.gate(self.app, self.m))

    def test_an_overlay_that_does_not_give_what_is_bound_now_stops_the_start(self):
        words = 'the storage slots of kicad are not the ones it was deployed with. Install or deploy it again'
        whole = self.overlay()
        for change in (lambda o: o['services']['viewer'].pop('volumes'),                             # a slot it was not given
                       lambda o: o['services']['app']['volumes'][0].update(read_only=True),              # given another way
                       lambda o: o['services']['app']['volumes'][0].update(target='/elsewhere'),
                       lambda o: o['services']['app']['volumes'][0].update(source=str(self.folder) + '-old'),   # bound somewhere else since
                       lambda o: o['services']['app']['volumes'].append(volume(self.dock / 'isle-slots/kicad/gone', '/gone', True))):
            overlay = json.loads(json.dumps(whole))
            change(overlay)
            self.deployed(overlay)
            self.assertIn(words, start.gate(self.app, self.m))
        self.deployed(app_overlay.build(COMPOSE, 'app'))                        # deployed before it declared slots
        self.assertIn(words, start.gate(self.app, self.m))

    def test_an_overlay_that_cannot_be_read_stops_the_start(self):
        self.assertIn('isle-overlay.yml', start.gate(self.app, self.m))          # never deployed
        (self.app / start.OVERLAY).write_text('services: [unclosed')
        self.assertTrue(start.gate(self.app, self.m))
        (self.app / start.OVERLAY).write_text('- a list\n')
        self.assertTrue(start.gate(self.app, self.m))

    def test_a_restart_asks_the_gate_only_for_an_app_with_slots(self):
        with mock.patch.object(start, 'gate', side_effect=AssertionError('an app without slots is not looked at')):
            self.assertIsNone(runtime_reconcile.slot_gate(self.m.tmp))
        with mock.patch.object(start, 'gate', return_value='the words that refuse it') as gate:
            self.assertEqual(runtime_reconcile.slot_gate(self.app), 'the words that refuse it')
        gate.assert_called_once_with(self.app)
        with mock.patch.object(start, 'gate', return_value=None): self.assertIsNone(runtime_reconcile.slot_gate(self.app))

    def test_a_restart_is_gated_before_the_app_is_brought_up(self):
        text = (HERE / 'runtime_reconcile.py').read_text()
        gate = text.index('why=storage_gate(directory,name) or slot_gate(directory)\n')
        self.assertLess(gate, text.index("run(args+['up','-d'"))
        self.assertLess(gate, text.index('path=fetch(artifact,'))               # and before anything is fetched for it
        self.assertIn("refused.append(name);continue", text[gate:gate + 400])


if __name__ == '__main__':
    unittest.main()
