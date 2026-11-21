#!/usr/bin/env python3
"""Split one scanned package index into the indexes the isle repository serves.

  main index   isle-* and polari-* packages only: what a device's own apt reads (the isle source line).
  deps indexes every other package: the operating system packages of the offline dependency sets. A set is
               built for ONE Ubuntu release, so each release has its own index (deps/<release name>/) and only
               the isle installer (isle apt install) reads it, on a device of that same release.

The rule for "isle or polari package" is the name, and it is the same rule the apt pin isle-mesh.pref uses
(Package: isle-* polari-*).

Which release an operating system package belongs to is not in the package, so it is kept by where the package
was filed: a package file stays in every deps/<release>/ index that already lists it (--served), the files of a
newly given set go to the release it was built for (--add), and a file that no set names goes to this core's own
release (the rule before there were sets per release). The same file may be in more than one set.

  apt_index_split.py <scanned Packages> <out dir> <own release> [--served <repository dir>] [--add <release> <dir>]
      writes <out dir>/main/Packages and <out dir>/deps/<release>/Packages (one per release that has packages);
      prints "main <count>" and then one line "deps <release> <count>" per release, in name order
"""
import os, re, sys

FIRST_PARTY = re.compile(r'(?:isle|polari)-[a-z0-9+.-]+\Z')
RELEASE = re.compile(r'[a-z][a-z0-9]*\Z')


def split(text):
    """-> (main text, deps text, main count, deps count). Stanzas keep their order and their bytes."""
    main, deps = [], []
    for block in text.split('\n\n'):
        block = block.strip('\n')
        if not block: continue
        names = [line.split(':', 1)[1].strip() for line in block.split('\n') if line.startswith('Package:')]
        if len(names) != 1: raise ValueError('index stanza without exactly one Package field')
        (main if FIRST_PARTY.match(names[0]) else deps).append(block + '\n\n')
    return ''.join(main), ''.join(deps), len(main), len(deps)


def files_of(text):
    """The package file names an index lists (the last part of every Filename field)."""
    return {line.split(':', 1)[1].strip().rsplit('/', 1)[-1] for line in text.split('\n') if line.startswith('Filename:')}


def by_release(deps_text, members, own):
    """The dependency stanzas -> {release: [stanza, ...]}. members = {package file name: set of releases}; a file in no set
    belongs to `own`. Stanzas keep their order and their bytes."""
    out = {}
    for block in deps_text.split('\n\n'):
        block = block.strip('\n')
        if not block: continue
        names = files_of(block)
        if len(names) != 1: raise ValueError('index stanza without exactly one Filename field')
        for release in sorted(members.get(names.pop()) or {own}):
            out.setdefault(release, []).append(block + '\n\n')
    return out


def served_members(repository):
    """What the repository's own deps/<release>/Packages files list today -> {package file name: set of releases}."""
    members, root = {}, os.path.join(repository, 'deps')
    for release in sorted(os.listdir(root)) if os.path.isdir(root) else ():
        index = os.path.join(root, release, 'Packages')
        if not RELEASE.match(release) or os.path.islink(os.path.join(root, release)) or not os.path.isfile(index): continue
        with open(index, encoding='utf-8', errors='surrogateescape') as source:
            for name in files_of(source.read()): members.setdefault(name, set()).add(release)
    return members


def command(argv):
    if len(argv) < 3: raise ValueError(__doc__)
    scanned, out, own = argv[:3]
    rest, members = argv[3:], {}
    if not RELEASE.match(own): raise ValueError('not a release name: %r' % own)
    while rest:
        if rest[0] == '--served' and len(rest) >= 2:
            for name, releases in served_members(rest[1]).items(): members.setdefault(name, set()).update(releases)
            rest = rest[2:]
        elif rest[0] == '--add' and len(rest) >= 3:
            if not RELEASE.match(rest[1]): raise ValueError('not a release name: %r' % rest[1])
            for name in os.listdir(rest[2]):
                if name.endswith('.deb'): members.setdefault(name, set()).add(rest[1])
            rest = rest[3:]
        else:
            raise ValueError(__doc__)
    with open(scanned, encoding='utf-8', errors='surrogateescape') as source:
        main_text, deps_text, main_count, _ = split(source.read())
    sets = by_release(deps_text, members, own)
    for directory, text in [(os.path.join(out, 'main'), main_text)] + [(os.path.join(out, 'deps', r), ''.join(b)) for r, b in sets.items()]:
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, 'Packages'), 'w', encoding='utf-8', errors='surrogateescape') as target: target.write(text)
    return ['main %d' % main_count] + ['deps %s %d' % (r, len(sets[r])) for r in sorted(sets)]


if __name__ == '__main__':
    try: lines = command(sys.argv[1:])
    except (OSError, ValueError) as error: sys.exit('index split failed: %s' % error)
    print('\n'.join(lines))
