#!/usr/bin/env python3
"""isle os list | check | devices: the operating systems the isle is run on, and this machine's place among them.

  isle os list [--table]       the record: every supported system, how far it is proven as a core and as a member,
                               and what differs between them for packages
  isle os check [--table]      this machine: its system, whether the record has it, and whether the core serves
                               a dependency set for its release.  Exit 0 = in the record, 1 = not
  isle os devices [--table]    what every machine of the isle last reported as its system (from the core's device list)

The record is the file apt/supported-os.json of this CLI.  It is data, not code: a system is added by adding its
entry there, after it has been proven.  `isle apt-repo publish` reads the same record to decide under which release
name a dependency set is filed (dependency_release below).  Every verb here only reads.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

RECORD = Path(__file__).resolve().parent.parent / 'apt' / 'supported-os.json'
OS_RELEASE = '/etc/os-release'
MODE_FILE = '/etc/isle-mesh/agent/agent.mode'
REPO = '/etc/isle-mesh/apt/repo'
CA_FILE = '/etc/isle-mesh/ca/isle-root.crt'
SYSTEM = re.compile(r'[a-z][a-z0-9]*-[0-9]+(?:\.[0-9]+)*\Z')
CODENAME = re.compile(r'[a-z][a-z0-9]*\Z')
MARKER = re.compile(r'([a-z][a-z0-9]*-[0-9]+(?:\.[0-9]+)*)\.txt\Z')
PROOFS = ('live-verified', 'source-tested', 'not proven')
FACTS = ('host.os_release', 'host.os_codename')
USAGE = 'usage: isle os list [--table] | check [--table] | devices [--table]'
ROLES = {'core': 'core', 'remote': 'member'}


def load(path=RECORD):
    """The record -> its systems.  ValueError (in plain words) when the file is not a record this version reads."""
    try: doc = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc: raise ValueError('The record of supported operating systems cannot be read: %s' % exc)
    try:
        systems = doc['systems']
        assert doc['schemaVersion'] == 1 and isinstance(systems, list) and systems
        for s in systems:
            assert SYSTEM.match(s['id']) and s['id'] == '%s-%s' % (s['distribution'], s['version']) and CODENAME.match(s['codename'])
            assert isinstance(s['architectures'], list) and s['architectures'] and all(isinstance(a, str) and CODENAME.match(a) for a in s['architectures'])
            assert s['core'] in PROOFS and s['member'] in PROOFS
            assert all(isinstance(s[k], str) and s[k] for k in ('baseImage', 'apt', 'python3'))
            assert isinstance(s['packageNames'], dict) and all(isinstance(k, str) and isinstance(v, str) and k and v for k, v in s['packageNames'].items())
            assert all(isinstance(s[k], list) and all(isinstance(line, str) for line in s[k]) for k in ('variations', 'evidence'))
        for key in ('id', 'codename'):
            assert len({s[key] for s in systems}) == len(systems)
    except (AssertionError, KeyError, TypeError):
        raise ValueError('The record of supported operating systems (%s) is not one this version of the isle CLI reads.' % path)
    return systems


def find(systems, **want):
    (key, value), = want.items()
    return next((s for s in systems if s[key] == value), None)


def read_os_release(path=OS_RELEASE):
    """-> (system id like ubuntu-22.04 or None, release name like jammy or None).  Never raises."""
    values = {}
    try:
        for line in Path(path).read_text().splitlines():
            key, mark, value = line.partition('=')
            if not mark or not re.fullmatch(r'[A-Z_][A-Z0-9_]*', key): continue
            try: words = shlex.split(value)
            except ValueError: continue
            values[key] = words[0] if words else ''
    except (OSError, UnicodeDecodeError):
        return None, None
    system = '%s-%s' % (values.get('ID', ''), values.get('VERSION_ID', ''))
    name = values.get('UBUNTU_CODENAME') or values.get('VERSION_CODENAME') or ''
    return system if SYSTEM.match(system) else None, name if CODENAME.match(name) else None


def dependency_release(directory, stated, own, systems):
    """Under which release name a dependency set is filed -> (release name, how it was decided).

    The set says it itself when it comes from an offline medium (the file <system id>.txt beside the packages);
    `stated` is what the operator typed (--dependencies-release); with neither it is this core's own release, as
    before.  A stated or marked release has to be in the record: a mistyped name would make a set nobody reads."""
    marks = sorted(m.group(1) for m in (MARKER.match(p.name) for p in Path(directory).iterdir()) if m)
    if len(marks) > 1:
        raise ValueError('The dependency directory says it is built for more than one system (%s).' % ', '.join(marks))
    marked = None
    if marks:
        system = find(systems, id=marks[0])
        if system is None:
            raise ValueError('The dependency directory is built for %s, which is not in the record of supported operating systems.' % marks[0])
        marked = system['codename']
    if stated:
        if not CODENAME.match(stated) or find(systems, codename=stated) is None:
            raise ValueError('%s is not a release name in the record of supported operating systems (%s).'
                             % (stated, ', '.join(s['codename'] for s in systems)))
        if marked and marked != stated:
            raise ValueError('The dependency directory is built for %s (%s), not for %s.' % (marks[0], marked, stated))
        return stated, 'as typed'
    if marked: return marked, 'the directory is marked %s' % marks[0]
    if not own or not CODENAME.match(own): raise ValueError('The release name of this core cannot be read.')
    return own, "this core's own release: the directory does not say what it is built for"


class Io:
    """Everything that touches this machine; a test replaces it."""
    record, os_release, mode_file, repo = RECORD, OS_RELEASE, MODE_FILE, REPO
    run = staticmethod(subprocess.run)

    def now(self): return datetime.now(timezone.utc).isoformat()
    def say(self, line=''): print(line)

    def architecture(self):
        try: done = self.run(['dpkg', '--print-architecture'], capture_output=True, text=True, timeout=8)
        except (OSError, subprocess.SubprocessError): return None
        name = done.stdout.strip()
        return name if done.returncode == 0 and CODENAME.match(name) else None

    def role(self):
        try: return ROLES.get(Path(self.mode_file).read_text().strip())
        except OSError: return None

    def set_served(self, role, name):
        """Whether the core serves a dependency set for this release name: 'served', 'not served' or 'not asked'."""
        if role == 'core':
            return 'served' if os.path.isfile(os.path.join(self.repo, 'deps', name, 'InRelease')) else 'not served'
        if role != 'member': return 'not asked'
        try:      # the same question apt-install.sh asks, over TLS verified against the isle root
            done = self.run(['curl', '--http1.1', '--silent', '--output', '/dev/null', '--max-time', '8', '--cacert', CA_FILE,
                             '--write-out', '%{http_code}', 'https://apt.isle/deps/%s/InRelease' % name], capture_output=True, text=True, timeout=12)
        except (OSError, subprocess.SubprocessError): return 'not asked'
        return {'200': 'served', '404': 'not served'}.get(done.stdout.strip(), 'not asked')


def check(io, systems):
    system, name = read_os_release(io.os_release)
    arch, role = io.architecture(), io.role()
    entry = find(systems, id=system) if system else None
    base = find(systems, codename=name) if name and entry is None else None
    return dict(system=system, codename=name, architecture=arch, role=role,
                inRecord=bool(entry and arch in entry['architectures']),
                proof=entry[role] if entry and role else None, basedOn=base['id'] if base else None,
                dependencySet=io.set_served(role, name) if name else 'not asked')


def devices(listing, systems):
    """The core's device list -> one row per machine in name order.  Another machine wrote each value: it is checked."""
    import storage_isle
    rows = []
    for d in listing:
        name = d.get('name')
        if d.get('is_mock') or not isinstance(name, str) or not storage_isle.DEVICE.match(name): continue
        evidence = d.get('capability_evidence')
        facts = evidence.get('facts') if isinstance(evidence, dict) else None
        read = []
        for key, pattern in zip(FACTS, (SYSTEM, CODENAME)):
            fact = facts.get(key) if isinstance(facts, dict) else None
            value = fact.get('value') if isinstance(fact, dict) and fact.get('status') == 'observed' else None
            read.append(value if isinstance(value, str) and pattern.match(value) else None)
        rows.append(dict(device=name, system=read[0], codename=read[1], inRecord=bool(read[0] and find(systems, id=read[0]))))
    return sorted(rows, key=lambda r: r['device'])


def _table(head, body):
    widths = [max(len(str(line[i])) for line in [head] + body) for i in range(len(head))]
    return '\n'.join('  '.join(str(cell).ljust(widths[i]) for i, cell in enumerate(line)).rstrip() for line in [head] + body)


def main(io, argv):
    plain = '--table' in argv
    words = [a for a in argv if a != '--table']
    if words not in (['list'], ['check'], ['devices']):
        io.say(USAGE)
        return 2
    verb = words[0]

    def answer(ok, **body):
        io.say(json.dumps(dict(ok=ok, evaluatedAt=io.now(), operation='os.' + verb, readOnly=True, **body)))
        return 0 if ok else 1

    def refuse(text):
        if plain: io.say(text); return 1
        return answer(False, error=text)
    try: systems = load(io.record)
    except ValueError as exc: return refuse(str(exc))
    if verb == 'list':
        if not plain: return answer(True, result=dict(systems=systems))
        io.say(_table(('SYSTEM', 'RELEASE NAME', 'ARCHITECTURES', 'AS A CORE', 'AS A MEMBER', 'APT', 'PYTHON'),
                      [(s['id'], s['codename'], ', '.join(s['architectures']), s['core'], s['member'], s['apt'], s['python3']) for s in systems]))
        for s in systems:
            io.say()
            io.say('%s (%s):' % (s['id'], s['codename']))
            for line in s['variations']: io.say('  - ' + line)
            for old, new in sorted(s['packageNames'].items()): io.say('  - package %s is named %s here' % (old, new))
        return 0
    if verb == 'check':
        found = check(io, systems)
        if not plain: return answer(found['inRecord'], result=found)
        io.say('This machine runs %s (release name %s), %s.' % (found['system'] or 'a system that cannot be read',
               found['codename'] or 'not readable', found['architecture'] or 'architecture not readable'))
        if found['inRecord']:
            io.say('It is in the record of supported operating systems.' + (' As a %s it is %s.' % (found['role'], found['proof']) if found['role'] else
                   ' This machine is not set up as a core or a member.'))
        elif found['basedOn']:
            io.say('It is not in the record of supported operating systems. Its packages are those of %s, which is.' % found['basedOn'])
        else:
            io.say('It is not in the record of supported operating systems (isle os list --table).')
        if found['codename'] and found['dependencySet'] != 'not asked':
            io.say('Dependency set for %s on the core: %s.' % (found['codename'], found['dependencySet']))
        return 0 if found['inRecord'] else 1
    import storage_isle
    try: rows = devices(storage_isle.device_list(io), systems)
    except ValueError as exc: return refuse(str(exc))
    names = sorted({r['codename'] for r in rows if r['codename']})
    if not plain: return answer(True, result=dict(devices=rows, releaseNames=names))
    if rows:
        io.say(_table(('DEVICE', 'SYSTEM', 'RELEASE NAME', 'IN THE RECORD'),
                      [(r['device'], r['system'] or 'not reported', r['codename'] or '-', ('yes' if r['inRecord'] else 'no') if r['system'] else '-') for r in rows]))
    else:
        io.say('The device list of the core has no machine.')
    if any(not r['system'] for r in rows): io.say('A machine that reports no system has an isle CLI older than this view, or has not reported yet.')
    if names: io.say('Release names in use: %s. A dependency set is filed per release name (isle apt-repo status).' % ', '.join(names))
    return 0


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'dependency-release':          # for apt-repo.sh: <directory> <own release> [stated]
        try: print('%s\t%s' % dependency_release(sys.argv[2], sys.argv[4] if len(sys.argv) > 4 else '', sys.argv[3], load()))
        except (ValueError, OSError) as error: sys.exit(str(error))
        sys.exit(0)
    sys.exit(main(Io(), sys.argv[1:]))
