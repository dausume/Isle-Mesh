"""isle storage role list|show --isle, and the fact each machine reports for them (storage_isle.py).

Fixtures only: the machines are the made-up one of test-storage-roles.py, the core's device list is a made-up
answer, and the one command that would ask the core (curl) is replaced.  No disk, record or network is touched.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import storage_isle as si
import storage_roles as sr


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

roles_test = load('roles_test', 'test-storage-roles.py')
ARCH, DOCK = roles_test.ARCH, roles_test.DOCK
AT = '2025-12-31T23:50:00+00:00'      # ten minutes before the fixture's "now"


class Machine(roles_test.Machine):
    """The made-up machine, plus the answer the core would give when asked for its device list."""
    listing, curl_code, asked = None, 0, None

    def run(self, cmd, **kw):
        assert cmd[0] == 'curl', cmd
        self.asked = (self.asked or []) + [cmd]
        body = json.dumps(dict(devices=self.listing)) if not isinstance(self.listing, str) else self.listing
        return types.SimpleNamespace(returncode=self.curl_code, stdout=body, stderr='')


def device(name, value=None, at=AT, status='observed', **more):
    facts = {} if value is None else {si.FACT: dict(status=status, source='fixture', observed_at=at,
                                                    value=value if isinstance(value, str) else json.dumps(value))}
    return dict(dict(name=name, is_mock=False, capability_evidence=dict(schema_version=1, facts=facts)), **more)


def part(role='git', identifier='aaaa-0001', **more):
    return dict(dict(role=role, filesystemUuid=identifier, deviceClass='fixed', sizeBytes=500000, availableBytes=400,
                     roleState='ok', datasets=[]), **more)


def fact(*partitions, **more): return dict(dict(schemaVersion=1, partitions=list(partitions), leftOut=0), **more)


class Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.m = Machine(self._tmp.name)

    def isle(self, *argv):
        code = self.m.call(*argv)
        return code, (None if '--table' in argv else json.loads(self.m.said[0]))


class TheFact(Case):
    def test_it_carries_the_partitions_with_a_role_and_no_serial(self):
        self.assertEqual(json.loads(si.report_fact(self.m)), dict(schemaVersion=1, partitions=[], leftOut=0))   # nothing assigned yet
        self.m.ready()
        self.assertEqual(self.m.call('partition', 'assign', DOCK, '--role', 'assets'), 0, self.m.text())
        text = si.report_fact(self.m)
        doc = json.loads(text)
        self.assertEqual([(p['role'], p['filesystemUuid'], p['deviceClass'], p['roleState']) for p in doc['partitions']],
                         sorted([('package-repo', ARCH, 'fixed', 'ok'), ('assets', DOCK, 'dock', 'ok')], key=lambda r: r[1]))
        archive = next(p for p in doc['partitions'] if p['filesystemUuid'] == ARCH)
        self.assertEqual((archive['datasets'], archive['availableBytes'], archive['sizeBytes']), (['apt-public-repo'], 400, 499000))
        self.assertNotIn('S2', text); self.assertNotIn('Serial', text); self.assertNotIn(str(self.m.tmp), text)   # no serial, no path
        self.assertEqual(si.read_fact(dict(status='observed', observed_at=AT, value=text))[0], doc['partitions'])   # the reader takes what is sent

    def test_a_partition_with_several_roles_is_one_row_per_role(self):
        self.m.ready()
        self.assertEqual(self.m.call('partition', 'assign', ARCH, '--role', 'backup,git'), 0, self.m.text())
        self.assertEqual(self.m.call('partition', 'assign', DOCK, '--role', 'assets'), 0, self.m.text())
        text = si.report_fact(self.m)
        doc = json.loads(text)
        self.assertEqual(doc['schemaVersion'], 1)                         # the same shape as before: a reader of one role per row reads it
        self.assertEqual(sorted((p['filesystemUuid'], p['role']) for p in doc['partitions']),
                         sorted([(ARCH, 'package-repo'), (ARCH, 'backup'), (ARCH, 'git'), (DOCK, 'assets')]))
        mine = [p for p in doc['partitions'] if p['filesystemUuid'] == ARCH]
        self.assertEqual([p['role'] for p in mine], [r for r in sr.ROLES if r in ('package-repo', 'backup', 'git')])   # in the order of the roles
        self.assertEqual({(p['sizeBytes'], p['availableBytes'], p['roleState'], tuple(p['datasets'])) for p in mine},
                         {(499000, 400, 'ok', ('apt-public-repo',))})
        self.assertNotIn('roles', text)
        self.assertEqual(si.read_fact(dict(status='observed', observed_at=AT, value=text))[0], doc['partitions'])

    def test_a_partition_that_is_away_is_reported_as_away(self):
        self.m.ready()
        self.assertEqual(self.m.call('partition', 'assign', DOCK, '--role', 'assets'), 0, self.m.text())
        self.m.disks = [d for d in self.m.disks if d['name'] != 'sdc']
        self.m.mount_rows = [r for r in self.m.mount_rows if r['uuid'] != DOCK]
        away = next(p for p in json.loads(si.report_fact(self.m))['partitions'] if p['filesystemUuid'] == DOCK)
        self.assertEqual((away['roleState'], away['deviceClass'], away['sizeBytes']), ('not present', 'dock', 0))
        self.assertNotIn('availableBytes', away)

    def test_it_is_bounded_and_says_what_it_left_out(self):
        self.m.ready()
        with patch.object(si, 'MAX_PARTITIONS', 0):
            self.assertEqual(json.loads(si.report_fact(self.m)), dict(schemaVersion=1, partitions=[], leftOut=1))

    def test_records_that_cannot_be_read_give_no_fact(self):
        self.m.records.parent.mkdir()
        self.m.records.write_text('{not json')
        self.assertIsNone(si.report_fact(self.m))
        self.assertEqual(self.m.records.read_text(), '{not json')

    def test_reporting_writes_nothing(self):
        self.m.ready()
        before = {p: p.read_bytes() for p in self.m.tmp.rglob('*') if p.is_file()}
        self.assertIsNotNone(si.report_fact(self.m))
        self.assertEqual({p: p.read_bytes() for p in self.m.tmp.rglob('*') if p.is_file()}, before)

    def test_the_device_report_carries_it_as_a_string_or_leaves_it_out(self):
        probe = load('probe', 'capabilities.py')
        def facts(module):
            with patch.dict(sys.modules, {'storage_isle': module, 'release_run': None}), \
                    patch.object(probe, 'run', return_value=(0, '200', '')), patch.object(probe.shutil, 'which', return_value='/bin/tool'):
                return probe.collect('fixture')['capability_evidence']['facts']
        def fake(fn):
            module = types.ModuleType('storage_isle')
            if fn: module.report_fact = fn
            return module
        sent = facts(fake(lambda: '{"schemaVersion":1}'))[si.FACT]
        self.assertEqual((sent['status'], sent['value']), ('observed', '{"schemaVersion":1}'))
        def boom(): raise RuntimeError('x')
        for module in (fake(lambda: None), fake(lambda: {'a': 1}), fake(None), fake(boom), None):
            self.assertNotIn(si.FACT, facts(module))


class Reading(unittest.TestCase):
    def read(self, value, **more):
        return si.read_fact(dict(dict(status='observed', observed_at=AT, value=value if isinstance(value, str) else json.dumps(value)), **more))

    def test_a_good_fact_is_read(self):
        rows, left, at = self.read(fact(part(), part('backup', 'bbbb-0002', deviceClass=None, roleState='not mounted'), leftOut=3))
        self.assertEqual(([p['role'] for p in rows], left, at.isoformat()), (['git', 'backup'], 3, AT))
        rows, _, _ = self.read(fact(part(surprise='x; rm')))
        self.assertNotIn('surprise', rows[0])                                   # only the known keys are kept

    def test_anything_else_is_not_read(self):
        for bad in ('{not json', '[]', fact(schemaVersion=2), fact(leftOut=-1), fact(leftOut='0'), dict(schemaVersion=1, leftOut=0),
                    fact(part(role='attic')), fact(part(identifier='a b')), fact(part(identifier='x\x1b[31m-0001')), fact(part(deviceClass='shelf')),
                    fact(part(sizeBytes=-1)), fact(part(sizeBytes='big')), fact(part(sizeBytes=True)), fact(part(availableBytes=-5)),
                    fact(part(roleState='fine')), fact(part(roleState='marker of another filesystem')), fact(part(datasets='apt')),
                    fact(part(datasets=['Bad Name'])), fact(part(datasets=['a'] * (si.MAX_DATASETS + 1))), fact('not a row'),
                    fact(*[part(identifier='aaaa-%04d' % i) for i in range(si.MAX_PARTITIONS + 1)])):
            self.assertIsNone(self.read(bad), bad)
        good = fact(part())
        self.assertIsNone(self.read(good, status='error'))
        self.assertIsNone(self.read(good, observed_at='yesterday'))
        self.assertIsNone(self.read(good, observed_at='2025-12-31T23:50:00'))   # a time without a zone says nothing
        self.assertIsNone(si.read_fact('a string')); self.assertIsNone(si.read_fact({}))


class TheIsle(Case):
    def setUp(self):
        super().setUp()
        self.m.listing = [
            device('pol', fact(part('git', 'aaaa-0001', datasets=['forge-repos']), part('backup', 'bbbb-0002', availableBytes=900))),
            device('core1', fact(part('package-repo', 'cccc-0003', deviceClass='dock', roleState='not present', sizeBytes=0), leftOut=2)),
            device('econ'),                                                     # an older CLI: no storage fact
            device('odd', '{not json'),
            device('ghost', fact(part('git', 'dddd-0004')), is_mock=True),
            device('Bad Name', fact(part('git', 'eeee-0005'))),
            dict(name='bare')]                                                  # a row with no report at all

    def test_role_list_puts_every_machine_side_by_side(self):
        code, doc = self.isle('role', 'list', '--isle')
        self.assertEqual((code, doc['ok'], doc['scope'], doc['readOnly'], doc['operation']), (0, True, 'isle', True, 'storage.role.list'))
        result = doc['result']
        self.assertEqual([r['role'] for r in result['roles']], list(sr.ROLES))
        by = {r['role']: r for r in result['roles']}
        self.assertEqual((by['git']['partitions'], by['git']['devices'], by['git']['datasets'], by['git']['availableBytes']), (1, ['pol'], ['forge-repos'], 400))
        self.assertEqual((by['package-repo']['devices'], by['package-repo']['availableBytes']), (['core1'], 0))   # away: its free space is not counted
        self.assertEqual((by['assets']['partitions'], by['assets']['devices']), (0, []))
        self.assertEqual([(p['device'], p['role'], p['filesystemUuid']) for p in result['partitions']],
                         [('core1', 'package-repo', 'cccc-0003'), ('pol', 'git', 'aaaa-0001'), ('pol', 'backup', 'bbbb-0002')])
        self.assertEqual({p['ageSeconds'] for p in result['partitions']}, {600})
        self.assertEqual([(d['device'], d['state']) for d in result['devices']],
                         [('bare', si.NO_REPORT), ('core1', 'reported'), ('econ', si.NO_REPORT), ('odd', si.NOT_READABLE), ('pol', 'reported')])
        self.assertNotIn('ghost', self.m.said[0]); self.assertNotIn('eeee-0005', self.m.said[0])

    def test_a_partition_reported_under_two_roles_is_in_both_and_the_table_says_so(self):
        self.m.listing = [device('pol', fact(part('git', 'aaaa-0001'), part('backup', 'aaaa-0001'), part('backup', 'bbbb-0002')))]
        code, doc = self.isle('role', 'list', '--isle')
        by = {r['role']: r for r in doc['result']['roles']}
        self.assertEqual((by['git']['partitions'], by['backup']['partitions'], by['git']['availableBytes'], by['backup']['availableBytes']), (1, 2, 400, 800))
        self.assertEqual(self.isle('role', 'list', '--isle', '--table')[0], 0)
        self.assertIn('A partition with more than one role has one line per role', self.m.text())
        self.assertEqual(self.isle('role', 'show', 'git', '--isle', '--table')[0], 0)
        self.assertNotIn('more than one role', self.m.text())             # one role is shown: nothing is on two lines

    def test_role_show_keeps_one_role(self):
        code, doc = self.isle('role', 'show', 'backup', '--isle')
        self.assertEqual(code, 0)
        self.assertEqual([r['role'] for r in doc['result']['roles']], ['backup'])
        self.assertEqual([(p['device'], p['filesystemUuid']) for p in doc['result']['partitions']], [('pol', 'bbbb-0002')])
        self.m.asked = None
        code, doc = self.isle('role', 'show', 'attic', '--isle')
        self.assertEqual((code, doc['ok']), (1, False)); self.assertIn('Unknown role: attic', doc['error'])
        self.assertIsNone(self.m.asked)                                         # an unknown role asks no one

    def test_the_table_says_what_it_is_and_who_did_not_report(self):
        self.assertEqual(self.isle('role', 'list', '--isle', '--table')[0], 0)
        text = self.m.text()
        for words in ('ROLE', 'pol', 'aaaa-0001', 'forge-repos', '10 min ago', 'not present', 'core1 left 2 more partitions out of its report.',
                      'econ: no storage report yet', 'odd: its storage report cannot be read by this version',
                      'This is what each machine last reported, not a check.', 'isle storage partition check --table'):
            self.assertIn(words, text)
        self.assertNotIn('ghost', text)
        self.assertEqual(self.isle('--isle', 'role', 'show', 'scratch', '--table')[0], 0)
        self.assertIn('No machine reports a partition with the role scratch.', self.m.text())
        self.m.listing = []
        self.assertEqual(self.isle('role', 'list', '--isle', '--table')[0], 0)
        self.assertIn('No machine reports a partition with a role.', self.m.text())

    def test_ages_are_plain(self):
        self.assertEqual([si._age(s) for s in (0, 119, 120, 7199, 7200, 172799, 172800)],
                         ['0 s ago', '119 s ago', '2 min ago', '119 min ago', '2 h ago', '47 h ago', '2 d ago'])
        self.m.listing = [device('late', fact(part()), at='2026-01-01T00:05:00+00:00')]   # a clock ahead of this one
        self.assertEqual(self.isle('role', 'list', '--isle')[1]['result']['partitions'][0]['ageSeconds'], 0)

    def test_the_core_is_asked_over_verified_tls_and_only_read(self):
        self.isle('role', 'list', '--isle')
        self.assertEqual(len(self.m.asked), 1)
        cmd = self.m.asked[0]
        self.assertEqual(cmd[-1], 'https://api.polari.isle/api/islemesh/devices')
        self.assertEqual(cmd[cmd.index('--cacert') + 1], si.CA_FILE)
        for never in ('-k', '--insecure', '-X', '-d', '--data', '--data-binary', '-H', '-u'): self.assertNotIn(never, cmd)
        self.assertFalse(any(a.startswith('-') and not a.startswith('--') and 'k' in a for a in cmd), cmd)
        self.assertFalse(self.m.records.exists())                               # the view writes nothing on this machine

    def test_a_list_that_cannot_be_read_says_so(self):
        for listing, code in (('{not json', 0), (json.dumps(dict(devices='x')), 0), (json.dumps([]), 0), (json.dumps(dict(devices=[])), 22)):
            self.m.listing, self.m.curl_code, self.m.asked = listing, code, None
            got, doc = self.isle('role', 'list', '--isle')
            self.assertEqual((got, doc['ok']), (1, False), listing)
            self.assertIn('device list of the core cannot be read', doc['error'])
        self.assertEqual(len(self.m.asked), 2)                                  # refused twice: by its name, then as the core asking itself
        self.assertIn('api.polari.isle:443:127.0.0.1', self.m.asked[1])
        self.assertEqual(self.isle('role', 'list', '--isle', '--table')[0], 1)
        self.assertEqual(self.m.said, ['The device list of the core cannot be read from this machine.'])

    def test_other_words_with_isle_are_usage(self):
        for argv in (('partition', 'check', '--isle'), ('device', 'list', '--isle'), ('role', '--isle'), ('partition', 'assign', ARCH, '--role', 'git', '--isle')):
            self.assertEqual(self.m.call(*argv), 2, argv)
            self.assertIn('Usage:', self.m.text())
        self.assertIsNone(self.m.asked); self.assertIsNone(self.m.record())

    def test_through_the_public_dispatch(self):
        tools = self.m.tmp / 'bin'
        tools.mkdir()
        (tools / 'curl').write_text('#!/usr/bin/env python3\nprint(' + repr(json.dumps(dict(devices=self.m.listing))) + ')\n')
        (tools / 'curl').chmod(0o755)                                           # the made-up core answers; the real one is never asked
        env = dict(os.environ, PATH=str(tools) + ':' + os.environ['PATH'])
        result = subprocess.run(['node', str(roles_test.ENTRY), 'storage', 'role', 'show', 'git', '--isle'], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([(p['device'], p['filesystemUuid']) for p in json.loads(result.stdout)['result']['partitions']], [('pol', 'aaaa-0001')])


if __name__ == '__main__':
    unittest.main()
