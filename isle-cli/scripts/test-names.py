#!/usr/bin/env python3
import importlib.util
import re
import tempfile
from unittest import mock
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('names',Path(__file__).with_name('names.py'))
names=importlib.util.module_from_spec(spec);spec.loader.exec_module(names)
class NamesTests(unittest.TestCase):
    def test_device_suffix_and_collision_number(self):
        self.assertEqual(names.instance_name('polari','econ-core',set()),'polari-econ-core')
        self.assertEqual(names.instance_name('polari','econ-core',{'polari-econ-core'}),'polari-2-econ-core')
    def test_all_catalog_domains_count(self):
        taken=names.occupied_names({'ok':True,'entries':[{'instances':[{'app':'different','domain':'polari-econ-core.isle'}]}]})
        self.assertEqual(names.instance_name('polari','econ-core',taken),'polari-2-econ-core')
    def test_unavailable_catalog_never_guesses(self):
        for value in (None,{}, {'ok':False,'entries':[]},{'ok':True,'entries':[None]}):
            with self.assertRaises(ValueError):names.occupied_names(value)
    def test_invalid_or_unnamed_device_rejected(self):
        for value in ('localhost','unknown','Bad Name','x.isle','-host','host-',''):
            with self.assertRaises(ValueError):names.instance_name('app',value,set())
    def test_long_name_not_silently_truncated(self):
        with self.assertRaises(ValueError):names.instance_name('polari','a'*50,set())
    def test_memorable_suggestion_is_unoccupied(self):
        all_names={a+'-'+b for a in names.ADJECTIVES for b in names.ANIMALS}
        self.assertEqual(names.suggest_device(all_names-{'eccentric-panda'}),'eccentric-panda')
        with self.assertRaises(ValueError):names.suggest_device(all_names)
    def test_no_machine_name_is_written_into_the_code(self):
        # A device with no saved Isle name is called by its OS name, whatever that is: no name is mapped to another.
        tmp=Path(tempfile.mkdtemp())
        with mock.patch.object(names,'Path',side_effect=lambda path:tmp/'absent'/Path(path).name), \
             mock.patch.object(names.socket,'gethostname',return_value='Any-Machine-7'):
            self.assertEqual(names.current_device(),'any-machine-7')
        # A saved name wins over the OS name.
        (tmp/'device-name').write_text('saved-name\n')
        with mock.patch.object(names,'Path',side_effect=lambda path:tmp/Path(path).name), \
             mock.patch.object(names.socket,'gethostname',return_value='any-machine-7'):
            self.assertEqual(names.current_device(),'saved-name')
        # No script rewrites the OS name with sed, and none holds a home folder path.
        here=Path(__file__).parent
        rewrite=re.compile(r'hostname[^\n|]*\|\s*sed')
        home=re.compile(r'/home/[a-z][a-z0-9_-]*/')
        found=[f.name for f in sorted(here.glob('*.sh'))+sorted(here.glob('*.py'))+[here.parent/'isle-wrapper.sh']
               if not f.name.startswith('test-') and (rewrite.search(f.read_text(errors='replace')) or home.search(f.read_text(errors='replace')))]
        self.assertEqual(found,[])
if __name__=='__main__':unittest.main()
