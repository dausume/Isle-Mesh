#!/usr/bin/env python3
"""Inventory only the public repository, serialized with its publishers."""
from pathlib import Path
import sys
from repository_lock import publication_lock
from storage_snapshot import encoded, inventory


def snapshot(repo):
    with publication_lock(repo):
        return encoded(inventory(str(repo)))


if __name__ == '__main__':
    try:
        if len(sys.argv) != 1:
            raise ValueError('Usage: sudo isle apt-repo snapshot (JSON to stdout)')
        sys.stdout.buffer.write(snapshot(Path('/etc/isle-mesh/apt/repo')))
    except (OSError, ValueError) as exc:
        print('repository snapshot: ' + str(exc), file=sys.stderr)
        sys.exit(1)
