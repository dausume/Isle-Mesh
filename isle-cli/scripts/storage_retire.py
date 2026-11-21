#!/usr/bin/env python3
"""isle storage partition retire | device retire

Takes away what `partition assign` and `device register` wrote: a role of a partition (from the marker at its root
and from this machine's record), and a device of this machine's record.

Nothing here erases, formats, unmounts, moves or removes data.  The one file ever removed is the marker, and only
when no role is left for it to name.  Where taking a role away changes what this machine does with data that is
already on the partition, the verb refuses and names the option that says "leave it where it is".
"""
import os
from pathlib import Path
import shlex
import subprocess

from device_backup_items import FOLDER as BACKUPS
from operations import atomic
import storage_devices
import storage_roles as sr

OPTIONS = ('--record-only', '--leave-data')


def kept_backups(mount):
    """The device-backup folder at the root of a partition when it holds anything, else None.  A folder that cannot
    be listed (it is readable by root only) counts as holding something."""
    folder = Path(mount) / BACKUPS
    try: return str(folder) if any(folder.iterdir()) else None
    except (FileNotFoundError, NotADirectoryError): return None
    except OSError: return str(folder)


def remove_marker(mount):
    base = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.unlink(sr.MARKER, dir_fd=base)       # removes the name itself; a link is never followed
        os.fsync(base)
    finally: os.close(base)


def _blocked(row, identifier):
    """Why the marker of this partition cannot be looked at or changed, or None."""
    if row['state'] == 'not present': return 'it is not present on this machine'
    if row['markerProblem']: return row['markerProblem']
    if not row['wholeMount']:
        return 'it is not mounted' + (' whole where it can be reached (only a folder of it is, or another mount covers it)' if row['mount'] else '')
    if row['marker'] and row['marker']['filesystemUuid'] != identifier:
        return 'the marker at its root names another filesystem (%s): this filesystem is a copy of that one' % row['marker']['filesystemUuid']
    return None


def partition(io, identifier, asked, record_only=False, leave_data=False):
    """Take roles away from a partition.  Afterwards neither the marker nor the record names them.  A role that is
    not typed stays as it is, in the marker and in the record: nothing is taken away unasked."""
    what = 'partition retire'
    wanted = (asked or '').split(',')
    if any(r not in sr.ROLES for r in wanted) or len(set(wanted)) != len(wanted):
        return sr._refuse(io, what, 'the role must be one of: %s (for more than one, put commas between them)' % ', '.join(sr.ROLES))
    if not sr.IDENTIFIER.match(identifier or ''): return sr._refuse(io, what, 'that is not a filesystem identifier')
    typed = 'sudo isle storage partition retire %s --role %s' % (identifier, asked)
    chosen = (' --record-only' if record_only else '') + (' --leave-data' if leave_data else '')
    try:
        _, _, partitions = sr.survey(io)
        rows = [p for p in partitions if p['filesystemUuid'] == identifier]
        present = [p for p in rows if p['state'] != 'not present']
        if not rows: return sr._refuse(io, what, 'this machine neither records nor sees a partition with the identifier %s (see: isle storage partition check --table)' % identifier)
        if len(present) > 1 and not record_only:
            return sr._refuse(io, what, 'two partitions report the identifier %s (one is a copy of the other), so it does not name one partition. '
                              'To change the record of this machine only: %s --record-only%s' % (identifier, typed, chosen))
        row = (present or rows)[0]
        name = 'partition %s' % row['name'] if row['name'] else 'the partition'
        recorded = row['recordedRoles']
        blocked = None if record_only else _blocked(row, identifier)
        if blocked:
            return sr._refuse(io, what, '%s (%s) cannot have its marker changed: %s. To take %s out of the record of this machine only: %s --record-only%s' % (
                name, identifier, blocked, _words_it(wanted), typed, chosen))
        marked = [] if record_only else row['markerRoles']
        from_record, from_marker = [r for r in wanted if r in recorded], [r for r in wanted if r in marked]
        taken = [r for r in wanted if r in from_record or r in from_marker]
        if not taken:
            io.say('%s (%s) does not carry %s%s. Nothing was changed.' % (name[0].upper() + name[1:], identifier, sr._words(wanted),
                   ' in the record of this machine' if record_only else '')); return 0
        if from_marker and row['mountedReadOnly']:
            return sr._refuse(io, what, '%s is mounted read-only, so its marker cannot be changed. To take %s out of the record of this machine only: %s --record-only%s' % (
                name, _words_it(wanted), typed, chosen))
        left, keep = [r for r in recorded if r not in wanted], [r for r in marked if r not in wanted]
        backups = kept_backups(row['wholeMount']) if 'backup' in from_record and row['wholeMount'] else None
        if backups and not leave_data:
            return sr._refuse(io, what, 'device-backups of this machine are kept on it (%s). Without the role, `isle backup` keeps new ones in the state folder of '
                              'this machine and no longer lists these. To take the role away and leave them where they are: %s%s --leave-data' % (backups, typed, chosen))
        if from_record and not left and row['datasets'] and not leave_data:
            return sr._refuse(io, what, '%s would carry no role, and it holds: %s. To take the role away and leave the data where it is: %s%s --leave-data' % (
                name, ', '.join(row['datasets']), typed, chosen))
        if not io.is_root():
            io.say('This changes the storage records of this machine. Type: %s%s' % (typed, chosen)); return 1
        at = io.now()
        with sr.locked(io):
            records = sr.read_records(io.records)
            held = records['partitions'].get(identifier)
            if (sr.roles_of(held) if held else []) != recorded:
                return sr._refuse(io, what, 'this machine now records it with %s' % (sr._words(sr.roles_of(held)) if held else 'no role'))
            if from_marker:
                still = sr.whole_mount(io.mounts(), identifier)[0]      # looked at again, now that the lock is held
                if not still or still['target'] != row['wholeMount'] or not io.is_mount(row['wholeMount']):
                    return sr._refuse(io, what, '%s is no longer mounted at %s' % (name, row['wholeMount']))
                if sr.read_marker(row['wholeMount']) != (row['marker'], None):
                    return sr._refuse(io, what, 'the marker at the root of %s changed while this command ran' % name)
                if keep: sr.write_marker(row['wholeMount'], sr.with_roles(dict(row['marker'], changedAt=at, changedOn=io.machine()), keep))
                else: remove_marker(row['wholeMount'])
            if from_record:
                if left: records['partitions'][identifier] = sr.with_roles(held, left)
                else: del records['partitions'][identifier]
                atomic(Path(io.records), records)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        return sr._refuse(io, what, str(exc) or exc.__class__.__name__)
    io.say('%s (%s) no longer carries %s. No data was moved or removed.' % (name[0].upper() + name[1:], identifier, sr._words(taken)))
    if len(taken) < len(wanted): io.say('It did not carry %s.' % sr._words([r for r in wanted if r not in taken]))
    if left: io.say('This machine still records it with %s.' % sr._words(left))
    elif from_record: io.say('This machine no longer records it.')
    if record_only: io.say('Only the record of this machine was changed. The marker on the partition was not looked at; if it names the role, it still does.')
    else:
        if from_marker and not keep: io.say('Its marker was removed.')
        if sorted(keep) != sorted(left):
            io.say('Its marker %s and this machine records %s. See: isle storage partition check --table' % (
                'names ' + sr._words(keep) if keep else 'names no role' if row['marker'] and not from_marker else 'is gone',
                'it with ' + sr._words(left) if left else 'no role for it'))
    if row['datasets']: io.say('Still on it: %s.' % ', '.join(row['datasets']))
    if backups: io.say('The device-backups stay in %s. `isle backup` now keeps new ones in the state folder of this machine and does not list these.' % backups)
    return 0


def _words_it(roles):
    return 'the role' if len(roles) == 1 else 'the roles'


def device(io, serial):
    """Take a device out of this machine's record.  The disk itself is not looked at, so one that is gone for good
    can be taken out too."""
    what = 'device retire'
    if not sr.SERIAL.match(serial or ''): return sr._refuse(io, what, 'that is not a hardware serial')

    def carried(records):
        return sorted((i, sr.roles_of(p)) for i, p in records['partitions'].items() if p['deviceSerial'] == serial)

    def refuse_carried(rows):
        return sr._refuse(io, what, 'this machine records %s on it. First, for each (add --record-only when the disk is away): %s' % (
            '1 partition with a role' if len(rows) == 1 else '%d partitions with roles' % len(rows),
            '; '.join('sudo isle storage partition retire %s --role %s' % (i, ','.join(roles)) for i, roles in rows)))
    try:
        records = sr.read_records(io.records)
        known = records['devices'].get(serial)
        if not known:
            io.say('Device %s is not registered on this machine. Nothing was changed.' % serial); return 0
        if carried(records): return refuse_carried(carried(records))
        if not io.is_root():
            io.say('This changes the storage records of this machine. Type: sudo isle storage device retire %s' % shlex.quote(serial)); return 1
        with sr.locked(io):
            records = sr.read_records(io.records)
            if serial not in records['devices']:
                io.say('Device %s is not registered on this machine. Nothing was changed.' % serial); return 0
            if carried(records): return refuse_carried(carried(records))
            del records['devices'][serial]
            atomic(Path(io.records), records)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        return sr._refuse(io, what, str(exc) or exc.__class__.__name__)
    io.say('Device %s (%s, %s, %s) is no longer registered on this machine. Nothing on the disk was changed.' % (
        serial, known.get('model') or 'no model reported',
        storage_devices._size(known['sizeBytes']) if isinstance(known.get('sizeBytes'), int) else 'size not recorded', known['class']))
    return 0


def main(io, argv):
    options = [a for a in argv if a in OPTIONS]
    words = [a for a in argv if a not in OPTIONS]
    if len(words) == 3 and words[:2] == ['device', 'retire'] and not options:
        return device(io, words[2])
    if len(words) == 5 and words[:2] == ['partition', 'retire'] and words[3] == '--role' and len(set(options)) == len(options):
        return partition(io, words[2], words[4], record_only='--record-only' in options, leave_data='--leave-data' in options)
    io.say(sr.USAGE)
    return 2
