#!/usr/bin/env python3
import importlib.util
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
if __name__=='__main__':unittest.main()
