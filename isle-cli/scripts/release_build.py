#!/usr/bin/env python3
"""isle release build: from one commit of the isle repository to a packet that `isle release run` takes.

  isle release build <out-dir> --previous <dir> [--source <checkout>] [--commit <ref>]
                     [--combined-builder <file>] [--device <name>]... [--verify <url>]... [--backup]

  <out-dir>            made by this command; it must not exist yet, or be empty
  --previous <dir>     the folder `members` of the build before this one.  Its packages give the next version
                       numbers, and the member packages a CLI release does not rebuild are taken from it
  --source <checkout>  a checkout of the isle repository (default: the one this file runs from)
  --commit <ref>       what to build (default: HEAD of that checkout).  The commit is built, not the files on
                       disk: a change that is not committed is not in the packet, and that is said
  --combined-builder   the script that merges the member packages into the one package a core installs
                       (default: build-polari-complete-deb.sh in the folder that holds the checkout)
  --device <name>      a device the packet names.  Without any, the packet says "every device": the core and
                       the members the core heard from in the last 24 hours, decided when the run starts
  --verify <url>       one more address that must answer 200 on every device after its steps
  --backup             every device takes a device-backup before it changes anything (the run stops on a
                       device whose backup is not complete)

It leaves in <out-dir>:
  packet/      release-run.json, SHA256SUMS, debs/ (what the release publishes)
  members/     every member package of this build: the --previous of the next one
  source/      the files of the commit, as they were built
  build.json   what was built from what: the commit, the versions, every checksum
  build.log    what the builders printed
Nothing is installed, published or sent, and nothing needs sudo.  A core is not asked anything.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import release_run as rr

VERSION = re.compile(r'(\d+)\.(\d+)\.(\d+)\+release(\d+)\Z')
CLI, STORE, COMBINED = 'isle-mesh-cli', 'isle-app-store', 'polari-complete-offline'
ROOT_CA = '/etc/isle-mesh/ca/isle-root.crt'
SHA1 = re.compile(r'[0-9a-f]{40}\Z')
SHARE = 'usr/share/isle-mesh'
# What build-cli-deb.sh packs, and what it leaves out.  Every other file of the commit below these folders is
# compared, byte for byte, between the commit and the built packages.
PACKED = ('isle-cli/', 'isle-agent/', 'mdns/', 'mesh-app-scaffolding/', 'openwrt-router/', 'polari-isle/')
LEFT_OUT = re.compile(r'(^|/)(node_modules|__pycache__|\.git)(/|$)|router-setup/images(/|$)|\.(pyc|backup|qcow2|img|img\.gz)$')
VERIFY = (rr.API + '/api/islemesh/updates', 'https://apt.isle/InRelease', rr.API + '/api/health')
BUILD_ENV = {'DPKG_DEB_THREADS_MAX': '1', 'DPKG_DEB_COMPRESSOR_TYPE': 'zstd', 'DPKG_DEB_COMPRESSOR_LEVEL': '3'}
USAGE = ('usage: isle release build <out-dir> --previous <dir> [--source <checkout>] [--commit <ref>] '
         '[--combined-builder <file>] [--device <name>]... [--verify <url>]... [--backup]')


class Refused(Exception): pass


class Io:
    """Everything that touches this machine; a test replaces it."""
    root_ca = ROOT_CA

    def say(self, line=''): print(line, flush=True)
    def is_root(self): return os.geteuid() == 0
    def now(self): return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    def run(self, argv, log=None, env=None, cwd=None):
        """(exit status, stdout).  With a log, both streams are appended to it and stdout is ''."""
        full = dict(os.environ, **(env or {}))
        if log is None:
            done = subprocess.run(argv, capture_output=True, text=True, env=full, cwd=cwd)
            return done.returncode, done.stdout
        with open(log, 'a') as out:
            out.write('\n$ %s\n' % ' '.join(str(a) for a in argv)); out.flush()
            return subprocess.run(argv, stdout=out, stderr=subprocess.STDOUT, env=full, cwd=cwd).returncode, ''


def next_version(version, what):
    m = VERSION.fullmatch(version)
    if not m: raise Refused('the previous %s package has the version %s, which is not <a>.<b>.<c>+release<n>; the next '
                            'version cannot be read from it' % (what, version))
    a, b, c, n = (int(g) for g in m.groups())
    return '%d.%d.%d+release%d' % (a, b, c + 1, n + 1), n + 1


def field(io, deb, name):
    status, out = io.run(['dpkg-deb', '-f', str(deb), name])
    if status != 0 or not out.strip(): raise Refused('%s cannot be read as a package' % deb)
    return out.strip()


def previous_members(io, directory):
    """{package name: file} of the build before this one.  One file per package, the two rebuilt ones among them."""
    if not directory.is_dir(): raise Refused('--previous %s is not a folder' % directory)
    found = {}
    for deb in sorted(directory.glob('*.deb')):
        name = field(io, deb, 'Package')
        if name.startswith('polari-complete'): continue          # the merged package is made again from its members
        if name in found: raise Refused('--previous %s holds two %s packages (%s and %s); it must hold one build'
                                        % (directory, name, found[name].name, deb.name))
        found[name] = deb
    for need in (CLI, STORE):
        if need not in found: raise Refused('--previous %s holds no %s package, so the next version is not known' % (directory, need))
    return found


def plan(io, out, previous, source, commit, builder, devices, verify, backup):
    """Everything that can refuse, before anything is written.  Returns what the build does."""
    if io.is_root():
        raise Refused('a build is run without sudo: it installs nothing, and what root builds the next build cannot write over')
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise Refused('%s exists and is not empty; a build never writes over another one' % out)
    members = previous_members(io, previous)
    cli, number = next_version(field(io, members[CLI], 'Version'), CLI)
    store, store_number = next_version(field(io, members[STORE], 'Version'), STORE)
    if number != store_number:
        raise Refused('the previous %s is of release %d and the previous %s of release %d; they are not one build'
                      % (CLI, number - 1, STORE, store_number - 1))
    status, sha = io.run(['git', '-C', str(source), 'rev-parse', '--verify', '--quiet', commit + '^{commit}'])
    if status != 0 or not SHA1.fullmatch(sha.strip()):
        raise Refused('%s is not a checkout of the isle repository that holds the commit %s (name one with --source)' % (source, commit))
    status, changed = io.run(['git', '-C', str(source), 'status', '--porcelain'])
    if not builder.is_file():
        raise Refused('the builder of the combined package is not at %s; name it: --combined-builder <the suite>/build-polari-complete-deb.sh' % builder)
    if not os.access(io.root_ca, os.R_OK):
        raise Refused('the root certificate of the isle (%s) cannot be read here. The store package carries it, so a '
                      'release is built on a machine of the isle it is for' % io.root_ca)
    for name in devices:
        if not rr.NAME.fullmatch(name): raise Refused('%r is not a device name' % name)
    steps = ([{'verb': 'backup'}] if backup else []) + [{'verb': 'update-apply', 'releaseId': rr.PUBLISHED}]
    doc = {'schemaVersion': 1, 'name': 'cli-release-%d' % number, 'publish': [{'kind': 'debs', 'dir': 'debs'}],
           'devices': {name: steps for name in (devices or ['*'])},
           'verify': [{'url': url, 'expectStatus': 200} for url in list(VERIFY) + [u for u in verify if u not in VERIFY]]}
    rr.validate_doc(doc)
    return {'number': number, 'cli': cli, 'store': store, 'commit': sha.strip(), 'members': members, 'doc': doc,
            'uncommitted': len([line for line in changed.splitlines() if line.strip()]) if status == 0 else None}


def tracked(io, source, commit):
    status, out = io.run(['git', '-C', str(source), 'ls-tree', '-r', '--name-only', commit])
    if status != 0: raise Refused('the files of the commit cannot be listed')
    return [name for name in out.splitlines() if name.startswith(PACKED) and not LEFT_OUT.search(name)]


def compare(io, out, deb, names, log):
    """Every compared file of the commit is in the package, byte for byte.  Returns the names that are not."""
    held = out / 'check'
    shutil.rmtree(held, ignore_errors=True)
    if io.run(['dpkg-deb', '-x', str(deb), str(held)], log=log)[0] != 0: raise Refused('%s cannot be unpacked (see %s)' % (deb.name, log))
    wrong = []
    for name in names:
        built, meant = held / SHARE / name, out / 'source' / name
        if meant.is_symlink():
            if not built.is_symlink() or os.readlink(built) != os.readlink(meant): wrong.append(name)
        elif built.is_symlink() or not built.is_file() or built.read_bytes() != meant.read_bytes(): wrong.append(name)
    shutil.rmtree(held, ignore_errors=True)
    return wrong


def build(io, out, source, builder, p):
    log = out / 'build.log'
    members, packet, src = out / 'members', out / 'packet', out / 'source'
    for folder in (members, packet / 'debs', src): folder.mkdir(parents=True)

    def step(words, argv, **more):
        io.say('  ' + words)
        if io.run(argv, log=log, **more)[0] != 0: raise Refused('%s failed; what it printed is in %s' % (words, log))

    step('the files of the commit', ['git', '-C', str(source), 'archive', '--format=tar', '-o', str(out / 'source.tar'), p['commit']])
    step('unpacked', ['tar', '-xf', str(out / 'source.tar'), '-C', str(src)])
    (out / 'source.tar').unlink(missing_ok=True)
    step('%s %s' % (CLI, p['cli']), ['bash', str(src / 'isle-cli/shells/build-cli-deb.sh'), '--version', p['cli'], '--output', str(members)], env=BUILD_ENV)
    step('%s %s' % (STORE, p['store']), ['bash', str(src / 'isle-cli/shells/tools/build-store-deb.sh'), '--ca', io.root_ca,
                                          '--version', p['store'], '--output', str(members)], env=BUILD_ENV)
    reused = {name: deb for name, deb in p['members'].items() if name not in (CLI, STORE)}
    for name, deb in sorted(reused.items()):
        io.say('  %s, as it was in the build before (%s)' % (name, deb.name))
        shutil.copy2(deb, members / deb.name)
    step('the combined package', ['bash', str(builder), '--debs-dir', str(members), '--flavor', 'offline'], env=BUILD_ENV)

    built = {}
    for deb in sorted(members.glob('*.deb')): built.setdefault(field(io, deb, 'Package'), []).append(deb)
    for name, want in ((CLI, p['cli']), (STORE, p['store']), (COMBINED, p['store'])):
        if len(built.get(name, [])) != 1: raise Refused('the build left %d %s packages, not one' % (len(built.get(name, [])), name))
        got = field(io, built[name][0], 'Version')
        if got != want: raise Refused('%s was built as %s, not as %s' % (name, got, want))
    names = tracked(io, source, p['commit'])
    for name in (CLI, COMBINED):
        wrong = compare(io, out, built[name][0], names, log)
        if wrong: raise Refused('%s does not hold the commit: %d of %d files differ or are missing (first: %s)'
                                % (built[name][0].name, len(wrong), len(names), wrong[0]))
    for name in (CLI, STORE, COMBINED): shutil.copy2(built[name][0], packet / 'debs' / built[name][0].name)
    (packet / 'release-run.json').write_text(json.dumps(p['doc'], indent=1) + '\n')
    listed = ['release-run.json'] + sorted('debs/' + deb.name for deb in (packet / 'debs').iterdir())
    (packet / 'SHA256SUMS').write_text(''.join('%s  %s\n' % (rr.sha256_file(packet / rel), rel) for rel in listed))
    pk = rr.load_packet(packet)
    record = {'schemaVersion': 1, 'release': p['number'], 'runId': pk.run_id, 'builtAt': io.now(),
              'source': {'commit': p['commit'], 'uncommittedFilesLeftOut': p['uncommitted'], 'comparedFiles': len(names)},
              'combinedBuilder': {'file': builder.name, 'sha256': rr.sha256_file(builder)},
              'packages': [{'name': name, 'version': field(io, deb, 'Version'), 'file': deb.name, 'sha256': rr.sha256_file(deb),
                            'reused': name in reused, 'published': name in (CLI, STORE, COMBINED)}
                           for name, debs in sorted(built.items()) for deb in debs]}
    (out / 'build.json').write_text(json.dumps(record, indent=1) + '\n')
    return pk, len(names)


def parse(argv):
    one = {'--previous': None, '--source': None, '--commit': 'HEAD', '--combined-builder': None}
    many = {'--device': [], '--verify': []}
    flags, rest, i = set(), [], 0
    while i < len(argv):
        word = argv[i]
        if word == '--backup': flags.add(word)
        elif word in one or word in many:
            if i + 1 >= len(argv): raise ValueError('%s needs a value' % word)
            i += 1
            if word in one: one[word] = argv[i]
            else: many[word].append(argv[i])
        elif word.startswith('-'): raise ValueError('unknown option %s' % word)
        else: rest.append(word)
        i += 1
    if len(rest) != 1 or not one['--previous']: raise ValueError('one <out-dir> and --previous <dir> are needed')
    return rest[0], one, many, flags


def main(argv, io=None):
    io = io or Io()
    if argv and argv[0] in ('-h', '--help', 'help'): io.say(__doc__); return 0
    try: target, one, many, flags = parse(argv)
    except ValueError as exc:
        io.say('release build: %s' % exc); io.say(USAGE); return 2
    out, started = Path(os.path.abspath(target)), False
    source = Path(os.path.abspath(one['--source'])) if one['--source'] else SCRIPTS.parent.parent
    builder = Path(os.path.abspath(one['--combined-builder'])) if one['--combined-builder'] else source.parent / 'build-polari-complete-deb.sh'
    try:
        p = plan(io, out, Path(os.path.abspath(one['--previous'])), source, one['--commit'], builder,
                 many['--device'], many['--verify'], '--backup' in flags)
        io.say('Release %d from the commit %s:' % (p['number'], p['commit'][:12]))
        if p['uncommitted']:
            io.say('  NOT in this build: %d changed or new files in the checkout that are not committed.' % p['uncommitted'])
        out.mkdir(parents=True, exist_ok=True)
        started = True
        pk, compared = build(io, out, source, builder, p)
    except (Refused, rr.PacketError) as exc:
        io.say('REFUSED: release build: %s' % exc)
        if started: io.say('What was made before that is left in %s to be read. It is not a packet; build again into another folder.' % out)
        return 1
    except OSError as exc:
        io.say('release build: %s' % exc); return 1
    io.say('Built. The %d files the CLI package packs are the same, byte for byte, in the commit, in %s and in %s.' % (compared, CLI, COMBINED))
    io.say('Packet: %s' % (out / 'packet'))
    for line in rr.summary_lines(pk.record, pk.run_id, pk.publications): io.say('  ' + line)
    if not many['--device']:
        io.say('The packet names no device: it is for the core and every member the core heard from in the last 24 hours.')
    if '--backup' not in flags:
        io.say('No device takes a device-backup in this run (build with --backup to have each one take it first).')
    io.say('It is built only: nothing is published or installed. To run it, on the core:')
    io.say('  sudo isle release run <the folder packet, copied there> --expect %s' % pk.run_id)
    io.say('The next build: isle release build <out-dir> --previous %s' % (out / 'members'))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
