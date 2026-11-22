#!/usr/bin/env python3
"""What the restore drill of a device-backup compares (isle backup restore <id> --drill).

The drill unpacks every archive of a device-backup into a scratch folder with the program a restore uses (tar),
and this file says whether the scratch copy is what the archive holds.  The archive is read here by Python and
not by tar, so the two are compared and neither is taken on trust:

  contents(archive)        every entry of the archive: kind, size, sha256, mode, owner
  compare(folder, rows)    what in the unpacked folder is missing, different or extra
  databases(folder)        every SQLite database in the folder: does it open whole, and its row count per table

Nothing here writes anywhere but into the scratch folder it is given (SQLite may leave its own side files there).
"""
import hashlib
import os
from pathlib import PurePosixPath
import sqlite3
import stat
import tarfile
from urllib.parse import quote

MiB = 1024 * 1024
SQLITE = b'SQLite format 3\x00'
SHOWN = 10          # lines printed per item before "and N more"


class DrillFailed(Exception):
    """An archive cannot be drilled at all."""


def _sha(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for block in iter(lambda: source.read(MiB), b''):
            digest.update(block)
    return digest.hexdigest()


def _inside(name):
    """The path of an entry below the folder it is unpacked into; '' is the folder itself."""
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts:
        raise DrillFailed('the archive holds an entry that would be written outside the folder (%s); it was not unpacked' % name[:120])
    return '/'.join(path.parts)


def contents(archive):
    """{path: row} for every entry of the archive.  An entry that would leave the folder refuses the whole archive."""
    rows = {}
    try:
        with tarfile.open(archive, 'r:gz') as tar:
            for member in tar:
                path = _inside(member.name)
                if member.isreg():
                    digest, source = hashlib.sha256(), tar.extractfile(member)
                    for block in iter(lambda: source.read(MiB), b''):
                        digest.update(block)
                    row = dict(type='file', size=member.size, sha256=digest.hexdigest())
                elif member.isdir():
                    row = dict(type='folder')
                elif member.issym():
                    row = dict(type='link', target=member.linkname)
                elif member.islnk():
                    row = dict(type='same-file', target=_inside(member.linkname))
                else:
                    row = dict(type='other')
                rows[path] = dict(row, mode=member.mode & 0o7777, uid=member.uid, gid=member.gid)
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise DrillFailed('the archive cannot be read (%s)' % exc)
    return rows


def totals(rows):
    """(entries, regular files, bytes of the regular files)."""
    files = [row for row in rows.values() if row['type'] == 'file']
    return len(rows), len(files), sum(row['size'] for row in files)


def _kind(mode):
    return 'folder' if stat.S_ISDIR(mode) else 'link' if stat.S_ISLNK(mode) else 'file' if stat.S_ISREG(mode) else 'other'


def compare(folder, rows):
    """Lines that say what in the unpacked folder is not what the archive holds; none when they are the same."""
    folder, wrong = str(folder), []
    for path, row in sorted(rows.items()):
        here, shown = os.path.join(folder, path) if path else folder, path or '.'
        try:
            found = os.lstat(here)
        except OSError:
            wrong.append('%s: in the archive, not unpacked' % shown); continue
        kind, want = _kind(found.st_mode), 'file' if row['type'] == 'same-file' else row['type']
        if kind != want:
            wrong.append('%s: unpacked as a %s, the archive holds a %s' % (shown, kind, want)); continue
        if row['type'] == 'file' and (found.st_size != row['size'] or _sha(here) != row['sha256']):
            wrong.append('%s: its bytes are not those of the archive' % shown)
        elif row['type'] == 'link' and os.readlink(here) != row['target']:
            wrong.append('%s: the link points somewhere else' % shown)
        elif row['type'] == 'same-file':
            other = os.path.join(folder, row['target'])
            if not os.path.exists(other) or not os.path.samefile(here, other):
                wrong.append('%s: no longer the same file as %s' % (shown, row['target']))
        if kind != 'link' and stat.S_IMODE(found.st_mode) != row['mode']:
            wrong.append('%s: its permissions are %o, the archive holds %o' % (shown, stat.S_IMODE(found.st_mode), row['mode']))
        if (found.st_uid, found.st_gid) != (row['uid'], row['gid']):
            wrong.append('%s: its owner is %d:%d, the archive holds %d:%d' % (shown, found.st_uid, found.st_gid, row['uid'], row['gid']))
    for here, folders, files in os.walk(folder):
        for name in sorted(folders + files):
            path = os.path.relpath(os.path.join(here, name), folder)
            if path not in rows:
                wrong.append('%s: unpacked, not in the archive' % path)
    return wrong


def databases(folder):
    """Every SQLite database below the folder: [{path, integrity, tables: {name: rows}}].  integrity is 'ok' or why not."""
    found = []
    for here, _, files in os.walk(str(folder)):
        for name in sorted(files):
            path = os.path.join(here, name)
            try:
                if os.path.islink(path) or not os.path.isfile(path):
                    continue
                with open(path, 'rb') as source:
                    if source.read(len(SQLITE)) != SQLITE:
                        continue
            except OSError:
                continue
            row = dict(path=os.path.relpath(path, str(folder)), tables={})
            try:
                connection = sqlite3.connect('file:%s?mode=ro' % quote(path), uri=True)
                try:
                    check = [str(line[0]) for line in connection.execute('PRAGMA integrity_check')]
                    names = [line[0] for line in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
                    row['tables'] = {table: connection.execute('SELECT count(*) FROM "%s"' % table.replace('"', '""')).fetchone()[0]
                                     for table in names}
                    row['integrity'] = 'ok' if check == ['ok'] else '; '.join(check[:3])[:300]
                finally:
                    connection.close()
            except sqlite3.Error as exc:
                row['integrity'] = 'cannot be read (%s)' % exc
            found.append(row)
    return sorted(found, key=lambda row: row['path'])
