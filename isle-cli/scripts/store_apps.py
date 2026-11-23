#!/usr/bin/env python3
"""A store app in a release packet: `isle release build --store-app <device>:<folder>`.

  <device>  the device that installs the app after its other steps (`isle store install <app>`)
  <folder>  what `isle store build` left: release.json with the real image names and digests, the image
            archives, the compose file and the files the release names

The packet carries the folder as catalog/<app>/ (a store app each in a folder of its own).  The core publishes it
as a step of the run (`isle store publish`), after the packages; the device waits until the core serves that very
release (the sha256 of the manifest as the core stores it, worked out here the way `isle store build` prints it),
installs it, and the run stops on that device when the app is not on that release afterwards.

One app may go to several devices (the option once per device, the same folder): it is published once.

Refused, before anything is written: a folder that is not a built release (a definition still carries
PIN-AT-BUILD), a file the release names that is not there, two folders for one app, one app twice on one device, a
device the packet does not name.

Nothing here asks Docker or a core anything: the folder is taken as it is.
"""
import json
import os
from pathlib import Path
import shutil

import catalog_build
import release_run as rr
from agent_images import Refused

DIR = 'catalog'


def read(folder):
    """One built release: {name, version, manifestSha256, folder, sources, bytes}."""
    where = '--store-app %s' % folder
    path = folder / 'release.json'
    if not path.is_file(): raise Refused('%s holds no release.json; it wants the output folder of `isle store build`' % where)
    text = path.read_text()
    if catalog_build.PIN in text or catalog_build.ZERO in text:
        raise Refused('%s is a definition, not a built release (it still says %s): build it first with `isle store build`' % (where, catalog_build.PIN))
    try: release = json.loads(text)
    except ValueError: raise Refused('%s: release.json is not valid JSON' % where)
    if not isinstance(release, dict) or not isinstance(release.get('name'), str) or not rr.NAME.fullmatch(release['name']) \
            or not isinstance(release.get('version'), str) or not catalog_build.VERSION.match(release['version']):
        raise Refused('%s: release.json needs the name and the version of a store app' % where)
    sources = rr.release_sources(release)
    for source in sources:
        try: rr.safe_relpath(source, 'release.json')
        except rr.PacketError as exc: raise Refused('%s: %s' % (where, exc))
        if not (folder / source).is_file() or (folder / source).is_symlink():
            raise Refused('%s: the release names the file %s, which is not in the folder' % (where, source))
    try: _, manifest_sha = catalog_build.published(release, folder)
    except catalog_build.ERRORS as exc: raise Refused('%s is not a release the store takes: %s' % (where, exc))
    return {'name': release['name'], 'version': release['version'], 'manifestSha256': manifest_sha, 'folder': folder,
            'sources': sources, 'bytes': sum((folder / source).stat().st_size for source in sources)}


def parse(values, devices):
    """['device:folder', ...] -> {'apps': [a built release each, once], 'steps': {device: [the store-install steps]}}."""
    apps, steps = {}, {}
    for value in values:
        device, mark, folder = value.partition(':')
        if not mark or not folder or not rr.NAME.fullmatch(device):
            raise Refused('--store-app wants <device>:<folder of a built store release>, not %r' % value)
        if devices and device not in devices:
            raise Refused('--store-app names the device %s, which this packet does not name (--device %s)' % (device, device))
        app = read(Path(os.path.abspath(folder)))
        held = apps.setdefault(app['name'], app)
        if held['folder'] != app['folder']:
            raise Refused('--store-app names two folders for the store app %s (%s and %s); a packet holds one release of an app'
                          % (app['name'], held['folder'], app['folder']))
        step = {'verb': 'store-install', 'app': app['name'], 'version': app['version'], 'manifestSha256': app['manifestSha256']}
        if step in steps.get(device, []): raise Refused('--store-app puts %s on %s twice' % (app['name'], device))
        steps.setdefault(device, []).append(step)
    return {'apps': [apps[name] for name in sorted(apps)], 'steps': steps}


def publish(wanted):
    return [{'kind': 'catalog', 'release': '%s/%s/release.json' % (DIR, app['name'])} for app in wanted['apps']]


def place(packet, wanted):
    """The folders into the packet, catalog/<app>/ each.  A large archive is linked when the packet lies on the same
    disk, and copied when it does not."""
    for app in wanted['apps']:
        for name in ['release.json'] + app['sources']:
            target = packet / DIR / app['name'] / name
            target.parent.mkdir(parents=True, exist_ok=True)
            try: os.link(app['folder'] / name, target)
            except OSError: shutil.copy2(app['folder'] / name, target)


def records(wanted):
    return [{'name': app['name'], 'version': app['version'], 'manifestSha256': app['manifestSha256'], 'bytes': app['bytes'],
             'devices': sorted(device for device, steps in wanted['steps'].items() if any(step['app'] == app['name'] for step in steps))}
            for app in wanted['apps']]


def words(io, wanted, reserve):
    for made in records(wanted):
        io.say('The packet carries the store app %s %s (%d MiB; manifest %s). The core publishes it, and %s installs it after '
               'its other steps.' % (made['name'], made['version'], made['bytes'] >> 20, made['manifestSha256'][:12], ' and '.join(made['devices'])))
    carried = sum(app['bytes'] for app in wanted['apps'])
    io.say('The core that runs it needs %d MiB and %d MiB more free where it keeps its runs, and the backend of the core has to '
           'know each of these apps (its catalog entry): `isle store show <app>` says so.' % (carried >> 20, reserve >> 20))
