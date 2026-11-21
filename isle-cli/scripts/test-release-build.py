#!/usr/bin/env python3
"""isle release build: fake builders, a fake git and fake packages in a temp folder; nothing is built, fetched or installed.

A fake package is a JSON file: {"Package", "Version", "files": {path: text}, "links": {path: target}}.  The fake
builders read the unpacked commit the way the real ones do (from the folder their script lies in), so a test can make
a builder leave a file out or change a byte and see the build refuse."""
import contextlib
import io as textio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import release_build as rb
import release_run as rr

COMMIT = 'c' * 40
TREE = {'isle-cli/scripts/release_run.py': 'run\n', 'isle-cli/apt/supported-os.json': '{}\n', 'isle-agent/agent.py': 'agent\n',
        'isle-cli/shells/build-cli-deb.sh': 'cli builder\n', 'isle-cli/shells/tools/build-store-deb.sh': 'store builder\n',
        'openwrt-router/router-setup/images/router.img': 'an image\n', 'mdns/old.backup': 'left out\n',
        'README.md': 'outside the packed folders\n', 'isle-release-follow.timer': 'unit\n'}
LINKS = {'polari-isle/current': 'releases/one'}


def package(path, name, version, files=None, links=None):
    Path(path).write_text(json.dumps({'Package': name, 'Version': version, 'files': files or {}, 'links': links or {}}))
    return Path(path)


class Fake(rb.Io):
    def __init__(self, tmp):
        self.tmp, self.lines, self.calls, self.root, self.dirty = Path(tmp), [], [], False, ''
        self.tree, self.links, self.commit = dict(TREE), dict(LINKS), COMMIT
        self.root_ca = str(self.tmp / 'root.crt'); Path(self.root_ca).write_text('certificate\n')
        self.fail = None                       # a word of a builder's file name: that builder exits 1
        self.cli_files = lambda files: files   # what the CLI builder does to the files it packs
        self.combined_files = lambda files: files
        self.cli_version = self.combined_version = None
        self.extra_cli = False

    def say(self, line=''): self.lines.append(line)
    def is_root(self): return self.root
    def now(self): return '2026-01-01T00:00:00Z'
    def text(self): return '\n'.join(self.lines)

    def run(self, argv, log=None, env=None, cwd=None):
        argv = [str(a) for a in argv]
        self.calls.append((argv, env))
        if argv[:2] == ['dpkg-deb', '-f']:
            try: return 0, str(json.loads(Path(argv[2]).read_text())[argv[3]]) + '\n'
            except (ValueError, KeyError, OSError): return 1, ''
        if argv[:2] == ['dpkg-deb', '-x']:
            deb = json.loads(Path(argv[2]).read_text())
            self._write(Path(argv[3]), deb['files'], deb['links'])
            return 0, ''
        if argv[0] == 'git':
            verb = argv[3]
            if verb == 'rev-parse': return (0, self.commit + '\n') if self.commit else (1, '')
            if verb == 'status': return 0, self.dirty
            if verb == 'ls-tree': return 0, '\n'.join(sorted(list(self.tree) + list(self.links) + ['embed-jinja'])) + '\n'
            if verb == 'archive':
                Path(argv[argv.index('-o') + 1]).write_text(json.dumps([self.tree, self.links]))
                return 0, ''
        if argv[0] == 'tar':
            tree, links = json.loads(Path(argv[2]).read_text())
            self._write(Path(argv[4]), tree, links)
            return 0, ''
        if argv[0] == 'bash':
            script = Path(argv[1])
            if self.fail and self.fail in script.name: return 1, ''
            opt = {argv[i]: argv[i + 1] for i in range(2, len(argv) - 1, 2)}
            if script.name == 'build-cli-deb.sh':
                repo, files, links = script.parents[2], {}, {}
                for full in sorted(repo.rglob('*')):
                    rel = full.relative_to(repo).as_posix()
                    if not rel.startswith(rb.PACKED) or rb.LEFT_OUT.search(rel) or full.is_dir() and not full.is_symlink(): continue
                    if full.is_symlink(): links[rb.SHARE + '/' + rel] = os.readlink(full)
                    else: files[rb.SHARE + '/' + rel] = full.read_text()
                version = self.cli_version or opt['--version']
                package(Path(opt['--output']) / ('isle-mesh-cli_%s_all.deb' % version), rb.CLI, version, self.cli_files(files), links)
                if self.extra_cli: package(Path(opt['--output']) / 'isle-mesh-cli_0.0.1_all.deb', rb.CLI, '0.0.1')
                return 0, ''
            if script.name == 'build-store-deb.sh':
                package(Path(opt['--output']) / ('isle-app-store_%s_all.deb' % opt['--version']), rb.STORE, opt['--version'],
                        {'usr/share/isle-app-store/isle-root.crt': Path(opt['--ca']).read_text()})
                return 0, ''
            if '--debs-dir' in opt:
                folder, files, links, version = Path(opt['--debs-dir']), {}, {}, None
                for deb in sorted(folder.glob('*.deb')):
                    doc = json.loads(deb.read_text())
                    files.update(doc['files']); links.update(doc['links'])
                    if doc['Package'] == rb.STORE: version = doc['Version']
                version = self.combined_version or version
                package(folder / ('polari-complete-offline_%s_amd64.deb' % version), rb.COMBINED, version, self.combined_files(files), links)
                return 0, ''
        raise AssertionError('a command the build should not run: %r' % argv)

    @staticmethod
    def _write(root, files, links):
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True); (root / rel).write_text(text)
        for rel, target in links.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True); os.symlink(target, root / rel)


class Build(unittest.TestCase):
    def setUp(self):
        self.holder = tempfile.TemporaryDirectory(); self.addCleanup(self.holder.cleanup)
        self.tmp = Path(self.holder.name)
        self.io = Fake(self.tmp)
        self.previous = self.tmp / 'previous'; self.previous.mkdir()
        package(self.previous / 'isle-mesh-cli_0.1.210+release90_all.deb', rb.CLI, '0.1.210+release90')
        package(self.previous / 'isle-app-store_0.1.90+release90_all.deb', rb.STORE, '0.1.90+release90')
        package(self.previous / 'polari-shell-core_0.1.50+release86_amd64.deb', 'polari-shell-core', '0.1.50+release86', {'opt/shell/run': 'shell\n'})
        package(self.previous / 'polari-isle_0.1.0_all.deb', 'polari-isle', '0.1.0')
        package(self.previous / 'polari-complete-offline_0.1.90+release90_amd64.deb', rb.COMBINED, '0.1.90+release90')
        self.source = self.tmp / 'checkout'; self.source.mkdir()
        self.builder = self.tmp / 'build-polari-complete-deb.sh'; self.builder.write_text('combined builder\n')
        self.out = self.tmp / 'out'

    def build(self, *more, out=None):
        return rb.main([str(out or self.out), '--previous', str(self.previous), '--source', str(self.source)] + list(more), self.io)

    def refused(self, words, *more):
        self.assertEqual(self.build(*more), 1, self.io.text())
        self.assertIn('REFUSED', self.io.text()); self.assertIn(words, self.io.text())
        self.assertFalse((self.out / 'packet' / 'SHA256SUMS').exists(), 'a refused build left something that reads as a packet')

    def test_builds_the_next_release_as_a_packet_the_runner_takes(self):
        self.assertEqual(self.build(), 0, self.io.text())
        pk = rr.load_packet(self.out / 'packet')
        self.assertEqual(sorted(os.listdir(self.out / 'packet' / 'debs')),
                         ['isle-app-store_0.1.91+release91_all.deb', 'isle-mesh-cli_0.1.211+release91_all.deb',
                          'polari-complete-offline_0.1.91+release91_amd64.deb'])
        self.assertEqual(pk.doc['name'], 'cli-release-91')
        self.assertEqual(pk.doc['devices'], {'*': [{'verb': 'update-apply', 'releaseId': 'published'}]})
        self.assertEqual([c['url'] for c in pk.doc['verify']], list(rb.VERIFY))
        self.assertIn('Run id: ' + pk.run_id, self.io.text())
        self.assertIn('--expect ' + pk.run_id, self.io.text())
        self.assertIn('names no device', self.io.text())
        self.assertIn('nothing is published or installed', self.io.text())

    def test_members_folder_is_the_previous_of_the_next_build(self):
        self.assertEqual(self.build(), 0, self.io.text())
        members = self.out / 'members'
        self.assertEqual(sorted(os.listdir(members)),
                         ['isle-app-store_0.1.91+release91_all.deb', 'isle-mesh-cli_0.1.211+release91_all.deb',
                          'polari-complete-offline_0.1.91+release91_amd64.deb', 'polari-isle_0.1.0_all.deb',
                          'polari-shell-core_0.1.50+release86_amd64.deb'])
        self.assertEqual((members / 'polari-isle_0.1.0_all.deb').read_bytes(), (self.previous / 'polari-isle_0.1.0_all.deb').read_bytes())
        again = Fake(self.tmp)
        self.assertEqual(rb.main([str(self.tmp / 'out2'), '--previous', str(members), '--source', str(self.source),
                                  '--combined-builder', str(self.builder)], again), 0, again.text())
        self.assertIn('isle-mesh-cli_0.1.212+release92_all.deb', os.listdir(self.tmp / 'out2' / 'packet' / 'debs'))

    def test_the_record_says_what_was_built_from_what(self):
        self.io.dirty = ' M isle-cli/scripts/a.py\n?? new-file\n'
        self.assertEqual(self.build(), 0, self.io.text())
        record = json.loads((self.out / 'build.json').read_text())
        self.assertEqual((record['release'], record['source']['commit'], record['source']['uncommittedFilesLeftOut']), (91, COMMIT, 2))
        self.assertEqual(record['runId'], rr.load_packet(self.out / 'packet').run_id)
        self.assertEqual(record['combinedBuilder']['sha256'], rr.sha256_file(self.builder))
        rows = {row['name']: row for row in record['packages']}
        self.assertEqual({n for n, r in rows.items() if r['reused']}, {'polari-shell-core', 'polari-isle'})
        self.assertEqual({n for n, r in rows.items() if r['published']}, {rb.CLI, rb.STORE, rb.COMBINED})
        for row in rows.values(): self.assertEqual(row['sha256'], rr.sha256_file(self.out / 'members' / row['file']))
        self.assertIn('NOT in this build: 2 changed or new files', self.io.text())

    def test_the_commit_is_built_not_the_checkout(self):
        (self.source / 'isle-cli').mkdir(); (self.source / 'isle-cli' / 'stray.py').write_text('not committed\n')
        self.assertEqual(self.build(), 0, self.io.text())
        self.assertFalse((self.out / 'source' / 'isle-cli' / 'stray.py').exists())
        self.assertEqual((self.out / 'source' / 'isle-cli/scripts/release_run.py').read_text(), 'run\n')
        scripts = [argv[1] for argv, _ in self.io.calls if argv[0] == 'bash' and 'isle-cli/shells' in argv[1]]
        self.assertEqual(len(scripts), 2)
        for script in scripts: self.assertTrue(script.startswith(str(self.out / 'source')), script)

    def test_builders_get_the_fixed_packing_settings_and_this_isles_root(self):
        self.assertEqual(self.build(), 0, self.io.text())
        ran = [(argv, env) for argv, env in self.io.calls if argv[0] == 'bash']
        self.assertEqual(len(ran), 3)
        for argv, env in ran: self.assertEqual(env, rb.BUILD_ENV)
        store = next(argv for argv, _ in ran if argv[1].endswith('build-store-deb.sh'))
        self.assertEqual(store[store.index('--ca') + 1], self.io.root_ca)

    def test_what_the_builder_leaves_out_is_not_compared_and_the_rest_is(self):
        self.assertEqual(self.build(), 0, self.io.text())
        self.assertIn('The 6 files the CLI package packs', self.io.text())          # 5 files and 1 link of TREE and LINKS
        self.assertEqual(json.loads((self.out / 'build.json').read_text())['source']['comparedFiles'], 6)

    def test_refuses_a_cli_package_that_lacks_a_file_of_the_commit(self):
        self.io.cli_files = lambda files: {k: v for k, v in files.items() if not k.endswith('supported-os.json')}
        self.refused('does not hold the commit: 1 of 6 files differ or are missing (first: isle-cli/apt/supported-os.json)')

    def test_refuses_a_cli_package_with_one_changed_byte(self):
        self.io.cli_files = lambda files: {k: (v + ' ' if k.endswith('agent.py') else v) for k, v in files.items()}
        self.refused('isle-mesh-cli_0.1.211+release91_all.deb does not hold the commit')

    def test_refuses_a_combined_package_that_differs(self):
        self.io.combined_files = lambda files: {k: (v.upper() if k.endswith('release_run.py') else v) for k, v in files.items()}
        self.refused('polari-complete-offline_0.1.91+release91_amd64.deb does not hold the commit')

    def test_refuses_a_link_that_became_a_file(self):
        self.io.cli_files = lambda files: dict(files, **{rb.SHARE + '/polari-isle/current': 'releases/one'})
        original = self.io.run
        def run(argv, **more):
            if [str(a) for a in argv[:2]] == ['dpkg-deb', '-x']:
                deb = json.loads(Path(argv[2]).read_text()); deb['links'] = {}
                Fake._write(Path(argv[3]), deb['files'], {}); return 0, ''
            return original(argv, **more)
        self.io.run = run
        self.refused('does not hold the commit')

    def test_refuses_a_builder_that_fails_and_names_the_log(self):
        self.io.fail = 'build-store-deb'
        self.refused('isle-app-store 0.1.91+release91 failed; what it printed is in ' + str(self.out / 'build.log'))
        self.assertIn('It is not a packet; build again into another folder', self.io.text())
        self.io.lines = []
        self.assertEqual(self.build(), 1)
        self.assertIn('exists and is not empty', self.io.text())

    def test_refuses_a_builder_that_made_another_version(self):
        self.io.cli_version = '0.1.999+release91'
        self.refused('isle-mesh-cli was built as 0.1.999+release91, not as 0.1.211+release91')

    def test_refuses_a_combined_package_of_another_version(self):
        self.io.combined_version = '0.1.90+release90'
        self.refused('polari-complete-offline was built as 0.1.90+release90, not as 0.1.91+release91')

    def test_refuses_two_packages_of_one_name_after_the_build(self):
        self.io.extra_cli = True
        self.refused('the build left 2 isle-mesh-cli packages, not one')

    def test_refuses_before_writing_anything(self):
        cases = []
        def case(words, change, *more): cases.append((words, change, more))
        case('exists and is not empty', lambda: (self.out.mkdir(), (self.out / 'kept').write_text('x')))
        case('a build is run without sudo', lambda: setattr(self.io, 'root', True))
        case('holds no isle-mesh-cli package', lambda: (self.previous / 'isle-mesh-cli_0.1.210+release90_all.deb').unlink())
        case('holds no isle-app-store package', lambda: (self.previous / 'isle-app-store_0.1.90+release90_all.deb').unlink())
        case('holds two isle-mesh-cli packages', lambda: package(self.previous / 'isle-mesh-cli_0.1.209+release89_all.deb', rb.CLI, '0.1.209+release89'))
        case('which is not <a>.<b>.<c>+release<n>', lambda: package(self.previous / 'isle-mesh-cli_0.1.210+release90_all.deb', rb.CLI, '0.1.210'))
        case('they are not one build', lambda: package(self.previous / 'isle-app-store_0.1.90+release90_all.deb', rb.STORE, '0.1.89+release89'))
        case('cannot be read as a package', lambda: (self.previous / 'broken.deb').write_text('not a package'))
        case('is not a folder', lambda: None, '--previous', str(self.tmp / 'nowhere'))
        case('is not a checkout of the isle repository that holds the commit', lambda: setattr(self.io, 'commit', None))
        case('the builder of the combined package is not at', lambda: self.builder.unlink())
        case('cannot be read here', lambda: os.unlink(self.io.root_ca))
        case('is not a device name', lambda: None, '--device', 'not a name')
        case('must be an https:// URL on a .isle host', lambda: None, '--verify', 'https://example.org/x')
        case('must be an https:// URL on a .isle host', lambda: None, '--verify', 'http://apt.isle/x')
        for words, change, more in cases:
            with self.subTest(words):
                self.holder.cleanup(); self.setUp()
                change()
                before = sorted(p.as_posix() for p in self.tmp.rglob('*'))
                self.assertEqual(self.build(*more), 1, self.io.text())
                self.assertIn('REFUSED', self.io.text()); self.assertIn(words, self.io.text())
                self.assertEqual(sorted(p.as_posix() for p in self.tmp.rglob('*')), before, 'a refusal wrote something')
                self.assertFalse([argv for argv, _ in self.io.calls if argv[0] in ('bash', 'tar')], 'a refusal started a builder')

    def test_named_devices_backup_first_and_more_checks(self):
        self.assertEqual(self.build('--device', 'core-one', '--device', 'member-two', '--backup',
                                    '--verify', 'https://apt.isle/deps/any/InRelease', '--verify', rb.VERIFY[0]), 0, self.io.text())
        doc = rr.load_packet(self.out / 'packet').doc
        steps = [{'verb': 'backup'}, {'verb': 'update-apply', 'releaseId': 'published'}]
        self.assertEqual(doc['devices'], {'core-one': steps, 'member-two': steps})
        self.assertEqual([c['url'] for c in doc['verify']], list(rb.VERIFY) + ['https://apt.isle/deps/any/InRelease'])
        self.assertNotIn('names no device', self.io.text())
        self.assertNotIn('No device takes a device-backup', self.io.text())

    def test_without_backup_that_is_said(self):
        self.assertEqual(self.build(), 0, self.io.text())
        self.assertIn('No device takes a device-backup in this run', self.io.text())

    def test_a_commit_can_be_named(self):
        self.assertEqual(self.build('--commit', 'some-branch'), 0, self.io.text())
        asked = next(argv for argv, _ in self.io.calls if argv[0] == 'git' and argv[3] == 'rev-parse')
        self.assertEqual(asked[-1], 'some-branch^{commit}')
        archived = next(argv for argv, _ in self.io.calls if argv[0] == 'git' and argv[3] == 'archive')
        self.assertEqual(archived[-1], COMMIT)

    def test_the_default_builder_lies_beside_the_checkout(self):
        self.assertEqual(self.build(), 0, self.io.text())             # setUp put it in the folder that holds the checkout
        self.builder.rename(self.tmp / 'elsewhere.sh')
        other = Fake(self.tmp)
        self.assertEqual(rb.main([str(self.tmp / 'o2'), '--previous', str(self.previous), '--source', str(self.source),
                                  '--combined-builder', str(self.tmp / 'elsewhere.sh')], other), 0, other.text())

    def test_wrong_words_are_answered_with_the_usage(self):
        for argv in ([], [str(self.out)], ['a', 'b', '--previous', 'p'], [str(self.out), '--previous'], [str(self.out), '--previous', 'p', '--nope'],
                     ['--nope', '--previous', 'p']):
            with self.subTest(argv):
                io = Fake(self.tmp)
                self.assertEqual(rb.main(argv, io), 2)
                self.assertIn('usage: isle release build', io.text())
                self.assertFalse(io.calls)

    def test_help_and_the_release_verb_reach_it(self):
        io = Fake(self.tmp)
        self.assertEqual(rb.main(['help'], io), 0)
        self.assertIn('--previous <dir>', io.text())
        said = []
        deps = type('D', (), {'say': lambda self, line='': said.append(line)})()
        self.assertEqual(rr.main(['help'], deps), 0)
        self.assertIn('isle release build <out-dir> --previous <dir>', '\n'.join(said))
        self.assertIn('build|run|', (HERE / 'release.sh').read_text())
        printed = textio.StringIO()
        with contextlib.redirect_stdout(printed): status = rr.main(['build', 'help'], deps)
        self.assertEqual(status, 0)
        self.assertIn('It leaves in <out-dir>:', printed.getvalue())


if __name__ == '__main__':
    unittest.main()
