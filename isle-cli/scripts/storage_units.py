#!/usr/bin/env python3
"""isle storage units check [--dataset D]   read-only, for anyone
   isle storage units apply --dataset D [--overrides keep|remove]   root, a journaled operation

The three systemd units of a dataset bound on this machine (the gate that hosts it, the sync service, the sync
timer) against the text this CLI writes today.  A package update does not rewrite units that a migration wrote
earlier; `check` says whether they still match and `apply` rewrites them.  Neither starts, stops nor restarts
the host: a changed gate unit is used from its next start.
"""
import argparse
from datetime import datetime, timezone
import json
import os
import subprocess
import sys

import storage_mount
from storage_cli import LOCAL_ROOT, read_copies
from storage_contract import DATASET

UNIT_DIR = '/etc/systemd/system'
NEEDED = ('dataset', 'mountpoint', 'relativePath', 'hostApp')


def _systemctl(args):
    try:
        return subprocess.run(['systemctl'] + list(args), capture_output=True, timeout=60).returncode
    except (OSError, subprocess.SubprocessError):
        return 1


def bound_datasets(root=LOCAL_ROOT):
    try:
        return sorted(n for n in os.listdir(root) if DATASET.match(n) and os.path.isfile(os.path.join(root, n, 'binding.json')))
    except FileNotFoundError:
        return []


def expected(binding):
    """Return {unit file name: text} for a binding record, exactly as the migration writes them."""
    missing = [k for k in NEEDED if not isinstance(binding.get(k), str) or not binding[k]]
    if missing:
        raise ValueError('The binding record lacks: ' + ', '.join(missing) + '.')
    dataset, mount, app = binding['dataset'], binding['mountpoint'], binding['hostApp']
    relative = binding['relativePath'].strip('/')
    if not relative or '..' in relative.split('/'):
        raise ValueError('The binding record has no usable relative path.')
    gate = storage_mount.unit_text(dataset, mount, mount.rstrip('/') + '/' + relative, app, 'isle-' + app)
    names, texts = storage_mount.sync_unit_names(dataset), storage_mount.sync_timer_text(dataset)
    return {'isle-storage-%s.service' % dataset: gate, names[0]: texts[0], names[1]: texts[1]}


def _override_dirs(name, unit_dir):
    dirs = [os.path.join(unit_dir, name + '.d')]
    if '@' in name:  # a drop-in on the template applies to every instance
        stem, suffix = name.split('@', 1)[0], name.rsplit('.', 1)[1]
        dirs.append(os.path.join(unit_dir, '%s@.%s.d' % (stem, suffix)))
    return dirs


def overrides(name, unit_dir):
    found = []
    for folder in _override_dirs(name, unit_dir):
        try:
            found += [os.path.join(folder, entry) for entry in sorted(os.listdir(folder))]
        except (FileNotFoundError, NotADirectoryError):
            pass
    return found


def _state(path, text):
    if os.path.islink(path):
        return 'a link, not a file'
    if not os.path.exists(path):
        return 'missing'
    if not os.path.isfile(path):
        return 'not a file'
    with open(path, encoding='utf-8', errors='replace') as handle:
        return 'matches' if handle.read(1 << 20) == text else 'differs'


def check(dataset, *, root=LOCAL_ROOT, unit_dir=UNIT_DIR, systemctl=_systemctl):
    """Return (ok, document) for one dataset.  Reads only."""
    if not isinstance(dataset, str) or not DATASET.match(dataset):
        raise ValueError('Dataset must match [a-z0-9][a-z0-9-]{0,63}.')
    binding, _ = read_copies(root, dataset)
    if not binding:
        return False, dict(dataset=dataset, bound=False, reason='This machine holds no binding for the dataset.')
    if binding.get('dataset') != dataset:
        raise ValueError('The binding record names another dataset.')
    texts = expected(binding)
    gate, service, timer = list(texts)
    units = []
    for name, text in texts.items():
        row = dict(unit=name, state=_state(os.path.join(unit_dir, name), text), overrides=overrides(name, unit_dir))
        if name != service:  # the sync service is started by its timer and is never enabled itself
            row['enabled'] = systemctl(['is-enabled', '--quiet', name]) == 0
        units.append(row)
    ok = all(u['state'] == 'matches' and not u['overrides'] and u.get('enabled', True) for u in units)
    return ok, dict(dataset=dataset, bound=True, unitDir=str(unit_dir), units=units,
                    gateActive=systemctl(['is-active', '--quiet', gate]) == 0,
                    timerActive=systemctl(['is-active', '--quiet', timer]) == 0)


def _write(path, text):
    temp = '%s.tmp-%d' % (path, os.getpid())
    with open(temp, 'w', encoding='utf-8') as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp, 0o644)
    os.replace(temp, path)  # replaces a link itself, never what it points at


def apply(dataset, *, drop_overrides=False, root=LOCAL_ROOT, unit_dir=UNIT_DIR, systemctl=_systemctl, euid=os.geteuid):
    """Rewrite the units that do not match, reload, enable.  Returns (ok, document)."""
    if euid() != 0:
        raise PermissionError('Run this with sudo: sudo isle storage units apply --dataset %s' % dataset)
    ok, before = check(dataset, root=root, unit_dir=unit_dir, systemctl=systemctl)
    if not before.get('bound'):
        return False, before
    binding, _ = read_copies(root, dataset)
    texts = expected(binding)
    written, removed, kept = [], [], []
    for row in before['units']:
        path = os.path.join(unit_dir, row['unit'])
        if row['state'] == 'not a file':
            raise ValueError('%s is neither a file nor a link; it is left alone.' % path)
        if row['state'] != 'matches':
            _write(path, texts[row['unit']])
            written.append(row['unit'])
        for extra in row['overrides']:
            if not drop_overrides:
                kept.append(extra)
            elif extra not in removed:
                if os.path.isdir(extra) and not os.path.islink(extra):
                    raise ValueError('%s is a folder; it is left alone.' % extra)
                os.unlink(extra)
                removed.append(extra)
    for folder in sorted({os.path.dirname(p) for p in removed}):
        try:
            os.rmdir(folder)
        except OSError:
            pass
    gate, _, timer = list(texts)
    for args in (['daemon-reload'], ['enable', gate], ['enable', '--now', timer]):
        code = systemctl(args)
        if code != 0:
            raise RuntimeError('systemctl %s failed (exit %s). The unit files are written; run the verb again.' % (' '.join(args), code))
    ok, after = check(dataset, root=root, unit_dir=unit_dir, systemctl=systemctl)
    after.update(written=written, overridesRemoved=removed, overridesKept=kept,
                 note='The host was not restarted. A rewritten gate unit is used from its next start.' if written else None)
    return ok, after


def main(argv=None, *, root=LOCAL_ROOT, unit_dir=None, systemctl=_systemctl, euid=os.geteuid, out=print):
    unit_dir = unit_dir or os.environ.get('ISLE_STORAGE_UNIT_DIR') or UNIT_DIR
    parser = argparse.ArgumentParser(prog='isle storage units', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('check').add_argument('--dataset')
    changing = commands.add_parser('apply')
    changing.add_argument('--dataset', required=True)
    changing.add_argument('--overrides', choices=('keep', 'remove'), default='keep')
    args = parser.parse_args(argv)
    evaluated = datetime.now(timezone.utc).isoformat()
    operation = 'storage.units.' + args.command
    read_only = args.command == 'check'
    try:
        if args.command == 'apply':
            ok, result = apply(args.dataset, drop_overrides=args.overrides == 'remove', root=root, unit_dir=unit_dir,
                               systemctl=systemctl, euid=euid)
        elif args.dataset:
            ok, result = check(args.dataset, root=root, unit_dir=unit_dir, systemctl=systemctl)
        else:
            rows = [check(name, root=root, unit_dir=unit_dir, systemctl=systemctl) for name in bound_datasets(root)]
            ok, result = all(good for good, _ in rows), dict(datasets=[row for _, row in rows])
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        out(json.dumps(dict(ok=False, evaluatedAt=evaluated, operation=operation, readOnly=read_only, error=str(exc))))
        return 1
    out(json.dumps(dict(ok=ok, evaluatedAt=evaluated, operation=operation, readOnly=read_only, result=result)))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
