#!/usr/bin/env python3
"""isle storage partition mount <filesystem-identifier> --at <folder>
   isle storage partition unmount <filesystem-identifier> [--with-datasets]

mount writes one systemd mount unit that names the filesystem by its identifier, and starts it.  The unit is wanted
by the device, not by the start of the machine: the partition is mounted each time the disk is present, and a disk
that is away holds nothing up.

unmount stops that unit and removes it.  Only a unit that `mount` wrote is ever stopped or removed; a mount made by
hand or by /etc/fstab is left alone.

Nothing here erases, formats or moves data.  /etc/fstab is read and never written.
"""
import os
from pathlib import Path
import re
import subprocess

from storage_mount import _absolute, mount_unit_name
import storage_roles as sr
import storage_slots
from storage_units import _write

HEAD = '# Written by: isle storage partition mount %s'
FILESYSTEM = re.compile(r'[a-z0-9]+')
OPTION = '--with-datasets'


class Refused(Exception):
    pass


def device_unit(identifier):
    """The systemd unit of the device node that carries this filesystem identifier."""
    return mount_unit_name('/dev/disk/by-uuid/' + identifier)[:-len('.mount')] + '.device'


def unit_text(identifier, folder, filesystem):
    return '\n'.join([
        HEAD % identifier,
        '[Unit]',
        'Description=Isle storage partition %s' % identifier,
        '',
        '[Mount]',
        'What=/dev/disk/by-uuid/%s' % identifier,
        'Where=%s' % folder,
        'Type=%s' % filesystem,
        '',
        '[Install]',
        'WantedBy=%s' % device_unit(identifier),
        ''])


def written(io, identifier):
    """The mount units this verb wrote for a filesystem: [(unit name, folder)]."""
    found = []
    try: names = sorted(os.listdir(io.unit_dir))
    except FileNotFoundError: return found
    for name in names:
        path = Path(io.unit_dir) / name
        if not name.endswith('.mount') or path.is_symlink() or not path.is_file(): continue
        lines = path.read_text(errors='replace').splitlines()
        if lines[:1] != [HEAD % identifier]: continue
        where = [line[len('Where='):] for line in lines if line.startswith('Where=')]
        found.append((name, where[0] if where else None))
    return found


def in_fstab(io, identifier, folder):
    """What /etc/fstab says about this filesystem or this folder, or None.  It is only read."""
    try: text = Path(io.fstab).read_text(errors='replace')
    except FileNotFoundError: return None
    for line in text.splitlines():
        fields = line.split('#', 1)[0].split()
        if len(fields) < 2: continue
        if fields[0].lower() in ('uuid=' + identifier.lower(), '/dev/disk/by-uuid/' + identifier.lower()):
            return '/etc/fstab names this filesystem (for %s)' % fields[1]
        if folder and fields[1].rstrip('/') == folder: return '/etc/fstab names that folder (for %s)' % fields[0]
    return None


def _folder(folder):
    """The folder as it is used: a plain absolute path that no link leads to."""
    folder = _absolute(folder, 'The folder').rstrip('/')
    if not folder or os.path.normpath(folder) != folder: raise Refused('the folder must be a plain absolute path, and not /')
    if os.path.realpath(folder) != folder: raise Refused('%s is reached through a link' % folder)
    return folder


def _free(io, folder):
    """Refuse a folder that is in use: it must not be there yet, or be an empty folder that nothing is mounted on."""
    if not os.path.lexists(folder): return
    if not os.path.isdir(folder): raise Refused('%s is there and is not a folder' % folder)
    if any(m.get('target') == folder for m in io.mounts()): raise Refused('something is mounted at %s already' % folder)
    if os.listdir(folder): raise Refused('%s is not empty: what is in it would be hidden by the mount' % folder)


def _systemctl(io, *args):
    return io.run(['systemctl'] + list(args), capture_output=True, text=True, timeout=180)


def _last(done):
    lines = [line.strip() for line in (done.stderr or '').splitlines() if line.strip()]
    return ' (%s)' % lines[-1] if lines else ''


def _row(io, identifier):
    """The one partition with this filesystem identifier that is present, checked for both verbs."""
    _, _, partitions = sr.survey(io)
    rows = [p for p in partitions if p['filesystemUuid'] == identifier]
    present = [p for p in rows if p['state'] != 'not present']
    if not present:
        raise Refused('this machine records the partition %s, and it is not present' % identifier if rows else
                      'this machine sees no filesystem with the identifier %s (see: isle storage partition list --table)' % identifier)
    if len(present) > 1: raise Refused('two partitions report the identifier %s (one is a copy of the other), so it does not name one partition' % identifier)
    return present[0]


def mount(io, identifier, asked):
    what = 'partition mount'
    if not sr.IDENTIFIER.match(identifier or ''): return sr._refuse(io, what, 'that is not a filesystem identifier')
    stage, unit = None, None
    try:
        row = _row(io, identifier)
        name = 'partition %s' % row['name']
        if row['system']: raise Refused('%s carries the running system of this machine' % name)
        if not row['deviceSerial'] or not row['deviceClass']:
            raise Refused('its device is not registered. First: sudo isle storage device register <serial> --class %s (the serial: isle storage device list --table)'
                          % '|'.join(sr.CLASSES))
        if not FILESYSTEM.fullmatch(row['filesystem'] or ''): raise Refused('%s holds no filesystem this verb can name' % name)
        folder = _folder(asked)
        unit = mount_unit_name(folder)
        text, path = unit_text(identifier, folder, row['filesystem']), Path(io.unit_dir) / unit
        ours = written(io, identifier)
        whole, mounted = sr.whole_mount(io.mounts(), identifier)
        if whole and whole['target'] == folder and ours == [(unit, folder)]:
            io.say('%s (%s) is mounted at %s by this verb already. Nothing was changed.' % (name[0].upper() + name[1:], identifier, folder)); return 0
        if mounted:
            where = sorted(m['target'] for m in io.mounts() if m.get('uuid') == identifier and isinstance(m.get('target'), str))
            raise Refused('%s is mounted already (%s), and not by a unit of this verb for that folder. This verb does not take over a mount' % (name, ', '.join(where)))
        elsewhere = [(n, f) for n, f in ours if n != unit]
        if elsewhere:
            raise Refused('it has a mount unit for %s already. First: sudo isle storage partition unmount %s' % (elsewhere[0][1] or elsewhere[0][0], identifier))
        named = in_fstab(io, identifier, folder)
        if named: raise Refused('%s. This verb does not write a second way to mount it' % named)
        if os.path.lexists(path) and (path.is_symlink() or path.read_text(errors='replace') != text):
            raise Refused('a unit for that folder exists (%s) and it is not the one this verb would write' % unit)
        _free(io, folder)
        if not io.is_root():
            io.say('This writes the mount unit %s and mounts %s at %s. Type: sudo isle storage partition mount %s --at %s' % (unit, name, folder, identifier, folder))
            return 1
        try: ready = _systemctl(io, '--version')
        except FileNotFoundError: raise Refused('systemctl is not on this machine, so there is nothing to keep a mount unit')
        if ready.returncode != 0: raise Refused('systemctl does not answer (exit %d)' % ready.returncode)
        with sr.locked(io):
            if sr.whole_mount(io.mounts(), identifier)[1]: raise Refused('%s was mounted while this command ran' % name)
            _write(str(path), text)
            stage = 'written'
            for step in (('daemon-reload',), ('enable', unit), ('start', unit)):
                done = _systemctl(io, *step)
                if done.returncode != 0: raise ValueError('systemctl %s ended with exit %d%s' % (step[0], done.returncode, _last(done)))
                if step[0] == 'enable': stage = 'enabled'
            now = sr.whole_mount(io.mounts(), identifier)[0]
            if not now or now['target'] != folder: raise ValueError('the unit started, and the partition does not show as mounted at %s' % folder)
    except (Refused, OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
        why = (str(exc) or exc.__class__.__name__).rstrip('.')
        if stage is None: return sr._refuse(io, what, why)
        io.say('FAILED: partition mount: %s. The unit %s is written%s, and the partition is not mounted by it. To try again, type the same command; '
               'to take the unit away: sudo isle storage partition unmount %s' % (why, unit, ' and enabled' if stage == 'enabled' else '', identifier))
        return 1
    io.say('%s (%s) is mounted at %s.' % (name[0].upper() + name[1:], identifier, folder))
    io.say('It is mounted again each time its device is present (unit %s). A start of this machine does not wait for it.' % unit)
    if row['recordedRoles']: io.say('See: isle storage partition check --table')
    else: io.say('It carries no role on this machine. To give it one: sudo isle storage partition assign %s --role <role>[,<role>...]' % identifier)
    return 0


def unmount(io, identifier, with_datasets=False):
    what = 'partition unmount'
    if not sr.IDENTIFIER.match(identifier or ''): return sr._refuse(io, what, 'that is not a filesystem identifier')
    typed = 'sudo isle storage partition unmount %s' % identifier
    stage, unit, folder = None, None, None
    try:
        ours = written(io, identifier)
        mounts = io.mounts()
        where = sorted(m['target'] for m in mounts if m.get('uuid') == identifier and isinstance(m.get('target'), str))
        if not ours:
            if where: raise Refused('it is mounted (%s), and not by a unit of this verb: by hand or by /etc/fstab. This verb leaves such a mount alone' % ', '.join(where))
            io.say('The filesystem %s has no mount unit of this verb and is not mounted. Nothing was changed.' % identifier); return 0
        if len(ours) > 1: raise Refused('more than one mount unit names it (%s): remove the one that is not wanted by hand' % ', '.join(n for n, _ in ours))
        unit, folder = ours[0]
        if not folder or mount_unit_name(folder) != unit: raise Refused('the unit %s does not read as this verb wrote it' % unit)
        datasets = sorted(d['dataset'] for d in io.datasets() if d['filesystemUuid'] == identifier)
        datasets += ['the storage slot %s' % slot for slot in storage_slots.bound_to(sr.read_records(io.records), identifier)]
        if datasets and not with_datasets:
            raise Refused('it holds: %s. What serves them is stopped when it is unmounted, and does not start until it is mounted at %s again. '
                          'To unmount it and let that stop: %s %s' % (', '.join(datasets), folder, typed, OPTION))
        if not io.is_root():
            io.say('This stops and removes the mount unit %s. Type: %s%s' % (unit, typed, ' ' + OPTION if with_datasets else '')); return 1
        with sr.locked(io):
            stage = 'stopping'
            done = _systemctl(io, 'stop', unit)
            if done.returncode != 0: raise ValueError('systemctl stop ended with exit %d%s' % (done.returncode, _last(done)))
            if any(m.get('uuid') == identifier and m.get('target') == folder for m in io.mounts()): raise ValueError('it still shows as mounted at %s' % folder)
            stage = 'stopped'
            done = _systemctl(io, 'disable', unit)
            if done.returncode != 0: raise ValueError('systemctl disable ended with exit %d%s' % (done.returncode, _last(done)))
            os.unlink(Path(io.unit_dir) / unit)
            stage = 'removed'
            done = _systemctl(io, 'daemon-reload')
            if done.returncode != 0: raise ValueError('systemctl daemon-reload ended with exit %d%s' % (done.returncode, _last(done)))
    except (Refused, OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
        why = (str(exc) or exc.__class__.__name__).rstrip('.')
        if stage is None: return sr._refuse(io, what, why)
        io.say('FAILED: partition unmount: %s. %s' % (why, {
            'stopping': 'The unit %s is kept and the partition may still be mounted. What depends on the mount may have been stopped.' % unit,
            'stopped': 'The partition is not mounted at %s any more; the unit %s is kept. To finish, type the same command.' % (folder, unit),
            'removed': 'The partition is not mounted and the unit %s is removed; systemd has not read that yet (a restart of the machine does it).' % unit}[stage]))
        return 1
    io.say('The filesystem %s is no longer mounted at %s, and the unit %s is removed. No data was changed.' % (identifier, folder, unit))
    io.say('The folder %s is left where it is.' % folder)
    if datasets: io.say('Held on it: %s. What serves them does not start until it is mounted at %s again.' % (', '.join(datasets), folder))
    return 0


def main(io, argv):
    if len(argv) == 5 and argv[:2] == ['partition', 'mount'] and argv[3] == '--at':
        return mount(io, argv[2], argv[4])
    words = [a for a in argv if a != OPTION]
    if len(words) == 3 and words[:2] == ['partition', 'unmount'] and len(argv) - len(words) <= 1:
        return unmount(io, words[2], with_datasets=OPTION in argv)
    io.say(sr.USAGE)
    return 2
