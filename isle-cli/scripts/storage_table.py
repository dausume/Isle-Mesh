#!/usr/bin/env python3
"""The partition table half of `isle storage partition create <serial> --whole`.

One thing only: give a device that has no partition table a GPT one with a single partition over the whole device,
whose partition identifier is chosen here, so the new partition can be found again by that identifier.

It never changes a table that is there, and it wipes nothing: an old filesystem that sits where the new partition
begins stays readable, and storage_create.py stops when it finds one.
"""
import subprocess

import storage_devices

TOOL, SETTLE = 'sfdisk', 'udevadm'
LINUX_FILESYSTEM = '0FC63DAF-8483-4772-8E79-3D69D8477DE4'      # the GPT type of a partition that holds a Linux filesystem
LOOKS = 20                                                      # how often the machine is asked for the new partition, a second apart


class NotStarted(Exception):
    """The table was not touched."""


def _run(io, path, identifier, rehearsal):
    command = [TOOL, '--quiet'] + (['--no-act'] if rehearsal else []) + ['--lock', '--wipe', 'never', '--wipe-partitions', 'never', path]
    return io.run(command, input='label: gpt\ntype=%s, uuid=%s\n' % (LINUX_FILESYSTEM, identifier), capture_output=True, text=True, timeout=600)


def last_line(done):
    lines = [line.strip() for line in (done.stderr or '').splitlines() if line.strip()]
    return ' (%s)' % lines[-1] if lines else ''


def rehearse(io, path, identifier):
    """The same call with --no-act, which writes nothing: a tool that is missing or refuses the call is found here."""
    try: done = _run(io, path, identifier, True)
    except FileNotFoundError: raise NotStarted('%s is not installed on this machine (package fdisk)' % TOOL)
    if done.returncode != 0: raise NotStarted('%s would not write the table: exit %d%s' % (TOOL, done.returncode, last_line(done)))


def write(io, path, identifier):
    done = _run(io, path, identifier, False)
    if done.returncode != 0: raise ValueError('%s ended with exit %d%s' % (TOOL, done.returncode, last_line(done)))


def appear(io, serial, identifier):
    """The node name of the new partition once this machine shows it, or None when it does not within the wait."""
    try: io.run([SETTLE, 'settle', '--timeout=30'], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError): pass          # the looks below wait as well
    for look in range(LOOKS):
        if look: io.sleep(1)
        for disk in storage_devices._disks(io.tree()):
            if storage_devices._text(disk.get('serial')) != serial: continue
            for child in disk.get('children') or []:
                if isinstance(child, dict) and (storage_devices._text(child.get('partuuid')) or '').lower() == identifier:
                    return str(child.get('name') or '')
    return None
