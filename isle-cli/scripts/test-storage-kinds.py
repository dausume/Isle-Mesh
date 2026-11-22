"""isle storage kind list | need list | association list, and what `partition assign` says a machine lacks.

Fixtures only, the made-up machine of test-storage-roles.py.  Which programs are on its path and which images its
Docker holds are made up too: the real path and the real Docker are never asked.  No real disk, no real record and
no network is touched.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import storage_kinds as sk
import storage_roles as sr

_spec = importlib.util.spec_from_file_location('storage_roles_fixtures', HERE / 'test-storage-roles.py')
fx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fx)
ARCH, DOCK, ENTRY = fx.ARCH, fx.DOCK, fx.ENTRY

WEED, DOCKER = 'the program weed', 'the program docker'
POOL_IMAGE, REGISTRY_IMAGE, FORGE_IMAGE = ('an image of chrislusf/seaweedfs in Docker', 'an image of registry in Docker',
                                           'an image of codeberg.org/forgejo/forgejo in Docker')
LAST = 'Nothing was installed. See: isle storage kind list --table'


class Case(fx.Case):
    """The made-up machine, with a made-up path and a made-up Docker."""

    def setUp(self):
        super().setUp()
        self.programs, self.images, self.docker = set(), [], 0      # docker: its exit status, or an exception to raise
        self.looked, self.commands, self.options = [], [], []
        self.m.which, self.m.run = self.on_path, self.docker_says

    def on_path(self, program):
        self.looked.append(program)
        return '/made/up/' + program if program in self.programs else None

    def docker_says(self, command, **options):
        self.commands.append(command); self.options.append(options)
        if isinstance(self.docker, BaseException): raise self.docker
        # as Docker does: only the repository that was asked for, one line for each tag it holds
        return subprocess.CompletedProcess(command, self.docker, stdout=''.join(i + '\n' for i in self.images if i == command[-1]) if not self.docker else '',
                                           stderr='permission denied' if self.docker else '')

    def has(self, *programs, images=()):
        self.programs, self.images = set(programs), list(images)

    def look(self, kind):
        return sk.look(self.m)[kind]


class TheLists(unittest.TestCase):
    def test_every_role_points_at_entries_that_exist(self):
        self.assertEqual(sorted(sk.ROLE), sorted(sr.ROLES))
        for role, (kind, needs, associations) in sk.ROLE.items():
            self.assertIn(kind, sk.KINDS, role)
            self.assertTrue(needs and associations, role)
            for need in needs: self.assertIn(need, sk.NEEDS, role)
            for association in associations: self.assertIn(association, sk.ASSOCIATIONS, role)
            self.assertEqual((len(set(needs)), len(set(associations))), (len(needs), len(associations)), role)
            self.assertEqual(sk.of(role), dict(kind=kind, distribution=list(needs), association=list(associations)))
        self.assertEqual({kind for kind, _, _ in sk.ROLE.values()}, set(sk.KINDS))      # no kind that no role uses

    def test_the_table_as_it_was_agreed(self):
        self.assertEqual(list(sk.KINDS), ['plain-folder', 'file-pool', 'image-registry', 'git'])
        self.assertEqual(list(sk.NEEDS), ['kept', 'served', 'pooled', 'cached', 'installed', 'carried', 'taken'])
        self.assertEqual(list(sk.ASSOCIATIONS), ['isle', 'app', 'machine', 'release'])
        self.assertEqual({role: kind for role, (kind, _, _) in sk.ROLE.items()}, {
            'package-repo': 'plain-folder', 'distribution': 'plain-folder', 'backup': 'plain-folder', 'scratch': 'plain-folder',
            'transfer': 'plain-folder', 'blob': 'file-pool', 'assets': 'file-pool', 'registry': 'image-registry', 'git': 'git'})
        # a restore must not need the pool to be running: device-backups are a plain folder, kept on their machine
        self.assertEqual(sk.ROLE['backup'], ('plain-folder', ('kept',), ('machine',)))
        self.assertEqual(sk.ROLE['assets'], ('file-pool', ('pooled', 'cached', 'taken'), ('app', 'isle')))
        self.assertEqual(sk.ROLE['registry'], ('image-registry', ('served', 'cached'), ('isle',)))

    def test_what_a_machine_must_have_in_words(self):
        self.assertEqual({kind: sk.requires(kind) for kind in sk.KINDS}, {
            'plain-folder': 'nothing more',
            'file-pool': WEED + '; or ' + DOCKER + ' and ' + POOL_IMAGE,
            'image-registry': DOCKER + ' and ' + REGISTRY_IMAGE,
            'git': DOCKER + ' and ' + FORGE_IMAGE})


class WhatTheMachineHas(Case):
    def test_a_plain_folder_needs_nothing_and_nothing_is_asked(self):
        self.assertEqual(sk.look(self.m, ['plain-folder']), {'plain-folder': dict(state='ready', missing=[], notKnown=[])})
        self.assertEqual((self.looked, self.commands), ([], []))

    def test_only_the_kinds_asked_for_are_looked_at(self):
        self.has('docker')
        self.assertEqual(list(sk.look(self.m, ['git'])), ['git'])
        self.assertEqual([c[-1] for c in self.commands], ['codeberg.org/forgejo/forgejo'])
        self.assertEqual(list(sk.look(self.m)), list(sk.KINDS))

    def test_the_file_pool_by_its_one_program(self):
        self.has('weed')
        self.assertEqual(self.look('file-pool'), dict(state='ready', missing=[], notKnown=[]))
        self.assertEqual(self.look('image-registry'), dict(state='not ready', missing=[DOCKER, REGISTRY_IMAGE], notKnown=[]))
        self.assertEqual(self.commands, [])                              # without Docker on the path, Docker is never run

    def test_the_file_pool_by_its_image(self):
        self.has('docker', images=['chrislusf/seaweedfs'])
        self.assertEqual(self.look('file-pool'), dict(state='ready', missing=[], notKnown=[]))
        self.assertEqual(self.look('git'), dict(state='not ready', missing=[FORGE_IMAGE], notKnown=[]))

    def test_one_part_of_a_way_is_not_enough(self):
        self.has('docker')
        self.assertEqual(self.look('file-pool'), dict(state='not ready', missing=[WEED, POOL_IMAGE], notKnown=[]))
        self.has()
        self.assertEqual(self.look('file-pool'), dict(state='not ready', missing=[WEED, DOCKER, POOL_IMAGE], notKnown=[]))
        self.assertEqual(self.look('git'), dict(state='not ready', missing=[DOCKER, FORGE_IMAGE], notKnown=[]))

    def test_an_image_counts_by_its_whole_repository_name(self):
        self.has('docker', images=['registry'])
        self.assertEqual(self.look('image-registry')['state'], 'ready')
        self.images = ['myregistry', 'registry-mirror', 'example/registry']
        real = self.docker_says
        self.m.run = lambda command, **options: subprocess.CompletedProcess(command, 0, stdout='\n'.join(self.images) + '\n') if real(command, **options) else None
        self.assertEqual(self.look('image-registry'), dict(state='not ready', missing=[REGISTRY_IMAGE], notKnown=[]))
        self.images.append('registry')
        self.assertEqual(self.look('image-registry')['state'], 'ready')

    def test_docker_is_only_ever_asked_to_list(self):
        self.has('docker', images=['registry'])
        sk.look(self.m)
        self.assertEqual(self.commands, [['docker', 'image', 'ls', '--format', '{{.Repository}}', name]
                                         for name in ('chrislusf/seaweedfs', 'registry', 'codeberg.org/forgejo/forgejo')])
        self.assertEqual(self.options, [dict(capture_output=True, text=True, timeout=15)] * 3)

    def test_docker_that_does_not_answer_is_not_known_and_never_missing(self):
        self.has('docker')
        for silent in (1, subprocess.TimeoutExpired('docker', 15), PermissionError(13, 'denied')):
            self.docker = silent
            self.assertEqual(self.look('image-registry'), dict(state='not known', missing=[], notKnown=[REGISTRY_IMAGE]), silent)
            self.assertEqual(self.look('file-pool'), dict(state='not known', missing=[WEED], notKnown=[POOL_IMAGE]), silent)
        self.programs.add('weed')                                        # one whole way is there: the other is not needed
        self.assertEqual(self.look('file-pool'), dict(state='ready', missing=[], notKnown=[]))

    def test_a_way_with_a_part_that_is_missing_is_not_a_way_that_may_be_there(self):
        made_up = {'one-way': dict(servedBy='-', requires=((('program', 'absent'), ('image', 'made/up')),)),
                   'two-images': dict(servedBy='-', requires=((('program', 'docker'), ('image', 'made/a')), (('program', 'docker'), ('image', 'made/b'))))}
        with mock.patch.dict(sk.KINDS, made_up):
            self.has('docker'); self.docker = 1
            self.assertEqual(sk.look(self.m, ['one-way']), {'one-way': dict(
                state='not ready', missing=['the program absent'], notKnown=['an image of made/up in Docker'])})
            self.has()                                                   # a part named by two ways is said once
            self.assertEqual(sk.look(self.m, ['two-images'])['two-images'], dict(
                state='not ready', missing=[DOCKER, 'an image of made/a in Docker', 'an image of made/b in Docker'], notKnown=[]))

    def test_no_output_at_all_is_an_image_that_is_missing(self):
        self.has('docker')
        self.m.run = lambda command, **options: subprocess.CompletedProcess(command, 0, stdout=None)
        self.assertEqual(self.look('git'), dict(state='not ready', missing=[FORGE_IMAGE], notKnown=[]))


class TheListings(Case):
    def test_kind_list(self):
        self.has('docker', images=['registry'])
        before = self.m.listing()
        self.m.root_user = False
        self.assertEqual(self.m.call('kind', 'list'), 0)
        data = json.loads(self.m.said[0])
        self.assertEqual((data['ok'], data['operation'], data['readOnly'], data['evaluatedAt']), (True, 'storage.kind.list', True, '2026-01-01T00:00:00+00:00'))
        rows = data['result']['kinds']
        self.assertEqual([r['kind'] for r in rows], list(sk.KINDS))
        self.assertEqual(rows[0], dict(kind='plain-folder', servedBy='a web server on a folder, or nothing', requires='nothing more',
                                       roles=['package-repo', 'distribution', 'backup', 'scratch', 'transfer'], thisMachine='ready', missing=[], notKnown=[]))
        self.assertEqual((rows[1]['roles'], rows[1]['thisMachine'], rows[1]['missing']), (['blob', 'assets'], 'not ready', [WEED, POOL_IMAGE]))
        self.assertEqual((rows[2]['roles'], rows[2]['thisMachine'], rows[2]['requires']), (['registry'], 'ready', DOCKER + ' and ' + REGISTRY_IMAGE))
        self.assertEqual((rows[3]['roles'], rows[3]['servedBy']), (['git'], 'the forge (Forgejo)'))
        self.assertEqual(self.m.listing(), before)                       # a listing writes nothing, and needs no root

    def test_kind_list_as_a_table(self):
        self.has('docker', images=['registry'])
        self.assertEqual(self.m.call('kind', 'list', '--table'), 0)
        lines = self.m.text().splitlines()
        self.assertEqual(lines[0].split()[:4], ['KIND', 'ROLES', 'THIS', 'MACHINE'])
        self.assertEqual([line.split()[0] for line in lines[1:5]], list(sk.KINDS))
        self.assertIn('blob, assets', lines[2]); self.assertIn('not ready', lines[2])
        self.assertRegex(lines[3], r'^image-registry\s+registry\s+ready\s')
        self.assertEqual(lines[5:], ['For file-pool this machine does not have: %s; %s.' % (WEED, POOL_IMAGE),
                                     'For git this machine does not have: %s.' % FORGE_IMAGE])
        self.docker = 1
        self.assertEqual(self.m.call('--table', 'kind', 'list'), 0)
        lines = self.m.text().splitlines()
        self.assertRegex(lines[3], r'^image-registry\s+registry\s+not known\s')
        self.assertEqual(lines[5:], ['For file-pool this machine does not have: %s.' % WEED, sk.NO_ANSWER])

    def test_need_list(self):
        self.assertEqual(self.m.call('need', 'list'), 0)
        data = json.loads(self.m.said[0])
        self.assertEqual((data['ok'], data['operation'], data['readOnly']), (True, 'storage.need.list', True))
        rows = data['result']['needs']
        self.assertEqual([r['need'] for r in rows], list(sk.NEEDS))
        self.assertEqual(rows[0], dict(need='kept', meaning='used only on the machine it sits on', planDoes='nothing', roles=['backup', 'scratch']))
        self.assertEqual({r['need']: r['roles'] for r in rows}, dict(
            kept=['backup', 'scratch'], served=['package-repo', 'distribution', 'registry', 'git'], pooled=['blob', 'assets'],
            cached=['registry', 'assets'], installed=[], carried=['distribution', 'transfer'], taken=['assets']))
        self.assertEqual(self.m.call('need', 'list', '--table'), 0)
        lines = self.m.text().splitlines()
        self.assertEqual((len(lines), lines[0].split()[:3]), (8, ['DISTRIBUTION', 'NEED', 'ROLES']))
        self.assertRegex(lines[7], r'^taken\s+assets\s+reserve the original, and apply the edits when the copy comes back\s+a copy on one device')
        self.assertRegex(lines[5], r'^installed\s+-\s+run an install on each device\s')
        self.assertEqual((self.looked, self.commands), ([], []))         # these two lists say nothing about the machine

    def test_association_list(self):
        self.assertEqual(self.m.call('association', 'list'), 0)
        data = json.loads(self.m.said[0])
        self.assertEqual(data['operation'], 'storage.association.list')
        self.assertEqual({r['association']: r['roles'] for r in data['result']['associations']}, dict(
            isle=['package-repo', 'distribution', 'registry', 'blob', 'git', 'assets', 'transfer'], app=['blob', 'assets'],
            machine=['backup', 'scratch'], release=['distribution']))
        self.assertEqual(data['result']['associations'][3]['meaning'], 'one release (its packet)')
        self.assertEqual(self.m.call('association', 'list', '--table'), 0)
        lines = self.m.text().splitlines()
        self.assertEqual((len(lines), lines[0].split()[:2]), (5, ['BELONGS', 'TO']))
        self.assertRegex(lines[3], r'^machine\s+backup, scratch\s+one machine$')
        self.assertEqual((self.looked, self.commands), ([], []))

    def test_anything_else_is_the_usage(self):
        for argv in (('kind',), ('kind', 'show', 'git'), ('kind', 'list', 'extra'), ('need',), ('need', 'show'), ('association', 'list', 'isle'),
                     ('association', 'check'), ('kind', '--table')):
            self.assertEqual(self.m.call(*argv), 2, argv)
            self.assertIn('Usage', self.m.text())
            self.assertIn('isle storage kind list [--table] | isle storage need list [--table] | isle storage association list [--table]', self.m.text())
        self.assertEqual((self.looked, self.commands, self.m.record()), ([], [], None))

    def test_role_listings_carry_what_a_role_allows(self):
        self.m.ready()
        self.assertEqual(self.m.call('role', 'list'), 0)
        self.assertEqual({r['role']: r['kind'] for r in json.loads(self.m.said[0])['result']['roles']}, {role: sk.ROLE[role][0] for role in sr.ROLES})
        self.assertEqual(self.m.call('role', 'list', '--table'), 0)
        lines = self.m.text().splitlines()
        self.assertEqual(lines[0].split(), ['ROLE', 'KIND', 'PARTITIONS', 'FREE', 'DATASETS'])
        self.assertRegex(lines[3], r'^registry\s+image-registry\s+0\s')
        self.assertEqual(self.m.call('role', 'show', 'assets'), 0)
        result = json.loads(self.m.said[0])['result']
        self.assertEqual((result['kind'], result['distribution'], result['association']), ('file-pool', ['pooled', 'cached', 'taken'], ['app', 'isle']))
        self.assertEqual(self.m.call('role', 'show', 'package-repo', '--table'), 0)
        self.assertEqual(self.m.said[0], 'Role package-repo: storage of the kind plain-folder; distribution: served; belongs to: isle.')
        self.assertEqual(self.m.call('role', 'show', 'assets', '--table'), 0)
        self.assertEqual(self.m.said, ['Role assets: storage of the kind file-pool; distribution: pooled, cached, taken; belongs to: app, isle.',
                                       'No partition of this machine carries the role assets.'])
        self.assertEqual((self.looked, self.commands), ([], []))         # what a role allows is the table, not a look at the machine


class AfterAssign(Case):
    def setUp(self):
        super().setUp()
        self.m.ready()
        self.looked, self.commands = [], []

    def test_a_plain_folder_adds_nothing_and_asks_nothing(self):
        self.assertEqual(self.m.call('partition', 'assign', ARCH, '--role', 'backup,scratch'), 0)
        self.assertEqual([line.split()[0] for line in self.m.said], ['Partition', 'It', 'Datasets'], self.m.said)    # as it was before this file
        self.assertEqual((self.looked, self.commands), ([], []))

    def test_a_role_whose_kind_this_machine_cannot_serve_is_recorded_and_said(self):
        self.assertEqual(self.m.call('partition', 'assign', DOCK, '--role', 'registry'), 0)
        self.assertEqual(self.m.said, [
            'Partition sdc1 (%s) on device S3 now carries the role registry. No data was moved.' % DOCK,
            'The role registry is storage of the kind image-registry. A machine that holds it needs: %s and %s.' % (DOCKER, REGISTRY_IMAGE),
            '  This machine does not have: %s; %s.' % (DOCKER, REGISTRY_IMAGE), LAST])
        self.assertEqual(self.m.record()['partitions'][DOCK]['role'], 'registry')
        self.assertEqual(json.loads(self.m.marker('dock').read_text())['role'], 'registry')

    def test_only_the_kinds_that_are_not_ready_are_named(self):
        self.has('docker', images=['registry'])
        self.assertEqual(self.m.call('partition', 'assign', DOCK, '--role', 'registry,assets,scratch'), 0)
        self.assertEqual(self.m.said[1:], [
            'The role assets is storage of the kind file-pool. A machine that holds it needs: %s; or %s and %s.' % (WEED, DOCKER, POOL_IMAGE),
            '  This machine does not have: %s; %s.' % (WEED, POOL_IMAGE), LAST])
        self.assertEqual([c[-1] for c in self.commands], ['registry', 'chrislusf/seaweedfs'])       # the forge was not asked about

    def test_a_machine_that_has_everything_adds_nothing(self):
        self.has('docker', 'weed', images=['registry', 'codeberg.org/forgejo/forgejo'])
        self.assertEqual(self.m.call('partition', 'assign', DOCK, '--role', 'registry,assets,git'), 0)
        self.assertEqual(self.m.said, ['Partition sdc1 (%s) on device S3 now carries the roles registry, assets, git. No data was moved.' % DOCK])

    def test_two_roles_of_one_kind_are_named_together(self):
        self.assertEqual(self.m.call('partition', 'assign', DOCK, '--role', 'blob,git,assets'), 0)
        self.assertEqual(self.m.said[1:], [
            'The roles blob, assets are storage of the kind file-pool. A machine that holds it needs: %s; or %s and %s.' % (WEED, DOCKER, POOL_IMAGE),
            '  This machine does not have: %s; %s; %s.' % (WEED, DOCKER, POOL_IMAGE),
            'The role git is storage of the kind git. A machine that holds it needs: %s and %s.' % (DOCKER, FORGE_IMAGE),
            '  This machine does not have: %s; %s.' % (DOCKER, FORGE_IMAGE), LAST])

    def test_the_roles_it_carried_before_are_looked_at_too(self):
        self.assertEqual(self.m.call('partition', 'assign', ARCH, '--role', 'git'), 0)
        self.has('weed')                                                 # enough for the role that is added now, not for the one before
        self.assertEqual(self.m.call('partition', 'assign', ARCH, '--role', 'assets'), 0)
        self.assertIn('now carries the roles package-repo, git, assets', self.m.said[0])
        self.assertEqual(self.m.said[3:], [
            'The role git is storage of the kind git. A machine that holds it needs: %s and %s.' % (DOCKER, FORGE_IMAGE),
            '  This machine does not have: %s; %s.' % (DOCKER, FORGE_IMAGE), LAST])

    def test_a_kind_is_looked_at_once_for_all_its_roles(self):
        self.has('docker')
        self.assertEqual(self.m.call('partition', 'assign', DOCK, '--role', 'blob,assets'), 0)
        self.assertEqual([c[-1] for c in self.commands], ['chrislusf/seaweedfs'])

    def test_it_is_said_again_when_nothing_was_changed(self):
        self.assertEqual(self.m.call('partition', 'assign', DOCK, '--role', 'registry'), 0)
        before = (self.m.listing(), self.m.records.read_bytes(), self.m.marker('dock').read_bytes())
        self.assertEqual(self.m.call('partition', 'assign', DOCK, '--role', 'registry'), 0)
        self.assertEqual(self.m.said[0], 'Partition sdc1 (%s) already carries the role registry. Nothing was changed.' % DOCK)
        self.assertEqual(self.m.said[2:], ['  This machine does not have: %s; %s.' % (DOCKER, REGISTRY_IMAGE), LAST])
        self.assertEqual((self.m.listing(), self.m.records.read_bytes(), self.m.marker('dock').read_bytes()), before)

    def test_docker_that_does_not_answer_is_said_and_stops_nothing(self):
        self.has('docker'); self.docker = 1
        self.assertEqual(self.m.call('partition', 'assign', DOCK, '--role', 'git'), 0)
        self.assertEqual(self.m.said[1:], [
            'The role git is storage of the kind git. A machine that holds it needs: %s and %s.' % (DOCKER, FORGE_IMAGE),
            '  Not known: %s. %s' % (FORGE_IMAGE, sk.NO_ANSWER), LAST])
        self.assertEqual(self.m.record()['partitions'][DOCK]['role'], 'git')

    def test_without_root_and_on_a_refusal_the_machine_is_not_looked_at(self):
        self.m.root_user = False
        self.assertEqual(self.m.call('partition', 'assign', DOCK, '--role', 'registry'), 1)
        self.assertEqual(self.m.said, ['This changes the storage records of this machine. Type: sudo isle storage partition assign %s --role registry' % DOCK])
        self.m.root_user = True
        self.assertEqual(self.m.call('partition', 'assign', fx.ROOT, '--role', 'registry'), 1)
        self.assertEqual(len(self.m.said), 1)
        self.assertTrue(self.m.said[0].startswith('REFUSED'))
        self.assertEqual((self.looked, self.commands), ([], []))


class ThroughThePublicDispatch(Case):
    def test_the_three_lists(self):
        tools = self.m.tmp / 'bin'
        tools.mkdir()
        # a made-up docker that holds every image it is asked about, so the real Docker is never asked
        (tools / 'docker').write_text('#!/bin/sh\nfor last in "$@"; do :; done\necho "$last"\n')
        (tools / 'docker').chmod(0o755)
        env = dict(os.environ, PATH=str(tools) + ':' + os.environ['PATH'])
        for word, key, names in (('kind', 'kinds', sk.KINDS), ('need', 'needs', sk.NEEDS), ('association', 'associations', sk.ASSOCIATIONS)):
            result = subprocess.run(['node', str(ENTRY), 'storage', word, 'list'], env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([row[word] for row in json.loads(result.stdout)['result'][key]], list(names))
            if word == 'kind':
                self.assertEqual([row['thisMachine'] for row in json.loads(result.stdout)['result'][key]], ['ready'] * 4)
        result = subprocess.run(['node', str(ENTRY), 'storage', 'kind', 'show'], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Usage', result.stdout)

    def test_the_real_look_at_the_path(self):
        self.assertEqual(sr.Io().which('sh') is not None, True)
        self.assertIsNone(sr.Io().which('no-such-program-of-the-isle'))


if __name__ == '__main__':
    unittest.main()
