#!/usr/bin/env python3
"""isle storage device register | partition assign | partition check | role list | role show
(`partition retire` and `device retire`, which take these away again, are in storage_retire.py.)

The storage records of one machine.  A device is declared by its hardware serial with a class (fixed, dock or
flash).  A partition is given one role or more than one.  Its roles are written in two places: in a marker file
at the root of the partition itself, so the disk says what it is for wherever it is plugged in, and in this
machine's record, so a partition that is away is still known here.

Nothing in this file creates, erases, formats, mounts or moves anything.  `partition assign` adopts a filesystem
that already exists: it writes the marker and the record and leaves every other file alone.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import uuid as uuid_module

from operations import atomic
import storage_devices

RECORDS = Path('/etc/isle-mesh/storage/device-records.json')
MARKER = 'isle-partition.json'
MARKER_KIND = 'isle.storage.partition'
MARKER_MAX = 65536
ROLES = ('package-repo', 'distribution', 'registry', 'blob', 'git', 'assets', 'backup', 'scratch', 'transfer')
CLASSES = {'fixed': 'stays in this machine', 'dock': 'a disk in a dock, which may be away',
           'flash': 'an ordinary USB flash drive, which may be away'}
MAY_BE_AWAY = ('dock', 'flash')
SERIAL = re.compile(r'[\x21-\x7e][\x20-\x7e]{0,126}\Z')
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{3,63}\Z')
#: what `partition check` counts as wrong on a partition this machine records; a dock or flash partition that is
#: away is not wrong
WRONG = ('marker missing', 'marker differs', 'marker cannot be read', 'device not registered', 'on another device')
AWAY = ('not present', 'not mounted', 'not mounted whole')
USAGE = ('Usage: isle storage device list [--table] | isle storage partition list [--table]\n'
         '       sudo isle storage device register <serial> --class %s\n'
         '       sudo isle storage partition assign <filesystem-identifier> --role <role>[,<role>...]\n'
         '       isle storage partition check [--table]\n'
         '       sudo isle storage partition retire <filesystem-identifier> --role <role>[,<role>...] [--record-only] [--leave-data]\n'
         '       sudo isle storage device retire <serial>\n'
         '       sudo isle storage device health <serial> [--table]\n'
         '       isle storage role list [--table] | isle storage role show <role> [--table]\n'
         '       isle storage role list --isle [--table] | isle storage role show <role> --isle [--table]\n'
         'Roles: %s') % ('|'.join(CLASSES), ', '.join(ROLES))


class Io:
    """Everything that touches this machine; a test replaces it."""
    records = RECORDS

    root = storage_devices.LOCAL_ROOT
    run = staticmethod(subprocess.run)

    def tree(self): return storage_devices.read_tree(self.run)
    def datasets(self): return storage_devices.local_datasets(self.root)[0]
    def usage(self, path): return storage_devices._usage(path)
    def is_root(self): return os.geteuid() == 0
    def is_mount(self, path): return os.path.ismount(path)
    def now(self): return datetime.now(timezone.utc).isoformat()
    def say(self, line=''): print(line)

    def machine(self):
        try: return Path('/etc/isle-mesh/device-name').read_text().strip() or None
        except OSError: return None

    def mounts(self):
        """Every mount with the part of the filesystem it shows: [{target, fsroot, uuid, options}]."""
        done = self.run(['findmnt', '--json', '--list', '-o', 'TARGET,FSROOT,UUID,OPTIONS'],
                        capture_output=True, text=True, check=True, timeout=15)
        rows = json.loads(done.stdout or '{}').get('filesystems')
        if not isinstance(rows, list): raise ValueError('The mount report could not be read.')
        return [r for r in rows if isinstance(r, dict)]


def roles_of(entry):
    """The roles a record entry or a marker names, in the order they were given.  ValueError when it names none, an
    unknown one or the same one twice.  With one role only `role` is written, as it was when a partition had exactly
    one; with more, `roles` holds all of them and `role` the first, so an older CLI still reads a role it knows."""
    listed = entry.get('roles', [entry.get('role')])
    if (not isinstance(listed, list) or not listed or any(not isinstance(r, str) or r not in ROLES for r in listed)
            or len(set(listed)) != len(listed) or entry.get('role', listed[0]) != listed[0]):
        raise ValueError('it does not name roles this CLI understands')
    return list(listed)


def with_roles(entry, roles):
    """The record entry or marker as it is written for these roles."""
    out = {k: v for k, v in entry.items() if k not in ('role', 'roles')}
    out['role'] = roles[0]
    if len(roles) > 1: out['roles'] = list(roles)
    return out


def _words(roles):
    return ('the role ' if len(roles) == 1 else 'the roles ') + ', '.join(roles)


def read_records(path):
    """This machine's record: {version, devices: {serial: {class, ...}}, partitions: {identifier: {role[, roles], deviceSerial, ...}}}.
    A record that is damaged is an error, never an empty one: an empty one would hide the roles that are recorded."""
    try: text = Path(path).read_text()
    except FileNotFoundError: return dict(version=1, devices={}, partitions={})
    except OSError as exc: raise ValueError('the storage records of this machine cannot be read (%s)' % exc.strerror)
    try:
        doc = json.loads(text)
        devices, partitions = doc['devices'], doc['partitions']
        good = (doc.get('version') == 1 and isinstance(devices, dict) and isinstance(partitions, dict)
                and all(isinstance(v, dict) and v.get('class') in CLASSES for v in devices.values())
                and all(isinstance(v, dict) and roles_of(v) and isinstance(v.get('deviceSerial'), str)
                        for v in partitions.values()))
    except (ValueError, KeyError, TypeError, AttributeError): good = False
    if not good: raise ValueError('the storage records of this machine are damaged (%s)' % path)
    return doc


@contextmanager
def locked(io):
    """One writer of the record at a time; held only around the read-change-write."""
    path = Path(io.records)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    with open(str(path) + '.lock', 'a+') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('the storage records are being changed by another command')
        yield


def read_marker(mount):
    """The marker at the root of a mounted partition: (marker or None, what is wrong with it or None)."""
    try: base = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
    except OSError: return None, 'the root of the partition cannot be opened'
    try:
        try: fd = os.open(MARKER, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=base)
        except FileNotFoundError: return None, None
        except OSError: return None, '%s at its root is not a plain file that can be read' % MARKER
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode): return None, '%s at its root is not a plain file' % MARKER
            raw = b''
            while len(raw) <= MARKER_MAX:
                piece = os.read(fd, MARKER_MAX + 1 - len(raw))
                if not piece: break
                raw += piece
        finally: os.close(fd)
    finally: os.close(base)
    try: doc = json.loads(raw) if len(raw) <= MARKER_MAX else None
    except ValueError: doc = None
    try: good = isinstance(doc, dict) and doc.get('kind') == MARKER_KIND and doc.get('version') == 1 and bool(roles_of(doc)) \
        and isinstance(doc.get('filesystemUuid'), str)
    except ValueError: good = False
    if not good: return None, '%s at its root is not a partition marker this CLI understands' % MARKER
    return doc, None


def write_marker(mount, marker):
    """Write the marker without following a link: a temporary file in the same folder, then a rename over the name."""
    base = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
    temporary = '.%s.%s.tmp' % (MARKER, uuid_module.uuid4().hex)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=base)
        try:
            with os.fdopen(fd, 'w') as out:
                json.dump(marker, out, indent=1, sort_keys=True); out.write('\n'); out.flush(); os.fsync(out.fileno())
            os.replace(temporary, MARKER, src_dir_fd=base, dst_dir_fd=base)
        except BaseException:
            try: os.unlink(temporary, dir_fd=base)
            except OSError: pass
            raise
        os.fsync(base)
    finally: os.close(base)


def whole_mount(mounts, identifier):
    """Where the whole filesystem is mounted and can be reached: (row or None, mounted at all).
    A mount that a later mount covers (on the same folder or on a folder above it) is not counted: opening its
    folder would reach the other filesystem."""
    targets = [m.get('target') if isinstance(m.get('target'), str) else None for m in mounts]
    whole, mounted = [], False
    for index, mount in enumerate(mounts):
        target = targets[index]
        if mount.get('uuid') != identifier or not target: continue
        mounted = True
        covered = any(later and (later == target or target.startswith(later.rstrip('/') + '/')) for later in targets[index + 1:])
        if mount.get('fsroot') == '/' and not covered: whole.append(mount)
    return (min(whole, key=lambda m: len(m['target'])) if whole else None), mounted


def survey(io):
    """One read of the machine: (records, devices, partitions), each partition with its roles, marker and state."""
    records = read_records(io.records)
    tree = io.tree()
    devices = storage_devices.devices(tree)
    for device in devices:
        known = records['devices'].get(device['serial']) if device['serial'] else None
        device['class'] = known['class'] if known else None
    partitions = storage_devices.partitions(tree, io.datasets(), io.usage)
    mounts = io.mounts()
    present = set()
    for row in partitions:
        identifier = row['filesystemUuid']
        recorded = records['partitions'].get(identifier) if identifier else None
        root, mounted = whole_mount(mounts, identifier) if identifier else (None, False)
        marker, problem = read_marker(root['target']) if root else (None, None)
        if identifier: present.add(identifier)
        registered = bool(row['deviceSerial']) and row['deviceSerial'] in records['devices']
        recorded_roles, marker_roles = roles_of(recorded) if recorded else [], roles_of(marker) if marker else []
        row.update(deviceClass=records['devices'][row['deviceSerial']]['class'] if registered else None,
                   recordedRoles=recorded_roles, markerRoles=marker_roles,
                   wholeMount=root['target'] if root else None,
                   mountedReadOnly=bool(root) and 'ro' in str(root.get('options') or '').split(','), marker=marker)
        row['markerProblem'] = problem
        copied = bool(marker) and marker['filesystemUuid'] != identifier
        if recorded:
            if problem: state = 'marker cannot be read'
            elif marker: state = 'ok' if sorted(marker_roles) == sorted(recorded_roles) and not copied else 'marker differs'
            else: state = 'marker missing' if root else 'not mounted whole' if mounted else 'not mounted'
            if state == 'ok' and recorded['deviceSerial'] != row['deviceSerial']: state = 'on another device'
            elif state == 'ok' and not registered: state = 'device not registered'
        elif marker: state = 'marker of another filesystem' if copied else 'not recorded here'
        else: state = None      # no role here; a file of that name that is not a marker is only looked at by `assign`
        row['roles'] = recorded_roles if recorded else marker_roles if state == 'not recorded here' else []
        row['roleState'] = state
    for identifier, recorded in sorted(records['partitions'].items()):
        if identifier in present: continue
        known = records['devices'].get(recorded['deviceSerial'])
        partitions.append(dict(name=None, device=None, deviceSerial=recorded['deviceSerial'], filesystemUuid=identifier,
                               deviceClass=known['class'] if known else None, roles=roles_of(recorded), recordedRoles=roles_of(recorded),
                               markerRoles=[], roleState='not present', state='not present', mount=None, wholeMount=None,
                               sizeBytes=0, system=False, datasets=[], marker=None, markerProblem=None))
    return records, devices, partitions


def wrong(row):
    """Whether a partition this machine records is in a state `partition check` reports as wrong."""
    if not row['recordedRoles']: return False
    return row['roleState'] in WRONG or (row['roleState'] in AWAY and row['deviceClass'] not in MAY_BE_AWAY)


def _refuse(io, what, why):
    io.say('REFUSED: %s: %s. Nothing was changed.' % (what, why))
    return 1


def register(io, serial, device_class):
    what = 'device register'
    if device_class not in CLASSES: return _refuse(io, what, 'the class must be one of: ' + ', '.join(CLASSES))
    if not SERIAL.match(serial or ''): return _refuse(io, what, 'that is not a hardware serial')
    try:
        records, devices, _ = survey(io)
        found = [d for d in devices if d['serial'] == serial]
        if not found: return _refuse(io, what, 'no storage device with the serial %s is present on this machine (see: isle storage device list --table)' % serial)
        if len(found) > 1: return _refuse(io, what, 'two devices report the serial %s, so it does not name one device' % serial)
        device, before = found[0], (records['devices'].get(serial) or {}).get('class')
        if before == device_class:
            io.say('Device %s is already registered as a %s device. Nothing was changed.' % (serial, device_class)); return 0
        if not io.is_root():
            io.say('This changes the storage records of this machine. Type: sudo isle storage device register %s --class %s' % (shlex.quote(serial), device_class)); return 1
        with locked(io):
            records = read_records(io.records)
            # a class change keeps what else is recorded for the device (its last health reading)
            records['devices'][serial] = dict(records['devices'].get(serial) or {}, **{'class': device_class}, model=device['model'],
                                              sizeBytes=device['sizeBytes'], registeredAt=io.now())
            atomic(Path(io.records), records)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        return _refuse(io, what, str(exc) or exc.__class__.__name__)
    io.say('Device %s (%s, %s) is registered as a %s device: %s.' % (serial, device['model'] or 'no model reported',
           storage_devices._size(device['sizeBytes']), device_class, CLASSES[device_class]))
    if before: io.say('Its class was %s before.' % before)
    return 0


def assign(io, identifier, asked):
    """Give a partition a role, or one more role.  Afterwards the marker and the record name the same roles.  A role
    that only the record or only the marker names is kept only when it is typed too: no role is taken over unasked."""
    what = 'partition assign'
    wanted = (asked or '').split(',')
    if any(r not in ROLES for r in wanted) or len(set(wanted)) != len(wanted):
        return _refuse(io, what, 'the role must be one of: %s (for more than one, put commas between them)' % ', '.join(ROLES))
    if not IDENTIFIER.match(identifier or ''): return _refuse(io, what, 'that is not a filesystem identifier')
    try:
        records, _, partitions = survey(io)
        found = [p for p in partitions if p['filesystemUuid'] == identifier and p['state'] != 'not present']
        if not found: return _refuse(io, what, 'no partition with a plain filesystem and the identifier %s is present on this machine (see: isle storage partition list --table)' % identifier)
        if len(found) > 1: return _refuse(io, what, 'two partitions report the identifier %s (one is a copy of the other), so it does not name one partition' % identifier)
        row = found[0]
        serial = row['deviceSerial']
        if not serial: return _refuse(io, what, 'its device reports no serial, so the device cannot be registered')
        if serial not in records['devices']:
            return _refuse(io, what, 'its device (serial %s) is not registered. First: sudo isle storage device register %s --class %s' % (serial, shlex.quote(serial), '|'.join(CLASSES)))
        if row['system']: return _refuse(io, what, 'partition %s carries the running system of this machine' % row['name'])
        if row['markerProblem']: return _refuse(io, what, row['markerProblem'])
        if row['marker'] and row['marker']['filesystemUuid'] != identifier:
            return _refuse(io, what, 'the marker at its root names another filesystem (%s): this filesystem is a copy of that one' % row['marker']['filesystemUuid'])
        if not row['wholeMount']:
            return _refuse(io, what, 'partition %s is not mounted%s, and the marker is written at its root' % (row['name'],
                           ' whole where it can be reached (only a folder of it is, or another mount covers it)' if row['mount'] else ''))
        recorded, marked = row['recordedRoles'], row['markerRoles']
        target = list(dict.fromkeys(recorded + marked + wanted))
        if row['marker'] and (set(recorded) ^ set(marked)) - set(wanted):
            return _refuse(io, what, '%s, and its marker (written %s on %s) names %s. To make both name all of them: sudo isle storage partition assign %s --role %s' % (
                'this machine records partition %s with %s' % (row['name'], _words(recorded)) if recorded else 'this machine does not record partition %s' % row['name'],
                row['marker'].get('assignedAt'), row['marker'].get('assignedOn'), _words(marked), identifier, ','.join(target))
                + '. To take a role away instead: sudo isle storage partition retire %s --role <role>' % identifier)
        mark = sorted(marked) != sorted(target)                  # the marker has to be written (again)
        if row['roleState'] == 'ok' and recorded == target:
            io.say('Partition %s (%s) already carries %s. Nothing was changed.' % (row['name'], identifier, _words(wanted))); return 0
        if not io.is_root():
            io.say('This changes the storage records of this machine. Type: sudo isle storage partition assign %s --role %s' % (identifier, asked)); return 1
        if row['mountedReadOnly'] and mark: return _refuse(io, what, 'partition %s is mounted read-only' % row['name'])
        at = io.now()
        with locked(io):
            records = read_records(io.records)
            held = records['partitions'].get(identifier)
            if (roles_of(held) if held else []) != recorded:
                return _refuse(io, what, 'this machine now records it with %s' % (_words(roles_of(held)) if held else 'no role'))
            if mark:
                still = whole_mount(io.mounts(), identifier)[0]      # looked at again, now that the lock is held
                if not still or still['target'] != row['wholeMount'] or not io.is_mount(row['wholeMount']):
                    return _refuse(io, what, 'partition %s is no longer mounted at %s' % (row['name'], row['wholeMount']))
                if read_marker(row['wholeMount']) != (row['marker'], None):
                    return _refuse(io, what, 'the marker at the root of partition %s changed while this command ran' % row['name'])
                fresh = dict(kind=MARKER_KIND, version=1, filesystemUuid=identifier, deviceSerial=serial, assignedAt=at, assignedOn=io.machine())
                write_marker(row['wholeMount'], with_roles(dict(row['marker'], changedAt=at, changedOn=io.machine()) if row['marker'] else fresh, target))
            moved = held['deviceSerial'] if held and held['deviceSerial'] != serial else None
            if not held or moved or recorded != target:
                records['partitions'][identifier] = with_roles(dict(
                    deviceSerial=serial, assignedAt=held['assignedAt'] if held and held.get('assignedAt') else at), target)
                atomic(Path(io.records), records)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        return _refuse(io, what, str(exc) or exc.__class__.__name__)
    io.say('Partition %s (%s) on device %s now carries %s. No data was moved.' % (row['name'], identifier, serial, _words(target)))
    if recorded and recorded != target: io.say('It carried %s before.' % _words(recorded))
    if moved: io.say('This machine recorded it on device %s before; the record now names the device it is on.' % moved)
    if row['marker'] and not recorded:
        io.say('Its marker was already there (written %s on %s, naming %s); this machine now records it.' % (
            row['marker'].get('assignedAt'), row['marker'].get('assignedOn'), _words(marked)))
    elif row['marker'] and sorted(marked) != sorted(recorded): io.say('Its marker named %s before.' % _words(marked))
    if row['datasets']: io.say('Datasets already on it: %s.' % ', '.join(row['datasets']))
    return 0


def _public(row):
    return {k: v for k, v in row.items() if k not in ('marker', 'wholeMount', 'mountedReadOnly')}


def _free(row):
    return storage_devices._size(row['availableBytes']) if 'availableBytes' in row else '-'


def _table(head, body):
    widths = [max(len(str(line[i])) for line in [head] + body) for i in range(len(head))]
    return '\n'.join('  '.join(str(cell).ljust(widths[i]) for i, cell in enumerate(line)).rstrip() for line in [head] + body)


def _partition_table(rows):
    return _table(('ROLES', 'PARTITION', 'IDENTIFIER', 'DEVICE SERIAL', 'CLASS', 'SIZE', 'FREE', 'STATE', 'DATASETS'),
                  [(', '.join(r['roles']) or '-', r['name'] or '-', r['filesystemUuid'], r['deviceSerial'] or '-', r['deviceClass'] or '-',
                    storage_devices._size(r['sizeBytes']) if r['sizeBytes'] else '-', _free(r), r['roleState'],
                    ', '.join(r['datasets']) or '-') for r in rows])


def report(io, argv):
    plain = '--table' in argv
    words = [a for a in argv if a != '--table']
    operation = 'storage.' + '.'.join(words[:2])
    evaluated = io.now()

    def answer(ok, **body):
        io.say(json.dumps(dict(ok=ok, evaluatedAt=evaluated, operation=operation, readOnly=True, **body)))
        return 0 if ok else 1
    try:
        _, _, partitions = survey(io)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
        return answer(False, error=str(exc) or exc.__class__.__name__)
    held = sorted((p for p in partitions if p['roleState']), key=lambda p: (ROLES.index(p['roles'][0]) if p['roles'] else len(ROLES), p['filesystemUuid']))
    shared = ('A partition with more than one role is counted under each of them; its free space is one space.'
              if any(len(p['roles']) > 1 for p in held) else None)
    if words == ['partition', 'check']:
        bad = [p for p in held if wrong(p)]
        if plain:
            io.say(_partition_table(held) if held else 'No partition of this machine carries a role yet.')
            io.say('%d of %d need attention.' % (len(bad), len(held)) if bad else 'Nothing needs attention.')
            return 1 if bad else 0
        return answer(not bad, result=dict(partitions=[_public(p) for p in held], needAttention=[p['filesystemUuid'] for p in bad]))
    if words == ['role', 'list']:
        rows = [dict(role=role, partitions=[p['filesystemUuid'] for p in held if role in p['roles']],
                     datasets=sorted(d for p in held if role in p['roles'] for d in p['datasets']),
                     availableBytes=sum(p.get('availableBytes', 0) for p in held if role in p['roles'] and p['roleState'] == 'ok'))
                for role in ROLES]
        if plain:
            io.say(_table(('ROLE', 'PARTITIONS', 'FREE', 'DATASETS'), [(r['role'], len(r['partitions']),
                   storage_devices._size(r['availableBytes']) if r['partitions'] else '-', ', '.join(r['datasets']) or '-') for r in rows]))
            if shared: io.say(shared)
            return 0
        return answer(True, result=dict(roles=rows))
    role = words[2]
    if role not in ROLES: return answer(False, error='Unknown role: %s. Roles: %s.' % (role, ', '.join(ROLES)))
    mine = [p for p in held if role in p['roles']]
    if plain:
        io.say(_partition_table(mine) if mine else 'No partition of this machine carries the role %s.' % role)
        return 0
    return answer(True, result=dict(role=role, partitions=[_public(p) for p in mine]))


def from_records(io):
    """For the listings: add the class and the roles this machine records.  Returns the note the listing carries."""
    def add(kind, rows):
        records = read_records(io.records)
        for row in rows:
            if kind == 'device': row['class'] = (records['devices'].get(row['serial']) or {}).get('class')
            else: row['roles'] = roles_of(records['partitions'][row['filesystemUuid']]) if row['filesystemUuid'] in records['partitions'] else []
        return ('The class and the roles are what this machine records. `isle storage partition check` compares the '
                'record with the marker on each partition.')
    return add


def main(argv=None, io=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    io = io or Io()
    if '--isle' in argv:        # what every machine of the isle reported, read from the core's device list
        import storage_isle
        return storage_isle.main(io, [a for a in argv if a != '--isle'])
    words = [a for a in argv if a != '--table']
    if words[:2] in (['partition', 'retire'], ['device', 'retire']):
        import storage_retire
        return storage_retire.main(io, argv)
    if words[:2] == ['device', 'health']:
        import storage_health
        return storage_health.main(io, argv)
    if words[:1] in (['device'], ['partition']) and (len(words) < 2 or words[1] not in storage_devices.RECORD_VERBS[words[0]]):
        return storage_devices.main(argv, run=io.run, root=io.root, usage=io.usage, out=io.say, records=from_records(io))
    if len(words) == 5 and words[:2] == ['device', 'register'] and words[3] == '--class' and '--table' not in argv:
        return register(io, words[2], words[4])
    if len(words) == 5 and words[:2] == ['partition', 'assign'] and words[3] == '--role' and '--table' not in argv:
        return assign(io, words[2], words[4])
    if words in (['partition', 'check'], ['role', 'list']) or (len(words) == 3 and words[:2] == ['role', 'show']):
        return report(io, argv)
    io.say(USAGE)
    return 2


if __name__ == '__main__':
    sys.exit(main())
