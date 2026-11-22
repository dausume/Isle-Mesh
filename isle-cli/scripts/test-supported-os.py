#!/usr/bin/env python3
"""isle os: the record of supported operating systems, this machine's place in it, and what the machines report.

Nothing here reads this machine's own system, the network or the core: every input is a fixture.
"""
import copy, json, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import supported_os as so

RECORD = json.loads(so.RECORD.read_text())
JAMMY = 'NAME="Ubuntu"\nVERSION_ID="22.04"\nID=ubuntu\nID_LIKE=debian\nVERSION_CODENAME=jammy\nUBUNTU_CODENAME=jammy\n'
NOBLE = 'PRETTY_NAME="Ubuntu 24.04.1 LTS"\nVERSION_ID="24.04"\nID=ubuntu\nVERSION_CODENAME=noble\n'


class Done:
    def __init__(self, out='', code=0): self.stdout, self.returncode = out, code


class Machine(so.Io):
    """A machine made of fixtures: its os-release text, its role, what the core answers."""
    def __init__(self, root, os_release=JAMMY, role='remote', arch='amd64', status='404', listing=None, record=None):
        self.root, self.lines, self.calls, self.listing, self.status, self.arch = Path(root), [], [], listing, status, arch
        self.os_release = self.root / 'os-release'; self.os_release.write_text(os_release)
        self.mode_file = self.root / 'agent.mode'
        if role is not None: self.mode_file.write_text(role + '\n')
        self.repo = str(self.root / 'repo')
        if record is not None:
            self.record = self.root / 'record.json'; self.record.write_text(record if isinstance(record, str) else json.dumps(record))

    def now(self): return '2026-11-21T12:00:00+00:00'
    def say(self, line=''): self.lines.extend(line.split('\n'))

    def run(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[0] == 'dpkg': return Done(self.arch + '\n') if self.arch else Done('', 1)
        if cmd[0] == 'curl' and '/api/islemesh/devices' in cmd[-1]:
            assert '--cacert' in cmd and '-k' not in cmd and '--insecure' not in cmd
            return Done(json.dumps({'devices': self.listing})) if self.listing is not None else Done('', 7)
        if cmd[0] == 'curl':
            assert '--cacert' in cmd and '-k' not in cmd and '--insecure' not in cmd
            return Done(self.status)
        raise AssertionError('unexpected command %r' % (cmd,))

    def text(self): return '\n'.join(self.lines)
    def json(self): return json.loads(self.lines[-1])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root = Path(self.tmp.name)

    def machine(self, **kw): return Machine(self.root, **kw)


class TheRecord(Base):
    def test_the_shipped_record_is_readable_and_says_what_was_proven(self):
        systems = so.load()
        self.assertEqual([(s['id'], s['codename'], s['core'], s['member']) for s in systems],
                         [('ubuntu-24.04', 'noble', 'live-verified', 'source-tested'), ('ubuntu-22.04', 'jammy', 'not proven', 'live-verified')])
        for s in systems:
            self.assertEqual(s['architectures'], ['amd64'])
            self.assertEqual(s['baseImage'], 'ubuntu:' + s['version'])
            self.assertTrue(s['variations'] and s['evidence'])
        self.assertEqual(so.find(systems, codename='jammy')['packageNames'], {'libgtk-3-0t64': 'libgtk-3-0', 'libasound2t64': 'libasound2', 'systemd-resolved': 'systemd'})
        self.assertIsNone(so.find(systems, codename='focal'))

    def test_the_record_names_no_machine_and_no_address(self):
        text = so.RECORD.read_text()
        import re
        self.assertIsNone(re.search(r'\b\d{1,3}(\.\d{1,3}){3}\b|@|-core\b', text))

    def test_a_record_this_version_cannot_read_is_refused_in_plain_words(self):
        def broken(change):
            doc = copy.deepcopy(RECORD); change(doc); path = self.root / 'r.json'; path.write_text(json.dumps(doc))
            with self.assertRaises(ValueError) as caught: so.load(path)
            self.assertIn('is not one this version of the isle CLI reads', str(caught.exception))
        broken(lambda d: d.update(schemaVersion=2))
        broken(lambda d: d.update(systems=[]))
        broken(lambda d: d['systems'][0].update(codename='Noble'))
        broken(lambda d: d['systems'][0].update(id='ubuntu-24.10'))             # id is distribution-version
        broken(lambda d: d['systems'][0].update(core='works'))
        broken(lambda d: d['systems'][0].pop('member'))
        broken(lambda d: d['systems'][0].update(architectures=[]))
        broken(lambda d: d['systems'][0].update(packageNames={'a': 1}))
        broken(lambda d: d['systems'][1].update(codename='noble'))               # two systems, one release name
        broken(lambda d: d['systems'].append(copy.deepcopy(d['systems'][0])))
        (self.root / 'bad.json').write_text('{')
        for path in (self.root / 'bad.json', self.root / 'absent.json'):
            with self.assertRaises(ValueError) as caught: so.load(path)
            self.assertIn('cannot be read', str(caught.exception))


class ThisMachine(Base):
    def read(self, text):
        path = self.root / 'os'; path.write_text(text); return so.read_os_release(path)

    def test_the_system_is_read_from_os_release(self):
        self.assertEqual(self.read(JAMMY), ('ubuntu-22.04', 'jammy'))
        self.assertEqual(self.read(NOBLE), ('ubuntu-24.04', 'noble'))
        self.assertEqual(self.read('ID=linuxmint\nVERSION_ID="21.3"\nVERSION_CODENAME=virginia\nUBUNTU_CODENAME=jammy\n'), ('linuxmint-21.3', 'jammy'))
        self.assertEqual(self.read("ID='debian'\nVERSION_ID='12'\nVERSION_CODENAME=bookworm\n"), ('debian-12', 'bookworm'))

    def test_what_cannot_be_read_is_none_and_nothing_is_run(self):
        self.assertEqual(self.read('ID=arch\nBUILD_ID=rolling\n'), (None, None))
        self.assertEqual(self.read('ID=ubuntu\nVERSION_ID="22.04; rm -rf /"\nVERSION_CODENAME="../x"\n'), (None, None))
        self.assertEqual(self.read('ID=$(touch %s/made)\nVERSION_ID=`id`\nVERSION_CODENAME="unclosed\n' % self.root), (None, None))
        self.assertFalse((self.root / 'made').exists())
        self.assertEqual(self.read('garbage\n=x\n# ID=ubuntu\n'), (None, None))
        self.assertEqual(so.read_os_release(self.root / 'absent'), (None, None))
        (self.root / 'bin').write_bytes(b'\xff\xfe\x00ID=ubuntu\n'); self.assertEqual(so.read_os_release(self.root / 'bin'), (None, None))

    def test_check_a_member_on_a_supported_system(self):
        m = self.machine(status='404')
        self.assertEqual(so.main(m, ['check']), 0)
        self.assertEqual(m.json()['result'], dict(system='ubuntu-22.04', codename='jammy', architecture='amd64', role='member', inRecord=True,
                                                  proof='live-verified', basedOn=None, dependencySet='not served'))
        self.assertTrue(m.json()['readOnly']); self.assertEqual(m.json()['operation'], 'os.check')
        m = self.machine(status='200'); self.assertEqual(so.main(m, ['check', '--table']), 0)
        self.assertEqual(m.lines, ['This machine runs ubuntu-22.04 (release name jammy), amd64.',
                                   'It is in the record of supported operating systems. As a member it is live-verified.',
                                   'Dependency set for jammy on the core: served.'])
        self.assertTrue(any(c[-1] == 'https://apt.isle/deps/jammy/InRelease' for c in m.calls))

    def test_check_a_member_that_cannot_reach_the_core_does_not_say_not_served(self):
        for status in ('000', '', '503'):
            m = self.machine(status=status); self.assertEqual(so.main(m, ['check', '--table']), 0)
            self.assertEqual(len(m.lines), 2, m.lines); self.assertNotIn('Dependency set', m.text())

    def test_check_the_core_reads_its_own_repository_and_asks_nobody(self):
        m = self.machine(os_release=NOBLE, role='core')
        self.assertEqual(so.main(m, ['check']), 0)
        self.assertEqual((m.json()['result']['proof'], m.json()['result']['dependencySet']), ('live-verified', 'not served'))
        (self.root / 'repo/deps/noble').mkdir(parents=True); (self.root / 'repo/deps/noble/InRelease').write_text('x')
        self.assertEqual(so.main(m, ['check']), 0); self.assertEqual(m.json()['result']['dependencySet'], 'served')
        self.assertFalse([c for c in m.calls if c[0] == 'curl'])

    def test_check_a_system_that_is_not_in_the_record(self):
        m = self.machine(os_release='ID=linuxmint\nVERSION_ID="21.3"\nUBUNTU_CODENAME=jammy\n')
        self.assertEqual(so.main(m, ['check', '--table']), 1)
        self.assertIn('It is not in the record of supported operating systems. Its packages are those of ubuntu-22.04, which is.', m.lines)
        m = self.machine(os_release='ID=debian\nVERSION_ID=12\nVERSION_CODENAME=bookworm\n'); self.assertEqual(so.main(m, ['check']), 1)
        self.assertEqual((m.json()['ok'], m.json()['result']['inRecord'], m.json()['result']['basedOn']), (False, False, None))
        m = self.machine(arch='arm64'); self.assertEqual(so.main(m, ['check']), 1)              # the system is listed, this architecture is not
        self.assertFalse(m.json()['result']['inRecord'])
        m = self.machine(os_release='nothing\n', role=None, arch=None); self.assertEqual(so.main(m, ['check', '--table']), 1)
        self.assertEqual(m.lines[0], 'This machine runs a system that cannot be read (release name not readable), architecture not readable.')
        self.assertFalse([c for c in m.calls if c[0] == 'curl'])

    def test_a_machine_that_is_neither_core_nor_member_is_told_so(self):
        m = self.machine(role='standalone'); self.assertEqual(so.main(m, ['check', '--table']), 0)
        self.assertEqual(m.lines[1], 'It is in the record of supported operating systems. This machine is not set up as a core or a member.')
        self.assertEqual(len(m.lines), 2)


class Verbs(Base):
    def test_list_prints_the_record(self):
        m = self.machine(); self.assertEqual(so.main(m, ['list']), 0)
        self.assertEqual(m.json()['result']['systems'], RECORD['systems'])
        m = self.machine(); self.assertEqual(so.main(m, ['list', '--table']), 0)
        self.assertEqual(m.lines[1].split(), ['ubuntu-24.04', 'noble', 'amd64', 'live-verified', 'source-tested', '2.8', '3.12'])
        self.assertEqual(m.lines[2].split(), ['ubuntu-22.04', 'jammy', 'amd64', 'not', 'proven', 'live-verified', '2.4', '3.10'])
        self.assertIn('  - package libgtk-3-0t64 is named libgtk-3-0 here', m.lines)
        self.assertFalse(m.calls)

    def test_an_unreadable_record_stops_every_verb(self):
        for verb in ('list', 'check', 'devices'):
            m = self.machine(record='{'); self.assertEqual(so.main(m, [verb]), 1); self.assertFalse(m.json()['ok'])
            m = self.machine(record='{'); self.assertEqual(so.main(m, [verb, '--table']), 1)
            self.assertIn('cannot be read', m.text()); self.assertFalse(m.calls)

    def test_other_words_print_the_usage(self):
        for words in ([], ['help'], ['list', 'x'], ['check', '--isle'], ['add', 'ubuntu-26.04']):
            m = self.machine(); self.assertEqual(so.main(m, words), 2); self.assertEqual(m.lines, [so.USAGE])

    def fact(self, value, status='observed'):
        return dict(status=status, source='x', observed_at='2026-11-21T11:00:00+00:00', value=value)

    def listing(self):
        f = self.fact
        return [dict(name='b-member', capability_evidence=dict(facts={'host.os_release': f('ubuntu-22.04'), 'host.os_codename': f('jammy')})),
                dict(name='a-core', capability_evidence=dict(facts={'host.os_release': f('ubuntu-24.04'), 'host.os_codename': f('noble')})),
                dict(name='c-old', capability_evidence=dict(facts={'host.architecture': f('x86_64')})),
                dict(name='d-other', capability_evidence=dict(facts={'host.os_release': f('debian-12'), 'host.os_codename': f('bookworm')})),
                dict(name='e-odd', capability_evidence=dict(facts={'host.os_release': f('ubuntu-22.04\x1b[31m'), 'host.os_codename': f(22)})),
                dict(name='f-error', capability_evidence=dict(facts={'host.os_release': f('ubuntu-22.04', 'error')})),
                dict(name='g-none', capability_evidence=None),
                dict(name='mock-one', is_mock=True, capability_evidence=dict(facts={'host.os_release': f('ubuntu-22.04')})),
                dict(name='Bad Name\x1b[2J'), 'not a row' and dict(name=7)]

    def test_devices_shows_what_each_machine_reported(self):
        m = self.machine(listing=self.listing()); self.assertEqual(so.main(m, ['devices']), 0)
        result = m.json()['result']
        self.assertEqual([(r['device'], r['system'], r['codename'], r['inRecord']) for r in result['devices']],
                         [('a-core', 'ubuntu-24.04', 'noble', True), ('b-member', 'ubuntu-22.04', 'jammy', True), ('c-old', None, None, False),
                          ('d-other', 'debian-12', 'bookworm', False), ('e-odd', None, None, False), ('f-error', None, None, False), ('g-none', None, None, False)])
        self.assertEqual(result['releaseNames'], ['bookworm', 'jammy', 'noble'])
        m = self.machine(listing=self.listing()); self.assertEqual(so.main(m, ['devices', '--table']), 0)
        self.assertEqual(m.lines[0].split('  ')[0], 'DEVICE')
        self.assertEqual(m.lines[1].split(), ['a-core', 'ubuntu-24.04', 'noble', 'yes'])
        self.assertEqual(m.lines[3].split(), ['c-old', 'not', 'reported', '-', '-'])
        self.assertEqual(m.lines[4].split(), ['d-other', 'debian-12', 'bookworm', 'no'])
        self.assertNotIn('\x1b', m.text())
        self.assertIn('Release names in use: bookworm, jammy, noble.', m.text())

    def test_devices_when_the_core_cannot_be_read_or_lists_nothing(self):
        m = self.machine(listing=None); self.assertEqual(so.main(m, ['devices', '--table']), 1)
        self.assertEqual(m.lines, ['The device list of the core cannot be read from this machine.'])
        m = self.machine(listing=None); self.assertEqual(so.main(m, ['devices']), 1); self.assertFalse(m.json()['ok'])
        m = self.machine(listing=[]); self.assertEqual(so.main(m, ['devices', '--table']), 0)
        self.assertEqual(m.lines, ['The device list of the core has no machine.'])


class DependencyRelease(Base):
    def decide(self, stated='', own='noble', marks=()):
        d = self.root / 'set'; d.mkdir(exist_ok=True)
        for old in d.iterdir(): old.unlink()
        (d / 'liba_1_all.deb').write_text('x'); (d / 'CLOSURE_URIS.txt').write_text('x'); (d / 'Packages').write_text('x')
        for name in marks: (d / name).write_text('x')
        return so.dependency_release(d, stated, own, so.load())

    def test_typed_marked_or_this_core_s_own(self):
        self.assertEqual(self.decide(stated='jammy'), ('jammy', 'as typed'))
        self.assertEqual(self.decide(marks=['ubuntu-22.04.txt']), ('jammy', 'the directory is marked ubuntu-22.04'))
        self.assertEqual(self.decide(stated='jammy', marks=['ubuntu-22.04.txt']), ('jammy', 'as typed'))
        self.assertEqual(self.decide()[0], 'noble'); self.assertIn("this core's own release", self.decide()[1])
        self.assertEqual(self.decide(own='focal')[0], 'focal')              # a core on a system the record does not have keeps the old rule
        self.assertEqual(self.decide(marks=['ubuntu-22.04.txt.bak', 'Ubuntu-22.04.txt', 'notes.txt'])[0], 'noble')    # not markers

    def test_refusals(self):
        for words, kw in (('jammi is not a release name in the record', dict(stated='jammi')),
                          ('ubuntu-22.04 is not a release name', dict(stated='ubuntu-22.04')),
                          ('focal is not a release name', dict(stated='focal')),
                          ('is built for ubuntu-22.04 (jammy), not for noble', dict(stated='noble', marks=['ubuntu-22.04.txt'])),
                          ('more than one system (ubuntu-22.04, ubuntu-24.04)', dict(marks=['ubuntu-24.04.txt', 'ubuntu-22.04.txt'])),
                          ('built for debian-12, which is not in the record', dict(marks=['debian-12.txt'])),
                          ('The release name of this core cannot be read', dict(own='')),
                          ('The release name of this core cannot be read', dict(own='../x'))):
            with self.assertRaises(ValueError) as caught: self.decide(**kw)
            self.assertIn(words, str(caught.exception))

    def test_the_command_form_apt_repo_uses(self):
        d = self.root / 'set'; d.mkdir(); (d / 'ubuntu-22.04.txt').write_text('x')
        run = lambda *words: subprocess.run([sys.executable, str(SCRIPTS / 'supported_os.py'), 'dependency-release'] + [str(w) for w in words], capture_output=True, text=True)
        p = run(d, 'noble', ''); self.assertEqual((p.returncode, p.stdout), (0, 'jammy\tthe directory is marked ubuntu-22.04\n'), p.stderr)
        p = run(d, 'noble'); self.assertEqual((p.returncode, p.stdout.split('\t')[0]), (0, 'jammy'))
        p = run(d, 'noble', 'noble'); self.assertEqual(p.returncode, 1); self.assertIn('not for noble', p.stderr); self.assertEqual(p.stdout, '')
        p = run(self.root / 'absent', 'noble', ''); self.assertEqual(p.returncode, 1); self.assertNotIn('Traceback', p.stderr)


class ForTheBuilders(Base):
    """What the scripts that build and prove an offline medium ask, by system id."""
    LIST = 'nodejs,docker.io | docker-ce,systemd-resolved,libgtk-3-0t64,libasound2t64,libx11-6'

    def entry(self, system): return so.system_of(so.load(), system)

    def test_the_newest_system_keeps_every_name_and_every_character(self):
        self.assertEqual(so.package_names(self.entry('ubuntu-24.04'), self.LIST), self.LIST)

    def test_the_older_system_gets_its_own_names_and_nothing_else_changes(self):
        self.assertEqual(so.package_names(self.entry('ubuntu-22.04'), self.LIST),
                         'nodejs,docker.io | docker-ce,systemd,libgtk-3-0,libasound2,libx11-6')
        self.assertEqual(so.package_names(self.entry('ubuntu-22.04'), ' libasound2t64 systemd-resolved:amd64 '), ' libasound2 systemd:amd64 ')

    def test_only_a_whole_name_is_replaced(self):
        entry = dict(self.entry('ubuntu-22.04'), packageNames={'libasound2t64': 'libasound2', 'old': 'new', 'new': 'newer'})
        for text in ('libasound2t64-dev', 'xlibasound2t64', 'libasound2t64.1', 'my-old', 'old+x', 'gold'):
            self.assertEqual(so.package_names(entry, text), text)
        self.assertEqual(so.package_names(entry, 'old,new | old'), 'new,newer | new')          # each name once, never again
        entry['packageNames'] = {'lib': 'a', 'lib-two': 'b'}                                   # one old name begins another
        self.assertEqual(so.package_names(entry, 'lib-two,lib,lib-two-x'), 'b,a,lib-two-x')

    def test_a_system_the_record_does_not_have_is_refused_with_the_ones_it_has(self):
        with self.assertRaises(ValueError) as caught: so.system_of(so.load(), 'ubuntu-20.04')
        self.assertEqual(str(caught.exception), 'ubuntu-20.04 is not in the record of supported operating systems (ubuntu-24.04, ubuntu-22.04).')

    def test_the_command_forms_the_builders_use(self):
        def run(*words): return subprocess.run([sys.executable, str(SCRIPTS / 'supported_os.py')] + list(words), capture_output=True, text=True)
        for system in so.load():
            done = run('base-image', system['id'])
            self.assertEqual((done.returncode, done.stdout), (0, system['baseImage'] + '\n'))
        done = run('package-names', 'ubuntu-22.04', self.LIST)
        self.assertEqual((done.returncode, done.stdout), (0, 'nodejs,docker.io | docker-ce,systemd,libgtk-3-0,libasound2,libx11-6\n'))
        for words in (('base-image', 'ubuntu-20.04'), ('package-names', 'debian-12', 'a,b')):
            done = run(*words)
            self.assertEqual((done.returncode, done.stdout), (1, ''))
            self.assertIn('is not in the record of supported operating systems (ubuntu-24.04, ubuntu-22.04).', done.stderr)
        for words in (('base-image',), ('base-image', 'ubuntu-22.04', 'more'), ('package-names', 'ubuntu-22.04')):
            done = run(*words)
            self.assertEqual((done.returncode, done.stdout), (1, ''))
            self.assertIn('usage: supported_os.py base-image', done.stderr)



class TheOfflineList(Base):
    """apt/offline-packages.json: what a dependency set is resolved from, beside what the isle's packages declare."""
    def entry(self, system): return so.system_of(so.load(), system)

    def written(self, doc):
        path = Path(self.tmp.name) / 'offline-packages.json'
        path.write_text(doc if isinstance(doc, str) else json.dumps(doc))
        return path

    def test_the_shipped_list_is_read_for_every_supported_system(self):
        doc = json.loads(so.OFFLINE.read_text())
        self.assertEqual(sorted(k for k in doc if k != 'about'), ['cacheOnly', 'install', 'schemaVersion'])
        for system in so.load():
            for part, key in so.PARTS.items():
                text = so.offline_packages(system, part)
                self.assertEqual(len(text.split(',')), len(doc[key]))
                self.assertEqual(text, so.package_names(system, ','.join(doc[key])))
        newest, older = so.offline_packages(self.entry('ubuntu-24.04'), 'install'), so.offline_packages(self.entry('ubuntu-22.04'), 'install')
        self.assertEqual(newest, ','.join(doc['install']))                # written with the names of the newest system
        self.assertIn('libgtk-3-0t64', newest); self.assertIn('libgtk-3-0,', older); self.assertNotIn('t64', older)
        self.assertIn('docker.io | docker-ce', older)                     # an either-or entry stays one entry
        self.assertEqual(so.offline_packages(self.entry('ubuntu-22.04'), 'cache-only'), ','.join(doc['cacheOnly']))
        for system in ('ubuntu-24.04', 'ubuntu-22.04'):                   # the tools the storage verbs call (the user, 2026-11-22)
            installed = so.offline_packages(self.entry(system), 'install').split(',')
            carried = so.offline_packages(self.entry(system), 'cache-only').split(',')
            self.assertIn('smartmontools', installed); self.assertIn('fdisk', installed)
            self.assertIn('fuse3', carried); self.assertNotIn('fuse3', installed)

    def test_a_list_this_version_does_not_read_is_refused(self):
        good = {'schemaVersion': 1, 'install': ['a', 'b | c'], 'cacheOnly': ['d']}
        entry = self.entry('ubuntu-22.04')
        self.assertEqual(so.offline_packages(entry, 'install', self.written(good)), 'a,b | c')
        for change in ({'schemaVersion': 2}, {'install': []}, {'cacheOnly': 'd'}, {'install': ['a', 7]}, {'install': ['a', '']},
                       {'install': ['a', ' b']}, {'install': ['a,b']}, {'install': ['a', 'a']}, {'cacheOnly': ['a']}):
            with self.assertRaises(ValueError) as caught: so.offline_packages(entry, 'install', self.written(dict(good, **change)))
            self.assertIn('is not one this version of the isle CLI reads', str(caught.exception))
        for missing in ('schemaVersion', 'install', 'cacheOnly'):
            with self.assertRaises(ValueError): so.offline_packages(entry, 'install', self.written({k: v for k, v in good.items() if k != missing}))
        for text in ('{', '[]', '"x"'):
            with self.assertRaises(ValueError): so.offline_packages(entry, 'install', self.written(text))
        with self.assertRaises(ValueError) as caught: so.offline_packages(entry, 'install', Path(self.tmp.name) / 'nowhere.json')
        self.assertIn('cannot be read', str(caught.exception))
        with self.assertRaises(ValueError) as caught: so.offline_packages(entry, 'all', self.written(good))
        self.assertEqual(str(caught.exception), 'The offline dependency list has the parts install and cache-only, not all.')

    def test_the_command_form_the_builders_use(self):
        def run(*words): return subprocess.run([sys.executable, str(SCRIPTS / 'supported_os.py')] + list(words), capture_output=True, text=True)
        for system in so.load():
            for part in so.PARTS:
                done = run('offline-packages', system['id'], part)
                self.assertEqual((done.returncode, done.stdout), (0, so.offline_packages(system, part) + '\n'))
        done = run('offline-packages', 'ubuntu-22.04', 'all')
        self.assertEqual(done.returncode, 1); self.assertIn('has the parts install and cache-only, not all', done.stderr)
        done = run('offline-packages', 'ubuntu-20.04', 'install')
        self.assertEqual(done.returncode, 1); self.assertIn('ubuntu-20.04 is not in the record', done.stderr)
        self.assertEqual(run('offline-packages', 'ubuntu-22.04').returncode, 1)

class DeviceReport(unittest.TestCase):
    def collect(self, answer):
        sys.modules['storage_isle'] = None            # the storage fact would read this machine's real disks
        try:
            import capabilities as probe
            with patch.object(so, 'read_os_release', side_effect=answer if isinstance(answer, Exception) else None,
                              return_value=None if isinstance(answer, Exception) else answer), \
                 patch.dict(sys.modules, {'release_run': None}), patch.object(probe, 'run', return_value=(0, '200', '')), \
                 patch.object(probe.shutil, 'which', return_value='/bin/tool'):
                return probe.collect('fixture')['capability_evidence']['facts']
        finally:
            del sys.modules['storage_isle']

    def test_the_device_report_carries_the_system(self):
        facts = self.collect(('ubuntu-22.04', 'jammy'))
        self.assertEqual((facts['host.os_release']['value'], facts['host.os_release']['status']), ('ubuntu-22.04', 'observed'))
        self.assertEqual(facts['host.os_codename']['value'], 'jammy')
        self.assertIn('host.architecture', facts)

    def test_what_cannot_be_read_is_left_out_and_the_report_still_goes(self):
        facts = self.collect((None, 'jammy')); self.assertNotIn('host.os_release', facts); self.assertEqual(facts['host.os_codename']['value'], 'jammy')
        for answer in ((None, None), RuntimeError('boom')):
            facts = self.collect(answer)
            self.assertNotIn('host.os_release', facts); self.assertNotIn('host.os_codename', facts); self.assertIn('host.architecture', facts)


if __name__ == '__main__': unittest.main()
