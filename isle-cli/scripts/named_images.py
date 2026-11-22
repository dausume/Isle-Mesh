#!/usr/bin/env python3
"""A runtime image of a release that was built elsewhere: `isle release build --runtime-image <container>=<image>`.

The isle builds its own agent images (agent_images.py).  The image of another runtime container - the Polari backend
and frontend of a provider - is built by what owns its source (`pol build image backend|frontend`), and a release
takes it as it is: Docker on this machine holds the image under the name given, the build saves it for the packet and
writes it into the list of runtime images in place of the one the core publishes for that container.

  <container>  a container the runtime release of the core names (https://apt.isle/runtime-release.json)
  <image>      the name of the image in Docker here

Refused: a container the core's release does not name; an image Docker does not hold, or of another platform; the
name the core publishes already for that container with other contents (a name does not change what it means).  An
image that IS the published one is left out: the release keeps the published image.

What changes on a device is decided by its update, as for every runtime image: the container is started again from
the new image, and a provider (a Polari instance on a member) follows on that device's `isle update apply`.
"""
import catalog_build
import agent_images
from agent_images import ERRORS, IMAGES, Refused, file_sha256, inspect, inspect_json, save


def parse(io, values, release):
    """['container=image', ...] -> what to place, checked against the runtime release of the core and against Docker
    here: everything that can be refused is refused here, before a build writes anything."""
    known = {row['container']: row['image'] for rows in release['runtimes'].values() for row in rows}
    out = []
    for value in values:
        container, mark, ref = value.partition('=')
        if not mark or not container or not ref or ' ' in ref:
            raise Refused('--runtime-image wants <container>=<image>, not %r' % value)
        if container not in known:
            raise Refused('the runtime release the core serves names no container %s (it names: %s)' % (container, ', '.join(sorted(known))))
        if container in {agent['container'] for agent in agent_images.AGENTS}:
            raise Refused('%s is an agent of the isle: its image is made from the commit with --agents' % container)
        if any(item['container'] == container for item in out):
            raise Refused('--runtime-image names %s twice' % container)
        published, found = known[container], inspect(io, ref, '{{.Id}}')
        if found is None: raise Refused('Docker on this machine holds no image named %s' % ref)
        item = {'container': container, 'ref': ref, 'configDigest': found, 'replaces': {'ref': published['ref'], 'configDigest': published['configDigest']},
                'kept': found in {published['configDigest'], published.get('manifestDigest')}}
        if not item['kept']:
            if ref == published['ref']:
                raise Refused('%s is the name the core publishes for %s already, and the image Docker holds under it here is another one. '
                              'A name does not change what it means: give the new image a new name' % (ref, container))
            platform = inspect(io, ref, '{{.Os}}/{{.Architecture}}')
            if platform != catalog_build.PLATFORM:
                raise Refused('the image %s is for %s; the isle runs %s' % (ref, platform, catalog_build.PLATFORM))
            item['layers'] = inspect_json(io, ref, '.RootFS.Layers')
        out.append(item)
    return out


def shared(io, base, layers):
    """How many layers the image has in common with the published one, when Docker here holds that one too."""
    if inspect(io, base, '{{.Id}}') is None: return None
    was = inspect_json(io, base, '.RootFS.Layers')
    return next((n for n, (a, b) in enumerate(zip(was, layers)) if a != b), min(len(was), len(layers)))


def place(io, out, manifest, wanted, log):
    """Save each named image for the packet and write it into the manifest (changed in place).  -> the records."""
    records = []
    for item in wanted:
        container, ref = item['container'], item['ref']
        record = {key: item[key] for key in ('container', 'ref', 'configDigest', 'replaces', 'kept')}
        io.say('  the image of %s: %s' % (container, ref))
        if item['kept']: records.append(record); continue
        archive = out / IMAGES / (container + '.tar')
        save(io, ref, archive, log)
        try: saved = catalog_build.identity(archive, ref)
        except ERRORS as exc: raise Refused('the archive saved for %s cannot be read as one image: %s' % (ref, exc))
        if item['configDigest'] not in {saved['configDigest'], saved.get('manifestDigest')}:
            raise Refused('the archive saved for %s holds the image %s, and Docker named %s before the build' % (ref, saved['configDigest'], item['configDigest']))
        image = {'ref': ref, 'configDigest': saved['configDigest'], 'archive': {'source': 'images/' + archive.name}}
        if 'manifestDigest' in saved: image['manifestDigest'] = saved['manifestDigest']
        for rows in manifest['runtimes'].values():
            for row in rows:
                if row['container'] == container: row['image'] = image
        records.append(dict(record, configDigest=image['configDigest'], layers=len(item['layers']), sharedLayers=shared(io, item['replaces']['ref'], item['layers']),
                            archiveSha256=file_sha256(archive), archiveBytes=archive.stat().st_size))
    return records
