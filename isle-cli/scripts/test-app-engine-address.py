#!/usr/bin/env python3
"""Exercise the actual engine declaration writer using only a scratch app dir."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('app-deploy.sh')


class EngineDeclarationTest(unittest.TestCase):
    def run_declaration(self, engine, domain='board-engines.isle'):
        text = SCRIPT.read_text()
        block = text.split('# ---- 5. engine declaration', 1)[1].split('\necho\nif [ "$DNS_OK"', 1)[0]
        # Restore the first comment line removed by the section delimiter.
        block = '#' + block
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'board').mkdir()
            subprocess.run(['bash', '-eu', '-c', 'sudo(){ "$@"; }; ok(){ :; };\n' + block],
                           env={**os.environ, 'APPS_DIR': temp, 'NAME': 'board', 'ENGINE': engine,
                                'DOMAIN': domain, 'CONTAINER': 'isle-board-worker-1', 'PORT': '9830'},
                           check=True, capture_output=True, text=True)
            return json.loads((root / 'board/engine.json').read_text())

    def test_default_is_reachable_across_device_boundaries(self):
        result = self.run_declaration('board')
        self.assertEqual(result['url'], 'https://board-engines.isle')
        self.assertEqual(result['provides'], 'board')
        self.assertEqual(result['container'], 'isle-board-worker-1')

    def test_custom_published_domain_is_used(self):
        self.assertEqual(self.run_declaration('scope', 'lab-scope.isle')['url'], 'https://lab-scope.isle')

    def test_explicit_endpoint_is_preserved(self):
        self.assertEqual(self.run_declaration('board@https://selected.isle/worker')['url'],
                         'https://selected.isle/worker')


if __name__ == '__main__':
    unittest.main()
