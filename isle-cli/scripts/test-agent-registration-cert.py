#!/usr/bin/env python3
"""An unready Isle identity cannot publish or overwrite an agent registration."""
from pathlib import Path
import subprocess
import tempfile
import unittest

AGENT = Path(__file__).resolve().parents[2] / 'isle-agent/scripts/agent-manager.sh'


class RegistrationCertificateTest(unittest.TestCase):
    def test_failed_identity_check_preserves_registry(self):
        function = AGENT.read_text().split('register_app() {', 1)[1].split('unregister_app() {', 1)[0]
        with tempfile.TemporaryDirectory() as temp:
            registry = Path(temp) / 'registry.json'
            original = '{"apps":{"existing":{"domain":"existing.isle"}}}\n'
            registry.write_text(original)
            result = subprocess.run(['bash', '-c', 'register_app() {' + function + r'''
SCRIPT_DIR="$1"; REGISTRY_FILE="$2"
log_error(){ echo "$*"; }
log_warn(){ echo "$*"; }
log_info(){ echo "$*"; }
log_success(){ echo "$*"; }
jq(){ echo 'UNEXPECTED REGISTRY WRITE'; return 99; }
bash(){ echo "identity check: $*"; return 1; }
init_agent_dir(){ echo 'UNEXPECTED INIT'; return 99; }
register_app --name board --domain board.isle --container board-worker --port 9830
''', 'test', str(AGENT.parent), str(registry)], capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('certs.sh ensure board.isle', result.stdout)
            self.assertIn('registry unchanged', result.stdout)
            self.assertNotIn('UNEXPECTED', result.stdout)
            self.assertEqual(registry.read_text(), original)


if __name__ == '__main__':
    unittest.main()
