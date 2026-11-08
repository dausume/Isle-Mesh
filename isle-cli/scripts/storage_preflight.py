"""Read-only local storage checks for app placement/migration operations.
A central report is advisory. Call this again on the target before writing.
This does not mount disks, grant approval, or establish boot dependencies.
"""
import json
import os
from pathlib import Path
import subprocess


def inspect(path, filesystem_uuid, required_bytes=0, *, run=subprocess.run, statvfs=os.statvfs):
    if not isinstance(path, str) or not path.startswith('/') or '\x00' in path:
        raise ValueError('Storage path must be absolute')
    if '..' in Path(path).parts:
        raise ValueError('Storage path must not traverse parent directories')
    if not isinstance(filesystem_uuid, str) or not filesystem_uuid.strip() or len(filesystem_uuid)>200:
        raise ValueError('An exact filesystem UUID is required')
    if type(required_bytes) is not int or required_bytes<0:
        raise ValueError('Required bytes must be a nonnegative integer')
    root=Path(path)
    for part in [root,*root.parents]:
        if part.is_symlink():raise ValueError('Storage path contains a symlink: '+str(part))
    ancestor=root
    while not ancestor.exists():ancestor=ancestor.parent
    if not ancestor.is_dir():raise ValueError('Storage path is not a directory')
    result=run(['findmnt','--json','--target',str(ancestor),'--output','TARGET,UUID,FSTYPE,OPTIONS'],capture_output=True,text=True,check=True)
    rows=json.loads(result.stdout).get('filesystems',[])
    if len(rows)!=1:raise ValueError('Cannot identify exactly one mounted filesystem')
    mount=rows[0];target=Path(mount['target'])
    if mount.get('uuid')!=filesystem_uuid:raise ValueError('Mounted filesystem UUID does not match the plan')
    if target==Path('/'):raise ValueError('External placement resolved to the root filesystem; refusing fallback')
    if not root.is_relative_to(target):raise ValueError('Storage path is outside the reported mount')
    options=set(str(mount.get('options','')).split(','))
    if 'ro' in options or 'rw' not in options:raise ValueError('Storage filesystem is not mounted read-write')
    capacity=statvfs(ancestor)
    if capacity.f_flag & getattr(os,'ST_RDONLY',1):raise ValueError('Filesystem reports read-only state')
    available=capacity.f_bavail*capacity.f_frsize
    if available<required_bytes:raise ValueError('Insufficient available storage for the planned copy')
    return dict(path=str(root),mount=str(target),filesystemUuid=filesystem_uuid,filesystem=mount.get('fstype'),
                availableBytes=available,requiredBytes=required_bytes,localMountVerified=True,
                bootDependencyVerified=False,
                note='Read-only preflight; approval, persistent mount dependency and operation execution are separate.')
