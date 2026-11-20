#!/usr/bin/env python3
"""Static checks of the isle-content-host release sources (no docker, no network).

build-release.sh is exercised only against a fake `docker` on PATH.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import catalog_artifacts as ca

REL = Path(__file__).resolve().parent.parent / 'releases' / 'isle-content-host'
ZERO = 'sha256:' + '0' * 64
REF = 'isle-content-host/nginx:2026.11.1'
FAKE_DOCKER = '''#!/bin/sh
case "$1 $2" in
  "image inspect") echo "nginx@sha256:%s";;
  "image save")
    shift; shift
    [ "$FAKE_NO_PLATFORM" = 1 ] && { echo "unknown flag: --platform" >&2; exit 125; }
    [ "$1" = --platform ] && [ "$2" = linux/amd64 ] && [ "$3" = -o ] || { echo "fake: save needs --platform linux/amd64 -o" >&2; exit 1; }
    d="$(mktemp -d)"; mt=application/vnd.oci.image.manifest.v1+json
    [ "$FAKE_ROOT" = index ] && mt=application/vnd.oci.image.index.v1+json
    printf '{"manifests":[{"mediaType":"%%s","digest":"sha256:%s","annotations":{"io.containerd.image.name":"%%s"}}]}' "$mt" "$5" > "$d/index.json"
    tar -cf "$4" -C "$d" index.json; rm -rf "$d"
    echo "$5" >> "$(dirname "$4")/saved-tags";;
  "tag "*) echo "$2 $3" >> "$(dirname "$0")/tags";;
esac
exit 0
''' % ('e' * 64, 'd' * 64)
# Stand-in for scripts/image_identity.py: same CLI (<archive> <ref>), deterministic output.
STUB_IDENTITY = '''import json, os, sys
if os.environ.get('STUB_MODE') == 'two':
    print('Select a single platform image archive before publication', file=sys.stderr); sys.exit(1)
print(json.dumps(dict(ref=sys.argv[2], configDigest='sha256:' + 'c' * 64, manifestDigest='sha256:' + 'd' * 64,
                      archive=dict(source=sys.argv[1]))))
'''


def published(m, root):
    """The manifest as `isle store publish` stores it (validate_manifest checks this form)."""
    m = json.loads(json.dumps(m))
    base = 'https://apt.isle/catalog/%s/%s/' % (m['name'], m['version'])
    def pub(item):
        path = Path(root) / item['source']
        data = path.read_bytes() if path.exists() else b'x'
        item.pop('source'); item.update(url=base + hashlib.sha256(data).hexdigest() + path.suffix,
                                        sha256=hashlib.sha256(data).hexdigest(), size=len(data))
    for i in m['images']: pub(i['archive'])
    pub(m['compose'])
    for f in m['files']: pub(f)
    return m


class ContentHostRelease(unittest.TestCase):
    def manifest(self):
        return json.loads((REL / 'release.json').read_text())

    def test_manifest_validates(self):
        m = self.manifest()
        self.assertEqual(m['schemaVersion'], 1)
        self.assertEqual((m['name'], m['version'], m['category']), ('isle-content-host', '2026.11.1', 'infrastructure'))
        ca.validate_manifest(published(m, REL))
        self.assertEqual(m['deploy'], {'service': 'content', 'port': 80})
        self.assertEqual(m['storage'], {'bindParameter': 'ISLE_DATASET_PATH', 'mount': '/data', 'readOnly': True})

    def test_placeholders_marked_pending(self):
        self.assertTrue((REL / 'PINS-PENDING').is_file())
        m = self.manifest()
        self.assertEqual(m['images'][0]['configDigest'], ZERO)
        self.assertEqual(m['images'][0]['manifestDigest'], ZERO)
        self.assertEqual(m['images'][0]['ref'], 'isle-content-host/nginx:PIN-AT-BUILD')
        self.assertIn('image: isle-content-host/nginx:PIN-AT-BUILD', (REL / 'docker-compose.yml').read_text())

    def test_compose(self):
        text = (REL / 'docker-compose.yml').read_text()
        for needle in ('content:', 'restart: "no"', 'create_host_path: false', 'read_only: true',
                       '${ISLE_DATASET_PATH}', 'target: /data'):
            self.assertIn(needle, text)
        self.assertNotIn('build:', text)
        try:
            import yaml
        except ImportError:
            return
        doc = yaml.safe_load(text)
        svc = doc['services']['content']
        self.assertEqual(svc['restart'], 'no')
        bind = [v for v in svc['volumes'] if v['target'] == '/data'][0]
        self.assertEqual((bind['type'], bind['read_only'], bind['bind']['create_host_path']), ('bind', True, False))
        self.assertEqual(bind['source'], '${ISLE_DATASET_PATH}')

    def test_compose_matches_manifest_ref(self):
        m = self.manifest()
        self.assertIn('image: ' + m['images'][0]['ref'], (REL / 'docker-compose.yml').read_text())
        self.assertEqual([f['path'] for f in m['files']], ['nginx.conf'])
        self.assertIn('./nginx.conf', (REL / 'docker-compose.yml').read_text())

    def test_nginx_conf(self):
        conf = (REL / 'nginx.conf').read_text()
        for needle in ('root /data/current;', 'autoindex on;', 'server_tokens off;', 'listen 80 ',
                       'location /.well-known/isle-storage/ {', 'alias /data/control/;',
                       'location = /.well-known/isle-storage/current {', 'alias /data/control/current;',
                       'default_type text/plain;', 'default_type application/json;',
                       'add_header Cache-Control "no-store"'):
            self.assertIn(needle, conf)
        self.assertNotIn('listen 443', conf)
        self.assertNotIn('$request_body', conf)
        self.assertEqual(conf.count('{'), conf.count('}'))

    def test_build_script_static(self):
        script = REL / 'build-release.sh'
        subprocess.run(['bash', '-n', str(script)], check=True)
        text = script.read_text()
        self.assertIn('placeholder digest still present; refusing to publish', text)
        self.assertIn('sha256:0{64}', text)
        self.assertIn('PINS-PENDING', text)
        self.assertIn('manifestSha256', text)
        self.assertIn('--platform linux/amd64', text)

    def env(self, tmp, **extra):
        (tmp / 'bin').mkdir(exist_ok=True); (tmp / 'scripts').mkdir(exist_ok=True)
        d = tmp / 'bin' / 'docker'
        d.write_text(FAKE_DOCKER); d.chmod(0o755)
        (tmp / 'scripts' / 'image_identity.py').write_text(STUB_IDENTITY)
        return dict(os.environ, PATH=str(tmp / 'bin') + os.pathsep + os.environ['PATH'],
                    ISLE_SCRIPTS=str(tmp / 'scripts'), **extra)

    def run_build(self, env, out):
        return subprocess.run(['bash', str(REL / 'build-release.sh'), str(out)], env=env, capture_output=True, text=True)

    def test_build_script_with_fake_docker(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            env = self.env(tmp)
            out = tmp / 'out'
            r = self.run_build(env, out)
            self.assertEqual(r.returncode, 0, r.stderr)
            m = json.loads((out / 'release.json').read_text())
            img = m['images'][0]
            self.assertEqual(img['ref'], REF)
            self.assertEqual(img['configDigest'], 'sha256:' + 'c' * 64)
            self.assertEqual(img['manifestDigest'], 'sha256:' + 'd' * 64)
            self.assertNotEqual(img['configDigest'], img['manifestDigest'])
            self.assertNotIn('b' * 64, json.dumps(m))
            compose = (out / 'docker-compose.yml').read_text()
            self.assertIn('image: ' + REF + '\n', compose)
            self.assertNotIn(ZERO, compose + json.dumps(m))
            self.assertNotIn('PIN-AT-BUILD', compose + json.dumps(m))
            self.assertEqual((tmp / 'bin' / 'tags').read_text().strip(), 'nginx:alpine ' + REF)
            self.assertEqual((out / 'images' / 'saved-tags').read_text().strip(), REF)
            self.assertFalse((out / 'PINS-PENDING').exists())
            self.assertTrue((REL / 'PINS-PENDING').exists())
            self.assertIn('PIN-AT-BUILD', (REL / 'release.json').read_text())
            ca.validate_manifest(published(m, out))
            printed = re.search(r'manifestSha256 = ([0-9a-f]{64})', r.stdout)
            self.assertTrue(printed, r.stdout)
            m = published(m, out)
            raw = (json.dumps(m, sort_keys=True, indent=2) + '\n').encode()
            self.assertEqual(printed.group(1), hashlib.sha256(raw).hexdigest())
            summary = json.loads(r.stdout[r.stdout.index('{'):])
            tar = (out / 'images' / 'nginx-alpine.tar').read_bytes()
            self.assertEqual(summary, dict(ref=REF, configDigest='sha256:' + 'c' * 64, manifestDigest='sha256:' + 'd' * 64,
                                           indexDigest='sha256:' + 'e' * 64, archiveSha256=hashlib.sha256(tar).hexdigest(), archiveBytes=len(tar),
                                           manifestSha256=printed.group(1)))
            # A second run into the same dir is refused.
            self.assertNotEqual(self.run_build(env, out).returncode, 0)

    def test_build_refuses_multi_manifest_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            r = self.run_build(self.env(tmp, STUB_MODE='two'), tmp / 'out')
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse((tmp / 'out' / 'release.json').exists())

    def test_build_refuses_index_root_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            r = self.run_build(self.env(tmp, FAKE_ROOT='index'), tmp / 'out')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('multi-platform archive', r.stderr)
            self.assertFalse((tmp / 'out' / 'release.json').exists())

    def test_build_refuses_when_docker_lacks_platform_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            r = self.run_build(self.env(tmp, FAKE_NO_PLATFORM='1'), tmp / 'out')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('docker image save --platform is required', r.stderr)
            self.assertFalse((tmp / 'out' / 'release.json').exists())

    def test_build_refuses_unresolved_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp); (tmp / 'bin').mkdir()
            d = tmp / 'bin' / 'docker'
            d.write_text('#!/bin/sh\necho "<none>"\nexit 0\n'); d.chmod(0o755)
            env = dict(os.environ, PATH=str(tmp / 'bin') + os.pathsep + os.environ['PATH'])
            r = subprocess.run(['bash', str(REL / 'build-release.sh'), str(tmp / 'o')], env=env, capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse((tmp / 'o' / 'release.json').exists())


if __name__ == '__main__':
    unittest.main()
