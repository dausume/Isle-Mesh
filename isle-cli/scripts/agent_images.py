#!/usr/bin/env python3
"""The two agent images of a release, made without the network: the image the isle runs now, plus one layer.

An agent image is nginx, a few packages and the agent's scripts.  The Dockerfiles in isle-agent/ build one whole,
and that needs the network (the image they start from, the packages).  A release that changes the scripts needs
none of it.  For each agent this takes the image the core publishes now, adds ONE layer that holds the scripts of
the commit (every file its Dockerfile copies), names the result <the same name>:release<n> and saves it for the
packet.  Devices get it the way they get every runtime image: `isle update publish-runtime` on the core, then
`isle update apply` on each device.

What a Dockerfile does other than COPY (the image it starts from, packages, folders, the entrypoint) is taken from
the image the isle runs, as it is.  Each image made here carries two labels: a checksum of the scripts it holds, and
a checksum of those other lines as the commit has them.  With them a later build
  - leaves an image alone whose scripts are the commit's already (the release then keeps the published image), and
  - refuses when the other lines changed: a layer cannot carry that, the image has to be built whole.
An image without the labels (one built before this file existed) is taken as it is, and that is said.

Every release that changes the scripts adds one small layer to the image.  Building the image whole starts again.

Called by `isle release build --agents`; `io` is release_build.Io (a test replaces it).
"""
import copy
import hashlib
import json
import re
import shutil
import tarfile
from pathlib import PurePosixPath

import catalog_build
from image_identity import verify_archive
from runtime_reconcile import PUBLISHED_RUNTIME, targets

RELEASE_URL = 'https://apt.isle/runtime-release.json'
AGENTS = ({'mode': 'core', 'container': 'isle-vlan-agent', 'dockerfile': 'isle-agent/isle-vlan-agent/Dockerfile',
           'context': 'isle-agent/isle-vlan-agent', 'archive': 'core-agent.tar'},
          {'mode': 'remote', 'container': 'isle-remote-agent', 'dockerfile': 'isle-agent/isle-remote-agent/Dockerfile.remote',
           'context': 'isle-agent', 'archive': 'remote-agent.tar'})
SCRIPTS_LABEL, RECIPE_LABEL = 'org.isle-mesh.agent.scripts', 'org.isle-mesh.agent.recipe'
BIN = '/usr/local/bin/'
FILE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z')
IMAGES = 'packet/runtime/images'
MANIFEST = 'runtime/manifest.json'      # in the packet
ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError, tarfile.TarError)


class Refused(Exception): pass


def sha256(data): return hashlib.sha256(data).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): digest.update(block)
    return digest.hexdigest()


def instructions(text):
    """The instructions of a Dockerfile, one per entry: comments out, continued lines joined, spaces single."""
    found, held = [], ''
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'): continue
        if line.endswith('\\'):
            held += line[:-1] + ' '; continue
        found.append(' '.join((held + line).split())); held = ''
    if held.strip(): found.append(' '.join(held.split()))
    return found


def recipe(text, where):
    """([(file in the build context, its name in /usr/local/bin)], a checksum of every other line).
    `RUN chmod +x` of copied files is not another line: the layer made here holds them executable."""
    copies, other = [], []
    for line in instructions(text):
        words = line.split()
        made = {BIN + name for _, name in copies}
        if words[0].upper() == 'COPY':
            source = PurePosixPath(words[1]) if len(words) == 3 and not words[1].startswith('-') else None
            name = words[2][len(BIN):] if len(words) == 3 and words[2].startswith(BIN) else ''
            if source is None or source.is_absolute() or '..' in source.parts or not FILE.match(name) or BIN + name in made:
                raise Refused('%s: `%s` is not a copy of one file to %s<its own name>. The layer this build adds holds such '
                              'files and nothing else' % (where, line, BIN))
            copies.append((str(source), name))
        elif words[:3] == ['RUN', 'chmod', '+x'] and len(words) > 3 and set(words[3:]) <= made: continue
        else: other.append(line)
    if not copies: raise Refused('%s copies no file, so there is nothing of the commit to put into the image' % where)
    return copies, sha256('\n'.join(other).encode())


def scripts_sum(files):
    return sha256(json.dumps(sorted([name, sha256(data)] for name, data in files)).encode())


def repository(ref):
    """The name of an image without its tag or digest."""
    name = ref.split('@')[0]
    return name.rsplit(':', 1)[0] if ':' in name.rsplit('/', 1)[-1] else name


def row_of(release, agent):
    return next((row for row in release['runtimes'].get(agent['mode'], []) if row['container'] == agent['container']), None)


def prepare(io, folder=None):
    """Before anything is written: Docker answers, and the core serves a runtime release that names both agents."""
    if folder is not None and not folder.is_dir(): raise Refused('--runtime-archives %s is not a folder' % folder)
    if io.run(['docker', 'version', '--format', '{{.Server.Version}}'])[0] != 0:
        raise Refused('Docker does not answer on this machine (docker version). --agents builds the two agent images with it')
    release = io.fetch_json(RELEASE_URL)
    try:
        for mode in ('core', 'remote'):
            for row in targets(release, mode):
                archive = row['image']['archive']
                if archive['url'] != PUBLISHED_RUNTIME + archive['sha256'] + '.tar':
                    raise ValueError('the image of %s is served at %s, not as %s<sha256>.tar' % (row['container'], archive['url'], PUBLISHED_RUNTIME))
    except ERRORS as exc:
        raise Refused('the runtime release the core serves (%s) cannot be used: %s' % (RELEASE_URL, exc))
    for agent in AGENTS:
        if row_of(release, agent) is None:
            raise Refused('the runtime release the core serves names no image for %s, so there is no image to add the '
                          "commit's scripts to. That image has to be built whole and published once" % agent['container'])
    return {'runtimes': copy.deepcopy(release['runtimes'])}


def inspect(io, ref, what):
    status, out = io.run(['docker', 'image', 'inspect', '--format', what, ref])
    return out.strip() if status == 0 else None


def inspect_json(io, ref, what):
    out = inspect(io, ref, '{{json %s}}' % what)
    try: return json.loads(out)
    except (TypeError, ValueError): raise Refused('Docker does not say %s of the image %s' % (what, ref))


def published_archive(io, out, row, folder):
    """The archive of a published image: from the folder that was named, when it holds that file; else from the core."""
    image, archive = row['image'], row['image']['archive']
    name, path = archive['sha256'] + '.tar', None
    if folder is not None:
        held = folder / name
        if held.is_file() and held.stat().st_size == archive['size'] and file_sha256(held) == archive['sha256']: path = held
    if path is None:
        io.say('  fetching the image of %s from the core: %d MiB' % (row['container'], archive['size'] >> 20))
        path = io.fetch(archive, out / 'published' / name)
    try: verify_archive(path, image)
    except ERRORS as exc: raise Refused('%s is not the image the core says it publishes for %s: %s' % (path, row['container'], exc))
    return path


def base_image(io, out, row, folder, log):
    """Docker here holds the image the core publishes for this agent, under its published name."""
    image = row['image']
    ids = {image['configDigest']} | ({image['manifestDigest']} if 'manifestDigest' in image else set())
    if inspect(io, image['ref'], '{{.Id}}') in ids: return 'it was in Docker here'
    path = published_archive(io, out, row, folder)
    if io.run(['docker', 'load', '--input', str(path)], log=log)[0] != 0:
        raise Refused('docker load of %s failed; what it printed is in %s' % (path, log))
    if inspect(io, image['ref'], '{{.Id}}') not in ids:
        raise Refused('Docker loaded %s, and %s is still not the image the core publishes for %s' % (path, image['ref'], row['container']))
    return 'loaded from %s' % ('the folder --runtime-archives names' if folder is not None and path.parent == folder else 'the core')


def save(io, ref, path, log):
    """One platform.  A Docker that does not know --platform saves what it has, which is one platform."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if io.run(['docker', 'image', 'save', '--platform', catalog_build.PLATFORM, '-o', str(path), ref], log=log)[0] != 0:
        path.unlink(missing_ok=True)
        if io.run(['docker', 'image', 'save', '-o', str(path), ref], log=log)[0] != 0:
            raise Refused('docker image save %s failed; what it printed is in %s' % (ref, log))


def rebuild(io, out, src, agent, row, number, folder, log):
    """One agent.  -> (its new row, or None when the published image holds the commit's scripts already; the record)."""
    copies, recipe_sum = recipe((src / agent['dockerfile']).read_text(), agent['dockerfile'])
    files = []
    for source, name in copies:
        full = src / agent['context'] / source
        if full.is_symlink() or not full.is_file():
            raise Refused('%s copies %s, and the commit holds no such file' % (agent['dockerfile'], source))
        files.append((name, full.read_bytes()))
    scripts, base = scripts_sum(files), row['image']['ref']
    record = {'container': agent['container'], 'builtFrom': {'ref': base, 'configDigest': row['image']['configDigest']},
              'scripts': scripts, 'files': sorted(name for name, _ in files)}
    record['base'] = base_image(io, out, row, folder, log)
    labels = inspect_json(io, base, '.Config.Labels') or {}
    if labels.get(RECIPE_LABEL) not in (None, recipe_sum):
        raise Refused('%s changed in lines other than COPY since the image the isle runs for %s was made. A layer cannot carry '
                      'that (the image it starts from, packages, folders, the entrypoint): build that image whole from the '
                      'Dockerfile on a machine with the network, publish it, and build the release after that'
                      % (agent['dockerfile'], agent['container']))
    if labels.get(SCRIPTS_LABEL) == scripts and labels.get(RECIPE_LABEL) == recipe_sum:
        return None, dict(record, kept=True, ref=base)
    context = out / 'agents' / agent['mode']
    context.mkdir(parents=True)
    for name, data in files:
        (context / name).write_bytes(data); (context / name).chmod(0o755)
    (context / 'Dockerfile').write_text('FROM %s\nLABEL %s="%s" %s="%s"\nCOPY %s %s\n' % (
        base, SCRIPTS_LABEL, scripts, RECIPE_LABEL, recipe_sum, ' '.join(sorted(name for name, _ in files)), BIN))
    ref, was = '%s:release%d' % (repository(base), number), inspect_json(io, base, '.RootFS.Layers')
    if io.run(['docker', 'build', '--pull=false', '--network', 'none', '-t', ref, str(context)], log=log)[0] != 0:
        raise Refused('docker build of %s failed; what it printed is in %s' % (ref, log))
    now = inspect_json(io, ref, '.RootFS.Layers')
    got = inspect_json(io, ref, '.Config.Labels') or {}
    if not isinstance(was, list) or not isinstance(now, list) or now[:-1] != was or len(now) != len(was) + 1 \
            or got.get(SCRIPTS_LABEL) != scripts or got.get(RECIPE_LABEL) != recipe_sum:
        raise Refused('what Docker built as %s is not the image the isle runs for %s plus one layer with its two labels' % (ref, agent['container']))
    archive = out / IMAGES / agent['archive']
    save(io, ref, archive, log)
    try: found = catalog_build.identity(archive, ref)
    except ERRORS as exc: raise Refused('the archive saved for %s cannot be read as one image: %s' % (ref, exc))
    image = {'ref': ref, 'configDigest': found['configDigest'], 'archive': {'source': 'images/' + agent['archive']}}
    if 'manifestDigest' in found: image['manifestDigest'] = found['manifestDigest']
    return dict(row, image=image), dict(record, kept=False, ref=ref, configDigest=image['configDigest'], layers=len(now),
                                        recipeOfBase='the same' if RECIPE_LABEL in labels else 'not labelled',
                                        archiveSha256=file_sha256(archive), archiveBytes=archive.stat().st_size)


def rebuild_all(io, out, src, manifest, number, log, folder=None):
    """Both agents, written into the manifest (changed in place).  -> (the records, how many images changed)."""
    records, changed = [], 0
    for agent in AGENTS:
        rows = manifest['runtimes'][agent['mode']]
        at = next(i for i, row in enumerate(rows) if row['container'] == agent['container'])
        io.say('  the image of %s' % agent['container'])
        row, record = rebuild(io, out, src, agent, rows[at], number, folder, log)
        records.append(record)
        if row is not None: rows[at] = row; changed += 1
    return records, changed


def finish(io, out, manifest, folder=None, whole=False):
    """The manifest of the release's runtime images, into the packet."""
    if whole:        # for a core whose CLI does not read a row that names the published file
        for rows in manifest['runtimes'].values():
            for row in rows:
                archive = row['image']['archive']
                if 'source' in archive: continue
                target = out / IMAGES / (archive['sha256'] + '.tar')
                if not target.exists():
                    io.say('  the image of %s, as the core publishes it (%d MiB)' % (row['container'], archive['size'] >> 20))
                    shutil.copyfile(published_archive(io, out, row, folder), target)
                row['image']['archive'] = {'source': 'images/' + target.name}
    (out / 'packet' / MANIFEST).write_text(json.dumps(manifest, indent=1) + '\n')
    return manifest


def build(io, out, src, release, number, log, folder=None, whole=False):
    """Both agents, and the manifest of the release's runtime images in the packet.  -> (manifest or None, records).
    None: both published images hold the commit's scripts already, and the packet publishes no runtime image."""
    manifest = copy.deepcopy(release)
    records, changed = rebuild_all(io, out, src, manifest, number, log, folder)
    return (finish(io, out, manifest, folder, whole) if changed else None), records
