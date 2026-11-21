#!/usr/bin/env python3
"""isle os dependencies build | check: the operating system packages one system needs offline (a dependency set).

  isle os dependencies build <out-dir> --system <system id> --packages <dir> [--bundle-builder <file>] [--cache <dir>]
        no sudo: resolve and fetch the set of one supported system (isle os list --table) into <out-dir>.
        Nothing is published and nothing on the isle changes.
  isle os dependencies check <set dir>
        read a set: its one system, its files against its own lists.  Changes nothing.

What a set is resolved from: what the isle's own packages in --packages declare (the folder `members` of a release
build), and the offline dependency list (apt/offline-packages.json), with the names that system uses.  It is resolved
in a fresh container of that system's image, so nothing installed on this machine decides it.

  --bundle-builder   the suite's build-offline-bundle.sh, which resolves, fetches and checks every file against the
                     signed package index of the system's archive.  Default: beside the checkout this command runs from
  --cache            a folder of package files kept between builds.  A file there is used only when its size and
                     sha256 are the ones the signed index names; what is fetched now is added to it

The set is the folder `isle release build <out-dir> --dependencies <set dir>` takes: the package files, the three
CLOSURE_* lists of the resolve, <system id>.txt and SHA256SUMS.  A build needs docker and the internet (or a cache
that holds every file), and fetches about 500 MiB for one system.
"""
import json
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_build as rb
import release_run as rr
import supported_os as so

Refused = rb.Refused


class Usage(Exception): pass


LISTS = ('CLOSURE_APT_RECORDS.txt', 'CLOSURE_METADATA.json', 'CLOSURE_URIS.txt')
WORK_MARK = 'isle-dependency-set-build'
USAGE = ('usage: isle os dependencies build <out-dir> --system <system id> --packages <dir> [--bundle-builder <file>] [--cache <dir>]\n'
         '       isle os dependencies check <set dir>')


def inventory(folder, where):
    """The resolve's own list of a set: [{file, size, sha256}], as the signed index named them."""
    try:
        doc = json.loads((folder / 'CLOSURE_METADATA.json').read_text())
        rows = doc['packages']
        assert rows and all(isinstance(r['file'], str) and r['file'].endswith('.deb') and '/' not in r['file']
                            and isinstance(r['size'], int) and isinstance(r['sha256'], str) and rr.HEX64.match(r['sha256']) for r in rows)
        assert len({r['file'] for r in rows}) == len(rows)
    except (OSError, ValueError, KeyError, TypeError, AssertionError):
        raise Refused('%s: CLOSURE_METADATA.json is not the list of a resolve (every package file with the size and sha256 '
                      'the signed index names)' % where)
    return rows


def against_inventory(folder, where, names):
    """Every package file of the set is one the signed index named, and none it named is missing."""
    rows = inventory(folder, where)
    listed = {r['file'] for r in rows}
    for name in sorted(n for n in names if n.endswith('.deb') and n not in listed):
        raise Refused('%s holds %s, which the signed index of the resolve does not name' % (where, name))
    for r in rows:
        path = folder / r['file']
        if r['file'] not in names: raise Refused('%s: %s is missing; the resolve names it' % (where, r['file']))
        if os.lstat(path).st_size != r['size'] or rr.sha256_file(path) != r['sha256']:
            raise Refused('%s: %s is not the file the signed index names (size or sha256 differs)' % (where, r['file']))
    return rows


def checked(io, folder, where):
    """A whole set, read: the rules a packet asks of it, and its package files against the resolve's list."""
    chosen = rb.chosen_set(io, folder, where)
    for name in LISTS + ('SHA256SUMS',):
        if name not in chosen['names']: raise Refused('%s has no %s; a built set carries it' % (where, name))
    against_inventory(folder, where, chosen['names'])
    return chosen


def plan(io, out, system, packages, builder, cache):
    """Everything a build refuses, before it writes or fetches anything.  -> what the build needs."""
    if io.is_root():
        raise Refused('a build is run without sudo: it installs nothing, and what root builds the next build cannot write over')
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise Refused('%s exists and is not empty; a build never writes over another one' % out)
    work = out.with_name(out.name + '.work')
    if work.exists() and not (work / WORK_MARK).is_file():
        raise Refused('%s exists and is not the work folder of a build; the build would write there' % work)
    try:
        entry = so.system_of(so.load(), system)
        roots = so.offline_packages(entry, 'install') + ',' + so.offline_packages(entry, 'cache-only')
    except ValueError as exc: raise Refused(str(exc))
    if not packages.is_dir(): raise Refused('--packages %s is not a folder' % packages)
    ours = sorted(packages.glob('*.deb'))
    if not ours: raise Refused('--packages %s holds no .deb file; it is the folder of the isle\'s own packages (members of a release build)' % packages)
    for deb in ours:
        if not rb.FIRST_PARTY.match(rb.field(io, deb, 'Package')):
            raise Refused('--packages %s holds %s, which is not a package of the isle. Only what the signed index names '
                          'may enter a set' % (packages, deb.name))
    if not builder.is_file():
        raise Refused('the builder of an offline bundle is not at %s; name it: --bundle-builder <the suite>/build-offline-bundle.sh' % builder)
    if cache is not None and cache.exists() and not cache.is_dir(): raise Refused('--cache %s is not a folder' % cache)
    return {'entry': entry, 'roots': roots, 'ours': len(ours), 'work': work}


def build(io, out, system, packages, builder, cache, p):
    work, log = p['work'], p['work'] / 'bundle.log'
    if work.exists():
        io.say('Removing the work folder a build that stopped left: %s' % work)
        shutil.rmtree(work)
    work.mkdir(parents=True)
    (work / WORK_MARK).write_text('the work folder of isle os dependencies build; the next build removes it\n')
    io.say('The dependency set of %s (release name %s), resolved in a fresh %s:' % (system, p['entry']['codename'], p['entry']['baseImage']))
    io.say('  resolving and fetching (the log: %s)' % log)
    status, _ = io.run(['bash', str(builder), '--debs-dir', str(packages), '--target', system, '--media', 'usb16', '--out', str(work / 'bundle'),
                        '--closure', p['roots'], '--download'], log=log, env={'POLARI_APT_CACHE': str(cache) if cache else ''})
    if status != 0:
        last = [line for line in log.read_text(errors='replace').splitlines() if line.strip()][-5:] if log.is_file() else []
        raise Refused('the builder stopped (exit %d). Its last lines:\n%s\nThe whole log: %s. Nothing was written to %s'
                      % (status, '\n'.join('    ' + line for line in last), log, out))
    repo, made = work / 'bundle' / 'pool' / 'repo', work / 'set'
    where = 'the bundle the builder made (%s)' % repo
    for name in LISTS:
        if not (repo / name).is_file(): raise Refused('%s has no %s' % (where, name))
    rows = inventory(repo, where)
    made.mkdir()
    for r in rows:
        if not (repo / r['file']).is_file(): raise Refused('%s lacks %s, which the resolve names; was it built without the fetch?' % (where, r['file']))
        os.replace(repo / r['file'], made / r['file'])
    for name in LISTS: shutil.copyfile(repo / name, made / name)
    (made / (system + '.txt')).write_text(system + '\n')
    names = sorted(os.listdir(made))
    against_inventory(made, 'the set', names)        # every file read again, after the move
    (made / 'SHA256SUMS').write_text(''.join('%s  %s\n' % (r['sha256'], r['file']) for r in sorted(rows, key=lambda r: r['file'])))
    chosen = checked(io, made, 'the set')
    if out.exists(): out.rmdir()
    os.replace(made, out)
    report = next((line for line in reversed(log.read_text(errors='replace').splitlines()) if line.startswith('[cache-report] apt ')), '')
    shutil.rmtree(work)
    return dict(chosen, folder=out), dict(part.split('=', 1) for part in report.split()[2:] if '=' in part)


def mib(count): return '%d MiB' % (int(count) >> 20)


def said(io, chosen):
    io.say('The dependency set of %s (release name %s): %d package files, %s, in %s.'
           % (chosen['system'], chosen['release'], chosen['packages'], mib(chosen['bytes']), chosen['folder']))
    io.say('Every package file has the size and sha256 the signed package index named when the set was resolved, and is the '
           'file its SHA256SUMS lists. No package of the isle is in it.')


def parse(argv):
    one, out, i = {'--system': None, '--packages': None, '--bundle-builder': None, '--cache': None}, None, 0
    while i < len(argv):
        word = argv[i]
        if word in one:
            if i + 1 >= len(argv) or one[word] is not None: raise Usage()
            one[word] = argv[i + 1]; i += 2
        elif word.startswith('-') or out is not None: raise Usage()
        else: out = word; i += 1
    if out is None or one['--system'] is None or one['--packages'] is None: raise Usage()
    return out, one


def main(argv, io=None):
    io = io or rb.Io()
    if not argv or argv[0] in ('help', '--help', '-h'):
        io.say(__doc__.strip()); return 0 if argv else 2
    try:
        if argv[0] == 'check':
            if len(argv) != 2: raise Usage()
            folder = Path(os.path.abspath(argv[1]))
            said(io, checked(io, folder, str(folder)))
            io.say('Nothing was changed.')
            return 0
        if argv[0] != 'build': raise Usage()
        out, one = parse(argv[1:])
        out, packages = Path(os.path.abspath(out)), Path(os.path.abspath(one['--packages']))
        builder = (Path(os.path.abspath(one['--bundle-builder'])) if one['--bundle-builder']
                   else Path(__file__).resolve().parents[3] / 'build-offline-bundle.sh')
        cache = Path(os.path.abspath(one['--cache'])) if one['--cache'] else None
        p = plan(io, out, one['--system'], packages, builder, cache)
        chosen, fetched = build(io, out, one['--system'], packages, builder, cache, p)
    except Usage:
        io.say(USAGE); return 2
    except Refused as exc:
        io.say('REFUSED: %s' % exc); return 1
    said(io, chosen)
    if fetched.get('fetched_bytes', '').isdecimal() and fetched.get('cached_bytes', '').isdecimal():
        io.say('Fetched now: %s. Taken from the cache: %s.' % (mib(fetched['fetched_bytes']), mib(fetched['cached_bytes'])))
    io.say('It was resolved from what the %d package(s) of the isle in %s declare and from the offline dependency list.' % (p['ours'], packages))
    io.say('It is built only: nothing is published. To put it in a packet:')
    io.say('  isle release build <out-dir> --dependencies %s' % out)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
