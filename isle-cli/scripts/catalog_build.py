#!/usr/bin/env python3
"""isle store build <definition folder> <output folder> [--pull]

Makes, out of a release definition, the folder that `isle store publish` takes.  For anyone who may use Docker;
it publishes nothing and starts nothing.

A definition is a folder with a release.json, the compose file it names and the files it names.  In it each image
is written as `<release name>/<label>:PIN-AT-BUILD` with digests of zeros, and says where it is built from
(`buildFrom`, the name of an image).  The build gives that image the name `<release name>/<label>:<version>`,
saves it for linux/amd64, reads its identity out of the saved archive, and writes copies of release.json and the
compose file that carry the real name and digests.  The definition itself is never changed.

Without --pull nothing is downloaded: an image that is not on this machine stops the build.  With --pull every
`buildFrom` image is pulled first.  The output's release.json is written last, so a build that stops half way
leaves nothing that can be published.
"""
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile

from catalog_artifacts import sha, validate_manifest
import image_identity
from operations import NAME

PIN = 'PIN-AT-BUILD'
ZERO = 'sha256:' + '0' * 64
VERSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z')        # also the tag of every image of the release
LABEL = re.compile(r'[a-z0-9][a-z0-9._-]{0,63}\Z')
FROM = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_./:@-]{0,255}\Z')
INDEXES = ('application/vnd.oci.image.index.v1+json', 'application/vnd.docker.distribution.manifest.list.v2+json')
PLATFORM = 'linux/amd64'
USAGE = 'Usage: isle store build <definition folder> <output folder> [--pull]'
ERRORS = (OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.SubprocessError)


def _inside(text, what):
    """A path of the definition: relative, and not out of its folder."""
    path = PurePosixPath(text) if isinstance(text, str) else None
    if path is None or not path.parts or path.is_absolute() or '..' in path.parts:
        raise ValueError('%s must be a path inside the definition folder' % what)
    return path


def read(source):
    """The definition, checked whole before Docker is asked anything: (manifest, [(image entry, real name)])."""
    m = json.loads((source / 'release.json').read_text())
    if m.get('schemaVersion') != 1 or not isinstance(m.get('name'), str) or not NAME.match(m['name']):
        raise ValueError('release.json needs schemaVersion 1 and a name')
    if not isinstance(m.get('version'), str) or not VERSION.match(m['version']):
        raise ValueError('the version must also be usable as the tag of an image: letters, digits, . _ -')
    if not isinstance(m.get('images'), list) or not m['images']: raise ValueError('a release needs at least one image')
    pairs, archives = [], set()
    for image in m['images']:
        ref = image.get('ref') if isinstance(image, dict) else None
        label = ref[len(m['name']) + 1:-len(PIN) - 1] if isinstance(ref, str) and ref.startswith(m['name'] + '/') and ref.endswith(':' + PIN) else ''
        if not LABEL.match(label): raise ValueError('an image of a definition is named %s/<label>:%s' % (m['name'], PIN))
        if not isinstance(image.get('buildFrom'), str) or not FROM.match(image['buildFrom']) or image['buildFrom'].startswith(m['name'] + '/'):
            raise ValueError('the image %s needs buildFrom: the name of the image it is built from' % ref)
        if image.get('configDigest') != ZERO or image.get('manifestDigest', ZERO) != ZERO:
            raise ValueError('the image %s already carries a digest; a definition carries zeros' % ref)
        archive = _inside(image.get('archive', {}).get('source'), 'the archive of %s' % ref)
        if archive.parts[0] != 'images' or archive.suffix != '.tar' or str(archive) in archives:
            raise ValueError('each image needs its own archive, images/<name>.tar')
        archives.add(str(archive))
        pairs.append((image, '%s/%s:%s' % (m['name'], label, m['version'])))
    if len({ref for _, ref in pairs}) != len(pairs): raise ValueError('two images of the definition have the same name')
    for item in [m.get('compose')] + list(m.get('files', [])):
        path = _inside((item or {}).get('source'), 'a file of the release')
        if path.parts[0] == 'images' or not (source / path).is_file(): raise ValueError('the definition has no file %s' % path)
    compose = (source / m['compose']['source']).read_text()
    run_by = re.findall(r'^[ \t]+image:(.*)$', compose, re.M)
    for image, _ in pairs:
        if ' ' + image['ref'] not in run_by: raise ValueError('the compose file does not run %s' % image['ref'])
    if set(run_by) - {' ' + image['ref'] for image, _ in pairs}:
        raise ValueError('the compose file runs an image the release does not hold (written as `image: <name>`, nothing after it)')
    probe = copy.deepcopy(m)   # what an install would refuse (a slot, an upload size, a port) is refused before Docker is asked
    for item in [image['archive'] for image in probe['images']] + [probe['compose']] + list(probe.get('files', [])):
        item.pop('source'); item.update(url='https://apt.isle/catalog/probe', sha256='0' * 64, size=1)
    validate_manifest(probe)
    return m, pairs


def save(run, ref, path):
    """Save one image for linux/amd64.  A Docker that does not know --platform saves what it has: one platform."""
    done = run(['docker', 'image', 'save', '--platform', PLATFORM, '-o', str(path), ref], capture_output=True, text=True)
    if done.returncode != 0 and 'unknown flag' in (done.stderr or ''):
        done = run(['docker', 'image', 'save', '-o', str(path), ref], capture_output=True, text=True)
    if done.returncode != 0:
        raise ValueError('docker image save %s failed: %s' % (ref, ((done.stderr or '').strip().splitlines() or ['no reason given'])[-1]))


def identity(path, ref):
    """The identity the saved archive carries.  An archive of several platforms has no single one and is refused."""
    with tarfile.open(path, 'r:*') as archive:
        try: index = json.load(archive.extractfile('index.json'))
        except KeyError: index = None
    if index is not None:
        roots = index.get('manifests', [])
        named = [r for r in roots if r.get('annotations', {}).get('io.containerd.image.name') in (ref, 'docker.io/' + ref)]
        if any(r.get('mediaType') in INDEXES for r in (named or roots)):
            raise ValueError('the archive of %s holds several platforms; a release holds %s only' % (ref, PLATFORM))
    return image_identity.describe(str(path), ref)


def published(m, out):
    """The manifest as `isle store publish` stores it, and its sha256: what a release packet names it by."""
    m = copy.deepcopy(m)
    base = 'https://apt.isle/catalog/%s/%s/' % (m['name'], m['version'])

    def asset(d):
        path = out / d.pop('source')
        d.update(url=base + sha(path) + path.suffix, sha256=sha(path), size=path.stat().st_size)
    for image in m['images']: asset(image['archive'])
    asset(m['compose'])
    for item in m.get('files', []): asset(item)
    validate_manifest(m)
    return m, hashlib.sha256((json.dumps(m, sort_keys=True, indent=2) + '\n').encode()).hexdigest()


def build(source, out, pull=False, run=subprocess.run, which=shutil.which):
    source, out = Path(source).resolve(), Path(out).resolve()
    m, pairs = read(source)
    if out == source or source in out.parents: raise ValueError('the output folder must be outside the definition folder')
    if (out / 'release.json').exists(): raise ValueError('%s holds a release already; use an empty folder' % out)
    if not which('docker'): raise ValueError('docker is not on this machine')
    for image, _ in pairs:
        source_image = image['buildFrom']
        if pull:
            if run(['docker', 'pull', source_image], stdout=sys.stderr).returncode != 0: raise ValueError('docker pull %s failed' % source_image)
        elif run(['docker', 'image', 'inspect', '--format', '{{.Id}}', source_image], capture_output=True, text=True).returncode != 0:
            raise ValueError('the image %s is not on this machine. Nothing is downloaded without --pull' % source_image)
    (out / 'images').mkdir(parents=True, exist_ok=True)
    compose, rows = (source / m['compose']['source']).read_text(), []
    for image, ref in pairs:
        placeholder, source_image = image['ref'], image.pop('buildFrom')
        if run(['docker', 'tag', source_image, ref], capture_output=True, text=True).returncode != 0: raise ValueError('docker tag %s failed' % ref)
        archive = out / image['archive']['source']
        save(run, ref, archive)
        found = identity(archive, ref)
        image.update(ref=ref, configDigest=found['configDigest'])
        if 'manifestDigest' in found: image['manifestDigest'] = found['manifestDigest']
        else: image.pop('manifestDigest', None)
        compose = compose.replace('image: %s\n' % placeholder, 'image: %s\n' % ref)
        rows.append(dict(ref=ref, builtFrom=source_image, configDigest=image['configDigest'], manifestDigest=image.get('manifestDigest'),
                         archiveSha256=sha(archive), archiveBytes=archive.stat().st_size))
    compose = ''.join(line for line in compose.splitlines(True) if not line.startswith('# ' + PIN))
    for item in m.get('files', []):
        target = out / item['source']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / item['source'], target)
    (out / m['compose']['source']).write_text(compose)
    text = json.dumps(m, indent=2) + '\n'
    if PIN in text or ZERO in text or PIN in compose: raise ValueError('a placeholder is left in the output; nothing to publish was written')
    _, manifest_sha = published(m, out)
    (out / 'release.json').write_text(text)
    return dict(name=m['name'], version=m['version'], manifestSha256=manifest_sha, release=str(out / 'release.json'), images=rows)


def main(argv):
    words = [a for a in argv if a != '--pull']
    if len(words) != 2 or any(w.startswith('-') for w in words):
        print(USAGE, file=sys.stderr); return 2
    try: result = build(words[0], words[1], pull='--pull' in argv)
    except ERRORS as error:
        print('store build: %s' % error, file=sys.stderr); return 1
    print(json.dumps(dict(ok=True, operation='store.build', result=result), indent=2))
    print('Nothing was published. On the core: sudo isle store publish %s   (the sha256 it prints must be the manifestSha256 above)'
          % result['release'], file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
