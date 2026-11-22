#!/usr/bin/env python3
"""isle storage role list --isle | role show <role> --isle | kind list --isle, and what each machine reports for them.

Storage is kept by the isle side, machine by machine: each one records its own devices and partitions
(storage_roles.py).  So that a plan can be made for the whole isle, every machine adds the partitions that carry a
role to the device report it already publishes (the fact isle.storage_partitions, capabilities.py).  The core's
device list then holds one such report per machine, and this file prints them together.  Polari reads the same rows
of the device list; nothing here adds a store or an API of its own.

The view is a report, not a record: it says what each machine said, with the time it said it.  Whether a partition
is really there is asked on the machine itself (`isle storage partition check`).  A hardware serial is not reported.

A row of the report holds one role.  A partition that carries more than one role is reported once per role, with
the same identifier, size and free space in each row: so a reader from before there could be more than one role
reads every row as it is.

A machine also reports which kinds of storage it is ready to hold (the fact isle.storage_kinds: one word per kind,
storage_kinds.py).  What it lacks for a kind is not reported; that is asked on the machine itself
(`isle storage kind list`).
"""
from datetime import datetime
import json
import re
import subprocess

import storage_contract as sc
import storage_devices
import storage_kinds as sk
import storage_roles as sr

FACT = 'isle.storage_partitions'
KINDS_FACT = 'isle.storage_kinds'
NOT_REPORTED = 'not reported'
KIND_STATES = (sk.READY, sk.NOT_READY, sk.NOT_KNOWN)
API = 'https://api.polari.isle'
CA_FILE = '/etc/isle-mesh/ca/isle-root.crt'
MAX_PARTITIONS = 64      # a report is bounded; what it leaves out is counted in it
MAX_DATASETS = 64
DEVICE = re.compile(r'[a-z0-9][a-z0-9-]{0,62}\Z')
STATES = ('ok',) + sr.WRONG + sr.AWAY + ('not recorded here',)
KEYS = ('role', 'filesystemUuid', 'deviceClass', 'sizeBytes', 'availableBytes', 'roleState', 'datasets')
NO_REPORT = 'no storage report'
NOT_READABLE = 'storage report not readable'


def report_fact(io=None):
    """For the device report: this machine's partitions that carry a role, as a JSON string.  None when they cannot
    be read (the fact is then left out, which the view shows as no report).  Never raises."""
    try:
        _, _, partitions = sr.survey(io or sr.Io())
        held = sorted(((p, role) for p in partitions if p['roleState'] in STATES for role in p['roles']),
                      key=lambda found: (found[0]['filesystemUuid'], sr.ROLES.index(found[1])))
        rows = []
        for p, role in held[:MAX_PARTITIONS]:
            row = dict(role=role, filesystemUuid=p['filesystemUuid'], deviceClass=p['deviceClass'],
                       sizeBytes=int(p['sizeBytes'] or 0), roleState=p['roleState'],
                       datasets=[d for d in sorted(p['datasets']) if sc.DATASET.match(d)][:MAX_DATASETS])
            if 'availableBytes' in p: row['availableBytes'] = int(p['availableBytes'])
            rows.append(row)
        return json.dumps(dict(schemaVersion=1, partitions=rows, leftOut=max(0, len(held) - MAX_PARTITIONS)),
                          separators=(',', ':'), sort_keys=True)
    except Exception:
        return None


def read_fact(fact):
    """One machine's reported fact -> (partitions, how many it left out, when it was observed), or None when this
    version cannot read it.  Every value that is printed later is checked here: another machine wrote it."""
    try:
        if fact['status'] != 'observed': return None
        at = datetime.fromisoformat(fact['observed_at'].replace('Z', '+00:00'))
        doc = json.loads(fact['value'])
        left = doc['leftOut']
        if at.tzinfo is None or doc['schemaVersion'] != 1 or type(left) is not int or left < 0: return None
        if not isinstance(doc['partitions'], list) or len(doc['partitions']) > MAX_PARTITIONS: return None
        rows = []
        for p in doc['partitions']:
            free = p.get('availableBytes', 0)
            good = (p['role'] in sr.ROLES and bool(sr.IDENTIFIER.match(p['filesystemUuid']))
                    and (p['deviceClass'] is None or p['deviceClass'] in sr.CLASSES)
                    and type(p['sizeBytes']) is int and p['sizeBytes'] >= 0 and type(free) is int and free >= 0
                    and p['roleState'] in STATES and isinstance(p['datasets'], list) and len(p['datasets']) <= MAX_DATASETS
                    and all(isinstance(d, str) and sc.DATASET.match(d) for d in p['datasets']))
            if not good: return None
            rows.append({k: p[k] for k in KEYS if k in p})
        return rows, left, at
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def kinds_fact(io=None):
    """For the device report: which kinds of storage this machine is ready to hold, as a JSON string.  None when it
    cannot be told at all.  Never raises."""
    try:
        found = sk.look(io or sr.Io())
        return json.dumps(dict(schemaVersion=1, kinds={kind: found[kind]['state'] for kind in sk.KINDS}),
                          separators=(',', ':'), sort_keys=True)
    except Exception:
        return None


def read_kinds(fact):
    """One machine's reported kinds -> ({kind: state}, when it was observed), or None when this version cannot read
    it.  Only the kinds this version knows are kept; one the machine did not name is `not reported`."""
    try:
        if fact['status'] != 'observed': return None
        at = datetime.fromisoformat(fact['observed_at'].replace('Z', '+00:00'))
        doc = json.loads(fact['value'])
        if at.tzinfo is None or doc['schemaVersion'] != 1 or not isinstance(doc['kinds'], dict): return None
        each = {kind: doc['kinds'].get(kind, NOT_REPORTED) for kind in sk.KINDS}
        if any(state not in KIND_STATES + (NOT_REPORTED,) for state in each.values()): return None
        return each, at
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def device_list(io):
    """The core's device list, read over TLS that is verified against the isle root.  ValueError when it cannot be read."""
    for resolve in (None, 'api.polari.isle:443:127.0.0.1'):       # the second try is the core asking itself
        cmd = ['curl', '-sf', '--max-time', '20', '--max-redirs', '0', '--cacert', CA_FILE, '--capath', '/nonexistent']
        if resolve: cmd += ['--resolve', resolve]
        try: done = io.run(cmd + [API + '/api/islemesh/devices'], capture_output=True, text=True, timeout=25)
        except (OSError, subprocess.SubprocessError): continue
        if done.returncode: continue
        try: listing = json.loads(done.stdout)['devices']
        except (ValueError, KeyError, TypeError): break
        if isinstance(listing, list): return [d for d in listing if isinstance(d, dict)]
        break
    raise ValueError('The device list of the core cannot be read from this machine.')


def machines(listing, now):
    """The device list -> one row per machine in name order: {device, state, partitions[, reportedAt, ageSeconds,
    leftOut], kinds: {state[, reportedAt, ageSeconds, each]}}."""
    out = []
    for d in listing:
        name = d.get('name')
        if d.get('is_mock') or not isinstance(name, str) or not DEVICE.match(name): continue
        evidence = d.get('capability_evidence')
        facts = evidence.get('facts') if isinstance(evidence, dict) else None
        fact = facts.get(FACT) if isinstance(facts, dict) else None
        said = facts.get(KINDS_FACT) if isinstance(facts, dict) else None
        told = read_kinds(said) if said is not None else None
        kinds = dict(state=NO_REPORT if said is None else NOT_READABLE) if told is None else dict(
            state='reported', reportedAt=told[1].isoformat(), ageSeconds=max(0, int((now - told[1]).total_seconds())), each=told[0])
        read = read_fact(fact) if fact is not None else None
        if read is None:
            out.append(dict(device=name, state=NO_REPORT if fact is None else NOT_READABLE, partitions=[], kinds=kinds)); continue
        rows, left, at = read
        out.append(dict(device=name, state='reported', reportedAt=at.isoformat(), leftOut=left,
                        ageSeconds=max(0, int((now - at).total_seconds())), partitions=rows, kinds=kinds))
    return sorted(out, key=lambda m: m['device'])


def _age(seconds):
    for limit, unit, each in ((120, 's', 1), (7200, 'min', 60), (172800, 'h', 3600)):
        if seconds < limit: return '%d %s ago' % (seconds // each, unit)
    return '%d d ago' % (seconds // 86400)


def _size(count): return storage_devices._size(count) if count else '-'


def _kinds(io, found, plain, answer):
    """`kind list` over the whole isle: which machine said it is ready to hold which kind of storage."""
    told = [m for m in found if m['kinds']['state'] == 'reported']
    rows = [dict(dict(kind=kind, roles=sk._roles(0, kind), requires=sk.requires(kind)),
                 **{key: [m['device'] for m in told if m['kinds']['each'][kind] == state]
                    for key, state in (('ready', sk.READY), ('notReady', sk.NOT_READY), ('notKnown', sk.NOT_KNOWN))})
            for kind in sk.KINDS]
    if not plain:
        return answer(True, result=dict(kinds=rows, devices=[dict(m['kinds'], device=m['device']) for m in found]))
    if told:
        io.say(sr._table(('DEVICE',) + tuple(sk.KINDS) + ('REPORTED',),
               [(m['device'],) + tuple(m['kinds']['each'][kind] for kind in sk.KINDS) + (_age(m['kinds']['ageSeconds']),) for m in told]))
    else:
        io.say('No machine reports which kinds of storage it is ready to hold.')
    for m in found:
        if m['kinds']['state'] != 'reported':
            io.say('%s: %s.' % (m['device'], 'no report of its kinds of storage yet (its isle CLI may be older than this view)'
                                if m['kinds']['state'] == NO_REPORT else 'its report of kinds cannot be read by this version of the isle CLI'))
    io.say('This is what each machine last reported, not a check. What a kind needs, and what a machine lacks for it, '
           'on the machine itself: isle storage kind list --table')
    return 0


def main(io, argv):
    """`role list`, `role show <role>` or `kind list` over the whole isle; argv is the words without --isle."""
    plain = '--table' in argv
    words = [a for a in argv if a != '--table']
    if words not in (['role', 'list'], ['kind', 'list']) and not (len(words) == 3 and words[:2] == ['role', 'show']):
        io.say(sr.USAGE)
        return 2
    evaluated = io.now()

    def answer(ok, **body):
        io.say(json.dumps(dict(ok=ok, evaluatedAt=evaluated, operation='storage.' + '.'.join(words[:2]), readOnly=True, scope='isle', **body)))
        return 0 if ok else 1
    role = words[2] if len(words) == 3 else None
    if role is not None and role not in sr.ROLES:
        return answer(False, error='Unknown role: %s. Roles: %s.' % (role, ', '.join(sr.ROLES)))
    try:
        found = machines(device_list(io), datetime.fromisoformat(evaluated))
    except ValueError as exc:
        if plain: io.say(str(exc)); return 1
        return answer(False, error=str(exc))
    if words[0] == 'kind': return _kinds(io, found, plain, answer)
    held = [dict(p, device=m['device'], reportedAt=m['reportedAt'], ageSeconds=m['ageSeconds'], kind=sk.ROLE[p['role']][0],
                 kindOnDevice=m['kinds'].get('each', {}).get(sk.ROLE[p['role']][0], NOT_REPORTED))
            for m in found for p in m['partitions'] if role is None or p['role'] == role]
    held.sort(key=lambda p: (sr.ROLES.index(p['role']), p['device'], p['filesystemUuid']))
    silent = [dict(device=m['device'], state=m['state']) for m in found if m['state'] != 'reported']
    roles = [dict(role=r, partitions=sum(1 for p in held if p['role'] == r), devices=sorted({p['device'] for p in held if p['role'] == r}),
                  datasets=sorted({d for p in held if p['role'] == r for d in p['datasets']}),
                  availableBytes=sum(p.get('availableBytes', 0) for p in held if p['role'] == r and p['roleState'] == 'ok'))
             for r in (sr.ROLES if role is None else (role,))]
    if not plain:
        return answer(True, result=dict(roles=roles, partitions=held, devices=found))
    if role is None:
        io.say(sr._table(('ROLE', 'PARTITIONS', 'ON', 'FREE', 'DATASETS'), [(r['role'], r['partitions'], ', '.join(r['devices']) or '-',
               _size(r['availableBytes']) if r['partitions'] else '-', ', '.join(r['datasets']) or '-') for r in roles]))
        io.say()
    if held:
        io.say(sr._table(('DEVICE', 'ROLE', 'IDENTIFIER', 'CLASS', 'SIZE', 'FREE', 'STATE', 'REPORTED', 'DATASETS'),
               [(p['device'], p['role'], p['filesystemUuid'], p['deviceClass'] or '-', _size(p['sizeBytes']),
                 storage_devices._size(p['availableBytes']) if 'availableBytes' in p else '-', p['roleState'], _age(p['ageSeconds']),
                 ', '.join(p['datasets']) or '-') for p in held]))
    else:
        io.say('No machine reports a partition %s.' % ('with a role' if role is None else 'with the role ' + role))
    for m in found:
        if m['state'] == 'reported' and m['leftOut']: io.say('%s left %d more partitions out of its report.' % (m['device'], m['leftOut']))
    for m in silent:
        io.say('%s: %s.' % (m['device'], 'no storage report yet (its isle CLI may be older than this view)' if m['state'] == NO_REPORT
                            else 'its storage report cannot be read by this version of the isle CLI'))
    short = sorted({(p['device'], p['kind'], p['kindOnDevice']) for p in held if p['kindOnDevice'] in (sk.NOT_READY, sk.NOT_KNOWN)})
    for name, kind, state in short:
        io.say('%s holds a partition for storage of the kind %s and reported that %s.' % (
            name, kind, 'it is not ready for it' if state == sk.NOT_READY else 'it could not tell whether it is ready for it'))
    if short: io.say('What it lacks, on that machine: isle storage kind list --table')
    if len({(p['device'], p['filesystemUuid']) for p in held}) < len(held):
        io.say('A partition with more than one role has one line per role; its size and free space are one space.')
    io.say('This is what each machine last reported, not a check. On a machine itself: isle storage partition check --table')
    return 0
