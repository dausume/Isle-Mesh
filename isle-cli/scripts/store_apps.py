#!/usr/bin/env python3
"""A store app in a release packet: `isle release build --store-app <device>:<folder>`.

  <device>  the device that installs the app after its other steps (`isle store install <app>`)
  <folder>  what `isle store build` left: release.json with the real image names and digests, the image
            archives, the compose file and the files the release names

Where the packet holds it: ONE store app lies at the root of the packet (release.json and its files beside it), which
every runner since release 92 reads.  SEVERAL lie in catalog/<app>/ each, which the core that runs the packet reads
from release 93 on (`isle release check` on an older core refuses the packet as a whole).  The core publishes it
as a step of the run (`isle store publish`), after the packages; the device waits until the core serves that very
release (the sha256 of the manifest as the core stores it, worked out here the way `isle store build` prints it),
installs it, and the run stops on that device when the app is not on that release afterwards.

One app may go to several devices (the option once per device, the same folder): it is published once.

Refused, before anything is written: a folder that is not a built release (a definition still carries
PIN-AT-BUILD), a file the release names that is not there, two folders for one app, one app twice on one device, a
device the packet does not name, a single app with a file where the packet keeps its own (debs/, runtime/, ...).

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
#: what a packet keeps at its root for itself; a store app that lies there may not name a file among these
OWN = ('release-run.json', 'SHA256SUMS', 'debs', 'runtime', 'dependencies', DIR)


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
    wanted = {'apps': [apps[name] for name in sorted(apps)], 'steps': steps}
    if len(wanted['apps']) == 1:
        app = wanted['apps'][0]
        for source in app['sources']:
            if Path(source).parts[0] in OWN:
                raise Refused('--store-app %s: the one store app of a packet lies at its root, and its file %s is where the packet '
                              'keeps its own (%s)' % (app['folder'], source, ', '.join(OWN)))
    return wanted


def prefix(wanted, app):
    """Where the packet holds the app: '' (its root) for the only one, catalog/<app>/ for one of several."""
    return '' if len(wanted['apps']) == 1 else '%s/%s/' % (DIR, app['name'])


def publish(wanted):
    return [{'kind': 'catalog', 'release': prefix(wanted, app) + 'release.json'} for app in wanted['apps']]


def place(packet, wanted):
    """The folders into the packet (prefix).  A large archive is linked when the packet lies on the same disk, and
    copied when it does not."""
    for app in wanted['apps']:
        for name in ['release.json'] + app['sources']:
            target = packet / (prefix(wanted, app) + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            try: os.link(app['folder'] / name, target)
            except OSError: shutil.copy2(app['folder'] / name, target)


def listed(wanted):
    """The files `place` put into the packet, as SHA256SUMS names them."""
    return [prefix(wanted, app) + name for app in wanted['apps'] for name in ['release.json'] + app['sources']]


def records(wanted):
    return [{'name': app['name'], 'version': app['version'], 'manifestSha256': app['manifestSha256'], 'bytes': app['bytes'],
             'devices': sorted(device for device, steps in wanted['steps'].items() if any(step['app'] == app['name'] for step in steps))}
            for app in wanted['apps']]


def words(io, wanted, reserve):
    for made in records(wanted):
        io.say('The packet carries the store app %s %s (%d MiB; manifest %s). The core publishes it, and %s installs it after '
               'its other steps.' % (made['name'], made['version'], made['bytes'] >> 20, made['manifestSha256'][:12], ' and '.join(made['devices'])))
    if len(wanted['apps']) == 1:
        io.say('The store app lies at the root of the packet, which a core on release 92 reads as well.')
    else:
        io.say('Each store app lies in a folder of its own (%s/<app>/): the core that runs this packet needs the CLI of release 93 '
               'or later, and `isle release check <packet>` there says so.' % DIR)
    carried = sum(app['bytes'] for app in wanted['apps'])
    io.say('The core that runs it needs %d MiB and %d MiB more free where it keeps its runs, and the backend of the core has to '
           'know each of these apps (its catalog entry): `isle store show <app>` says so.' % (carried >> 20, reserve >> 20))
