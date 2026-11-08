#!/usr/bin/env python3
"""Shared read-only storage operations for operators and placement clients."""
import argparse
from datetime import datetime, timezone
import json
import subprocess
import sys

from resource_snapshot import storage_inventory
from storage_preflight import inspect


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list', help='Report disks and mounted volumes without mounting or writing')
    check = commands.add_parser('check', help='Validate a local destination against an exact filesystem UUID')
    check.add_argument('--path', required=True)
    check.add_argument('--uuid', required=True)
    check.add_argument('--required-bytes', type=int, default=0)
    args = parser.parse_args(argv)
    evaluated = datetime.now(timezone.utc).isoformat()
    try:
        if args.command == 'list':
            result = subprocess.run(
                ['lsblk', '--json', '--bytes', '--output',
                 'NAME,TYPE,SIZE,ROTA,TRAN,MOUNTPOINTS,MAJ:MIN,UUID,FSTYPE,RO'],
                capture_output=True, text=True, check=True, timeout=15)
            tree = json.loads(result.stdout)
            data = storage_inventory(tree)
            # Retain unmounted partitions and their UUIDs. A disk observation
            # does not authorize mounting, formatting or using its contents.
            data['blockDevices'] = tree.get('blockdevices', [])
        else:
            data = inspect(args.path, args.uuid, args.required_bytes)
        print(json.dumps(dict(ok=True, evaluatedAt=evaluated, operation='storage.' + args.command,
                              readOnly=True, result=data)))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(json.dumps(dict(ok=False, evaluatedAt=evaluated, operation='storage.' + args.command,
                              readOnly=True, error=str(exc))))
        return 1


if __name__ == '__main__':
    sys.exit(main())
