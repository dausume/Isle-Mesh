"""Content inventory for a verified copy; never mounts, copies or deletes data.

Use the application's publication lock while taking a source inventory. A hash
inventory authenticates bytes only when its digest arrives through a trusted
plan/channel; it does not establish service readiness or authorize cutover.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat


def encoded(document):
    return (json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode()


def inventory(path):
    root = Path(path)
    if not root.is_absolute() or '..' in root.parts:
        raise ValueError('Snapshot path must be absolute without parent traversal')
    if any(p.is_symlink() for p in [root, *root.parents]):
        raise ValueError('Snapshot root contains a symlink')
    rows = []
    total = 0
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        root_device = os.fstat(root_fd).st_dev
        signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        def visit(directory, prefix):
            nonlocal total
            before_names = sorted(os.listdir(directory))
            for name in before_names:
                relative = prefix + name
                if name.startswith('.') or name.endswith(('.part', '.partial')):
                    raise ValueError('Snapshot refuses hidden or incomplete publication content: ' + relative)
                if len(rows) >= 100000:
                    raise ValueError('Snapshot exceeds 100000 entries')
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    try:
                        opened_dir = os.fstat(child)
                        if (opened_dir.st_dev, opened_dir.st_ino) != (info.st_dev, info.st_ino):
                            raise ValueError('Directory changed during snapshot')
                        if opened_dir.st_dev != root_device:
                            raise ValueError('Snapshot refuses a nested filesystem')
                        rows.append(dict(path=relative, kind='directory'))
                        visit(child, relative + '/')
                        if signature(opened_dir) != signature(os.stat(name, dir_fd=directory, follow_symlinks=False)):
                            raise ValueError('Directory changed during snapshot')
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                    with os.fdopen(fd, 'rb') as stream:
                        opened = os.fstat(stream.fileno())
                        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                            raise ValueError('File changed during snapshot')
                        h = hashlib.sha256()
                        for block in iter(lambda: stream.read(1024 * 1024), b''):
                            h.update(block)
                        after = os.fstat(stream.fileno())
                        current = os.stat(name, dir_fd=directory, follow_symlinks=False)
                        if signature(opened) != signature(after) or signature(after) != signature(current):
                            raise ValueError('File changed during snapshot')
                    rows.append(dict(path=relative, kind='file', size=info.st_size, sha256=h.hexdigest()))
                    total += info.st_size
                else:
                    raise ValueError('Snapshot refuses symlinks, hardlinks and special files: ' + relative)
            if sorted(os.listdir(directory)) != before_names:
                raise ValueError('Directory changed during snapshot')
        visit(root_fd, '')
    finally:
        os.close(root_fd)
    return dict(schemaVersion=1, kind='isle-content-snapshot', totalBytes=total,
                entries=sorted(rows, key=lambda row: row['path']))


def verify(path, manifest, expected_sha256):
    if not isinstance(expected_sha256, str) or not re.fullmatch('[a-f0-9]{64}', expected_sha256):
        raise ValueError('An authenticated snapshot SHA256 is required')
    with Path(manifest).open('rb') as stream:
        raw = stream.read(64 * 1024 * 1024 + 1)
    if len(raw) > 64 * 1024 * 1024:
        raise ValueError('Snapshot manifest exceeds size limit')
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Snapshot manifest checksum mismatch')
    document = json.loads(raw)
    observed = inventory(path)
    if observed != document:
        raise ValueError('Content differs from snapshot (missing, extra or changed entries)')
    return dict(contentVerified=True, snapshotSha256=expected_sha256,
                totalBytes=observed['totalBytes'], serviceReadyVerified=False,
                note='Bytes match; mount persistence, signatures, service readiness and cutover remain separate gates.')
