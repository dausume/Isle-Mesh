#!/usr/bin/env python3
"""A store release's "doors" in `isle app deploy --door NAME=SERVICE:PORT`: the blocks of app-deploy.sh that read the option and
register each door, run from the script itself with a fake sudo, docker and agent manager. A door is another service of the
app under its own name <NAME>.<the app's domain>, registered as <app>--<NAME>; a door the previous deploy had and this one
does not is removed; undeploy removes every door. Run: python3 test-app-doors.py"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
TEXT = (HERE / 'app-deploy.sh').read_text()
APP = (HERE / 'app.sh').read_text()
PARSE = TEXT.split('# ---- --door NAME=SERVICE:PORT', 1)[1].split('\n', 1)[1].split('# ---- --bind PARAM=PATH', 1)[0]
REGISTER = TEXT.split('# ---- 2b. the app\'s doors', 1)[1].split('\n', 1)[1].split('# Make the new local app reachable', 1)[0]
HEAD = 'G=; Y=; R=; N=\nok(){ echo "OK $*"; }\ndie(){ echo "FAIL $*"; exit 1; }\n'


class Doors(unittest.TestCase):
    def parse(self, *doors, service='esp-engines', services=('esp-engines', 'twin-door')):
        script = HEAD + 'set -u\nSERVICE=%s; SERVICES=(%s); DOORS=(%s)\n%s\nprintf "%%s|" "${DOOR_NAMES[@]+"${DOOR_NAMES[@]}"}" "${DOOR_SERVICES[@]+"${DOOR_SERVICES[@]}"}" "${DOOR_PORTS[@]+"${DOOR_PORTS[@]}"}"' % (
            service, ' '.join(services), ' '.join("'%s'" % d for d in doors), PARSE)
        r = subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=10)
        return r.returncode, r.stdout

    def test_a_door_is_read_as_its_name_service_and_port(self):
        self.assertEqual(self.parse('twin=twin-door:9864'), (0, 'twin|twin-door|9864|'))
        self.assertEqual(self.parse(), (0, '|'))     # none: the app as before (printf with no arguments prints one '|')

    def test_a_malformed_door_is_refused_before_anything_is_changed(self):
        for bad, words in (('twin', 'NAME=SERVICE:PORT'), ('Twin=twin-door:9864', 'lowercase'), ('twin=twin-door:0', '1 to 65535'),
                           ('twin=twin-door:x', '1 to 65535'), ('twin=nope:9864', "'nope' is not in the compose file"),
                           ('twin=esp-engines:9850', "the app's own service")):
            code, out = self.parse(bad)
            self.assertEqual(code, 1, bad)
            self.assertIn(words, out, bad)
            self.assertIn('nothing was changed' if 'own service' not in words else 'a door names another one', out, bad)
        code, out = self.parse('twin=twin-door:9864', 'twin=twin-door:9865')
        self.assertEqual((code, 'named twice' in out), (1, True))

    def register(self, doors, previous=None):
        """Run the registration block: -> (exit code, output, agent-manager calls, doors.json written)."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'apps' / 'esp-engines').mkdir(parents=True)
            if previous is not None:
                (root / 'apps' / 'esp-engines' / 'doors.json').write_text(json.dumps(previous))
            calls = root / 'calls'
            (root / 'sudo').write_text('#!/bin/sh\n[ "$1" = env ] && shift\nexec "$@"\n')
            (root / 'docker').write_text('#!/bin/sh\necho isle-esp-engines-twin-door-1\n')
            (root / 'agent-manager.sh').write_text('#!/bin/sh\necho "$*" >> %s\n' % calls)
            for f in ('sudo', 'docker', 'agent-manager.sh'):
                (root / f).chmod(0o755)
            names = ' '.join(d[0] for d in doors)
            svcs = ' '.join(d[1] for d in doors)
            ports = ' '.join(str(d[2]) for d in doors)
            script = HEAD + ('set -u\nNAME=esp-engines; DOMAIN=esp-engines.isle; APPS_DIR=%s/apps; AGENT_MANAGER=%s/agent-manager.sh; BIND_ENV=()\n'
                             'DOOR_NAMES=(%s); DOOR_SERVICES=(%s); DOOR_PORTS=(%s)\n%s') % (root, root, names, svcs, ports, REGISTER)
            r = subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=20,
                               env={**os.environ, 'PATH': temp + ':' + os.environ['PATH']})
            made = root / 'apps' / 'esp-engines' / 'doors.json'
            return (r.returncode, r.stdout + r.stderr, calls.read_text().splitlines() if calls.exists() else [],
                    json.loads(made.read_text()) if made.exists() else None)

    def test_each_door_is_registered_under_its_own_name_and_recorded(self):
        code, out, calls, made = self.register([('twin', 'twin-door', 9864)])
        self.assertEqual(code, 0, out)
        self.assertEqual(calls, ['register --name esp-engines--twin --domain twin.esp-engines.isle --container isle-esp-engines-twin-door-1 --port 9864 --protocol http'])
        self.assertEqual(made, [{'name': 'twin', 'service': 'twin-door', 'port': 9864}])
        self.assertIn('door registered: https://twin.esp-engines.isle -> twin-door:9864', out)

    def test_a_door_the_previous_deploy_had_and_this_one_does_not_is_removed(self):
        code, out, calls, made = self.register([], previous=[{'name': 'twin', 'service': 'twin-door', 'port': 9864}])
        self.assertEqual((code, calls, made), (0, ['unregister --name esp-engines--twin'], []), out)
        code, out, calls, made = self.register([('twin', 'twin-door', 9864)], previous=[{'name': 'twin', 'service': 'twin-door', 'port': 9864}])
        self.assertEqual([c.split()[0] for c in calls], ['register'], out)     # kept: registered again, never removed first

    def test_dns_and_the_reach_check_cover_every_door_and_only_when_published(self):
        published = TEXT.split('if [ "$STAGED" = 1 ]; then\n    echo "staged: domain not published', 1)[1].split('\nfi\n', 1)[0]
        self.assertIn('dns_publication.py" "$dn.$DOMAIN" "$AGENT_IP"', published.split('\nelse\n', 1)[1])
        reach = TEXT.split('# A door is reached when', 1)[1]
        self.assertIn('if [ "$STAGED" != 1 ]; then', reach.split('\n')[1])
        self.assertIn('"https://$dn.$DOMAIN/"', reach)
        self.assertNotIn('-k', reach.split('done\nfi\n', 1)[0].split())          # verified TLS, always

    def test_undeploy_removes_every_door(self):
        block = APP.split('    undeploy)\n', 1)[1].split('    scaffold)', 1)[0]
        self.assertIn('doors.json', block)
        self.assertIn('unregister --name "$NAME--$door"', block)


if __name__ == '__main__':
    unittest.main(verbosity=2)
