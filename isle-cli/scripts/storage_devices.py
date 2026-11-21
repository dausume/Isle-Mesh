#!/usr/bin/env python3
"""isle storage device list | isle storage partition list - read-only, for anyone.

What this machine has, by identity: a device by its hardware serial, a partition by its filesystem identifier.
Nothing is mounted, written or registered.  The declared device class and the roles come from the storage records
of this machine (storage_roles.py), which hands them in; this file never guesses them.
"""
from datetime import datetime, timezone
import json
import os
import subprocess
import sys

from storage_cli import LOCAL_ROOT, read_copies

COLUMNS = 'NAME,TYPE,SIZE,ROTA,TRAN,RM,HOTPLUG,RO,SERIAL,MODEL,FSTYPE,UUID,PARTUUID,LABEL,MOUNTPOINTS'
SYSTEM_MOUNTS = ('/', '/boot', '/boot/efi', '/usr', '/var', '/home', '[SWAP]')
NOT_A_FILESYSTEM = ('crypto_LUKS', 'LVM2_member', 'linux_raid_member', 'swap')
VERBS = {'device': ('list',), 'partition': ('list',)}
LATER = {'device': (), 'partition': ('create',)}
#: verbs of the same two words that storage_roles.py answers
RECORD_VERBS = {'device': ('register', 'retire', 'health'), 'partition': ('assign', 'check', 'retire')}


def read_tree(run=subprocess.run):
    result = run(['lsblk', '--json', '--bytes', '--output', COLUMNS], capture_output=True, text=True, check=True, timeout=15)
    tree = json.loads(result.stdout)
    if not isinstance(tree, dict) or not isinstance(tree.get('blockdevices'), list):
        raise ValueError('The block device report could not be read.')
    return tree


def _disks(tree):
    return [n for n in tree['blockdevices'] if isinstance(n, dict) and n.get('type') == 'disk']


def _all_mounts(node):
    """Every mount of the node and of what is stacked on it (encryption, volume groups)."""
    found = [m for m in (node.get('mountpoints') or []) if m]
    for child in node.get('children') or []:
        found += _all_mounts(child)
    return found


def _system(node):
    return any(m in SYSTEM_MOUNTS for m in _all_mounts(node))


def _medium(disk):
    if disk.get('rota') in (True, 1):
        return 'rotational'
    if disk.get('rota') in (False, 0):
        return 'nvme' if disk.get('tran') == 'nvme' else 'solid-state'
    return 'unknown'


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def devices(tree):
    rows = []
    for disk in _disks(tree):
        serial = _text(disk.get('serial'))
        rows.append(dict(
            name=str(disk.get('name', '')), serial=serial, model=_text(disk.get('model')),
            sizeBytes=int(disk.get('size') or 0), medium=_medium(disk), transport=_text(disk.get('tran')),
            hotplug=bool(disk.get('hotplug') or disk.get('rm')), readOnly=bool(disk.get('ro')), system=_system(disk),
            partitions=sum(1 for c in disk.get('children') or [] if c.get('type') == 'part'),
            wholeDeviceFilesystem=bool(disk.get('fstype')),
            identity='serial' if serial else 'none: no serial is reported, so this device cannot be told from another'))
    return rows


def local_datasets(root=LOCAL_ROOT):
    """Datasets bound on this machine, from the copies on the root filesystem: (rows, status)."""
    try:
        names = sorted(os.listdir(root))
    except FileNotFoundError:
        return [], 'none'
    except OSError:
        return [], 'not readable'
    rows = []
    for name in names:
        try:
            binding, _ = read_copies(root, name)
        except ValueError:
            continue
        if binding and isinstance(binding.get('filesystemUuid'), str):
            rows.append(dict(dataset=name, filesystemUuid=binding['filesystemUuid'],
                             relativePath=binding.get('relativePath'), hostApp=binding.get('hostApp')))
    return rows, 'read'


def _usage(path):
    stat = os.statvfs(path)
    return stat.f_blocks * stat.f_frsize, stat.f_bavail * stat.f_frsize


def partitions(tree, datasets=(), usage=_usage):
    rows = []
    for disk in _disks(tree):
        serial = _text(disk.get('serial'))
        parts = [(disk, True)] if disk.get('fstype') else []
        parts += [(c, False) for c in disk.get('children') or [] if c.get('type') == 'part']
        for node, whole in parts:
            fstype, uuid = _text(node.get('fstype')), _text(node.get('uuid'))
            mounts = [m for m in (node.get('mountpoints') or []) if isinstance(m, str) and m.startswith('/')]
            plain = bool(fstype) and fstype not in NOT_A_FILESYSTEM
            row = dict(name=str(node.get('name', '')), device=str(disk.get('name', '')), deviceSerial=serial,
                       wholeDevice=whole, sizeBytes=int(node.get('size') or 0), filesystem=fstype,
                       filesystemUuid=uuid if plain else None, partitionUuid=_text(node.get('partuuid')),
                       label=_text(node.get('label')), mount=min(mounts, key=len) if mounts else None,
                       readOnly=bool(node.get('ro')), system=_system(node),
                       state='no filesystem reported' if not fstype else 'not a plain filesystem' if not plain
                       else 'mounted' if mounts else 'not mounted',
                       datasets=sorted(d['dataset'] for d in datasets if plain and uuid and d['filesystemUuid'] == uuid))
            if plain and mounts:
                try:
                    row['totalBytes'], row['availableBytes'] = usage(row['mount'])
                except OSError:
                    row['capacityStatus'] = 'not readable'
            rows.append(row)
    return rows


def _size(count):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if count < 1000 or unit == 'TB':
            return ('%d %s' % (count, unit)) if unit == 'B' else ('%.1f %s' % (count, unit))
        count /= 1000.0


def table(kind, rows):
    if kind == 'device':
        head = ('DEVICE', 'SERIAL', 'CLASS', 'MODEL', 'SIZE', 'MEDIUM', 'BUS', 'HOTPLUG', 'SYSTEM', 'PARTITIONS')
        body = [(r['name'], r['serial'] or '(none)', r.get('class') or '-', r['model'] or '-', _size(r['sizeBytes']), r['medium'], r['transport'] or '-',
                 'yes' if r['hotplug'] else 'no', 'yes' if r['system'] else 'no',
                 'whole device' if r['wholeDeviceFilesystem'] else str(r['partitions'])) for r in rows]
    else:
        head = ('PARTITION', 'DEVICE', 'SIZE', 'FILESYSTEM', 'IDENTIFIER', 'ROLES', 'STATE', 'MOUNT', 'FREE', 'SYSTEM', 'DATASETS')
        body = [(r['name'], r['device'], _size(r['sizeBytes']), r['filesystem'] or '-', r['filesystemUuid'] or '-', ', '.join(r.get('roles') or ()) or '-', r['state'],
                 r['mount'] or '-', _size(r['availableBytes']) if 'availableBytes' in r else '-',
                 'yes' if r['system'] else 'no', ', '.join(r['datasets']) or '-') for r in rows]
    widths = [max(len(str(line[i])) for line in [head] + body) for i in range(len(head))]
    return '\n'.join('  '.join(str(cell).ljust(widths[i]) for i, cell in enumerate(line)).rstrip() for line in [head] + body)


def main(argv=None, *, run=subprocess.run, root=LOCAL_ROOT, usage=_usage, out=None, records=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    say = out or print
    plain = '--table' in argv
    words = [a for a in argv if a != '--table']
    kind, verb = (words + ['', ''])[:2]
    operation = 'storage.%s.%s' % (kind or 'device', verb or 'list')
    evaluated = datetime.now(timezone.utc).isoformat()

    def refuse(reason):
        say(json.dumps(dict(ok=False, evaluatedAt=evaluated, operation=operation, readOnly=True, error=reason)))
        return 1
    if kind not in VERBS or len(words) != 2:
        return refuse('Usage: isle storage device list [--table] | isle storage partition list [--table]')
    if verb in LATER[kind]:
        return refuse('isle storage %s %s is not in this CLI yet.' % (kind, verb))
    if verb in RECORD_VERBS[kind]:
        return refuse('isle storage %s %s is not a listing; storage_roles.py answers it.' % (kind, verb))
    if verb not in VERBS[kind]:
        return refuse('Unknown verb: isle storage %s %s. Known: %s.' % (kind, verb, ', '.join(VERBS[kind] + RECORD_VERBS[kind])))
    try:
        tree = read_tree(run)
        if kind == 'device':
            rows, result = devices(tree), {}
        else:
            datasets, status = local_datasets(root)
            rows, result = partitions(tree, datasets, usage), dict(datasetsStatus=status)
        note = records(kind, rows) if records else 'Read-only report. The class and the roles were not asked for.'
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
        return refuse(str(exc) or exc.__class__.__name__)
    if plain:
        say(table(kind, rows))
        return 0
    result.update({kind + 's': rows, 'note': note})
    say(json.dumps(dict(ok=True, evaluatedAt=evaluated, operation=operation, readOnly=True, result=result)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
