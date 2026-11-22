#!/usr/bin/env python3
"""An app's upload size is data: release -> deploy -> the agent's registry -> the agent's proxy, for that app only.

Nothing here reaches the network, the agent of this machine or its registry: the registration runs against a
temporary registry, and the generator runs with a made-up `nginx` that only notes how it was called.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import upload_size

HERE = Path(__file__).resolve().parent
AGENT = HERE.parents[1] / 'isle-agent'
MANAGER = AGENT / 'scripts/agent-manager.sh'
GENERATOR = AGENT / 'isle-vlan-agent/generate-nginx-configs.sh'
DEPLOY = (HERE / 'app-deploy.sh').read_text()
GOOD = ('0', '1', '64m', '2g', '512k', '999999g')
BAD = ('', '01', '64M', '1.5g', '64m;', '1 m', '1234567', '-1', '2gb', 'm', '2g\n', '0k', None, 64)


class TheRule(unittest.TestCase):
    def test_sizes(self):
        for value in GOOD:
            self.assertEqual(upload_size.validate(value), value)
        for value in BAD:
            with self.assertRaises(ValueError, msg=repr(value)):
                upload_size.validate(value)

    def test_the_agent_and_its_generator_carry_the_same_pattern(self):
        # they cannot import upload_size.py: the three patterns are kept the same by this test
        core = upload_size.SIZE.pattern.replace('(?:', '').replace(r')\Z', '')
        self.assertEqual(core, '0|[1-9][0-9]{0,5}[kmg]?')
        self.assertIn('=~ ^(%s)$ ]]' % core, MANAGER.read_text())
        self.assertIn("grep -Eqx '%s'" % core, GENERATOR.read_text())


def register(temp, *args):
    """register_app of the agent's manager, against a registry in a temporary folder and with nothing of the machine."""
    function = MANAGER.read_text().split('register_app() {', 1)[1].split('unregister_app() {', 1)[0]
    registry = Path(temp) / 'registry.json'
    if not registry.exists():
        registry.write_text('{"apps":{"kept":{"domain":"kept.isle"}}}\n')
    return registry, subprocess.run(['bash', '-c', 'register_app() {' + function + r'''
SCRIPT_DIR="$1"; REGISTRY_FILE="$2"; ISLE_AGENT_DIR="$3"; shift 3
log_error(){ echo "ERROR $*"; }
log_warn(){ :; }
log_info(){ :; }
log_success(){ :; }
bash(){ return 0; }          # the identity check of an .isle name: said to be ready
sudo(){ :; }
openssl(){ return 1; }
grep(){ return 0; }          # the name is "already" in the mDNS list, should this machine have one
touch(){ :; }
init_agent_dir(){ echo 'UNEXPECTED INIT'; return 99; }
register_app "$@"
''', 'test', str(MANAGER.parent), str(registry), str(temp)] + list(args), capture_output=True, text=True, timeout=20)


class Registration(unittest.TestCase):
    BASE = ('--name', 'images', '--domain', 'images.isle', '--container', 'isle-images-registry-1', '--port', '5000')

    def test_a_size_is_kept_on_the_service(self):
        with tempfile.TemporaryDirectory() as temp:
            registry, done = register(temp, *self.BASE, '--max-body', '2g')
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            apps = json.loads(registry.read_text())['apps']
            self.assertEqual(apps['images']['services'], [dict(name='images', subdomain='', container='isle-images-registry-1',
                                                               port=5000, protocol='http', maxBody='2g')])
            self.assertEqual(apps['kept'], {'domain': 'kept.isle'})
            self.assertIn('Max body:  2g', done.stdout)

    def test_without_a_size_the_entry_is_the_one_it_always_was(self):
        with tempfile.TemporaryDirectory() as temp:
            registry, done = register(temp, *self.BASE)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            service = json.loads(registry.read_text())['apps']['images']['services'][0]
            self.assertEqual(sorted(service), ['container', 'name', 'port', 'protocol', 'subdomain'])
            self.assertNotIn('Max body', done.stdout)

    def test_a_registration_typed_again_without_the_size_puts_the_default_back(self):
        with tempfile.TemporaryDirectory() as temp:
            register(temp, *self.BASE, '--max-body', '64m')
            registry, _ = register(temp, *self.BASE)
            self.assertNotIn('maxBody', json.loads(registry.read_text())['apps']['images']['services'][0])

    def test_a_size_that_does_not_read_as_one_changes_nothing(self):
        for value in ('64M', '1.5g', '2g; proxy_pass http://elsewhere', '1234567', ''):
            with tempfile.TemporaryDirectory() as temp:
                registry, done = register(temp, *self.BASE, '--max-body', value)
                if value == '':   # an empty size is no size
                    self.assertEqual(done.returncode, 0, done.stdout)
                    self.assertNotIn('maxBody', registry.read_text())
                    continue
                self.assertNotEqual(done.returncode, 0, value)
                self.assertIn('registry unchanged', done.stdout)
                self.assertEqual(registry.read_text(), '{"apps":{"kept":{"domain":"kept.isle"}}}\n')

    def test_the_option_without_a_value(self):
        with tempfile.TemporaryDirectory() as temp:
            registry, done = register(temp, *self.BASE, '--max-body')
            self.assertNotEqual(done.returncode, 0)
            self.assertNotIn('images', registry.read_text())


def service(name, **more):
    return dict(name=name, subdomain='', container='isle-%s-web-1' % name, port=8080, protocol='http', **more)


def generate(temp, apps):
    """The agent's own generator, with the real jq and a made-up nginx; returns (result, the published text or None)."""
    temp = Path(temp)
    registry, out, tools = temp / 'registry.json', temp / 'configs', temp / 'tools'
    tools.mkdir(exist_ok=True)
    nginx = tools / 'nginx'
    nginx.write_text('#!/bin/sh\necho "nginx $*" >> "%s"\nexit 0\n' % (temp / 'nginx.calls'))
    nginx.chmod(0o755)
    main = temp / 'nginx.conf'
    main.write_text('http {\n    include /etc/nginx/configs/*.conf;\n}\n')
    registry.write_text(json.dumps({'apps': apps}))
    done = subprocess.run(['sh', str(GENERATOR), str(registry), str(out)], capture_output=True, text=True, timeout=60,
                          env={**os.environ, 'PATH': '%s:%s' % (tools, os.environ['PATH']), 'NGINX_MAIN_CONFIG': str(main)})
    published = out / 'isle-generated.conf'
    return done, (published.read_text() if published.is_file() else None)


def block(text, name):
    """The part of the generated text that belongs to one app."""
    return text.split('# Auto-generated config for app: %s\n' % name, 1)[1].split('# Auto-generated config for app: ', 1)[0]


class Generator(unittest.TestCase):
    APPS = {'images': {'domain': 'images.isle', 'services': [service('images', maxBody='2g')]},
            'notes': {'domain': 'notes.isle', 'services': [service('notes')]},
            'open': {'domain': 'open.isle', 'services': [service('open', maxBody='0')]}}

    def test_only_the_app_that_declared_a_size_is_given_it(self):
        with tempfile.TemporaryDirectory() as temp:
            done, text = generate(temp, self.APPS)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            images, notes, opened = (block(text, name) for name in ('images', 'notes', 'open'))
            self.assertEqual(images.count('    client_max_body_size 2g;\n'), 1)
            self.assertEqual(images.count('        proxy_request_buffering off;\n'), 1)
            self.assertEqual(opened.count('    client_max_body_size 0;\n'), 1)
            for word in ('client_max_body_size', 'proxy_request_buffering'):
                self.assertNotIn(word, notes)
            self.assertEqual(text.count('client_max_body_size'), 2)
            self.assertIn('nginx -t -c', (Path(temp) / 'nginx.calls').read_text())   # the candidate was put to nginx first

    def test_the_size_is_inside_the_https_server_and_the_passing_on_inside_its_location(self):
        with tempfile.TemporaryDirectory() as temp:
            _, text = generate(temp, self.APPS)
            images = block(text, 'images')
            https = images.split('listen 443 ssl http2;', 1)[1]
            self.assertNotIn('client_max_body_size', images.split('listen 443 ssl http2;', 1)[0])   # not on the redirect
            self.assertLess(https.index('client_max_body_size 2g;'), https.index('location / {'))
            self.assertLess(https.index('location / {'), https.index('proxy_request_buffering off;'))
            self.assertEqual(images.count('{'), images.count('}'))

    def test_an_app_without_a_size_is_written_as_it_was_before(self):
        with tempfile.TemporaryDirectory() as temp:
            _, with_size = generate(temp, self.APPS)
        with tempfile.TemporaryDirectory() as temp:
            _, alone = generate(temp, {'notes': self.APPS['notes']})
        self.assertEqual(block(with_size, 'notes').rstrip('\n'), block(alone, 'notes').rstrip('\n'))
        self.assertIn('ssl_certificate_key /etc/nginx/ssl/keys/notes.isle.key;\n\n    # Proxy to backend\n', alone)
        self.assertIn('        proxy_busy_buffers_size 8k;\n    }\n}\n', alone)

    def test_a_registry_edited_by_hand_cannot_write_into_the_proxy(self):
        for value in ('2g; proxy_pass http://elsewhere', '64M', '1.5g', '2g\n    return 200', '$(id)', {'a': 1}):
            with tempfile.TemporaryDirectory() as temp:
                done, text = generate(temp, {'images': {'domain': 'images.isle', 'services': [service('images', maxBody=value)]}})
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn('unreadable maxBody; the default (1 MiB) is kept', done.stdout)
                for word in ('client_max_body_size', 'proxy_request_buffering', 'elsewhere', 'return 200', 'uid='):
                    self.assertNotIn(word, text, repr(value))

    def test_a_number_is_read_as_a_size(self):
        with tempfile.TemporaryDirectory() as temp:
            _, text = generate(temp, {'images': {'domain': 'images.isle', 'services': [service('images', maxBody=4096)]}})
            self.assertIn('    client_max_body_size 4096;\n', text)


class Deploy(unittest.TestCase):
    def test_syntax(self):
        subprocess.run(['bash', '-n', str(HERE / 'app-deploy.sh')], check=True)
        subprocess.run(['bash', '-n', str(MANAGER)], check=True)
        subprocess.run(['sh', '-n', str(GENERATOR)], check=True)

    def upload_args(self, *typed):
        """Run only the part of app-deploy.sh that reads the option; return what it would hand the registration."""
        loop = 'COMPOSE=""' + DEPLOY.split('COMPOSE=""', 1)[1].split('# --image <ref> synthesizes', 1)[0]
        return subprocess.run(['bash', '-c', 'die(){ echo "DIE $*"; exit 1; }\nSCRIPT_DIR="$1"; NAME=x; shift\n' + loop +
                               'printf "%s|" "${UPLOAD_ARGS[@]+"${UPLOAD_ARGS[@]}"}"', 'test', str(HERE)] + list(typed),
                              capture_output=True, text=True, timeout=20)

    def test_the_option_becomes_the_registration_size(self):
        self.assertEqual(self.upload_args('--max-upload', '2g').stdout, '--max-body|2g|')
        self.assertEqual(self.upload_args('--port', '80').stdout, '|')   # no option: nothing is handed on

    def test_a_bad_size_stops_the_deploy_before_anything_is_started(self):
        for value in ('64M', '2g;', '1.5g'):
            done = self.upload_args('--max-upload', value)
            self.assertNotEqual(done.returncode, 0)
            self.assertIn('DIE invalid --max-upload; nothing was changed', done.stdout)
        self.assertIn('DIE --max-upload needs a size', self.upload_args('--max-upload').stdout)
        # the check stands before the first thing the deploy changes
        self.assertLess(DEPLOY.index('upload_size.py'), DEPLOY.index('sudo mkdir -p "$APPS_DIR/$NAME"'))
        self.assertLess(DEPLOY.index('upload_size.py'), DEPLOY.index('certs.sh" ensure'))

    def test_the_registration_is_handed_the_size(self):
        call = DEPLOY.split('sudo bash "$AGENT_MANAGER" register', 1)[1].split('|| die', 1)[0]
        self.assertIn('"${UPLOAD_ARGS[@]+"${UPLOAD_ARGS[@]}"}"', call)
        self.assertEqual(DEPLOY.count('"$AGENT_MANAGER" register'), 1)


class PolarisOwnNames(unittest.TestCase):
    """isle-polari-deploy.sh registers the engine's address with a size and the pages' address without one."""
    TEXT = (HERE / 'isle-polari-deploy.sh').read_text()

    def registrations(self, **env):
        part = 'step "2/5' + self.TEXT.split('step "2/5', 1)[1].split("# record the CORE's modules", 1)[0]
        setting = [line for line in self.TEXT.splitlines() if line.startswith('API_MAX_UPLOAD=')]
        self.assertEqual(len(setting), 1)
        with tempfile.TemporaryDirectory() as temp:
            calls = Path(temp, 'calls')
            done = subprocess.run(['bash', '-c', 'step(){ :; }; ok(){ echo "OK $*"; }; die(){ echo "DIE $*"; exit 1; }\n'
                                   'sudo(){ printf "%s|" "$@" >> "$CALLS"; printf "\\n" >> "$CALLS"; }\n' + setting[0] + '\n' + part],
                                  cwd=HERE, env=dict(os.environ, CALLS=str(calls), **env), capture_output=True, text=True, timeout=20)
            return done, (calls.read_text().splitlines() if calls.exists() else [])

    def test_syntax(self):
        subprocess.run(['bash', '-n', str(HERE / 'isle-polari-deploy.sh')], check=True)

    def test_the_engine_gets_a_size_and_the_pages_do_not(self):
        done, calls = self.registrations()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len(calls), 2)
        pages, engine = calls
        self.assertIn('|--domain|polari.isle|', pages)
        self.assertNotIn('--max-body', pages)
        self.assertIn('|--domain|api.polari.isle|', engine)
        self.assertTrue(engine.endswith('|--max-body|64m|'), engine)
        self.assertIn('OK api.polari.isle (uploads up to 64m)', done.stdout)

    def test_the_default_is_above_the_file_size_the_engine_takes(self):
        default = self.TEXT.split('API_MAX_UPLOAD="${POLARI_API_MAX_UPLOAD:-', 1)[1].split('}', 1)[0]
        self.assertEqual(upload_size.validate(default), default)
        self.assertGreater(int(default[:-1]) * {'m': 2 ** 20, 'g': 2 ** 30}[default[-1]], 52428800)

    def test_the_setting_changes_it(self):
        _, calls = self.registrations(POLARI_API_MAX_UPLOAD='0')
        self.assertTrue(calls[1].endswith('|--max-body|0|'), calls)

    def test_a_setting_that_is_no_size_registers_nothing(self):
        for value in ('64M', '2g;echo', ' '):
            done, calls = self.registrations(POLARI_API_MAX_UPLOAD=value)
            self.assertNotEqual(done.returncode, 0, value)
            self.assertIn('nothing was registered', done.stdout)
            self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
