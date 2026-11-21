#!/usr/bin/env python3
"""isle os dependencies build | check: a fake bundle builder and fake packages in a temp folder.

Nothing is resolved, fetched or published, and no container is started.  A fake package is a JSON file with a
"Package" field; the fake builder writes the bundle the real one leaves (pool/repo with the isle's packages, the
package files of the resolve and the three CLOSURE_* lists), and a test can make it leave a file out or change one."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dependency_set as ds
import release_build as rb
import supported_os as so

RESOLVED = ('libfoo_1.0_amd64.deb', 'libbar_2%3a1_all.deb', 'zlib1g_1.2_amd64.deb')


def package(path, name):
    Path(path).write_text(json.dumps({'Package': name, 'Version': '1', 'pad': Path(path).name}))
    return Path(path)


class Fake(rb.Io):
    def __init__(self, tmp):
        self.tmp, self.lines, self.calls, self.root = Path(tmp), [], [], False
        self.exit = 0
        self.after = lambda repo: None          # what a test does to the bundle after the builder wrote it
        self.report = '[cache-report] apt cached_bytes=3145728 fetched_bytes=1048576 entries=3'

    def say(self, line=''): self.lines.append(line)
    def is_root(self): return self.root
    def text(self): return '\n'.join(self.lines)
    def builds(self): return [argv for argv, env in self.calls if argv[0] == 'bash']

    def run(self, argv, log=None, env=None, cwd=None):
        argv = [str(a) for a in argv]
        self.calls.append((argv, env))
        if argv[:2] == ['dpkg-deb', '-f']:
            try: return 0, str(json.loads(Path(argv[2]).read_text())[argv[3]]) + '\n'
            except (ValueError, KeyError, OSError): return 1, ''
        if argv[0] == 'bash':
            opt = {argv[i]: argv[i + 1] for i in range(2, len(argv) - 1) if argv[i].startswith('--')}
            with open(log, 'a') as out: out.write('resolving\nfetching\n')
            if self.exit:
                with open(log, 'a') as out: out.write('[FAIL] closure resolution failed\n')
                return self.exit, ''
            repo = Path(opt['--out']) / 'pool' / 'repo'
            repo.mkdir(parents=True)
            for deb in Path(opt['--debs-dir']).glob('*.deb'): (repo / deb.name).write_text(deb.read_text())
            rows = []
            for name in RESOLVED:
                data = package(repo / name, name.split('_')[0]).read_bytes()
                rows.append({'uri': 'http://archive.example/pool/' + name, 'file': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
            (repo / 'CLOSURE_METADATA.json').write_text(json.dumps({'schemaVersion': 1, 'source': 'signed-apt-print-uris-SHA256', 'packages': rows}))
            (repo / 'CLOSURE_URIS.txt').write_text(''.join(r['uri'] + '\n' for r in rows))
            (repo / 'CLOSURE_APT_RECORDS.txt').write_text('records\n')
            self.after(repo)
            with open(log, 'a') as out: out.write(self.report + '\n[ OK ] OFFLINE BUNDLE COMPLETE\n')
            return 0, ''
        raise AssertionError('a command the build should not run: %r' % argv)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(); self.addCleanup(self.dir.cleanup)
        self.tmp = Path(self.dir.name)
        self.io = Fake(self.tmp)
        self.packages = self.tmp / 'members'; self.packages.mkdir()
        package(self.packages / 'isle-mesh-cli_1_all.deb', 'isle-mesh-cli')
        package(self.packages / 'polari-complete-offline_1_amd64.deb', 'polari-complete-offline')
        self.builder = self.tmp / 'build-offline-bundle.sh'; self.builder.write_text('#!/bin/bash\n')
        self.out = self.tmp / 'set'

    def build(self, *more, system='ubuntu-22.04', out=None):
        return ds.main(['build', str(out or self.out), '--system', system, '--packages', str(self.packages),
                        '--bundle-builder', str(self.builder)] + [str(m) for m in more], self.io)

    def built(self):
        self.assertEqual(self.build(), 0, self.io.text())
        self.io.lines.clear(); self.io.calls.clear()
        return self.out

    def refused(self, words, status=1):
        self.assertEqual(status, 1)
        self.assertIn('REFUSED', self.io.text()); self.assertIn(words, self.io.text())


class Build(Base):
    def test_a_build_leaves_the_set_and_nothing_else(self):
        self.assertEqual(self.build(), 0, self.io.text())
        self.assertEqual(sorted(os.listdir(self.out)), sorted(RESOLVED + ds.LISTS + ('SHA256SUMS', 'ubuntu-22.04.txt')))
        self.assertEqual((self.out / 'ubuntu-22.04.txt').read_text(), 'ubuntu-22.04\n')
        sums = dict(reversed(line.split('  ')) for line in (self.out / 'SHA256SUMS').read_text().splitlines())
        self.assertEqual(sorted(sums), sorted(RESOLVED))
        for name, sha in sums.items(): self.assertEqual(hashlib.sha256((self.out / name).read_bytes()).hexdigest(), sha)
        self.assertFalse((self.tmp / 'set.work').exists())
        text = self.io.text()
        self.assertIn('The dependency set of ubuntu-22.04 (release name jammy): 3 package files', text)
        self.assertIn('Fetched now: 1 MiB. Taken from the cache: 3 MiB.', text)
        self.assertIn('from what the 2 package(s) of the isle in %s declare' % self.packages, text)
        self.assertIn('nothing is published', text)
        self.assertIn('isle release build <out-dir> --dependencies %s' % self.out, text)

    def test_the_builder_is_asked_for_that_system_with_its_names_and_the_whole_list(self):
        self.build()
        (argv,), ((_, env),) = self.io.builds(), [c for c in self.io.calls if c[0][0] == 'bash']
        entry = so.system_of(so.load(), 'ubuntu-22.04')
        roots = so.offline_packages(entry, 'install') + ',' + so.offline_packages(entry, 'cache-only')
        self.assertEqual(argv, ['bash', str(self.builder), '--debs-dir', str(self.packages), '--target', 'ubuntu-22.04', '--media', 'usb16',
                                '--out', str(self.tmp / 'set.work' / 'bundle'), '--closure', roots, '--download'])
        self.assertIn('libgtk-3-0,', roots); self.assertNotIn('t64', roots); self.assertIn('openssh-server', roots)
        self.assertEqual(env, {'POLARI_APT_CACHE': ''})          # a cache this shell happens to name is not used unasked

    def test_a_cache_is_handed_to_the_builder(self):
        self.assertEqual(self.build('--cache', self.tmp / 'kept'), 0, self.io.text())
        self.assertEqual(self.io.calls[-1][1] if self.io.calls[-1][0][0] == 'bash' else
                         [env for argv, env in self.io.calls if argv[0] == 'bash'][0], {'POLARI_APT_CACHE': str(self.tmp / 'kept')})

    def test_only_what_the_resolve_names_enters_the_set(self):
        self.io.after = lambda repo: package(repo / 'stray_1_amd64.deb', 'stray')
        self.assertEqual(self.build(), 0, self.io.text())
        names = os.listdir(self.out)
        self.assertNotIn('stray_1_amd64.deb', names)
        self.assertFalse([n for n in names if n.startswith(('isle-', 'polari-'))])

    def test_a_packet_can_be_built_from_the_set(self):
        self.built()
        chosen = rb.chosen_set(self.io, self.out)
        self.assertEqual((chosen['system'], chosen['release'], chosen['packages']), ('ubuntu-22.04', 'jammy', 3))

    def test_an_empty_out_folder_is_used(self):
        self.out.mkdir()
        self.assertEqual(self.build(), 0, self.io.text())
        self.assertIn('SHA256SUMS', os.listdir(self.out))

    def test_the_newest_system_keeps_the_newest_names(self):
        self.assertEqual(self.build(system='ubuntu-24.04'), 0, self.io.text())
        self.assertIn('libgtk-3-0t64', self.io.builds()[0][self.io.builds()[0].index('--closure') + 1])
        self.assertIn('ubuntu-24.04.txt', os.listdir(self.out))
        self.assertIn('release name noble', self.io.text())


class Refusals(Base):
    def nothing(self, words, *more, **named):
        self.io.lines.clear(); self.io.calls.clear()
        before = sorted(str(p) for p in self.tmp.rglob('*'))
        self.assertEqual(self.build(*more, **named), 1, self.io.text())
        self.refused(words)
        self.assertEqual(self.io.builds(), [])
        self.assertEqual(sorted(str(p) for p in self.tmp.rglob('*')), before)

    def test_refused_before_anything_is_run_or_written(self):
        self.io.root = True; self.nothing('run without sudo'); self.io.root = False
        self.nothing('debian-12 is not in the record of supported operating systems', system='debian-12')
        full = self.tmp / 'full'; full.mkdir(); (full / 'x').write_text('x')
        self.nothing('exists and is not empty', out=full)
        self.nothing('exists and is not empty', out=self.builder)
        (self.tmp / 'set.work').mkdir()
        self.nothing('is not the work folder of a build')
        (self.tmp / 'set.work').rmdir()
        kept = self.tmp / 'kept'; kept.write_text('a file')
        self.nothing('--cache %s is not a folder' % kept, '--cache', kept)
        builder, self.builder = self.builder, self.tmp / 'nowhere.sh'
        self.nothing('name it: --bundle-builder <the suite>/build-offline-bundle.sh')
        self.builder = builder
        stray = package(self.packages / 'libfoo_1_amd64.deb', 'libfoo')
        self.nothing('holds libfoo_1_amd64.deb, which is not a package of the isle')
        stray.write_text('not a package')
        self.nothing('cannot be read as a package')
        stray.unlink()
        for deb in self.packages.glob('*.deb'): deb.unlink()
        self.nothing('holds no .deb file')
        self.packages.rmdir()
        self.nothing('--packages %s is not a folder' % self.packages)

    def test_words_that_are_not_the_verb_print_the_usage(self):
        base = ['build', str(self.out), '--system', 'ubuntu-22.04', '--packages', str(self.packages)]
        for argv in (['build'], base[:4], base[:2] + base[4:], base + ['--system', 'ubuntu-24.04'], base + ['--fast'], base + ['other'],
                     base + ['--cache'], ['check'], ['check', 'a', 'b'], ['fetch', 'x']):
            self.io.lines.clear()
            self.assertEqual(ds.main(argv, self.io), 2, argv)
            self.assertEqual(self.io.text(), ds.USAGE)
        self.assertEqual(self.io.calls, [])
        self.assertFalse(self.out.exists())

    def test_help(self):
        self.assertEqual(ds.main(['help'], self.io), 0)
        for line in ds.USAGE.replace('usage: ', '').splitlines(): self.assertIn(line.strip(), self.io.text())
        self.io.lines.clear()
        self.assertEqual(ds.main([], self.io), 2)
        self.assertIn('isle os dependencies check <set dir>', self.io.text())


class AStoppedBuilder(Base):
    def test_its_last_lines_and_its_log_are_said_and_the_next_build_starts_clean(self):
        self.io.exit = 7
        self.assertEqual(self.build(), 1)
        work = self.tmp / 'set.work'
        self.refused('the builder stopped (exit 7)')
        self.assertIn('    [FAIL] closure resolution failed', self.io.text())
        self.assertIn('The whole log: %s' % (work / 'bundle.log'), self.io.text())
        self.assertFalse(self.out.exists())
        self.assertTrue((work / 'bundle.log').is_file())
        self.io.exit = 0; self.io.lines.clear()
        self.assertEqual(self.build(), 0, self.io.text())
        self.assertIn('Removing the work folder a build that stopped left', self.io.text())
        self.assertFalse(work.exists())
        self.assertNotIn('closure resolution failed', self.io.text())

    def change(self, repo, name, how):
        doc = json.loads((repo / name).read_text()); doc['pad'] = how(doc['pad']); (repo / name).write_text(json.dumps(doc))

    def test_what_the_builder_made_is_checked_before_a_set_exists(self):
        def lose(name): return lambda repo: (repo / name).unlink()
        def meta(edit):
            def after(repo):
                doc = json.loads((repo / 'CLOSURE_METADATA.json').read_text()); edit(doc); (repo / 'CLOSURE_METADATA.json').write_text(json.dumps(doc))
            return after
        cases = [
            (lose('CLOSURE_URIS.txt'), 'has no CLOSURE_URIS.txt'),
            (lose('CLOSURE_METADATA.json'), 'has no CLOSURE_METADATA.json'),
            (lambda repo: (repo / 'CLOSURE_METADATA.json').write_text('{'), 'is not the list of a resolve'),
            (meta(lambda doc: doc.update(packages=[])), 'is not the list of a resolve'),
            (meta(lambda doc: doc['packages'][0].update(file='../up.deb')), 'is not the list of a resolve'),
            (meta(lambda doc: doc['packages'][0].update(sha256='x')), 'is not the list of a resolve'),
            (meta(lambda doc: doc['packages'].append(dict(doc['packages'][0]))), 'is not the list of a resolve'),
            (lose(RESOLVED[1]), 'lacks %s, which the resolve names' % RESOLVED[1]),
            (lambda repo: self.change(repo, RESOLVED[0], lambda pad: pad.upper()), '%s is not the file the signed index names' % RESOLVED[0]),
            (lambda repo: self.change(repo, RESOLVED[2], lambda pad: pad + '!'), '%s is not the file the signed index names' % RESOLVED[2]),
        ]
        for after, words in cases:
            self.io.after = after; self.io.lines.clear()
            self.assertEqual(self.build(), 1, words)
            self.refused(words)
            self.assertFalse(self.out.exists(), words)


class Check(Base):
    def check(self): self.io.lines.clear(); return ds.main(['check', str(self.out)], self.io)

    def test_a_built_set_is_read_and_nothing_changes(self):
        self.built()
        before = sorted((p.name, p.stat().st_mtime_ns, p.read_bytes()) for p in self.out.iterdir())
        self.assertEqual(self.check(), 0, self.io.text())
        self.assertIn('The dependency set of ubuntu-22.04 (release name jammy): 3 package files', self.io.text())
        self.assertIn('Nothing was changed.', self.io.text())
        self.assertEqual(sorted((p.name, p.stat().st_mtime_ns, p.read_bytes()) for p in self.out.iterdir()), before)
        self.assertEqual(self.io.builds(), [])

    def sums(self, edit):
        lines = dict(reversed(line.split('  ')) for line in (self.out / 'SHA256SUMS').read_text().splitlines())
        edit(lines)
        (self.out / 'SHA256SUMS').write_text(''.join('%s  %s\n' % (sha, name) for name, sha in sorted(lines.items())))

    def test_a_set_that_is_not_what_was_resolved_is_refused(self):
        def changed():
            doc = json.loads((self.out / RESOLVED[0]).read_text()); doc['pad'] = 'other'; text = json.dumps(doc)
            (self.out / RESOLVED[0]).write_text(text)
            self.sums(lambda lines: lines.update({RESOLVED[0]: hashlib.sha256(text.encode()).hexdigest()}))
        def added():
            data = package(self.out / 'stray_1_amd64.deb', 'stray').read_bytes()
            self.sums(lambda lines: lines.update({'stray_1_amd64.deb': hashlib.sha256(data).hexdigest()}))
        def gone():
            (self.out / RESOLVED[1]).unlink(); self.sums(lambda lines: lines.pop(RESOLVED[1]))
        cases = [
            (changed, '%s is not the file the signed index names' % RESOLVED[0]),
            (added, 'holds stray_1_amd64.deb, which the signed index of the resolve does not name'),
            (gone, '%s is missing; the resolve names it' % RESOLVED[1]),
            (lambda: (self.out / 'CLOSURE_METADATA.json').unlink(), 'has no CLOSURE_METADATA.json; a built set carries it'),
            (lambda: (self.out / 'CLOSURE_METADATA.json').write_text('[]'), 'CLOSURE_METADATA.json is not the list of a resolve'),
            (lambda: (self.out / 'SHA256SUMS').unlink(), 'has no SHA256SUMS; a built set carries it'),
            (lambda: (self.out / RESOLVED[2]).write_text('x'), 'cannot be read as a package'),
            (lambda: (self.out / 'ubuntu-22.04.txt').unlink(), 'has to say the one system it is built for'),
            (lambda: package(self.out / 'isle-mesh-cli_1_all.deb', 'isle-mesh-cli'), 'a package of the isle itself'),
            (lambda: (self.out / 'sub').mkdir(), 'sub is not a regular file'),
        ]
        for spoil, words in cases:
            if self.out.exists():
                for p in self.out.iterdir(): p.rmdir() if p.is_dir() else p.unlink()
                self.out.rmdir()
            self.built(); spoil()
            self.assertEqual(self.check(), 1, words)
            self.refused(words)
            self.assertIn(str(self.out), self.io.text())
            self.assertNotIn('--dependencies', self.io.text())
        self.assertEqual(ds.main(['check', str(self.tmp / 'nowhere')], self.io), 1)
        self.assertIn('is not a folder', self.io.text())


class TheVerb(unittest.TestCase):
    def test_isle_os_hands_dependencies_to_this_code_and_keeps_its_own_verbs(self):
        done = subprocess.run(['bash', str(HERE / 'os.sh'), 'dependencies', 'help'], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('isle os dependencies build <out-dir> --system <system id> --packages <dir>', done.stdout)
        done = subprocess.run(['bash', str(HERE / 'os.sh'), 'dependencies'], capture_output=True, text=True)
        self.assertEqual(done.returncode, 2)
        done = subprocess.run(['bash', str(HERE / 'os.sh'), 'list', '--table'], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('ubuntu-22.04', done.stdout)


if __name__ == '__main__':
    unittest.main()
