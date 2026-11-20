#!/usr/bin/env python3
"""apt_index_split.py: one scanned index in, the main index (isle-* and polari-*) and the dependency index out."""
import re, subprocess, sys, tempfile, unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from apt_index_split import FIRST_PARTY, split

def stanza(name, version='1', more=''):
    return f'Package: {name}\nVersion: {version}\nArchitecture: all\n{more}Filename: ./{name}_{version}_all.deb\nDescription: x\n continued line\n'

class Split(unittest.TestCase):
    def test_names_decide_and_the_rule_is_the_rule_of_the_pin(self):
        first = ['isle-mesh-cli', 'isle-app-store', 'polari-shell-core', 'polari-module-hal', 'isle-x', 'polari-a+b.c-d']
        other = ['libc6', 'systemd', 'apt', 'docker-ce', 'islet', 'isle', 'polari', 'polarimeter', 'libisle-dev', 'python3-polari-x', 'isle-', 'polari-']
        for name in first: self.assertTrue(FIRST_PARTY.match(name), name)
        for name in other: self.assertFalse(FIRST_PARTY.match(name), name)
        pin = (SCRIPTS.parent / 'apt' / 'isle-mesh.pref').read_text()
        self.assertIn('Package: isle-* polari-*\nPin: release o=isle-mesh\nPin-Priority: 500\n', pin)

    def test_every_stanza_lands_in_exactly_one_index_with_its_bytes_and_order(self):
        blocks = [stanza('libc6', '2.39'), stanza('isle-mesh-cli', '0.1.209+release89'), stanza('libc6', '2.38'),
                  stanza('polari-shell-core', '1', 'Depends: libc6 (>= 2.38), isle-mesh-cli\n'), stanza('zlib1g')]
        main, deps, main_count, deps_count = split('\n'.join(blocks) + '\n')
        self.assertEqual((main_count, deps_count), (2, 3))
        self.assertEqual(main, blocks[1] + '\n' + blocks[3] + '\n')
        self.assertEqual(deps, blocks[0] + '\n' + blocks[2] + '\n' + blocks[4] + '\n')
        self.assertEqual(re.findall(r'^Version: (.+)$', deps, re.M), ['2.39', '2.38', '1'])        # every version kept

    def test_a_depends_line_naming_an_isle_package_does_not_move_a_stanza(self):
        main, deps, _, _ = split(stanza('libfoo', '1', 'Depends: isle-mesh-cli\nProvides: polari-thing\n') + '\n')
        self.assertEqual(main, ''); self.assertIn('Package: libfoo', deps)

    def test_empty_and_one_sided_inputs(self):
        self.assertEqual(split(''), ('', '', 0, 0))
        self.assertEqual(split('\n\n\n')[2:], (0, 0))
        self.assertEqual(split(stanza('isle-a') + '\n')[2:], (1, 0))
        self.assertEqual(split(stanza('liba') + '\n\n\n' + stanza('libb'))[2:], (0, 2))                  # no final blank line

    def test_a_stanza_without_one_package_field_is_refused(self):
        for bad in ('Version: 1\nFilename: ./x.deb\n', 'Package: a\nPackage: isle-a\nVersion: 1\n'):
            with self.assertRaises(ValueError): split(stanza('isle-a') + '\n' + bad + '\n')

    def test_the_command_writes_both_files_and_prints_the_counts(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); (d / 'scanned').write_bytes((stanza('isle-a') + '\n' + stanza('libb', '1', 'Maintainer: J\xfcrgen\n') + '\n').encode('latin-1'))
            run = lambda source: subprocess.run([sys.executable, str(SCRIPTS / 'apt_index_split.py'), str(source), str(d / 'main'), str(d / 'deps')], capture_output=True, text=True)
            p = run(d / 'scanned'); self.assertEqual((p.returncode, p.stdout), (0, '1 1\n'), p.stderr)
            self.assertEqual((d / 'main').read_bytes() + (d / 'deps').read_bytes(), (d / 'scanned').read_bytes())   # bytes kept, even non-UTF-8
            p = run(d / 'absent'); self.assertNotEqual(p.returncode, 0); self.assertIn('index split failed', p.stderr)
            (d / 'bad').write_text('Version: 1\n'); p = run(d / 'bad'); self.assertNotEqual(p.returncode, 0); self.assertIn('index split failed', p.stderr)

if __name__ == '__main__': unittest.main()
