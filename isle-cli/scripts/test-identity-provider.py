#!/usr/bin/env python3
"""isle polari security provider: a fake device and a fake Keycloak; no network, no file outside a temp folder."""
import contextlib
import importlib.util
import io as textio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import identity_provider as ip

ISSUER = 'https://auth.example.isle/realms/Polari'
KEYS = ISSUER + '/protocol/openid-connect/certs'
RECORD = dict(issuer=ISSUER, realm='Polari', jwksUri=KEYS, clientId='polari-frontend', setAt='t0', setOn='core-one')


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def keycloak(issuer=ISSUER, keys=KEYS, status=200, raw=None):
    def http(url):
        if status != 200: return status, b'refused'
        return 200, raw if raw is not None else json.dumps(dict(issuer=issuer, jwks_uri=keys)).encode()
    return http


class Device:
    api = 'https://api.example.isle'

    def __init__(self, folder, mode='core', root=True, http=None, start='core-only', publish_fails=False):
        self.setting_file = str(Path(folder) / 'identity' / 'provider.json')
        self._mode, self._root, self._http, self._start, self._fails = mode, root, http or keycloak(), start, publish_fails
        self.lines, self.published, self.asked = [], [], []

    def say(self, line=''): self.lines.append(line)
    def now(self): return 't1'
    def is_root(self): return self._root
    def mode(self): return self._mode
    def device(self): return 'core-one' if self._mode == 'core' else 'member-one'
    def start_setting(self): return self._start

    def http(self, url):
        self.asked.append(url)
        return self._http(url)

    def write(self, record): ip.IO.write(self, record)
    def remove(self): ip.IO.remove(self)

    def publish(self, record):
        if self._fails: raise RuntimeError('no release notice yet')
        self.published.append(record)

    @property
    def said(self): return '\n'.join(self.lines)
    def stored(self): return ip.local_record(self.setting_file)


class Case(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)

    def device(self, **kwargs): return Device(self.folder, **kwargs)


class Address(unittest.TestCase):
    def test_kept_as_typed_less_a_closing_slash(self):
        self.assertEqual(ip.checked_issuer(ISSUER + '/'), (ISSUER, 'Polari'))
        self.assertEqual(ip.checked_issuer('https://Auth.Example.isle:8443/kc/realms/a.b-c'), ('https://Auth.Example.isle:8443/kc/realms/a.b-c', 'a.b-c'))

    def test_refusals(self):
        for bad, word in (('http://auth.example.isle/realms/Polari', 'HTTPS'), ('https://u:p@auth.example.isle/realms/Polari', 'HTTPS'),
                          (ISSUER + '?x=1', 'HTTPS'), (ISSUER + '#x', 'HTTPS'), ('https://auth.example.isle', 'realms'),
                          ('https://auth.example.isle/realms/Polari/account', 'realms'), ('https://auth.example.isle:x/realms/Polari', 'port'),
                          ('https://auth_example.isle/realms/Polari', 'host name'), (None, 'missing')):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, word):
                ip.checked_issuer(bad)


class Discovery(unittest.TestCase):
    def test_returns_the_key_address_the_keycloak_states(self):
        asked = []
        def http(url):
            asked.append(url)
            return keycloak()(url)
        self.assertEqual(ip.discover(http, ISSUER), KEYS)
        self.assertEqual(asked, [ISSUER + '/.well-known/openid-configuration'])

    def test_refusals(self):
        for http, word in ((keycloak(status=0), 'no answer'), (keycloak(status=302), 'HTTP 302'), (keycloak(raw=b'<html>'), 'not a Keycloak'),
                           (keycloak(raw=b'[]'), 'not a Keycloak'), (keycloak(raw=json.dumps(dict(issuer=ISSUER, jwks_uri=KEYS, pad='x' * 70000)).encode()), 'not a Keycloak'),
                           (keycloak(issuer=ISSUER + 'x'), 'names itself'), (keycloak(issuer=ISSUER.replace('auth', 'Auth')), 'names itself'),
                           (keycloak(keys=KEYS.replace('https', 'http')), 'signing keys'),
                           (keycloak(keys='https://elsewhere.example/certs'), 'signing keys'), (keycloak(keys=None), 'signing keys')):
            with self.subTest(word=word), self.assertRaisesRegex(ValueError, word):
                ip.discover(http, ISSUER)


class Records(unittest.TestCase):
    def test_valid_keeps_only_known_keys(self):
        self.assertEqual(ip.valid(dict(RECORD, extra='x', realm='ignored')), RECORD)

    def test_malformed_is_none(self):
        for bad in (None, [], {}, dict(RECORD, issuer='http://a/realms/b'), dict(RECORD, jwksUri='https://other.example/certs'),
                    dict(RECORD, jwksUri=None), dict(RECORD, clientId='a b'), dict(RECORD, clientId=None)):
            with self.subTest(bad=bad):
                self.assertIsNone(ip.valid(bad))

    def test_published(self):
        answer = lambda release: (lambda url: (200, json.dumps(dict(ok=True, release=release)).encode()))
        self.assertEqual(ip.published_record(answer({'id': 'x', ip.NOTICE_KEY: RECORD}), 'https://api'), (RECORD, None))
        for release in ({'id': 'x'}, {'id': 'x', ip.NOTICE_KEY: None}, None, {ip.NOTICE_KEY: {'issuer': 'junk'}}):
            self.assertEqual(ip.published_record(answer(release), 'https://api'), (None, None))
        self.assertEqual(ip.published_record(lambda url: (0, b''), 'https://api'), (None, 'the core did not answer'))
        self.assertEqual(ip.published_record(lambda url: (200, b'<html>'), 'https://api')[1], 'the answer of the core could not be read')

    def test_backend_environment(self):
        self.assertEqual(ip.backend_environment(None), {})
        self.assertEqual(ip.backend_environment(RECORD), {
            'POLARI_KEYCLOAK_ISSUER_URI': ISSUER, 'POLARI_KEYCLOAK_JWKS_URI': KEYS, 'POLARI_KEYCLOAK_REALM': 'Polari',
            'POLARI_KEYCLOAK_FRONTEND_CLIENT_ID': 'polari-frontend'})

    def test_fill_frontend(self):
        config = {'frontend': {'x': 1}, 'keycloak': {'authority': 'https://old.example/realms/Old', 'clientId': 'polari-frontend', 'scope': 'openid', 'enabled': True}}
        self.assertEqual(ip.fill_frontend(config, RECORD), ['authority', 'realm'])
        self.assertEqual(config['keycloak'], {'authority': ISSUER, 'clientId': 'polari-frontend', 'realm': 'Polari', 'scope': 'openid', 'enabled': True})
        self.assertEqual((ip.fill_frontend(config, RECORD), config['frontend']), ([], {'x': 1}))
        fresh = {}
        ip.fill_frontend(fresh, RECORD)
        self.assertIs(fresh['keycloak']['enabled'], False)


class Show(Case):
    def test_core(self):
        d = self.device()
        self.assertEqual(ip.main([], d), 0)
        self.assertIn('none named', d.said)
        self.assertIn('sudo isle polari security provider', d.said)
        d.write(RECORD)
        d.lines.clear()
        ip.main([], d)
        self.assertIn(ISSUER + ' (client polari-frontend), set t0 on core-one', d.said)
        self.assertEqual(d.asked, [])

    def test_member_reads_what_the_core_publishes(self):
        d = self.device(mode='remote', http=lambda url: (200, json.dumps({'release': {ip.NOTICE_KEY: RECORD}}).encode()))
        self.assertEqual(ip.main([], d), 0)
        self.assertIn('as the core publishes it: ' + ISSUER, d.said)
        self.assertEqual(d.asked, ['https://api.example.isle/api/islemesh/updates'])
        d = self.device(mode='remote', http=lambda url: (0, b''))
        ip.main([], d)
        self.assertIn('not known on member-one (the core did not answer)', d.said)

    def test_neither(self):
        d = self.device(mode='')
        self.assertEqual(ip.main([], d), 1)
        self.assertEqual(ip.known(d)[0], None)


class Set(Case):
    def test_writes_publishes_and_switches_nothing_on(self):
        d = self.device()
        self.assertEqual(ip.main([ISSUER + '/'], d), 0)
        expected = dict(RECORD, setAt='t1')
        self.assertEqual((d.stored(), d.published), (expected, [expected]))
        self.assertEqual(os.stat(d.setting_file).st_mode & 0o777, 0o644)
        self.assertIn('Nothing was switched on', d.said)
        self.assertEqual(ip.known(d), (expected, None))

    def test_client(self):
        d = self.device()
        self.assertEqual(ip.main([ISSUER, '--client', 'isle-app'], d), 0)
        self.assertEqual(d.stored()['clientId'], 'isle-app')
        d = self.device()
        self.assertEqual(ip.main([ISSUER, '--client', 'a b'], d), 1)
        self.assertIn('client id', d.said)

    def test_member_is_refused_before_anything_is_asked(self):
        d = self.device(mode='remote', root=False)
        self.assertEqual(ip.main([ISSUER], d), 1)
        self.assertIn('on the core only', d.said)
        self.assertEqual((d.asked, d.published, d.stored()), ([], [], None))

    def test_bad_address_or_silent_keycloak_changes_nothing(self):
        for argv, http, word in ((['https://auth.example.isle'], None, 'realms'), ([ISSUER], keycloak(status=0), 'no Keycloak realm answered'),
                                 ([ISSUER], keycloak(issuer='https://other/realms/Polari'), 'names itself')):
            d = self.device(http=http)
            d.write(dict(RECORD, clientId='kept'))
            self.assertEqual(ip.main(argv, d), 1)
            self.assertIn(word, d.said)
            self.assertIn('Nothing was changed', d.said)
            self.assertEqual((d.stored()['clientId'], d.published), ('kept', []))

    def test_without_root_it_names_the_command_and_changes_nothing(self):
        d = self.device(root=False)
        self.assertEqual(ip.main([ISSUER, '--client', 'isle-app'], d), 1)
        self.assertIn('Type: sudo isle polari security provider %s --client isle-app' % ISSUER, d.said)
        self.assertEqual((d.stored(), d.published), (None, []))

    def test_recorded_even_when_the_notice_cannot_be_written(self):
        d = self.device(publish_fails=True)
        self.assertEqual(ip.main([ISSUER], d), 0)
        self.assertEqual(d.stored()['issuer'], ISSUER)
        self.assertIn('after the next publication', d.said)

    def test_usage(self):
        d = self.device()
        self.assertEqual(ip.main([ISSUER, 'extra'], d), 2)
        self.assertEqual(ip.main(['help'], d), 0)
        self.assertEqual(d.stored(), None)


class Clear(Case):
    def test_removes_and_publishes_none(self):
        d = self.device()
        d.write(RECORD)
        self.assertEqual(ip.main(['none'], d), 0)
        self.assertEqual((d.stored(), d.published, os.path.exists(d.setting_file)), (None, [None], False))
        self.assertEqual(ip.main(['none'], d), 0)

    def test_refused_while_the_isle_starts_releases_from_any_device(self):
        d = self.device(start='any-device')
        d.write(RECORD)
        self.assertEqual(ip.main(['none'], d), 1)
        self.assertIn('sudo isle release setting core-only', d.said)
        self.assertEqual((d.stored(), d.published), (RECORD, []))

    def test_member_and_no_root(self):
        d = self.device(mode='remote')
        self.assertEqual(ip.main(['none'], d), 1)
        d = self.device(root=False)
        d.write(RECORD)
        self.assertEqual(ip.main(['none'], d), 1)
        self.assertIn('Type: sudo isle polari security provider none', d.said)
        self.assertEqual(d.stored(), RECORD)


class NoticeWriter(Case):
    """The writer that runs inside the core's backend: it may replace this one key and leaves the rest alone."""
    def write(self, key, value):
        import release_run
        return subprocess.run([sys.executable, '-c', release_run.NOTICE_WRITE, key], input=json.dumps(value), text=True,
                              capture_output=True, env=dict(os.environ, DATABASE_PATH=str(self.folder / 'polari.db')))

    def test_replaces_only_its_key(self):
        notice = self.folder / 'isle-release.json'
        notice.write_text(json.dumps({'id': 'r', 'packages': [], 'run': {'runId': 'x'}, 'startSetting': {'start': 'core-only'}}))
        self.assertEqual(self.write(ip.NOTICE_KEY, RECORD).returncode, 0)
        self.assertEqual(json.loads(notice.read_text()), {'id': 'r', 'packages': [], 'run': {'runId': 'x'},
                                                          'startSetting': {'start': 'core-only'}, ip.NOTICE_KEY: RECORD})
        self.assertEqual(self.write(ip.NOTICE_KEY, None).returncode, 0)
        self.assertIsNone(json.loads(notice.read_text())[ip.NOTICE_KEY])
        self.assertNotEqual(self.write('packages', []).returncode, 0)

    def test_refuses_without_a_release_notice(self):
        self.assertNotEqual(self.write(ip.NOTICE_KEY, RECORD).returncode, 0)
        self.assertFalse((self.folder / 'isle-release.json').exists())


class Generated(unittest.TestCase):
    """A deployed backend is told the isle's Keycloak; nothing else about the deployment changes."""
    def make(self, role, **kwargs):
        config = load('polari-component-config')
        return config.generate('compute', 'islemesh', role, '/tmp/x', backend_url='https://api.compute.isle',
                               images={'backend': 'sha256:' + 'a' * 64, 'frontend': 'sha256:' + 'b' * 64}, **kwargs)

    def test_backend_environment(self):
        plain = self.make('both')
        told = self.make('both', provider=RECORD)
        env = told[0]['services']['backend']['environment']
        self.assertEqual({k: v for k, v in env.items() if 'KEYCLOAK' in k}, ip.backend_environment(RECORD))
        self.assertFalse([k for k in plain[0]['services']['backend']['environment'] if 'KEYCLOAK' in k or 'PERMISSIONS' in k])
        self.assertFalse([k for k in env if 'PERMISSIONS' in k])
        for key in ip.backend_environment(RECORD): del env[key]
        self.assertEqual(told, plain)

    def test_frontend_only_is_untouched(self):
        self.assertEqual(self.make('frontend', provider=RECORD), self.make('frontend'))


class Switch(Case):
    """isle polari security enable|require write the isle's Keycloak into the frontend file first."""
    def run_switch(self, config, known, *argv):
        path = self.folder / 'runtime-config.json'
        path.write_text(json.dumps(config))
        security = load('polari-security')
        out = textio.StringIO()
        with patch.object(sys, 'argv', ['polari-security.py', *argv, '--config', str(path)]), patch.object(os, 'geteuid', lambda: 0), \
             patch.object(ip, 'IO', lambda: None), patch.object(ip, 'known', lambda io: known), contextlib.redirect_stdout(out):
            try: security.main()
            except SystemExit as stop: return stop.code, out.getvalue(), json.loads(path.read_text())
        return 0, out.getvalue(), json.loads(path.read_text())

    def test_enable_takes_the_isles_keycloak(self):
        old = {'keycloak': {'authority': 'https://old.example/realms/Old', 'clientId': 'x', 'enabled': False}}
        code, said, config = self.run_switch(old, (RECORD, None), 'enable')
        self.assertEqual((code, config['keycloak']['authority'], config['keycloak']['clientId'], config['keycloak']['enabled']),
                         (0, ISSUER, 'polari-frontend', True))
        self.assertIn('written into this file', said)
        code, said, config = self.run_switch({}, (RECORD, None), 'require', '--feature', 'release-any-device')
        self.assertEqual((code, config['keycloak']['requiredBy'], config['keycloak']['realm']), (0, ['release-any-device'], 'Polari'))

    def test_with_none_named_the_file_is_used_as_before(self):
        old = {'keycloak': {'authority': 'https://old.example/realms/Old', 'clientId': 'x', 'enabled': False}}
        code, said, config = self.run_switch(old, (None, None), 'enable')
        self.assertEqual((code, config['keycloak']['authority'], config['keycloak']['enabled']), (0, 'https://old.example/realms/Old', True))
        self.assertIn('names no Keycloak', said)
        code, said, config = self.run_switch(old, (None, 'the core did not answer'), 'enable')
        self.assertIn('not known here (the core did not answer)', said)
        code, said, config = self.run_switch({}, (None, None), 'enable')
        self.assertEqual((code, config), (1, {}))

    def test_status_and_disable_never_ask(self):
        boom = lambda io: (_ for _ in ()).throw(AssertionError('asked'))
        path = self.folder / 'runtime-config.json'
        path.write_text(json.dumps({'keycloak': {'authority': ISSUER, 'clientId': 'x', 'enabled': True}}))
        security = load('polari-security')
        for action in ('status', 'disable'):
            with patch.object(sys, 'argv', ['polari-security.py', action, '--config', str(path)]), patch.object(os, 'geteuid', lambda: 0), \
                 patch.object(ip, 'known', boom), contextlib.redirect_stdout(textio.StringIO()):
                security.main()
        self.assertFalse(json.loads(path.read_text())['keycloak']['enabled'])


if __name__ == '__main__':
    unittest.main()
