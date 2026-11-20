#!/usr/bin/env python3
"""stm-6A: `isle app deploy --staged/--bind`, the overlay generator, storage_cli check-path and guard-domain."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

import app_overlay
import storage_cli

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / 'app-deploy.sh'
TEXT = SCRIPT.read_text()
COMPOSE = {'services': {'web': {'image': 'x', 'networks': ['inner']}, 'db': {'image': 'y'}}}


class StagedScript(unittest.TestCase):
    def test_syntax(self):
        subprocess.run(['bash', '-n', str(SCRIPT)], check=True)

    def test_staged_path_never_calls_dns_publication(self):
        staged, _, rest = TEXT.partition('if [ "$STAGED" = 1 ]; then\n    echo "staged: domain not published')
        self.assertIn('staged: domain not published; cutover is a separate core operation', TEXT)
        branch, _, other = rest.partition('\nelse\n')
        self.assertNotIn('dns_publication', branch)
        self.assertIn('dns_publication.py', other)
        self.assertEqual(TEXT.count('dns_publication.py'), 1)
        self.assertLess(other.index('guard-domain'), other.index('dns_publication.py'))

    def test_health_check_uses_resolve_when_staged(self):
        self.assertIn('RESOLVE_ARGS=(--resolve "$DOMAIN:443:$PROBE_IP")', TEXT)
        self.assertIn('curl "${RESOLVE_ARGS[@]+"${RESOLVE_ARGS[@]}"}" --http1.1 --fail', TEXT)

    def probe_resolve(self, staged, inspect_out, inspect_rc, driver):
        """Run only the probe-address block with a fake sudo/docker; return the RESOLVE_ARGS it builds."""
        block = TEXT.split('RESOLVE_ARGS=()\n', 1)[1].split('for attempt in', 1)[0]
        with tempfile.TemporaryDirectory() as temp:
            sudo = Path(temp) / 'sudo'
            sudo.write_text('#!/bin/sh\n[ "$1" = -n ] && shift\nexec "$@"\n')
            docker = Path(temp) / 'docker'
            docker.write_text('#!/bin/sh\nif [ "$1" = network ]; then echo %s; exit 0; fi\necho %s; exit %d\n'
                              % (driver, inspect_out, inspect_rc))
            for f in (sudo, docker):
                f.chmod(0o755)
            r = subprocess.run(['bash', '-c', 'set -euo pipefail; DOMAIN=x.isle; AGENT_IP=10.10.0.116; STAGED=%s; RESOLVE_ARGS=()\n%s'
                                'printf "%%s|" "${RESOLVE_ARGS[@]+"${RESOLVE_ARGS[@]}"}"' % (staged, block)],
                               env={**os.environ, 'PATH': temp + ':' + os.environ['PATH']},
                               capture_output=True, text=True, timeout=10)
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout

    def test_staged_probe_goes_via_the_agent_bridge_not_the_macvlan_child(self):
        # pol-core 2026-11-14: the host cannot reach its own macvlan child (10.10.0.116, No route to host);
        # the agent's isle-agent-net bridge address (172.20.0.2) answers 200.
        self.assertEqual(self.probe_resolve(1, '172.20.0.2', 0, 'bridge'), '--resolve|x.isle:443:172.20.0.2|')

    def test_staged_probe_falls_back_to_agent_ip_without_a_verified_bridge(self):
        self.assertEqual(self.probe_resolve(1, '', 1, 'bridge'), '--resolve|x.isle:443:10.10.0.116|')
        self.assertEqual(self.probe_resolve(1, '172.20.0.2', 0, 'macvlan'), '--resolve|x.isle:443:10.10.0.116|')
        self.assertEqual(self.probe_resolve(1, '127.0.0.1', 0, 'bridge'), '--resolve|x.isle:443:10.10.0.116|')
        self.assertEqual(self.probe_resolve(1, 'junk', 0, 'bridge'), '--resolve|x.isle:443:10.10.0.116|')

    def test_published_probe_uses_dns_no_resolve(self):
        self.assertEqual(self.probe_resolve(0, '172.20.0.2', 0, 'bridge'), '|')  # printf with no args prints one '|': empty RESOLVE_ARGS

    def test_payload_carries_staged(self):
        self.assertIn(',"staged":true', TEXT)
        self.assertIn("report['staged'] = True", TEXT)

    def run_script(self, *args):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            compose = root / 'c.yml'
            compose.write_text('services:\n  web:\n    image: fixture:1\n    expose: [80]\n')
            docker = root / 'docker'
            docker.write_text('#!/bin/sh\n[ "$1" = ps ] && echo isle-remote-agent\nexit 0\n')
            docker.chmod(0o755)
            sudo = root / 'sudo'
            sudo.write_text('#!/bin/sh\necho "sudo $*" >> "$CALLS"\nexit 1\n')
            sudo.chmod(0o755)
            return subprocess.run(['bash', str(SCRIPT), 'host', '--compose', str(compose), *args],
                                  env={**os.environ, 'PATH': temp + ':' + os.environ['PATH'], 'CALLS': str(root / 'calls')},
                                  capture_output=True, text=True, timeout=10)

    def test_bind_refused_without_staged_or_binding(self):
        result = self.run_script('--bind', 'ISLE_DATASET_PATH=/srv/x')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--bind is accepted only with --staged', result.stdout)

    def test_bind_needs_the_dataset_parameter(self):
        result = self.run_script('--staged', '--bind', 'OTHER=/srv/x')
        self.assertIn('ISLE_DATASET_PATH=<absolute directory>', result.stdout)
        result = self.run_script('--staged', '--bind', '/srv/x')
        self.assertIn('ISLE_DATASET_PATH=<absolute directory>', result.stdout)

    def test_unproven_bind_path_stops_before_any_host_mutation(self):
        for path in ('relative/dir', '/', '/nonexistent-stm6-dir'):
            result = self.run_script('--staged', '--bind', 'ISLE_DATASET_PATH=' + path)
            self.assertNotEqual(result.returncode, 0, path)
            self.assertIn('not a proven separate mounted directory', result.stdout)
            self.assertNotIn('deploying', result.stdout)


class Overlay(unittest.TestCase):
    def test_unbound_overlay_matches_the_old_generator(self):
        overlay = app_overlay.build(COMPOSE)
        self.assertEqual(overlay['services']['web'], {'networks': ['inner', 'isle-agent-net']})
        self.assertEqual(overlay['services']['db'], {'networks': ['isle-agent-net']})
        self.assertEqual(overlay['networks'], {'isle-agent-net': {'external': True}})

    def test_bound_overlay_is_readonly_and_never_creates_the_host_path(self):
        overlay = app_overlay.build(COMPOSE, 'web', '/mnt/disk/data')
        self.assertEqual(overlay['services']['web']['volumes'], [{
            'type': 'bind', 'source': '/mnt/disk/data', 'target': '/data', 'read_only': True,
            'bind': {'create_host_path': False}}])
        self.assertEqual(overlay['services']['web']['environment'], {'ISLE_DATASET_PATH': '/mnt/disk/data'})
        self.assertNotIn('volumes', overlay['services']['db'])
        self.assertIn('create_host_path: false', yaml.safe_dump(overlay))

    def test_unknown_service_refused(self):
        with self.assertRaises(ValueError):
            app_overlay.build(COMPOSE, 'nope', '/mnt/disk/data')

    def test_cli_prints_yaml(self):
        with tempfile.TemporaryDirectory() as temp:
            compose = Path(temp) / 'c.yml'
            compose.write_text(yaml.safe_dump(COMPOSE))
            out = subprocess.run(['python3', str(HERE / 'app_overlay.py'), str(compose), '--service', 'web', '--bind-path', '/mnt/d'],
                                 capture_output=True, text=True, check=True).stdout
            self.assertEqual(yaml.safe_load(out)['services']['web']['volumes'][0]['bind'], {'create_host_path': False})


class CheckPath(unittest.TestCase):
    def mount(self, target):
        return lambda path: {'target': target, 'source': '/dev/sdb1', 'uuid': 'u-1'}

    def test_valid_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            real = os.path.realpath(temp)
            result = storage_cli.check_path(real, self.mount('/mnt/disk'))
            self.assertEqual((result['mountpoint'], result['source'], result['uuid']), ('/mnt/disk', '/dev/sdb1', 'u-1'))

    def test_refusals(self):
        with tempfile.TemporaryDirectory() as temp:
            real = os.path.realpath(temp)
            (Path(real) / 'file').write_text('x')
            (Path(real) / 'dir').mkdir()
            os.symlink(Path(real) / 'dir', Path(real) / 'link')
            cases = {'relative': 'a/b', 'root': '/', 'missing': real + '/missing', 'file': real + '/file',
                     'symlink leaf': real + '/link', 'symlink component': real + '/link/x', 'dotdot': real + '/dir/../dir',
                     'trailing slash': real + '/dir/'}
            for label, path in cases.items():
                with self.assertRaises(ValueError, msg=label):
                    storage_cli.check_path(path, self.mount('/mnt/disk'))
            with self.assertRaisesRegex(ValueError, 'root filesystem'):
                storage_cli.check_path(real + '/dir', self.mount('/'))

    def test_cli_reports_refusal_as_json(self):
        result = subprocess.run(['python3', str(HERE / 'storage_cli.py'), 'check-path', '--path', 'relative'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)['ok'])


class DomainGuard(unittest.TestCase):
    def view(self, state, device):
        return json.dumps({'datasets': {'d': {'dataset': 'd', 'domain': 'apt.isle', 'device': device, 'state': state}}})

    def test_other_owner_in_cutover_or_hosting_refused_naming_owner(self):
        for state in ('cutover', 'hosting'):
            with self.assertRaisesRegex(ValueError, 'owned by pol-edge'):
                storage_cli.domain_guard('apt.isle', 'pol-core', [self.view(state, 'pol-edge')])

    def test_allowed_cases(self):
        for text in ('', '   ', 'not json', self.view('hosting', 'pol-core'), self.view('authorized', 'pol-edge'),
                     self.view('migrated', 'pol-edge'), json.dumps({'datasets': {}})):
            storage_cli.domain_guard('apt.isle', 'pol-core', [text])
        storage_cli.domain_guard('other.isle', 'pol-core', [self.view('hosting', 'pol-edge')])

    def test_cli_uses_view_file_and_empty_means_no_placement(self):
        with tempfile.TemporaryDirectory() as temp:
            view = Path(temp) / 'v'
            base = ['python3', str(HERE / 'storage_cli.py'), 'guard-domain', '--domain', 'apt.isle', '--device', 'pol-core',
                    '--view', str(view), '--local', str(Path(temp) / 'absent')]
            view.write_text('')
            self.assertEqual(subprocess.run(base, capture_output=True, text=True).returncode, 0)
            view.write_text(self.view('hosting', 'pol-edge'))
            result = subprocess.run(base, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('pol-edge', result.stdout)


if __name__ == '__main__':
    unittest.main()
