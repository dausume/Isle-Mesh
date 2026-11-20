#!/usr/bin/env python3
"""Split one scanned package index into the two indexes the isle repository serves.

  main index   isle-* and polari-* packages only: what a device's own apt reads (the isle source line).
  deps index   every other package: the operating system packages of the offline dependency set. They are
               built for ONE Ubuntu release, so they are served apart (deps/<release name>/) and only the
               isle installer (isle apt install) reads them, on a device of that same release.

The rule for "isle or polari package" is the name, and it is the same rule the apt pin isle-mesh.pref uses
(Package: isle-* polari-*). Both files are always written; either may be empty.

  apt_index_split.py <scanned Packages> <main Packages out> <deps Packages out>     prints "<main count> <deps count>"
"""
import re, sys

FIRST_PARTY = re.compile(r'(?:isle|polari)-[a-z0-9+.-]+\Z')


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


if __name__ == '__main__':
    if len(sys.argv) != 4: sys.exit(__doc__)
    try:
        with open(sys.argv[1], encoding='utf-8', errors='surrogateescape') as source:
            main_text, deps_text, main_count, deps_count = split(source.read())
        for path, text in ((sys.argv[2], main_text), (sys.argv[3], deps_text)):
            with open(path, 'w', encoding='utf-8', errors='surrogateescape') as out: out.write(text)
    except (OSError, ValueError) as error:
        sys.exit('index split failed: %s' % error)
    print(main_count, deps_count)
