#!/usr/bin/env python3
"""isle storage partition create <serial>
   isle storage partition create <serial> --partition <partition-identifier> --role <role>[,<role>...] [--label <label>] [--expect <plan-id>]
   isle storage partition create <serial> --whole --role <role>[,<role>...] [--label <label>] [--expect <plan-id>]

Makes a filesystem with the marker of its roles at its root, and records it on this machine.  THIS ERASES.
  --partition: on one partition of the device that has no filesystem;
  --whole:     on a device that has no partition table and no filesystem: it gets a table with one partition over
               the whole device first.

It works in two steps, and only the second one writes:
  1. without --expect it reads the machine and prints the plan (what is made, what is lost) and the id of that plan;
  2. with --expect <that id>, as root, it reads the machine again and goes on only when the plan is still the same
     one, so what is done is what was read.

With the serial alone it says what the device holds and which of the two fits.

It never overwrites a filesystem, never changes a partition table that is there (adding a partition to one is not
built), and does not mount.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import uuid as uuid_module

from operations import atomic
import storage_devices
import storage_roles as sr
import storage_table

KIND = 'isle.storage.partition.create'
FILESYSTEM = 'ext4'
RESERVE = 1                     # percent of the blocks kept for root, where mkfs keeps 5 by default
MAKER, PROBER = 'mkfs.ext4', 'blkid'
LABEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,15}\Z')
PLAN_ID = re.compile(r'[0-9a-f]{64}\Z')
NODE = re.compile(r'[A-Za-z0-9]+\Z')
VALUED, FLAGS = ('--partition', '--role', '--label', '--expect'), ('--whole',)
NEVER = 'This verb never overwrites a filesystem'


class Refused(Exception):
    pass


def plan_id(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _text(value): return storage_devices._text(value)
def _size(value): return storage_devices._size(int(value or 0))


def _device(tree, records, serial):
    """The one disk with this serial, checked: (disk node of the block device report, recorded entry)."""
    disks = [d for d in storage_devices._disks(tree) if _text(d.get('serial')) == serial]
    if not disks: raise Refused('no storage device with the serial %s is present on this machine (see: isle storage device list --table)' % serial)
    if len(disks) > 1: raise Refused('two devices report the serial %s, so it does not name one device' % serial)
    known = records['devices'].get(serial)
    if not known:
        raise Refused('the device is not registered. First: sudo isle storage device register %s --class %s' % (shlex.quote(serial), '|'.join(sr.CLASSES)))
    if storage_devices._system(disks[0]): raise Refused('the device carries the running system of this machine')
    if disks[0].get('ro'): raise Refused('the device is read-only')
    return disks[0], known


def _under(disk):
    return [c for c in disk.get('children') or [] if isinstance(c, dict)]


def _empty(node):
    """True when the block device report shows nothing on a partition: no filesystem, nothing stacked, no mount."""
    return node.get('type') == 'part' and not node.get('fstype') and not node.get('children') and not storage_devices._all_mounts(node) and not node.get('ro')


def _free(disk):
    """The partitions of a device that --partition can take: they have an identifier and show nothing on them."""
    return [c for c in _under(disk) if _empty(c) and _text(c.get('partuuid'))]


def _named(node):
    return '%s (%s, partition identifier %s)' % (node.get('name'), _size(node.get('size')), _text(node.get('partuuid')))


def _everywhere(tree, identifier):
    """How many nodes of the whole report carry this partition identifier (a block copy carries it twice)."""
    def walk(nodes):
        return sum((_text(n.get('partuuid')) == identifier) + walk(n.get('children') or []) for n in nodes if isinstance(n, dict))
    return walk(tree['blockdevices'])


def _name(node, what):
    name = str(node.get('name') or '')
    if not NODE.match(name): raise Refused('the %s has no name this CLI can use' % what)
    return name


def _holds(name, node):
    """Why a partition cannot take a new filesystem, or None."""
    if node.get('fstype'): return 'partition %s holds %s%s. %s' % (name, node['fstype'], ' (identifier %s)' % node['uuid'] if node.get('uuid') else '', NEVER)
    if node.get('children'): return 'something is stacked on partition %s (%s)' % (name, ', '.join(str(c.get('name')) for c in node['children'] if isinstance(c, dict)))
    if storage_devices._all_mounts(node): return 'partition %s is in use (%s)' % (name, ', '.join(storage_devices._all_mounts(node)))
    if node.get('ro'): return 'partition %s is read-only' % name
    return None


def bare(disk):
    """The names of what the system shows under a device that has no partition table and no filesystem (a disk filled
    with a test pattern can show partitions that are not there).  Refused when the device is not such a device."""
    name = _name(disk, 'device')
    if disk.get('fstype'): raise Refused('the device holds %s directly, with no partition table. %s' % (disk['fstype'], NEVER))
    if storage_devices._all_mounts(dict(disk, children=[])): raise Refused('the device is in use (%s)' % ', '.join(storage_devices._all_mounts(dict(disk, children=[]))))
    real = [c for c in _under(disk) if _text(c.get('partuuid'))]
    if disk.get('pttype') or real:
        free = '; '.join(_named(c) for c in _free(disk))
        raise Refused('the device has a partition table%s with %s. --whole is for a device without one, and adding a partition to a table is not built.%s' % (
            ' (%s)' % disk['pttype'] if disk.get('pttype') else '', '1 partition' if len(real) == 1 else '%d partitions' % len(real),
            ' For a partition of it that has no filesystem, --partition: %s' % free if free else ''))
    shown = []
    for child in _under(disk):
        if child.get('type') != 'part': raise Refused('something is stacked on the device (%s)' % child.get('name'))
        shown.append(_name(child, 'partition'))
        if _holds(shown[-1], child): raise Refused(_holds(shown[-1], child))
    return name, shown


def read(io, serial, identifier, roles, label):
    """Read the machine and build the plan: (plan, names).  identifier None is --whole.  Refused when it cannot be one."""
    records = sr.read_records(io.records)
    tree = io.tree()
    disk, known = _device(tree, records, serial)
    plan = dict(kind=KIND, version=1, deviceSerial=serial, deviceModel=_text(disk.get('model')), deviceSizeBytes=int(disk.get('size') or 0),
                deviceClass=known['class'], filesystem=FILESYSTEM, reservePercent=RESERVE, roles=list(roles), label=label)
    if plan['deviceSizeBytes'] <= 0: raise Refused('the device reports no size')
    if identifier is None:
        name, shown = bare(disk)
        plan.update(layout='whole-device', shownPartitionSizes=[int(c.get('size') or 0) for c in _under(disk)])
        return plan, dict(disk=name, shown=shown, partition=None)
    found = [c for c in _under(disk) if c.get('type') == 'part' and _text(c.get('partuuid')) == identifier]
    if not found:
        raise Refused('the device has no partition with the identifier %s. Its partitions with no filesystem: %s' % (
            identifier, '; '.join(_named(c) for c in _free(disk)) or 'none'))
    if len(found) > 1 or _everywhere(tree, identifier) > 1:
        raise Refused('two partitions report the identifier %s (one is a copy of the other), so it does not name one partition' % identifier)
    name = _name(found[0], 'partition')
    if _holds(name, found[0]): raise Refused(_holds(name, found[0]))
    size = int(found[0].get('size') or 0)
    if size <= 0: raise Refused('partition %s reports no size' % name)
    plan.update(layout='existing-partition', partitionUuid=identifier, partitionSizeBytes=size)
    return plan, dict(disk=_name(disk, 'device'), shown=[], partition=name)


def probe(io, path):
    """What is at a place on the disk itself, read from the disk: {} when nothing is recognised there."""
    try: done = io.run([PROBER, '--probe', '--output', 'export', path], capture_output=True, text=True, timeout=60)
    except FileNotFoundError: raise Refused('%s is not installed on this machine (package util-linux)' % PROBER)
    found = dict(line.split('=', 1) for line in (done.stdout or '').splitlines() if '=' in line)
    found.pop('DEVNAME', None)
    # On a partition blkid also reports the partition's own entry in the table (PART_ENTRY_*): that is the partition itself,
    # never something written inside it, so it is not what this probe looks for.
    entry = [key for key in found if key.startswith('PART_ENTRY_')]
    for key in entry: found.pop(key)
    if done.returncode == 2 and not found: return {}
    if done.returncode == 0 and entry and not found: return {}
    if done.returncode not in (0, 2) or not found:
        raise Refused('the disk could not be probed (%s ended with exit %d)' % (PROBER, done.returncode))
    return found


def _shows(found):
    return ', '.join('%s=%s' % pair for pair in sorted(found.items()))


def make(io, path, identifier, label, marker):
    """One mkfs call: the filesystem with the chosen identifier, and the marker at its root from the first moment."""
    with tempfile.TemporaryDirectory(prefix='isle-partition-') as folder:
        root = Path(folder) / 'root'
        root.mkdir(mode=0o755)
        os.chmod(root, 0o755)                   # the root folder of the new filesystem takes this mode
        fd = os.open(root / sr.MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, 'w') as out:
            os.fchmod(out.fileno(), 0o644); out.write(json.dumps(marker, indent=1, sort_keys=True) + '\n')
        # mkfs.ext4 does not ask before it overwrites when no terminal is attached, so it is no second guard:
        # probe() just before this call is the guard.
        command = [MAKER, '-q', '-t', FILESYSTEM, '-m', str(RESERVE), '-U', identifier] + (['-L', label] if label else []) + ['-d', str(root), path]
        try: return io.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=3600)
        except FileNotFoundError: raise Refused('%s is not installed on this machine (package e2fsprogs)' % MAKER)


def _typed(serial, identifier, asked, label):
    return 'isle storage partition create %s %s --role %s%s' % (shlex.quote(serial), '--partition %s' % identifier if identifier else '--whole', asked,
                                                                ' --label %s' % label if label else '')


def candidates(io, serial):
    what = 'partition create'
    if not sr.SERIAL.match(serial or ''): return sr._refuse(io, what, 'that is not a hardware serial')
    try:
        disk, known = _device(io.tree(), sr.read_records(io.records), serial)
        try: shown, whole = bare(disk)[1], None
        except Refused as exc: shown, whole = [], str(exc)
    except Refused as exc: return sr._refuse(io, what, str(exc).rstrip('.'))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
        return sr._refuse(io, what, str(exc) or exc.__class__.__name__)
    typed = 'isle storage partition create %s' % shlex.quote(serial)
    io.say('Device %s (%s, %s, %s).' % (serial, _text(disk.get('model')) or 'no model reported', _size(disk.get('size')), known['class']))
    if whole is None:
        io.say('It has no partition table and no filesystem that this machine recognises.' + (
            ' The system shows %d partitions on it that have no identifier; a disk filled with a test pattern looks like that.' % len(shown) if shown else ''))
        io.say('To read the plan for one partition over the whole device: %s --whole --role <role>[,<role>...]' % typed)
        return 0
    free = _free(disk)
    io.say('%d of its %d partitions have no filesystem%s' % (len(free), len(_under(disk)), ':' if free else '.'))
    for node in free: io.say('  ' + _named(node))
    if free: io.say('To read the plan for one of them: %s --partition <partition-identifier> --role <role>[,<role>...]' % typed)
    else: io.say('Nothing can be made here: %s.' % whole.rstrip('.'))
    return 0


def _plan_lines(io, plan, names, serial, identifier, wanted, label):
    device = 'device %s (%s, %s, %s)' % (serial, plan['deviceModel'] or 'no model reported', _size(plan['deviceSizeBytes']), plan['deviceClass'])
    labelled = ', labelled %s' % label if label else ''
    if identifier:
        io.say('Plan: make a filesystem on partition %s of %s.' % (names['partition'], device))
        io.say('  The partition: %s, partition identifier %s. This machine reports no filesystem on it; the partition itself is probed again before '
               'anything is written.' % (_size(plan['partitionSizeBytes']), identifier))
        io.say('  What is made: one %s filesystem over the whole partition (%d%% kept in reserve)%s, with the marker of %s at its root.' % (
            FILESYSTEM, RESERVE, labelled, sr._words(wanted)))
        io.say('  What is lost: everything that is on that partition now. No other partition of the device is touched.')
    else:
        io.say('Plan: give %s a partition table with one partition over the whole device, and make a filesystem on it.' % device)
        io.say('  The device: this machine reports no partition table and no filesystem on it%s; the device itself is probed again before anything is '
               'written.' % ('. The system shows %d partitions without an identifier, as a disk filled with a test pattern does' % len(names['shown'])
                             if names['shown'] else ''))
        io.say('  What is made: a GPT partition table, one partition, and on it one %s filesystem (%d%% kept in reserve)%s, with the marker of %s at its '
               'root.' % (FILESYSTEM, RESERVE, labelled, sr._words(wanted)))
        io.say('  What is lost: everything that is on the device now. One thing is kept: when the new partition shows a filesystem from before, the '
               'verb stops there and does not overwrite it.')
    io.say('  Afterwards: this machine records the new filesystem with %s. It is not mounted by this verb.' % sr._words(wanted))


def create(io, serial, identifier, asked, label=None, expect=None):
    what = 'partition create'
    wanted = (asked or '').split(',')
    if any(r not in sr.ROLES for r in wanted) or len(set(wanted)) != len(wanted):
        return sr._refuse(io, what, 'the role must be one of: %s (for more than one, put commas between them)' % ', '.join(sr.ROLES))
    if not sr.SERIAL.match(serial or ''): return sr._refuse(io, what, 'that is not a hardware serial')
    if identifier is not None and not sr.IDENTIFIER.match(identifier): return sr._refuse(io, what, 'that is not a partition identifier')
    if label is not None and not LABEL.match(label): return sr._refuse(io, what, 'a label is 1 to 16 letters, digits, dots, dashes or underscores')
    if expect is not None and not PLAN_ID.match(expect): return sr._refuse(io, what, 'that is not a plan id')
    typed = _typed(serial, identifier, asked, label)
    stage, name, new_partition, fresh = None, None, None, None
    try:
        plan, names = read(io, serial, identifier, wanted, label)
        ident = plan_id(plan)
        if expect is None:
            _plan_lines(io, plan, names, serial, identifier, wanted, label)
            io.say('Plan id: %s' % ident)
            io.say('To do it, type: sudo %s --expect %s' % (typed, ident))
            return 0
        if ident != expect:
            return sr._refuse(io, what, 'the plan is not the one with that id: the machine, or what was typed, differs from what was read then. Read it again: %s' % typed)
        if not io.is_root():
            io.say('This erases a %s. Type: sudo %s --expect %s' % ('partition' if identifier else 'device', typed, expect)); return 1
        at = io.now()
        with sr.locked(io):
            plan, names = read(io, serial, identifier, wanted, label)       # read again, now that the lock is held
            if plan_id(plan) != expect: return sr._refuse(io, what, 'the machine changed while this command ran. Read the plan again: %s' % typed)
            name = names['partition']
            if identifier is None:
                for shown in [names['disk']] + names['shown']:
                    seen = probe(io, io.node(shown))
                    if seen: return sr._refuse(io, what, '%s is not empty: the disk itself shows %s there, which this machine did not report. %s' % (shown, _shows(seen), NEVER))
                new_partition = str(uuid_module.uuid4())
                try: storage_table.rehearse(io, io.node(names['disk']), new_partition)
                except storage_table.NotStarted as exc: raise Refused(str(exc))
                stage = 'table'
                storage_table.write(io, io.node(names['disk']), new_partition)
                stage = 'partition'
                name = storage_table.appear(io, serial, new_partition)
                if name is None: raise ValueError('this machine does not show the new partition yet')
                if not NODE.match(name): raise ValueError('the new partition has no name this CLI can use')
            seen = probe(io, io.node(name))
            if seen:
                if stage is None: return sr._refuse(io, what, 'partition %s is not empty: the disk itself shows %s there, which this machine did not report' % (name, _shows(seen)))
                raise ValueError('the new partition shows %s, which was on the disk before. %s' % (_shows(seen), NEVER))
            fresh = str(uuid_module.uuid4())
            if fresh in sr.read_records(io.records)['partitions']: raise ValueError('the new identifier is already recorded')
            marker = sr.with_roles(dict(kind=sr.MARKER_KIND, version=1, filesystemUuid=fresh, deviceSerial=serial, assignedAt=at, assignedOn=io.machine()), wanted)
            before, stage = stage, 'filesystem'
            try: done = make(io, io.node(name), fresh, label, marker)
            except Refused: stage = before; raise                           # the tool is missing: no filesystem was started
            if done.returncode != 0: raise ValueError('%s ended with exit %d%s' % (MAKER, done.returncode, storage_table.last_line(done)))
            after = probe(io, io.node(name))                                 # once something was written, no failure is a refusal (see below)
            if after.get('UUID') != fresh or after.get('TYPE') != FILESYSTEM: raise ValueError('the new filesystem does not read back as it was made')
            stage = 'made'
            records = sr.read_records(io.records)
            records['partitions'][fresh] = sr.with_roles(dict(deviceSerial=serial, assignedAt=at), wanted)
            atomic(Path(io.records), records)
    except (Refused, OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
        why = str(exc) or exc.__class__.__name__
        if stage is None: return sr._refuse(io, what, why.rstrip('.'))
        io.say('FAILED: partition create: %s. %s' % (why.rstrip('.'), _left(stage, serial, name, new_partition, fresh, wanted, asked, label)))
        return 1
    if new_partition: io.say('Device %s now has a GPT partition table with one partition, %s (partition identifier %s).' % (serial, name, new_partition))
    io.say('Partition %s of device %s now holds one %s filesystem, identifier %s. What was there before is gone.' % (name, serial, FILESYSTEM, fresh))
    io.say('It carries %s: the marker is at its root and this machine records it.' % sr._words(wanted))
    io.say('It is not mounted. See: isle storage partition check --table')
    return sr._lacks(io, wanted)


def _left(stage, serial, name, new_partition, fresh, wanted, asked, label):
    """What state the device is in after a failure, said exactly: "nothing was changed" would be untrue from here on."""
    if stage == 'table':
        return ('Writing the partition table of device %s had started, so the device may hold a part of one. No filesystem was made and this machine '
                'records nothing for it. See: isle storage partition create %s' % (serial, shlex.quote(serial)))
    if stage == 'partition':
        return ('Device %s now has a partition table with one partition (partition identifier %s). No filesystem was made and this machine records '
                'nothing for it. When this machine shows that partition with no filesystem (unplug the device and plug it in again, or restart), go on '
                'with: %s' % (serial, new_partition, _typed(serial, new_partition, asked, label)))
    if stage == 'filesystem':
        return ('Making the filesystem on partition %s had started, so what was on it is lost and it may hold a part of a filesystem. This machine '
                'records nothing for it. See: isle storage partition list --table' % name)
    return ('The filesystem was made on partition %s (identifier %s) and the marker of %s is at its root, but this machine does not record it. Once it '
            'is mounted: sudo isle storage partition assign %s --role %s' % (name, fresh, sr._words(wanted), fresh, asked))


def main(io, argv):
    words, options, index = [], {}, 0
    while index < len(argv):
        word = argv[index]
        if word in VALUED:
            if word in options or index + 1 >= len(argv): words = None; break
            options[word] = argv[index + 1]; index += 2
        elif word in FLAGS:
            if word in options: words = None; break
            options[word] = True; index += 1
        else:
            words.append(word); index += 1
    if words and len(words) == 3 and words[:2] == ['partition', 'create']:
        if not options: return candidates(io, words[2])
        if '--role' in options and ('--partition' in options) != ('--whole' in options):
            return create(io, words[2], options.get('--partition'), options['--role'], options.get('--label'), options.get('--expect'))
    io.say(sr.USAGE)
    return 2
