#!/usr/bin/env python3
"""Shared read-only storage operations for operators and placement clients."""
import argparse
from datetime import datetime, timezone
import json
import os
import subprocess
import sys
from urllib.parse import quote

from resource_snapshot import storage_inventory
from storage_preflight import inspect
from storage_contract import DATASET
from storage_receive import storage_check
from storage_snapshot import inventory, encoded, verify

LOCAL_ROOT = '/var/lib/isle-mesh/storage'


def read_copies(root, dataset):
    """Return (binding, last receipt) from the root-filesystem copies of a dataset; None for what is absent."""
    if not DATASET.match(dataset):
        raise ValueError('Dataset must match [a-z0-9][a-z0-9-]{0,63}.')
    folder = os.path.join(root, dataset)
    binding = receipt = None
    try:
        with open(os.path.join(folder, 'binding.json'), 'rb') as handle:
            binding = json.loads(handle.read(1 << 20))
    except (OSError, ValueError):
        pass
    try:
        with open(os.path.join(folder, 'receipts.jsonl'), 'rb') as handle:
            for line in handle.read(64 << 20).splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                receipt = row if isinstance(row, dict) else receipt
    except OSError:
        pass
    return binding if isinstance(binding, dict) else None, receipt


def findmnt(path):
    """Return {target, source, uuid} of the mount holding path (read-only findmnt)."""
    result = subprocess.run(['findmnt', '--json', '--noheadings', '-o', 'TARGET,SOURCE,UUID', '-T', path],
                            capture_output=True, text=True, timeout=15)
    rows = (json.loads(result.stdout or '{}').get('filesystems') or []) if result.returncode == 0 else []
    if not rows:
        raise ValueError('Cannot determine the mount holding %s.' % path)
    return rows[0]


def check_path(path, mount_of=findmnt):
    """Read-only proof that path may be bind-mounted into a hosting app: absolute, not '/', no symlink in
    any component, an existing directory, and not on the root filesystem (a missing disk must fail, not fall back)."""
    if not isinstance(path, str) or not path.startswith('/') or '\x00' in path:
        raise ValueError('Bind path must be absolute.')
    if path == '/' or os.path.normpath(path) != path:
        raise ValueError('Bind path must be a normalised path other than /.')
    walk = ''
    for part in path.strip('/').split('/'):
        walk += '/' + part
        if os.path.islink(walk):
            raise ValueError('Bind path contains a symlink: %s.' % walk)
    if not os.path.isdir(path):
        raise ValueError('Bind path is not an existing directory: %s.' % path)
    mount = mount_of(path)
    if mount.get('target') == '/':
        raise ValueError('Bind path is on the root filesystem, not a separate mounted disk.')
    return dict(path=path, mountpoint=mount.get('target'), source=mount.get('source'), uuid=mount.get('uuid'))


LOCAL_PLACEMENT = '/etc/isle-mesh/storage/placement.json'


def _entries(node):
    """Yield every dict under node that looks like a placement entry (has domain and state)."""
    if isinstance(node, dict):
        if 'domain' in node and 'state' in node:
            yield node
        for value in node.values():
            yield from _entries(value)
    elif isinstance(node, list):
        for value in node:
            yield from _entries(value)


def domain_guard(domain, device, documents):
    """Raise ValueError when any placement document says domain is in cutover|hosting owned by another device.
    An empty or unparsable document means no placement (the view is a hint; the core CA/DNS gates stay authoritative)."""
    for text in documents:
        try:
            document = json.loads(text) if text and text.strip() else None
        except ValueError:
            continue
        for entry in _entries(document):
            if entry.get('domain') == domain and entry.get('state') in ('cutover', 'hosting') and entry.get('device') != device:
                raise ValueError('%s is managed storage placement in state %s owned by %s, not %s.'
                                 % (domain, entry.get('state'), entry.get('device'), device))
    return dict(domain=domain, device=device, managedElsewhere=False)


def _placement_view(dataset, http):
    """Return (view dict or None, source) from the core placement door; never raises."""
    import storage_migrate as sm
    url = '%s/api/islemesh/storage/placement?dataset=%s' % (sm.API, quote(dataset))
    try:
        view = sm._unwrap((http or sm.CurlHttp()).get_json(url), 'view')
    except sm.HttpError as error:
        return None, ('no-entry' if error.status == 404 else 'unreachable')
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, 'unreachable'
    return (view, 'core') if isinstance(view, dict) and view.get('state') else (None, 'unreachable')


def _operation(dataset, latest):
    """Return {id, state, stage} of the newest storage-<dataset> operation, or None when the journal is unreadable/empty."""
    try:
        row = latest('storage-%s' % dataset)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(row, dict):
        return None
    return dict(id=row.get('id'), state=row.get('state'), stage=row.get('stage'))


def status_document(root, dataset, *, http=None, latest=None, unit_dir=None, check=storage_check, is_enabled=None):
    """Return the section 2.7 status: the root-filesystem copies, plus (when reachable) the core placement view,
    the newest journal operation, the sync timer and the disk evidence.  What cannot be read stays null."""
    import storage_mount
    binding, receipt = read_copies(root, dataset)
    plan = (receipt or {}).get('plan') or {}
    view, placement_source = _placement_view(dataset, http)
    if latest is None:
        import operations
        latest = operations.latest
    kwargs = {} if is_enabled is None else dict(systemctl_is_enabled=is_enabled)
    timer = storage_mount.sync_timer_installed(dataset, unit_dir or storage_migrate_unit_dir(), **kwargs)
    mount = dict(bound=binding is not None, mounted=None, uuidMatches=None,
                 currentGeneration=(binding or {}).get('currentGeneration'), hostRunning=None, pointerAhead=None)
    source, note = 'root-filesystem-copies', 'Reported from the copies under %s; the disk was not read.' % root
    block = binding or {}
    if all(isinstance(block.get(k), str) for k in ('mountpoint', 'filesystemUuid', 'relativePath')):
        found = check(block['mountpoint'], block['filesystemUuid'], block['relativePath'], placement=view)
        if found.get('mounted') and found.get('uuidMatches'):
            mount.update(bound=found['bound'], mounted=True, uuidMatches=True, currentGeneration=found['currentGeneration'],
                         pointerAhead=found['pointerAhead'], markerMatches=found['markerMatches'],
                         placementAgrees=found['placementAgrees'])
            source, note = 'disk', 'Disk read at %s; copies under %s.' % (block['mountpoint'], root)
    document = dict(dataset=dataset, device=((receipt or {}).get('target') or {}).get('device'),
                    placement=view, placementSource=placement_source, operation=_operation(dataset, latest), timer=timer,
                    receipt=None if receipt is None else dict(ok=receipt.get('ok'), digest=plan.get('digest'), at=receipt.get('at')),
                    mount=mount, source=source, note=note)
    try:
        with open(os.path.join(root, dataset, 'sync-status.json'), 'rb') as handle:
            sync = json.loads(handle.read(1 << 20))
        if isinstance(sync, dict):
            document['syncStatus'] = sync
    except (OSError, ValueError):
        pass
    try:
        with open(os.path.join(root, dataset, 'unit-proof.json'), 'rb') as handle:
            proof = json.loads(handle.read(1 << 20))
        if isinstance(proof, dict):
            document['unitProof'] = proof
    except (OSError, ValueError):
        pass
    return document


CHECK_STATES = ('migrated', 'cutover', 'hosting')


def check_dataset(dataset, *, state_dir=None, http=None, check=storage_check, mountpoint=None, uuid=None, path=None,
                  identity=None, staged=False):
    """Contract section 4: `isle storage check --dataset D`, read-only.  Returns (exit code, document).
    Mountpoint, uuid and relativePath come from the root-filesystem binding copy under the state dir
    (ISLE_STORAGE_STATE_DIR) unless given explicitly.  placementAgrees is None when the core view cannot be
    read (an unreachable view must not block boot); a view naming another device or a state outside
    migrated|cutover|hosting makes it False.  staged=True (the migrate-time staged deploy, which runs before
    mark-migrated) also admits 'authorized'.  Exit 0 only when every proof holds."""
    root = state_dir or os.environ.get('ISLE_STORAGE_STATE_DIR') or LOCAL_ROOT
    binding, receipt = read_copies(root, dataset)
    block = binding or {}
    mountpoint, uuid, path = (mountpoint or block.get('mountpoint'), uuid or block.get('filesystemUuid'),
                              path or block.get('relativePath'))
    if not all(isinstance(v, str) and v for v in (mountpoint, uuid, path)):
        raise ValueError('No binding copy for %s under %s (needs mountpoint, filesystemUuid and relativePath).' % (dataset, root))
    view, source = _placement_view(dataset, http)
    found = dict(check(mountpoint, uuid, path, placement=view))
    if source == 'unreachable':
        found['placementAgrees'] = None
    else:
        device = ((receipt or {}).get('target') or {}).get('device')
        if device is None:
            try:
                device = (identity or storage_migrate_identity)()
            except Exception:
                device = None
        found['placementAgrees'] = bool(view and found.get('placementAgrees') and view.get('state') in (CHECK_STATES + ('authorized',) if staged else CHECK_STATES)
                                        and (device is None or view.get('device') == device))
    found['placementSource'] = source
    good = bool(found.get('bound') and found.get('mounted') and found.get('uuidMatches') and found.get('markerMatches')
                and found.get('currentGeneration') and not found.get('pointerAhead')
                and found['placementAgrees'] in (True, None))
    return (0 if good else 1), found


def storage_migrate_identity():
    import storage_migrate
    return storage_migrate.default_identity()


def storage_migrate_unit_dir():
    import storage_migrate
    return storage_migrate.UNIT_DIR


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list', help='Report disks and mounted volumes without mounting or writing')
    check = commands.add_parser('check', help='Validate a local destination against an exact filesystem UUID')
    check.add_argument('--path', help='Absolute destination (preflight), or with --dataset the path relative to the mount')
    check.add_argument('--uuid')
    check.add_argument('--required-bytes', type=int, default=0)
    check.add_argument('--dataset', help='Check a bound dataset instead: mountpoint, uuid and relative path come from the '
                       'binding copy under $ISLE_STORAGE_STATE_DIR (or are given with --mountpoint, --uuid, --path)')
    check.add_argument('--mountpoint')
    check.add_argument('--staged', action='store_true', help='With --dataset: the migrate-time staged deploy (placement may still be authorized)')
    probe = commands.add_parser('check-path', help='Prove a directory may be bound into a hosting app (read-only)')
    probe.add_argument('--path', required=True)
    guard = commands.add_parser('guard-domain', help='Refuse when a managed domain is owned by another device (read-only)')
    guard.add_argument('--domain', required=True)
    guard.add_argument('--device', required=True)
    guard.add_argument('--view', help='File holding the core placement view for the domain (may be empty)')
    guard.add_argument('--local', default=LOCAL_PLACEMENT, help='The core placement record, read when present')
    status = commands.add_parser('status', help='Report a dataset from the local copies of its binding and receipts')
    status.add_argument('--dataset', required=True)
    status.add_argument('--root', default=LOCAL_ROOT)
    snapshot = commands.add_parser('snapshot', help='Print a content manifest; caller must quiesce its writers')
    snapshot.add_argument('--path', required=True)
    verification = commands.add_parser('verify', help='Compare destination bytes with an authenticated content snapshot')
    verification.add_argument('--path', required=True)
    verification.add_argument('--manifest', required=True)
    verification.add_argument('--sha256', required=True)
    args = parser.parse_args(argv)
    if args.command == 'check':
        needed = () if args.dataset else ('path', 'uuid')
        missing = [name for name in needed if not getattr(args, name)]
        if missing:
            parser.error('check needs ' + ', '.join('--' + name for name in missing))
    evaluated = datetime.now(timezone.utc).isoformat()
    try:
        if args.command == 'snapshot':
            sys.stdout.buffer.write(encoded(inventory(args.path)))
            return 0
        elif args.command == 'check-path':
            data = check_path(args.path)
        elif args.command == 'guard-domain':
            texts = []
            for name in (args.view, args.local):
                try:
                    with open(name) as handle:
                        texts.append(handle.read(1 << 22))
                except (OSError, TypeError):
                    pass
            data = domain_guard(args.domain, args.device, texts)
        elif args.command == 'status':
            print(json.dumps(status_document(args.root, args.dataset)))
            return 0
        elif args.command == 'check' and args.dataset:
            if not DATASET.match(args.dataset):
                raise ValueError('Dataset must match [a-z0-9][a-z0-9-]{0,63}.')
            code, data = check_dataset(args.dataset, mountpoint=args.mountpoint, uuid=args.uuid, path=args.path,
                                     staged=args.staged)
            if code != 0:
                print(json.dumps(dict(ok=False, evaluatedAt=evaluated, operation='storage.check', readOnly=True,
                                      error='Dataset is not bound to the expected mounted filesystem.', result=data)))
                return 1
        elif args.command == 'verify':
            data = verify(args.path, args.manifest, args.sha256)
        elif args.command == 'list':
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
