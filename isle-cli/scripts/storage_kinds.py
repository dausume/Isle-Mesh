#!/usr/bin/env python3
"""isle storage kind list | need list | association list, and what a machine lacks for a role

A role is only a purpose.  Three more things are named apart from it, each a short fixed list:

  the kind of storage    what serves the data, and so what the machine that holds it must have;
  the distribution need  how the data has to reach the devices that use it;
  the association        what the data belongs to.

ROLE says which of them each role allows.  No record and no marker holds any of this: they keep naming roles only.

Nothing in this file installs, starts or changes anything.  It looks whether a program is on the path and whether
Docker holds an image, and says what it found.
"""
import json
import subprocess

import storage_roles as sr

#: kind -> what serves it, and the ways a machine can have what it needs (any one way is enough; every part of a
#: way is needed).  A part is ('program', its name) or ('image', its repository, with any tag).
KINDS = {
    'plain-folder': dict(servedBy='a web server on a folder, or nothing', requires=()),
    'file-pool': dict(servedBy='one storage process of the file store (SeaweedFS) for each partition',
                      requires=((('program', 'weed'),), (('program', 'docker'), ('image', 'chrislusf/seaweedfs')))),
    'image-registry': dict(servedBy='a container registry with its data folder on the partition',
                           requires=((('program', 'docker'), ('image', 'registry')),)),
    'git': dict(servedBy='the forge (Forgejo)',
                requires=((('program', 'docker'), ('image', 'codeberg.org/forgejo/forgejo')),)),
}
#: distribution need -> (what it means, what a plan has to do for it)
NEEDS = {
    'kept': ('used only on the machine it sits on', 'nothing'),
    'served': ('one copy, and every device reads it by an isle name', 'give it an isle name'),
    'pooled': ('pieces on several machines behind one isle name', 'start a storage process for each partition'),
    'cached': ('the primary stays where it is; a device keeps a bounded copy while it uses it and drops it by itself',
               'mount it with a cache'),
    'installed': ('a copy must sit on every device that runs it', 'run an install on each device'),
    'carried': ('moved by hand on a disk or a stick', 'nothing; a person carries it'),
    'taken': ('a copy on one device that owes its edits back to its original; while it is out everyone still reads the '
              'original and nobody else changes it', 'reserve the original, and apply the edits when the copy comes back'),
}
ASSOCIATIONS = {'isle': 'shared by every device of the isle', 'app': 'one app (its file space)',
                'machine': 'one machine', 'release': 'one release (its packet)'}
#: role -> (kind, the distribution needs it allows, the associations it allows)
ROLE = {
    'package-repo': ('plain-folder', ('served',), ('isle',)),
    'distribution': ('plain-folder', ('served', 'carried'), ('isle', 'release')),
    'registry': ('image-registry', ('served', 'cached'), ('isle',)),
    'blob': ('file-pool', ('pooled',), ('isle', 'app')),
    'git': ('git', ('served',), ('isle',)),
    'assets': ('file-pool', ('pooled', 'cached', 'taken'), ('app', 'isle')),
    'backup': ('plain-folder', ('kept',), ('machine',)),
    'scratch': ('plain-folder', ('kept',), ('machine',)),
    'transfer': ('plain-folder', ('carried',), ('isle',)),
}
LISTS = {'kind': KINDS, 'need': NEEDS, 'association': ASSOCIATIONS}
READY, NOT_READY, NOT_KNOWN = 'ready', 'not ready', 'not known'
NO_ANSWER = 'Docker did not answer, so its images could not be looked at (it answers root and the users of its group).'


def of(role):
    """What a role allows: {kind, distribution, association}."""
    kind, needs, associations = ROLE[role]
    return dict(kind=kind, distribution=list(needs), association=list(associations))


def _part(part):
    return 'the program %s' % part[1] if part[0] == 'program' else 'an image of %s in Docker' % part[1]


def requires(kind):
    """In words, what a machine must have for a kind."""
    ways = KINDS[kind]['requires']
    return '; or '.join(' and '.join(_part(part) for part in way) for way in ways) if ways else 'nothing more'


def _has(io, part):
    """True, False, or None when it cannot be told."""
    if part[0] == 'program': return bool(io.which(part[1]))
    if not io.which('docker'): return False
    try: done = io.run(['docker', 'image', 'ls', '--format', '{{.Repository}}', part[1]], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError): return None
    if done.returncode != 0: return None
    return part[1] in (done.stdout or '').split()


def look(io, kinds=None):
    """What this machine has for each kind: {kind: {state, missing: [words], notKnown: [words]}}.  A kind is ready
    when one of its ways is there whole.  It is not known when no way is there whole and one may be."""
    out = {}
    for kind in kinds or KINDS:
        ways = [[(part, _has(io, part)) for part in way] for way in KINDS[kind]['requires']]
        if not ways or any(all(found for _, found in way) for way in ways):
            out[kind] = dict(state=READY, missing=[], notKnown=[]); continue
        parts = list(dict.fromkeys(pair for way in ways for pair in way))
        out[kind] = dict(state=NOT_KNOWN if any(all(found is not False for _, found in way) for way in ways) else NOT_READY,
                         missing=[_part(part) for part, found in parts if found is False],
                         notKnown=[_part(part) for part, found in parts if found is None])
    return out


def notice(io, roles):
    """The lines a verb adds after it gave a partition these roles: what this machine lacks to serve them.  Never an
    error: the roles are recorded all the same, and nothing is installed."""
    kinds = list(dict.fromkeys(ROLE[role][0] for role in roles))
    lines = []
    for kind, found in look(io, kinds).items():
        if found['state'] == READY: continue
        mine = sr._words([role for role in roles if ROLE[role][0] == kind])
        lines.append('%s storage of the kind %s. A machine that holds it needs: %s.' % (
            mine[0].upper() + mine[1:] + (' are' if mine.startswith('the roles') else ' is'), kind, requires(kind)))
        if found['missing']: lines.append('  This machine does not have: %s.' % '; '.join(found['missing']))
        if found['notKnown']: lines.append('  Not known: %s. %s' % ('; '.join(found['notKnown']), NO_ANSWER))
    if lines: lines.append('Nothing was installed. See: isle storage kind list --table')
    return lines


def _roles(index, name):
    return [role for role in sr.ROLES if (ROLE[role][0] == name if index == 0 else name in ROLE[role][index])]


def main(io, argv):
    plain = '--table' in argv
    words = [a for a in argv if a != '--table']
    if len(words) != 2 or words[0] not in LISTS or words[1] != 'list':
        io.say(sr.USAGE)
        return 2
    what = words[0]

    def answer(**result):
        io.say(json.dumps(dict(ok=True, evaluatedAt=io.now(), operation='storage.%s.list' % what, readOnly=True, result=result)))
        return 0
    if what == 'kind':
        found = look(io)
        rows = [dict(kind=kind, servedBy=entry['servedBy'], requires=requires(kind), roles=_roles(0, kind),
                     thisMachine=found[kind]['state'], missing=found[kind]['missing'], notKnown=found[kind]['notKnown'])
                for kind, entry in KINDS.items()]
        if not plain: return answer(kinds=rows)
        io.say(sr._table(('KIND', 'ROLES', 'THIS MACHINE', 'SERVED BY', 'A MACHINE MUST HAVE'),
                         [(r['kind'], ', '.join(r['roles']) or '-', r['thisMachine'], r['servedBy'], r['requires']) for r in rows]))
        for row in rows:
            if row['missing']: io.say('For %s this machine does not have: %s.' % (row['kind'], '; '.join(row['missing'])))
        if any(row['notKnown'] for row in rows): io.say(NO_ANSWER)
        return 0
    if what == 'need':
        rows = [dict(need=need, meaning=meaning, planDoes=does, roles=_roles(1, need)) for need, (meaning, does) in NEEDS.items()]
        if not plain: return answer(needs=rows)
        io.say(sr._table(('DISTRIBUTION NEED', 'ROLES', 'WHAT A PLAN DOES FOR IT', 'MEANING'),
                         [(r['need'], ', '.join(r['roles']) or '-', r['planDoes'], r['meaning']) for r in rows]))
        return 0
    rows = [dict(association=name, meaning=meaning, roles=_roles(2, name)) for name, meaning in ASSOCIATIONS.items()]
    if not plain: return answer(associations=rows)
    io.say(sr._table(('BELONGS TO', 'ROLES', 'MEANING'), [(r['association'], ', '.join(r['roles']) or '-', r['meaning']) for r in rows]))
    return 0
