"""Mount gate rendering for a hosting app (stm-4): the systemd unit text and the compose bind fragment.
Pure text and dict builders.  Nothing is written here; the installer slice writes the files.
See docs/STORAGE-MIGRATION-CONTRACT.md section 4.
"""
import re

from storage_contract import DATASET

SAFE_PATH = re.compile(r'/[A-Za-z0-9_.+@:/-]*\Z')
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z')
PARAM = re.compile(r'[A-Z][A-Z0-9_]{0,63}\Z')
# The system CLI entry point: install-node-services.sh creates it (and dns-reconcile.sh calls it); the deb installs
# no /usr/bin/isle.  Units written with /usr/bin/isle failed 203/EXEC on pol-core and the core (phase A, 2026-11-14).
ISLE_CLI = '/usr/local/bin/isle'
APPS_DIR = '/etc/isle-mesh/apps'   # app-deploy.sh APPS_DIR: docker-compose.yml + isle-overlay.yml per instance
UNIT_SAFE = frozenset('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:_.')


def _absolute(path, name):
    """Return path when it is absolute, made of safe characters and free of '..' components."""
    if not isinstance(path, str) or not SAFE_PATH.match(path) or '..' in path.split('/'):
        raise ValueError('%s must be an absolute path of letters, digits and _.+@:/- without "..".' % name)
    return path


def mount_unit_name(path):
    """Return the systemd mount unit name of an absolute path, like `systemd-escape --path --suffix=mount`.
    '/mnt/pol-ssd' gives 'mnt-pol\\x2dssd.mount'; '/' gives '-.mount'."""
    _absolute(path, 'Mount path')
    parts = [part for part in path.split('/') if part]
    if not parts:
        return '-.mount'
    text = '/'.join(parts)
    out = []
    for index, char in enumerate(text):
        if char == '/':
            out.append('-')
        elif char in UNIT_SAFE and not (index == 0 and char == '.'):
            out.append(char)
        else:
            out.extend('\\x%02x' % byte for byte in char.encode('utf-8'))
    return ''.join(out) + '.mount'


def unit_text(dataset, mountpoint, dataset_path, app, compose_project):
    """Return the text of isle-storage-<dataset>.service: the gate that stops the host with the disk."""
    if not isinstance(dataset, str) or not DATASET.match(dataset):
        raise ValueError('Dataset must match [a-z0-9][a-z0-9-]{0,63}.')
    _absolute(mountpoint, 'Mountpoint')
    _absolute(dataset_path, 'Dataset path')
    if not (dataset_path + '/').startswith(mountpoint.rstrip('/') + '/') or dataset_path.rstrip('/') == mountpoint.rstrip('/'):
        raise ValueError('Dataset path must lie below the mountpoint.')
    for value, name in ((app, 'App'), (compose_project, 'Compose project')):
        if not isinstance(value, str) or not NAME.match(value):
            raise ValueError('%s must match [A-Za-z0-9][A-Za-z0-9_.-]*.' % name)
    unit = mount_unit_name(mountpoint)
    path = dataset_path.rstrip('/')
    return '\n'.join([
        '[Unit]',
        'Description=Isle storage gate and host for dataset %s (%s)' % (dataset, app),
        'RequiresMountsFor=' + mountpoint,
        'BindsTo=' + unit,
        'After=' + unit,
        'AssertPathExists=%s/binding.json' % path,
        '',
        '[Service]',
        'Type=oneshot',
        'RemainAfterExit=yes',
        'ExecStartPre=%s storage check --dataset %s' % (ISLE_CLI, dataset),
        # The same compose invocation as app-deploy.sh: from the app dir, both files, the bind parameter in the
        # environment (docker-compose.yml interpolates ${ISLE_DATASET_PATH}), offline.  Phase A 2026-11-14: without
        # -f/WorkingDirectory compose exited 14 "no configuration file provided" and the host would not survive a reboot.
        'WorkingDirectory=%s/%s' % (APPS_DIR, app),
        'Environment=ISLE_DATASET_PATH=' + path,
        'ExecStart=/usr/bin/docker compose -p %s -f docker-compose.yml -f isle-overlay.yml up -d --no-build --pull never' % compose_project,
        'ExecStop=/usr/bin/docker compose -p %s -f docker-compose.yml -f isle-overlay.yml stop' % compose_project,
        '',
        '[Install]',
        'WantedBy=multi-user.target',
        ''])


def overlay_bind(param, verified_path):
    """Return the compose long-syntax bind fragment for a dataset path that `storage check` has proven.
    The disk must be mounted for the bind to work: create_host_path is false.  Mounted read-only at /data."""
    if not isinstance(param, str) or not PARAM.match(param):
        raise ValueError('Bind parameter must look like ISLE_DATASET_PATH.')
    _absolute(verified_path, 'Verified path')
    return {'type': 'bind', 'source': verified_path, 'target': '/data', 'read_only': True,
            'bind': {'create_host_path': False}}


def sync_unit_names(dataset):
    """Return (service name, timer name) of the sync timer pair for a dataset."""
    if not isinstance(dataset, str) or not DATASET.match(dataset):
        raise ValueError('Dataset must match [a-z0-9][a-z0-9-]{0,63}.')
    return 'isle-storage-sync@%s.service' % dataset, 'isle-storage-sync@%s.timer' % dataset


def sync_timer_text(dataset):
    """Return (service_text, timer_text) of isle-storage-sync@<dataset>.service and .timer (contract section 2.5).
    The timer fires 5 minutes after boot and 10 minutes after each run; the service runs `isle storage sync` as root."""
    service_name, _ = sync_unit_names(dataset)
    service = '\n'.join([
        '[Unit]',
        'Description=Isle storage replica sync for dataset %s' % dataset,
        '',
        '[Service]',
        'Type=oneshot',
        'User=root',
        'ExecStart=%s storage sync --dataset %s' % (ISLE_CLI, dataset),
        ''])
    timer = '\n'.join([
        '[Unit]',
        'Description=Isle storage replica sync timer for dataset %s' % dataset,
        '',
        '[Timer]',
        'OnBootSec=5min',
        'OnUnitActiveSec=10min',
        'Persistent=true',
        'Unit=' + service_name,
        '',
        '[Install]',
        'WantedBy=timers.target',
        ''])
    return service, timer


def default_systemctl_is_enabled(name):
    """True when `systemctl is-enabled <name>` succeeds."""
    import subprocess
    try:
        return subprocess.run(['systemctl', 'is-enabled', '--quiet', name], timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def sync_timer_installed(dataset, unit_dir, systemctl_is_enabled=default_systemctl_is_enabled):
    """True when both unit files are present in unit_dir and the timer is enabled (injectable systemctl_is_enabled(name))."""
    import os
    service_name, timer_name = sync_unit_names(dataset)
    if not all(os.path.isfile(os.path.join(str(unit_dir), name)) for name in (service_name, timer_name)):
        return False
    return bool(systemctl_is_enabled(timer_name))
