#!/usr/bin/env python3
"""isle storage slot ready | gate | ungate: the unit that stops an app when a partition of its storage slots goes away.

Fixtures only, the made-up machine of test-storage-slot-start.py: every "partition" is a temporary folder, the apps
folder, the record and the folder of units are temporary too, and systemctl is a made-up one that only writes down what
it was asked.  No real unit, no real disk, no real app, no container and no network is touched.
"""
import fcntl
import importlib.util
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
from storage_mount import mount_unit_name
import storage_roles as sr
import storage_slot_gate as gate
import storage_slots as ss

_spec = importlib.util.spec_from_file_location('storage_slot_start_fixtures', HERE / 'test-storage-slot-start.py')
st = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(st)
ARCH, DOCK = st.base.ARCH, st.DOCK
UNIT = 'isle-app-kicad.service'
APP_SH = (HERE / 'app.sh').read_text()


class Case(st.Case):
    """The app `kicad` deployed with its two slots, both on the dock partition; a made-up systemctl."""

    def setUp(self):
        super().setUp()
        (self.app / gate.COMPOSE).write_text(yaml.safe_dump(st.COMPOSE))
        self.deployed()
        self.units = Path(self.m.unit_dir)
        self.units.mkdir()
        self.unit = self.units / UNIT
        self.commands, self.answers, self.at_stop = [], {}, None
        self.m.run = self.run_tool
        self.text = gate.unit_text('kicad', str(self.m.apps_dir), [str(self.dock)])
        self.keep()

    def keep(self):
        self.before = self.state()

    def state(self):
        return self.m.records.read_bytes(), self.m.listing(), {p.name: p.read_text() for p in sorted(self.units.iterdir()) if p.is_file()}

    def run_tool(self, command, **kwargs):
        self.commands.append(list(command))
        self.assertEqual(command[0], 'systemctl')                                    # nothing else is ever started: no docker, no mount
        self.assertTrue(kwargs.get('capture_output') and kwargs.get('text') and kwargs.get('timeout'), kwargs)
        result = self.answers.get(command[1], (0, ''))
        if callable(result): result = result()
        if isinstance(result, FileNotFoundError): self.commands.pop()
        if isinstance(result, BaseException): raise result
        if command[1] == 'stop': self.at_stop = self.unit.read_text()
        return subprocess.CompletedProcess(command, result[0], stdout='', stderr=result[1])

    def call(self, verb, *argv):
        self.commands = []
        return self.m.call('slot', verb, *(argv or ('kicad',)))

    def verbs(self):
        return [c[1:] for c in self.commands]

    def refused(self, code, *words):
        super().refused(code, *words)
        self.assertEqual([c for c in self.commands if c[1] in ('daemon-reload', 'reenable', 'enable', 'start', 'stop', 'disable')], [])
        self.assertEqual(self.state(), self.before)

    def failed(self, code, verb, *words):
        self.assertEqual(code, 1, self.m.text())
        self.assertTrue(self.m.text().startswith('FAILED: slot %s: ' % verb), self.m.text())
        self.assertNotIn('Nothing was changed', self.m.text())
        for word in words: self.assertIn(word, self.m.text())

    def onto_the_archive(self, slot='library'):
        """One slot bound to the archive partition instead, and the app deployed again with that."""
        self.assertEqual(self.m.call('partition', 'assign', ARCH, '--role', 'package-repo,assets'), 0, self.m.text())
        self.assertEqual(self.m.call('slot', 'unbind', 'kicad', slot), 0, self.m.text())
        self.assertEqual(self.bind(slot, ARCH), 0, self.m.text())
        self.deployed()


class TheUnit(Case):
    def test_the_text(self):
        dock = mount_unit_name(str(self.dock))
        self.assertEqual(self.text.splitlines(), [
            '# Written by: isle storage slot gate kicad',
            '[Unit]',
            'Description=Isle app kicad, started and stopped with the partitions of its storage slots',
            'BindsTo=' + dock,
            'After=%s docker.service' % dock,
            'Wants=docker.service',
            '',
            '[Service]',
            'Type=oneshot',
            'RemainAfterExit=yes',
            'WorkingDirectory=%s/kicad' % self.m.apps_dir,
            'ExecStartPre=/usr/local/bin/isle storage slot ready kicad',
            'ExecStart=/usr/bin/docker compose -p isle-kicad -f docker-compose.yml -f isle-overlay.yml up -d --no-build --pull never',
            'ExecStop=/usr/bin/docker compose -p isle-kicad -f docker-compose.yml -f isle-overlay.yml stop',
            '',
            '[Install]',
            'WantedBy=' + dock])
        self.assertTrue(self.text.endswith('\n'))
        self.assertNotIn('multi-user.target', self.text)                             # a start of the machine never waits for a disk
        self.assertNotIn(' down', self.text)                                         # a stop keeps the containers and what they hold
        self.assertEqual(gate.unit_name('kicad'), UNIT)

    def test_every_partition_of_its_slots_is_named(self):
        both = '%s %s' % (mount_unit_name('/mnt/a'), mount_unit_name('/mnt/b-b'))
        lines = gate.unit_text('kicad', '/apps', ['/mnt/a', '/mnt/b-b']).splitlines()
        self.assertIn('BindsTo=' + both, lines)
        self.assertIn('After=%s docker.service' % both, lines)
        self.assertIn('WantedBy=' + both, lines)
        self.assertEqual(both.count(' '), 1)

    def test_the_unit_that_stops_nothing(self):
        text = gate.released_text('kicad')
        self.assertEqual(text.splitlines()[0], '# Written by: isle storage slot gate kicad')
        for word in ('ExecStop', 'BindsTo', 'WantedBy', 'docker', '[Install]'): self.assertNotIn(word, text)
        self.assertIn('ExecStart=/bin/true\n', text)
        self.assertIn('RemainAfterExit=yes\n', text)

    def test_whose_unit_it_is(self):
        self.assertEqual(gate.kind(self.m, 'kicad'), 'none')
        self.unit.write_text(self.text)
        self.assertEqual(gate.kind(self.m, 'kicad'), 'ours')
        self.unit.write_text(gate.released_text('kicad'))
        self.assertEqual(gate.kind(self.m, 'kicad'), 'ours')
        for other in ('', '[Unit]\n' + self.text, self.text.replace('kicad', 'kicad2', 1), '\n' + self.text):
            self.unit.write_text(other)
            self.assertEqual(gate.kind(self.m, 'kicad'), 'foreign', other)
        self.unit.unlink()
        (self.units / 'real').write_text(self.text)
        os.symlink(self.units / 'real', self.unit)
        self.assertEqual(gate.kind(self.m, 'kicad'), 'foreign')                      # a link is never followed
        self.unit.unlink()
        os.symlink(self.units / 'nowhere', self.unit)
        self.assertEqual(gate.kind(self.m, 'kicad'), 'foreign')
        self.unit.unlink()
        self.unit.mkdir()
        self.assertEqual(gate.kind(self.m, 'kicad'), 'foreign')

    def test_the_unit_expected_now(self):
        rows = ss.rows(self.m)
        self.assertEqual(gate.expected(self.m, 'kicad', rows), self.text)
        self.assertIsNone(gate.expected(self.m, 'freecad', rows))
        self.onto_the_archive()
        both = sorted([str(self.dock), str(self.m.folders['archive'])])
        self.assertEqual(gate.expected(self.m, 'kicad', ss.rows(self.m)), gate.unit_text('kicad', str(self.m.apps_dir), both))
        del self.m.mount_rows[2]                                                     # the dock partition goes away
        self.assertIsNone(gate.expected(self.m, 'kicad', ss.rows(self.m)))


class Ready(Case):
    def test_an_app_as_it_is_deployed(self):
        for root in (True, False):
            self.m.root_user = root
            self.assertEqual(self.call('ready'), 0)
            self.assertEqual(self.m.said, ['The app kicad can be started with its storage slots as it is deployed.'])
        self.assertEqual((self.commands, self.state()), ([], self.before))

    def test_what_stops_a_start(self):
        (self.app / st.start.OVERLAY).unlink()
        self.assertEqual(self.call('ready'), 1)
        self.assertTrue(self.m.text().startswith('NOT READY: '), self.m.text())
        self.deployed()
        self.assertEqual(self.call('ready'), 0)
        del self.m.mount_rows[2]
        self.assertEqual(self.call('ready'), 1)
        self.assertRegex(self.m.text(), r'^NOT READY: .*partition away.*[^.]\.$')
        with mock.patch.object(st.start, 'gate', return_value='a reason that ends with its own stop.'):
            self.assertEqual(self.call('ready'), 1)
            self.assertEqual(self.m.said, ['NOT READY: a reason that ends with its own stop.'])
            self.keep()
            self.refused(self.call('gate'), 'slot gate: a reason that ends with its own stop. Nothing was changed.')
        self.assertEqual(self.commands, [])

    def test_an_app_without_slots_and_what_is_not_a_command(self):
        self.assertEqual(self.call('ready', 'freecad'), 0)
        self.assertEqual(self.m.said, ['The app freecad declares no storage slot.'])
        for argv in (('ready',), ('ready', 'kicad', 'more'), ('ready', 'Kicad'), ('ready', '../kicad'), ('gate',), ('gate', 'kicad', '--now'),
                     ('ungate',), ('ungate', 'kicad', '--stopped')):
            self.assertEqual(self.m.call('slot', *argv), 2, argv)
            self.assertEqual(self.m.said, [sr.USAGE], argv)
        self.assertIn('isle storage slot ready <app> | sudo isle storage slot gate <app> | sudo isle storage slot ungate <app>', sr.USAGE)
        self.assertEqual((self.commands, self.state()), ([], self.before))


class Gating(Case):
    def test_the_unit_is_written_enabled_and_started(self):
        self.assertEqual(self.call('gate'), 0, self.m.text())
        self.assertEqual(self.verbs(), [['--version'], ['daemon-reload'], ['reenable', UNIT], ['start', UNIT]])
        self.assertEqual(self.unit.read_text(), self.text)
        self.assertEqual(oct(self.unit.stat().st_mode & 0o777), '0o644')
        self.assertEqual(sorted(p.name for p in self.units.iterdir()), [UNIT])       # no temporary file is left
        self.assertEqual(self.m.said, [
            'The app kicad is now started and stopped with the partitions of its storage slots (unit %s).' % UNIT,
            'It is stopped when one of them goes away, and started when they are there again. A start of this machine does not wait for them.',
            'To take the unit away and leave the app as it is: sudo isle storage slot ungate kicad'])
        self.assertEqual(self.state()[0], self.before[0])                            # the record is not written

    def test_a_second_time_changes_nothing(self):
        self.assertEqual(self.call('gate'), 0)
        self.keep()
        for root in (True, False):
            self.m.root_user = root
            self.assertEqual(self.call('gate'), 0)
            self.assertEqual(self.m.said, ['The app kicad is started and stopped with its partitions already (unit %s). Nothing was changed.' % UNIT])
            self.assertEqual(self.verbs(), [['is-enabled', UNIT], ['is-active', UNIT]])
            self.assertEqual(self.state(), self.before)

    def test_a_unit_that_is_not_enabled_or_not_started_is_put_right(self):
        self.assertEqual(self.call('gate'), 0)
        for question, asked in (('is-enabled', 1), ('is-active', 2)):
            self.answers = {question: (1, '')}
            self.assertEqual(self.call('gate'), 0, self.m.text())
            self.assertEqual(self.verbs()[asked:], [['--version'], ['daemon-reload'], ['reenable', UNIT], ['start', UNIT]], question)
        self.answers = {'is-enabled': subprocess.TimeoutExpired('systemctl', 180)}   # a question without an answer is a no
        self.assertEqual(self.call('gate'), 0, self.m.text())
        self.assertEqual(self.verbs()[-1], ['start', UNIT])

    def test_without_root_it_says_what_it_would_do(self):
        self.m.root_user = False
        self.assertEqual(self.call('gate'), 1)
        self.assertEqual(self.m.said, ['This writes the unit %s and starts it: the app kicad is started if it is not running. '
                                       'Type: sudo isle storage slot gate kicad' % UNIT])
        self.assertEqual((self.commands, self.state()), ([], self.before))

    def test_every_partition_of_the_slots_is_in_the_unit(self):
        self.onto_the_archive()
        self.assertEqual(self.call('gate'), 0, self.m.text())
        both = ' '.join(mount_unit_name(str(folder)) for folder in sorted([self.dock, self.m.folders['archive']]))
        self.assertIn('BindsTo=%s\n' % both, self.unit.read_text())
        self.assertIn('WantedBy=%s\n' % both, self.unit.read_text())

    def test_an_app_that_cannot_be_gated(self):
        self.refused(self.call('gate', 'freecad'), 'the app freecad declares no storage slot, so no partition has to be there for it')
        self.refused(self.call('gate', '-kicad'), 'that is not the name of an app')
        ss.write_declaration(self.m, 'freecad', [])
        self.keep()
        self.refused(self.call('gate', 'freecad'), 'declares no storage slot')
        (Path(self.m.apps_dir) / 'blender').mkdir()
        (Path(self.m.apps_dir) / 'blender' / ss.FILE).write_text('{not json')
        self.keep()
        self.refused(self.call('gate', 'blender'), 'the app blender: ')

    def test_an_app_that_is_not_deployed_as_its_slots_are_bound(self):
        (self.app / 'storage-bind.json').write_text('{}')
        self.keep()
        self.refused(self.call('gate'), 'hosts a dataset, and the unit of that dataset starts and stops it already')
        (self.app / 'storage-bind.json').unlink()
        for name in (gate.COMPOSE, st.start.OVERLAY):
            kept = (self.app / name).read_text()
            (self.app / name).unlink()
            self.keep()
            self.refused(self.call('gate'), 'the app kicad is not deployed on this machine (it has no %s). First install or deploy it' % name)
            (self.app / name).mkdir()                                                # a folder of that name is not the file
            self.keep()
            self.refused(self.call('gate'), 'it has no %s' % name)
            (self.app / name).rmdir()
            (self.app / name).write_text(kept)
        self.assertEqual(self.m.call('slot', 'unbind', 'kicad', 'library'), 0)
        self.keep()
        self.refused(self.call('gate'), 'library')
        self.assertEqual(self.bind('library'), 0)
        self.onto_the_archive()
        self.deployed(st.app_overlay.build(st.COMPOSE, 'app', None, []))             # deployed before the slots were given
        self.keep()
        self.refused(self.call('gate'), 'are not the ones it was deployed with')
        self.assertEqual(self.commands, [])

    def test_a_unit_of_that_name_that_is_not_ours_is_left_alone(self):
        self.unit.write_text('[Unit]\nDescription=written by hand\n')
        self.keep()
        self.refused(self.call('gate'), 'a unit %s exists, and this verb did not write it' % UNIT)
        self.unit.unlink()
        (self.units / 'real').write_text(self.text)
        os.symlink(self.units / 'real', self.unit)
        self.keep()
        self.refused(self.call('gate'), 'this verb did not write it')
        self.assertEqual(self.commands, [])

    def test_a_machine_whose_systemctl_cannot_be_used(self):
        self.answers = {'--version': FileNotFoundError('systemctl')}
        self.refused(self.call('gate'), 'systemctl is not on this machine')
        self.answers = {'--version': (3, '')}
        self.refused(self.call('gate'), 'systemctl does not answer (exit 3)')
        self.answers = {}
        with open(str(self.m.records) + '.lock', 'a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.keep()
            self.refused(self.call('gate'), 'the storage records are being changed by another command')

    def test_a_step_that_fails_is_said_with_what_was_reached(self):
        for verb, enabled in (('daemon-reload', False), ('reenable', False), ('start', True)):
            self.answers = {verb: (1, 'first line\nJob failed. See the journal.\n\n')}
            self.failed(self.call('gate'), 'gate', 'systemctl %s ended with exit 1 (Job failed. See the journal.). The unit %s is written%s, and it did not start.'
                        % (verb, UNIT, ' and enabled' if enabled else ''), 'to take the unit away: sudo isle storage slot ungate kicad')
            self.assertEqual(self.verbs()[-1][0], verb)
            self.assertEqual(self.unit.read_text(), self.text)
            self.unit.unlink()
        self.answers = {'start': subprocess.TimeoutExpired('systemctl', 180)}
        self.failed(self.call('gate'), 'gate', 'The unit %s is written and enabled' % UNIT)
        self.answers = {'start': (5, ''), 'is-active': (1, '')}
        self.failed(self.call('gate'), 'gate', 'systemctl start ended with exit 5. The unit')
        self.answers = {}
        self.assertEqual(self.call('gate'), 0)                                       # the same command again finishes it

    def test_a_unit_folder_that_cannot_be_written(self):
        self.units.rmdir()
        self.m.unit_dir = self.units / 'not' / 'there'
        self.units = self.m.tmp                                                      # for the look at the state
        self.keep()
        self.refused(self.call('gate'), 'No such file')
        self.assertEqual(self.verbs(), [['--version']])


class OutOfDate(Case):
    def test_a_slot_bound_elsewhere_makes_the_unit_late(self):
        self.assertEqual(self.call('gate'), 0)
        self.assertEqual(gate.out_of_date(self.m, ss.rows(self.m)), [])
        self.assertEqual(self.rows('check')[1]['result']['unitsOutOfDate'], [])
        self.assertEqual(self.m.call('slot', 'check', '--table'), 0)
        self.assertNotIn('slot gate', self.m.text())
        self.onto_the_archive()
        self.assertEqual(gate.out_of_date(self.m, ss.rows(self.m)), ['kicad'])
        for verb in ('list', 'check'):
            code, doc = self.rows(verb)
            self.assertEqual((code, doc['ok'], doc['result']['unitsOutOfDate'], doc['result']['needAttention']), (0, True, ['kicad'], []), verb)
            self.assertEqual(self.m.call('slot', verb, '--table'), 0)                 # the check before a deploy is not failed by it
            self.assertEqual(self.m.said[1:], ['The unit that starts and stops kicad with its partitions does not name the partitions its '
                                               'slots are bound to now. Type: sudo isle storage slot gate kicad']
                             + (['The slots themselves need no attention.'] if verb == 'check' else []))
        self.assertEqual(self.rows('check', '--app', 'freecad')[1]['result']['unitsOutOfDate'], [])
        self.assertEqual(self.call('gate'), 0, self.m.text())                         # the same verb writes it again
        self.assertEqual(self.verbs(), [['--version'], ['daemon-reload'], ['reenable', UNIT], ['start', UNIT]])
        self.assertIn(mount_unit_name(str(self.m.folders['archive'])), self.unit.read_text())
        self.assertEqual(gate.out_of_date(self.m, ss.rows(self.m)), [])

    def test_only_a_unit_of_this_verb_with_usable_slots_is_judged(self):
        self.assertEqual(gate.out_of_date(self.m, ss.rows(self.m)), [])               # no unit
        self.unit.write_text('[Unit]\nDescription=written by hand\n')
        self.assertEqual(gate.out_of_date(self.m, ss.rows(self.m)), [])               # not ours
        self.unit.write_text(gate.released_text('kicad'))
        self.assertEqual(gate.out_of_date(self.m, ss.rows(self.m)), ['kicad'])        # an ungate that did not finish
        del self.m.mount_rows[2]
        self.assertEqual(gate.out_of_date(self.m, ss.rows(self.m)), [])               # away: the slot's own state says what to do
        self.assertEqual(gate.out_of_date(self.m, []), [])


class Ungating(Case):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.call('gate'), 0, self.m.text())
        self.keep()

    def test_the_unit_is_removed_and_the_app_is_left_as_it_is(self):
        self.assertEqual(self.call('ungate'), 0, self.m.text())
        self.assertEqual(self.verbs(), [['--version'], ['disable', UNIT], ['daemon-reload'], ['stop', UNIT], ['daemon-reload']])
        self.assertEqual(self.at_stop, gate.released_text('kicad'))                   # what is stopped is a unit that stops nothing
        self.assertEqual(sorted(p.name for p in self.units.iterdir()), [])
        self.assertEqual(self.m.said, ['The unit %s is removed. The app kicad was left as it is, and nothing stops it now when a partition of '
                                       'its storage slots goes away.' % UNIT])
        self.assertEqual(self.state()[0], self.before[0])
        self.assertEqual(self.call('ungate'), 0)
        self.assertEqual(self.m.said, ['The app kicad has no unit of `isle storage slot gate`. Nothing was changed.'])
        self.assertEqual(self.commands, [])

    def test_an_app_whose_slots_are_gone_can_still_be_ungated(self):
        del self.m.mount_rows[2]
        (self.app / st.start.OVERLAY).unlink()
        self.assertEqual(self.call('ungate'), 0, self.m.text())
        self.assertFalse(self.unit.exists())

    def test_without_root_it_says_what_it_would_do(self):
        self.m.root_user = False
        self.assertEqual(self.call('ungate'), 1)
        self.assertEqual(self.m.said, ['This removes the unit %s. The app kicad is left as it is, running or not. '
                                       'Type: sudo isle storage slot ungate kicad' % UNIT])
        self.assertEqual((self.commands, self.state()), ([], self.before))

    def test_what_is_refused(self):
        self.refused(self.call('ungate', '-kicad'), 'that is not the name of an app')
        self.unit.write_text('[Unit]\nDescription=written by hand\n')
        self.keep()
        self.refused(self.call('ungate'), 'a unit %s exists, and this verb did not write it' % UNIT)
        self.unit.write_text(self.text)
        self.keep()
        self.answers = {'--version': FileNotFoundError('systemctl')}
        self.refused(self.call('ungate'), 'systemctl is not on this machine')
        self.answers = {'--version': (1, '')}
        self.refused(self.call('ungate'), 'systemctl does not answer (exit 1)')
        self.answers = {}
        with open(str(self.m.records) + '.lock', 'a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.refused(self.call('ungate'), 'the storage records are being changed by another command')

    def test_a_step_that_fails_is_said_and_the_same_command_finishes(self):
        self.answers = {'disable': (1, 'no\n')}
        self.failed(self.call('ungate'), 'ungate', 'systemctl disable ended with exit 1 (no). The unit %s is kept. To finish, type the same command.' % UNIT)
        self.assertEqual(self.unit.read_text(), self.text)
        self.answers = {'stop': (1, '')}
        self.failed(self.call('ungate'), 'ungate', 'The unit %s is kept, and it is no longer enabled. To finish, type the same command.' % UNIT)
        self.assertEqual(self.unit.read_text(), gate.released_text('kicad'))
        self.answers = {'stop': subprocess.TimeoutExpired('systemctl', 180)}
        self.failed(self.call('ungate'), 'ungate', 'is kept, and it is no longer enabled')
        reloads = []
        self.answers = {'daemon-reload': lambda: reloads.append(1) or ((1, '') if len(reloads) == 2 else (0, ''))}
        self.failed(self.call('ungate'), 'ungate', 'The unit %s is removed; systemd has not read that yet (a restart of the machine does it).' % UNIT)
        self.assertFalse(os.path.lexists(self.unit))
        self.unit.write_text(gate.released_text('kicad'))                             # as a stop that failed leaves it
        self.answers = {}
        self.assertEqual(self.call('ungate'), 0, self.m.text())
        self.assertFalse(os.path.lexists(self.unit))


class Undeploy(unittest.TestCase):
    """Only the slot lines of `isle app undeploy`, with a made-up sudo."""
    MARK = '# ---- storage slots:'

    def block(self, unit, code=0):
        lines = APP_SH.split(self.MARK, 1)[1].split('        sudo docker compose', 1)[0]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            if unit == 'link':
                (root / 'real').write_text('# Written by: isle storage slot gate kicad\n')
                os.symlink(root / 'real', root / UNIT)
            elif unit is not None: (root / UNIT).write_text(unit)
            self.assertEqual(lines.count('/etc/systemd/system'), 1)
            r = subprocess.run(['bash', '-c', 'set -uo pipefail\nsudo(){ echo "$*" >> %s/calls; return %d; }\nSCRIPT_DIR=/scripts; NAME=kicad\n# %s\necho "went on"'
                                % (root, code, lines.replace('/etc/systemd/system', str(root)))], capture_output=True, text=True, timeout=30)
            calls = (root / 'calls').read_text().splitlines() if (root / 'calls').exists() else []
            return r.returncode, r.stdout, calls

    def test_a_unit_of_the_gate_is_taken_away_before_the_app_is_taken_down(self):
        self.assertEqual(self.block(gate.unit_text('kicad', '/apps', ['/mnt/a'])), (0, 'went on\n', ['python3 /scripts/storage_roles.py slot ungate kicad']))
        self.assertEqual(self.block(gate.released_text('kicad'))[2], ['python3 /scripts/storage_roles.py slot ungate kicad'])

    def test_an_app_without_such_a_unit_is_taken_down_as_before(self):
        for unit in (None, '[Unit]\nDescription=written by hand\n', gate.unit_text('kicad2', '/apps', ['/mnt/a']), 'link', ''):
            self.assertEqual(self.block(unit), (0, 'went on\n', []), unit)

    def test_a_unit_that_cannot_be_removed_stops_the_undeploy(self):
        code, out, calls = self.block(gate.unit_text('kicad', '/apps', ['/mnt/a']), code=1)
        self.assertEqual((code, calls), (1, ['python3 /scripts/storage_roles.py slot ungate kicad']))
        self.assertIn('kicad was not taken down', out)
        self.assertNotIn('went on', out)

    def test_the_lines_come_before_the_app_is_taken_down(self):
        self.assertEqual(APP_SH.count(self.MARK), 1)
        self.assertLess(APP_SH.index('removal_guard.py" "$NAME"'), APP_SH.index(self.MARK))
        self.assertLess(APP_SH.index(self.MARK), APP_SH.index('isle-overlay.yml" down'))
        self.assertEqual(subprocess.run(['bash', '-n', str(HERE / 'app.sh')]).returncode, 0)


if __name__ == '__main__':
    unittest.main()
