"""Receive engine for a storage migration (stm-4): store/, generations/, current, binding.

Everything here is pure local filesystem work.  Fetching is injected, so no network
code lives in this file.  Every write goes through directory descriptors that are
derived from one mount root descriptor (openat, linkat, renameat, unlinkat, fsync),
so no path string is looked up again between a check and its use.
See docs/STORAGE-MIGRATION-CONTRACT.md sections 2.2 (stages 2-5), 2.5, 2.7, 3 and 4.

Layout under <mount>/<relativePath>/:
  binding.json, store/<sha256>, generations/<contentSha>/<path>,
  current -> generations/<contentSha> (relative symlink), staging/<opid>/,
  control/manifest-<manifestSha>.json (before the swap), control/latest.json + control/current (after it; promote)

Two hashes: manifestSha is the sha256 of the published document bytes (inventory plus
binding), checked once by the caller at fetch time.  contentSha is the sha256 of the
canonical encoding of the document without its binding (the content projection); it
names generations, current and binding.currentGeneration.  The sha256 of a file is the
key the injected fetch uses (one fetch per distinct sha256).
"""
import contextlib
import hashlib
import json
import os
import re
import stat
import subprocess
import unicodedata

from storage_snapshot import RESERVED_TOP_LEVEL, encoded

HEX64 = re.compile(r'[a-f0-9]{64}\Z')
OP_ID = re.compile(r'[A-Za-z0-9_.-]{1,80}\Z')
MiB = 1024 * 1024
PROGRESS_STEP = 64 * MiB
HEADROOM_MIN = 64 * MiB
HEADROOM_PERCENT = 5
SEGMENT_MAX = 200
PATH_MAX = 4096
BINDING_MAX = MiB
FORBIDDEN_CHARS = '\\?#'  # '%' is allowed: dpkg names epoch files with it (bsdutils_1%3a2.39...deb)
STAGE = 'receiving-content'
PLACEMENT_LIVE_STATES = ('authorized', 'migrated', 'cutover', 'hosting')
OPEN_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class ReceiveError(Exception):
    """A refusal or failure of the receive engine.
    retryable is True when trying the same operation again later can succeed
    (an unplugged disk, a bad transfer); stage names the migrate stage that failed."""

    def __init__(self, message, retryable=False, stage=None):
        super().__init__(message)
        self.retryable = retryable
        self.stage = stage


# ---------------------------------------------------------------- manifest paths

def manifest_sha(raw_bytes):
    """Return the sha256 of the published manifest bytes (the whole document, binding included)."""
    return hashlib.sha256(raw_bytes).hexdigest()


def content_sha(document):
    """Return the sha256 of the canonical encoding of document without its binding block."""
    if not isinstance(document, dict):
        raise ValueError('Manifest document must be an object.')
    return hashlib.sha256(encoded({k: v for k, v in document.items() if k != 'binding'})).hexdigest()


def validate_manifest_path(path):
    """Return the segment list of a manifest path or raise ValueError.
    The path must be relative with non-empty segments and none of: '.', '..',
    backslash, '?', '#', NUL or other control characters.  '%' is a literal file-name
    character (dpkg epoch files, e.g. name_1%3a2.0_amd64.deb); object_url() encodes it as %25.  Segments are at
    most 200 bytes and the whole path at most 4096 bytes (UTF-8)."""
    if not isinstance(path, str) or not path:
        raise ValueError('Manifest path must be a non-empty string.')
    if path.startswith('/'):
        raise ValueError('Manifest path must be relative: ' + repr(path))
    for char in path:
        if char == '\x00' or unicodedata.category(char) == 'Cc':
            raise ValueError('Manifest path contains a control character: ' + repr(path))
        if char in FORBIDDEN_CHARS:
            raise ValueError('Manifest path contains a forbidden character %r: %r' % (char, path))
    try:
        total = len(path.encode('utf-8'))
        segments = path.split('/')
        sizes = [len(segment.encode('utf-8')) for segment in segments]
    except UnicodeEncodeError:
        raise ValueError('Manifest path is not valid text: ' + repr(path))
    if total > PATH_MAX or max(sizes) > SEGMENT_MAX:
        raise ValueError('Manifest path is too long.')
    for segment in segments:
        if segment in ('', '.', '..'):
            raise ValueError('Manifest path has an empty, "." or ".." segment: ' + repr(path))
    return segments


def validate_manifest(manifest):
    """Return the validated entry list of an isle-content-snapshot manifest or raise ValueError.
    Every path is checked, files need an integer size and a sha256, duplicates are refused
    and totalBytes must equal the sum of the file sizes."""
    if not isinstance(manifest, dict) or manifest.get('schemaVersion') != 1 \
            or manifest.get('kind') != 'isle-content-snapshot':
        raise ValueError('Manifest must be an isle-content-snapshot of schema version 1.')
    entries = manifest.get('entries')
    if not isinstance(entries, list):
        raise ValueError('Manifest entries must be a list.')
    seen, total, sizes = set(), 0, {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('Manifest entry must be an object.')
        segments = validate_manifest_path(entry.get('path'))
        if entry['path'] in seen:
            raise ValueError('Manifest lists a path twice: ' + entry['path'])
        seen.add(entry['path'])
        if segments[0] in RESERVED_TOP_LEVEL and entry.get('kind') == 'directory' and len(segments) == 1:
            raise ValueError('Manifest lists the reserved top-level directory: ' + entry['path'])
        if entry.get('kind') == 'file':
            size, sha = entry.get('size'), entry.get('sha256')
            if type(size) is not int or size < 0 or not isinstance(sha, str) or not HEX64.match(sha):
                raise ValueError('Manifest file entry needs an integer size and a sha256: ' + entry['path'])
            total += size
            if sizes.setdefault(sha, size) != size:
                raise ValueError('Manifest gives one sha256 two different sizes: ' + sha)
        elif entry.get('kind') != 'directory':
            raise ValueError('Manifest entry kind must be file or directory: ' + entry['path'])
    if manifest.get('totalBytes') != total:
        raise ValueError('Manifest totalBytes does not equal the sum of its file sizes.')
    return entries


# ---------------------------------------------------------------- descriptors

def default_findmnt_json(path):
    """Return findmnt's JSON for the filesystem holding path (read-only; real system call)."""
    result = subprocess.run(['findmnt', '--json', '--target', path, '--output', 'TARGET,UUID,FSTYPE,OPTIONS'],
                            capture_output=True, text=True, check=True, timeout=15)
    return json.loads(result.stdout)


def _one_row(data):
    """Return the single filesystem row of a findmnt document or raise ReceiveError."""
    rows = data.get('filesystems') if isinstance(data, dict) else None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ReceiveError('Cannot identify exactly one mounted filesystem.', retryable=True, stage='mount')
    return rows[0]


def _clean_part(part):
    """Return part when it is one safe directory-name component or raise ReceiveError."""
    if not isinstance(part, str) or part in ('', '.', '..') or '/' in part or '\x00' in part:
        raise ReceiveError('Unsafe path component: %r.' % (part,), stage='path')
    return part


def _open_dir(parent_fd, name, create):
    """Open one directory under parent_fd without following a symlink, creating it 0755 if asked."""
    _clean_part(name)
    if create:
        try:
            os.mkdir(name, 0o755, dir_fd=parent_fd)
        except FileExistsError:
            pass
    try:
        return os.open(name, OPEN_DIR, dir_fd=parent_fd)
    except FileNotFoundError:
        raise
    except OSError as error:
        raise ReceiveError('%r is not a real directory (symlink or special file?): %s' % (name, error), stage='path')


def _walk(start_fd, parts, create):
    """Return a new descriptor for parts below start_fd, opening each component with O_NOFOLLOW."""
    current = os.dup(start_fd)
    try:
        for part in parts:
            child = _open_dir(current, part, create)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


class MountRoot:
    """A verified mount root held as a directory descriptor.
    The descriptor is opened with O_DIRECTORY|O_NOFOLLOW.  verify() re-asks findmnt
    (injectable) about the descriptor, compares the UUID and mount target, and checks
    that the descriptor's st_dev and the path's current st_dev and inode still agree."""

    def __init__(self, mountpoint, expected_uuid, findmnt_json=default_findmnt_json):
        if not isinstance(mountpoint, str) or not mountpoint.startswith('/') or '\x00' in mountpoint:
            raise ReceiveError('Mountpoint must be an absolute path.', stage='mount')
        if os.path.normpath(mountpoint) in ('/', '//') or '..' in mountpoint.split('/'):
            raise ReceiveError('Mountpoint must not be / or contain parent traversal.', stage='mount')
        if not isinstance(expected_uuid, str) or not expected_uuid.strip():
            raise ReceiveError('An expected filesystem UUID is required.', stage='mount')
        self.mountpoint = os.path.normpath(mountpoint)
        if os.path.realpath(self.mountpoint) != self.mountpoint:
            raise ReceiveError('Mountpoint path contains a symlink.', stage='mount')
        self.expected_uuid = expected_uuid
        self.findmnt_json = findmnt_json
        self.fd = None
        self.st_dev = None
        self._open()
        try:
            self.verify()
        except BaseException:
            self.close()
            raise

    def _open(self):
        try:
            fd = os.open(self.mountpoint, OPEN_DIR)
        except OSError as error:
            raise ReceiveError('Cannot open the mount root: %s' % error, retryable=True, stage='mount')
        self.fd = fd
        self.st_dev = os.fstat(fd).st_dev

    def verify(self):
        """Raise a retryable ReceiveError unless the descriptor still is the expected mounted filesystem."""
        if self.fd is None:
            raise ReceiveError('Mount root is closed.', retryable=True, stage='mount')
        held = os.fstat(self.fd)
        if held.st_dev != self.st_dev:
            raise ReceiveError('Mount root st_dev changed.', retryable=True, stage='mount')
        try:
            now = os.stat(self.mountpoint, follow_symlinks=False)
        except OSError as error:
            raise ReceiveError('Mount root path is gone: %s' % error, retryable=True, stage='mount')
        if (now.st_dev, now.st_ino) != (held.st_dev, held.st_ino):
            raise ReceiveError('Mount root path no longer names the opened directory.', retryable=True, stage='mount')
        try:
            row = _one_row(self.findmnt_json('/proc/%d/fd/%d' % (os.getpid(), self.fd)))
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise ReceiveError('Cannot read mount information: %s' % error, retryable=True, stage='mount')
        if row.get('uuid') != self.expected_uuid:
            raise ReceiveError('Filesystem UUID is %r, expected %r.' % (row.get('uuid'), self.expected_uuid),
                               retryable=True, stage='mount')
        if os.path.normpath(str(row.get('target'))) != self.mountpoint:
            raise ReceiveError('Mounted target is %r, expected %r (not a mount point?).'
                               % (row.get('target'), self.mountpoint), retryable=True, stage='mount')

    def reopen(self):
        """Close and reopen the root, then verify it again; the new st_dev becomes the baseline."""
        self.close()
        self._open()
        try:
            self.verify()
        except BaseException:
            self.close()
            raise

    def dir_fd(self, relative_parts, create=True):
        """Return a new descriptor for relative_parts below the root (caller closes it).
        Each component is opened with O_NOFOLLOW; missing ones are made 0755 when create is true,
        otherwise FileNotFoundError is raised."""
        return _walk(self.fd, [_clean_part(p) for p in relative_parts], create)

    @contextlib.contextmanager
    def opened(self, relative_parts, create=True):
        """Context manager around dir_fd that always closes the descriptor."""
        fd = self.dir_fd(relative_parts, create)
        try:
            yield fd
        finally:
            os.close(fd)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


# ---------------------------------------------------------------- small fd helpers

def _fsync_dir(fd):
    os.fsync(fd)


def _stat_at(dir_fd, name):
    """Return lstat of name under dir_fd or None when absent."""
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _is_regular(info, size=None):
    return info is not None and stat.S_ISREG(info.st_mode) and (size is None or info.st_size == size)


def _link(src_fd, src_name, dst_fd, dst_name):
    os.link(src_name, dst_name, src_dir_fd=src_fd, dst_dir_fd=dst_fd, follow_symlinks=False)


def _parts(parts):
    if not isinstance(parts, (list, tuple)) or not parts:
        raise ReceiveError('Dataset path parts must be a non-empty list.', stage='path')
    return [_clean_part(p) for p in parts]


def _sha(value):
    if not isinstance(value, str) or not HEX64.match(value):
        raise ReceiveError('Expected 64 lowercase hex characters.', stage='path')
    return value


def headroom(total_bytes):
    """Return the free space to keep beyond the data: 5 percent of the filesystem, at least 64 MiB."""
    return max(HEADROOM_MIN, total_bytes * HEADROOM_PERCENT // 100)


# ---------------------------------------------------------------- planning

def plan_receive(manifest, store_dir_fd):
    """Return what a receive has to fetch.
    {'toFetch': entries, 'reuse': entries, 'bytesToFetch': int, 'largestFile': int}.
    A file entry is reused when store/<sha> exists as a regular file of the right size, or
    when an earlier entry of the same manifest has the same sha256 (toFetch is keyed by sha256,
    so equal content is fetched once and is in store/ by the time the later entry is linked).
    store_dir_fd may be None when there is no store yet."""
    to_fetch, reuse, queued = [], [], set()
    for entry in validate_manifest(manifest):
        if entry['kind'] != 'file':
            continue
        key = entry['sha256']
        have = store_dir_fd is not None and _is_regular(_stat_at(store_dir_fd, entry['sha256']), entry['size'])
        if have or key in queued:
            reuse.append(entry)
        else:
            queued.add(key)
            to_fetch.append(entry)
    sizes = [e['size'] for e in to_fetch]
    return {'toFetch': to_fetch, 'reuse': reuse, 'bytesToFetch': sum(sizes), 'largestFile': max(sizes, default=0)}


# ---------------------------------------------------------------- receiving

class _Sink:
    """The write-only file object handed to fetch: it refuses to take more bytes than the entry's size."""

    def __init__(self, stream, limit):
        self._stream, self._limit, self.written = stream, limit, 0

    def write(self, data):
        if self.written + len(data) > self._limit:
            raise ReceiveError('Source sent more bytes than the manifest size.', retryable=True, stage=STAGE)
        self.written += len(data)
        return self._stream.write(data)

    def flush(self):
        self._stream.flush()

    def tell(self):
        return self.written

    def writable(self):
        return True


def _verified(root):
    """root.verify(), reporting any failure as the receiving stage."""
    try:
        root.verify()
    except ReceiveError as error:
        raise ReceiveError(str(error), retryable=True, stage=STAGE)


def _stage_one(staging_fd, name, entry, fetch):
    """Fetch one file into staging, check size and sha256, fsync, and return its open descriptor.
    The staging file is removed on any failure.  The caller closes the descriptor."""
    fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=staging_fd)
    done = False
    try:
        stream = os.fdopen(os.dup(fd), 'r+b')
        try:
            sink = _Sink(stream, entry['size'])
            try:
                fetch(entry, sink)
            except ReceiveError:
                raise
            except Exception as error:
                raise ReceiveError('Fetching %s failed: %s' % (entry['path'], error), retryable=True, stage=STAGE)
            stream.flush()
        finally:
            stream.close()
        info = os.fstat(fd)
        if info.st_size != entry['size']:
            raise ReceiveError('Size of %s is %d, manifest says %d.' % (entry['path'], info.st_size, entry['size']),
                               retryable=True, stage=STAGE)
        digest = hashlib.sha256()
        os.lseek(fd, 0, os.SEEK_SET)
        for block in iter(lambda: os.read(fd, MiB), b''):
            digest.update(block)
        if digest.hexdigest() != entry['sha256']:
            raise ReceiveError('sha256 of %s does not match the manifest.' % entry['path'], retryable=True, stage=STAGE)
        os.fsync(fd)
        os.fchmod(fd, 0o444)
        done = True
        return fd
    finally:
        if not done:
            os.close(fd)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(name, dir_fd=staging_fd)


def _into_store(staging_fd, name, store_fd, entry):
    """Hard-link a verified staging file to store/<sha>; an existing file of the same size is fine."""
    try:
        _link(staging_fd, name, store_fd, entry['sha256'])
    except FileExistsError:
        if not _is_regular(_stat_at(store_fd, entry['sha256']), entry['size']):
            raise ReceiveError('store/%s exists with a different size.' % entry['sha256'], stage=STAGE)
    os.unlink(name, dir_fd=staging_fd)


def _into_generation(store_fd, entry, parent_fd, leaf):
    """Hard-link store/<sha> as leaf under parent_fd; replace a different leftover from an earlier attempt."""
    try:
        _link(store_fd, entry['sha256'], parent_fd, leaf)
    except FileExistsError:
        have, want = _stat_at(parent_fd, leaf), _stat_at(store_fd, entry['sha256'])
        if (have.st_dev, have.st_ino) == (want.st_dev, want.st_ino):
            return
        os.unlink(leaf, dir_fd=parent_fd)
        _link(store_fd, entry['sha256'], parent_fd, leaf)


class _ParentCache:
    """Keeps the descriptor of the directory being filled, and fsyncs it when it changes."""

    def __init__(self, gen_fd):
        self.gen_fd, self.key, self.fd = gen_fd, None, None

    def get(self, parts):
        if self.key != parts:
            self.close()
            self.fd = _walk(self.gen_fd, parts, True)
            self.key = parts
        return self.fd

    def close(self):
        if self.fd is not None:
            _fsync_dir(self.fd)
            os.close(self.fd)
        self.fd = self.key = None


def receive(root, dataset_rel_parts, manifest, fetch, *, op_id, progress=None, statvfs=os.fstatvfs):
    """Receive every file of manifest into generations/<contentSha> and return its path parts (not yet promoted).
    fetch(entry, sink) gets the manifest entry (its sha256 is the fetch key, e.g. snapshot/objects/<sha256>)
    and must write that object's bytes to sink, a write-only file object that takes at most entry['size'] bytes.
    progress(bytes_done, bytes_total, files_done, files_total) is called every PROGRESS_STEP bytes
    and once at the end.
    Per file: the mount is verified, free space is re-checked, then fetch, size and sha256 check,
    fsync, link into store/ and into generations/<manifestSha>/<path>.
    Raises ReceiveError (retryable for a bad transfer or a changed mount, not for lack of space)."""
    parts = _parts(dataset_rel_parts)
    if not isinstance(op_id, str) or not OP_ID.match(op_id) or op_id in ('.', '..'):
        raise ReceiveError('Operation id must match [A-Za-z0-9_.-]{1,80}.', stage=STAGE)
    try:
        entries = validate_manifest(manifest)
    except ValueError as error:
        raise ReceiveError(str(error), stage=STAGE)
    sha = content_sha(manifest)
    _verified(root)
    with contextlib.ExitStack() as stack:
        def opened(fd):
            stack.callback(os.close, fd)
            return fd
        base = opened(root.dir_fd(parts))
        store = opened(_walk(base, ['store'], True))
        gen = opened(_walk(base, ['generations', sha], True))
        staging = opened(_walk(base, ['staging', op_id], True))
        plan = plan_receive(manifest, store)
        remaining = plan['bytesToFetch']
        files_total = sum(1 for e in entries if e['kind'] == 'file')
        state = dict(bytes=0, files=0, reported=0)
        total_bytes = manifest['totalBytes']
        cache = _ParentCache(gen)
        stack.callback(cache.close)
        try:
            for number, entry in enumerate(entries):
                segments = entry['path'].split('/')
                if entry['kind'] == 'directory':
                    os.close(_walk(gen, segments, True))
                    continue
                _verified(root)
                if not _is_regular(_stat_at(store, entry['sha256']), entry['size']):
                    _check_capacity(root, remaining, statvfs)
                    name = 'f%08d' % number
                    stage_fd = _stage_one(staging, name, entry, fetch)
                    os.close(stage_fd)
                    _into_store(staging, name, store, entry)
                    remaining -= entry['size']
                _into_generation(store, entry, cache.get(segments[:-1]), segments[-1])
                state['bytes'] += entry['size']
                state['files'] += 1
                if progress and state['bytes'] - state['reported'] >= PROGRESS_STEP:
                    state['reported'] = state['bytes']
                    progress(state['bytes'], total_bytes, state['files'], files_total)
            cache.close()
            for fd in (store, staging, gen):
                _fsync_dir(fd)
            _fsync_dir(base)
        finally:
            with contextlib.suppress(OSError):
                parent = _walk(base, ['staging'], False)
                try:
                    os.rmdir(op_id, dir_fd=parent)
                finally:
                    os.close(parent)
        if progress:
            progress(state['bytes'], total_bytes, state['files'], files_total)
    return parts + ['generations', sha]


def _check_capacity(root, remaining, statvfs):
    """Refuse (not retryable) unless free space covers the bytes still to fetch plus the headroom."""
    info = statvfs(root.fd)
    available = info.f_bavail * info.f_frsize
    need = remaining + headroom(info.f_blocks * info.f_frsize)
    if available < need:
        raise ReceiveError('Insufficient space: %d bytes free, %d needed (data plus headroom).' % (available, need),
                           retryable=False, stage=STAGE)


# ---------------------------------------------------------------- verify, promote, binding

def inventory_generation(root, generation_parts, store_dir_fd):
    """Inventory a received generation with directory descriptors and return the snapshot dict.
    The shape equals storage_snapshot.inventory (so content_sha applies).  Symlinks, special files,
    hidden or partial names and nested filesystems are refused.  A file with more than one link is
    accepted only when it is the same inode as store/<its sha256>, our own store link; any other
    multi-link file is refused."""
    rows, total = [], [0]
    base = _walk(root.fd, [_clean_part(p) for p in generation_parts], False)
    root_dev = os.fstat(base).st_dev

    def refuse(message):
        raise ReceiveError(message, stage='verifying-content')

    def file_row(directory, name, relative, info):
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except OSError as error:
            refuse('Cannot open %s: %s' % (relative, error))
        with os.fdopen(fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                refuse('File changed during inventory: ' + relative)
            digest = hashlib.sha256()
            for block in iter(lambda: stream.read(MiB), b''):
                digest.update(block)
        sha = digest.hexdigest()
        if opened.st_nlink != 1:
            ours = _stat_at(store_dir_fd, sha)
            if ours is None or (ours.st_dev, ours.st_ino) != (opened.st_dev, opened.st_ino):
                refuse('Multi-link file that is not our store link: ' + relative)
        total[0] += opened.st_size
        return dict(path=relative, kind='file', size=opened.st_size, sha256=sha)

    def visit(directory, prefix):
        for name in sorted(os.listdir(directory)):
            relative = prefix + name
            if name.startswith('.') or name.endswith(('.part', '.partial')):
                refuse('Generation holds hidden or incomplete content: ' + relative)
            if len(rows) >= 100000:
                refuse('Generation exceeds 100000 entries.')
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = _open_dir(directory, name, False)
                try:
                    if os.fstat(child).st_dev != root_dev:
                        refuse('Generation holds a nested filesystem: ' + relative)
                    rows.append(dict(path=relative, kind='directory'))
                    visit(child, relative + '/')
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                rows.append(file_row(directory, name, relative, info))
            else:
                refuse('Generation holds a symlink or special file: ' + relative)

    try:
        visit(base, '')
    finally:
        os.close(base)
    return dict(schemaVersion=1, kind='isle-content-snapshot', totalBytes=total[0],
                entries=sorted(rows, key=lambda row: row['path']))


def verify_generation(root, dataset_rel_parts, expected_content_sha, manifest_bytes):
    """Re-inventory generations/<contentSha> and require its content projection to equal contentSha.
    manifest_bytes is the published document (its own sha was checked by the caller at fetch time);
    its content projection must also equal expected_content_sha.  Returns contentSha."""
    parts = _parts(dataset_rel_parts)
    sha = _sha(expected_content_sha)
    try:
        document = json.loads(manifest_bytes)
        declared = content_sha(document)
    except ValueError:
        raise ReceiveError('Manifest is not a valid JSON object.', stage='verifying-content')
    if declared != sha:
        raise ReceiveError('Manifest content does not hash to the generation contentSha.', stage='verifying-content')
    _verified(root)
    try:
        with root.opened(parts, create=False) as base:
            store = _walk(base, ['store'], False)
            try:
                observed = inventory_generation(root, parts + ['generations', sha], store)
            finally:
                os.close(store)
    except FileNotFoundError:
        raise ReceiveError('Generation or store is missing.', stage='verifying-content')
    if content_sha(observed) != sha:
        raise ReceiveError('Generation content differs from the manifest.', stage='verifying-content')
    return sha


def _atomic_file(base_fd, name, data):
    """Write data to name under base_fd: temp file, fsync, rename, fsync of the directory."""
    tmp = '%s.tmp-%d' % (name, os.getpid())
    with contextlib.suppress(FileNotFoundError):
        os.unlink(tmp, dir_fd=base_fd)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=base_fd)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(tmp, name, src_dir_fd=base_fd, dst_dir_fd=base_fd)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp, dir_fd=base_fd)
        raise
    _fsync_dir(base_fd)


def promote(root, dataset_rel_parts, expected_content_sha, manifest_bytes, *, dataset, generation,
            op_id=None, promoted_at=None):
    """Atomically point current at generations/<contentSha> (F6: one committed pointer).
    Order: control/manifest-<manifestSha>.json (immutable, harmless) -> rename the relative symlink over
    current -> control/latest.json {contentSha, manifestSha, promotedAt, dataset, epoch} (epoch is the placement
    generation seen at promote; informational) and control/current (one line, the contentSha), both derived from readlink(current).
    A crash between the swap and latest.json leaves latest behind; storage_check reports pointerAhead and the
    next promote rewrites latest."""
    parts, sha = _parts(dataset_rel_parts), _sha(expected_content_sha)
    tmp = 'current.tmp-%s' % (op_id or os.getpid())
    if not OP_ID.match(tmp[len('current.tmp-'):]):
        raise ReceiveError('Bad operation id for promote.', stage='writing-binding')
    if type(generation) is not int or generation < 0:
        raise ReceiveError('Placement generation must be a non-negative integer.', stage='writing-binding')
    try:
        declared = content_sha(json.loads(manifest_bytes))
    except ValueError:
        raise ReceiveError('Manifest is not a valid JSON object.', stage='writing-binding')
    if declared != sha:
        raise ReceiveError('Manifest content does not hash to the contentSha being promoted.', stage='writing-binding')
    from datetime import datetime, timezone
    latest = dict(contentSha=sha, manifestSha=manifest_sha(manifest_bytes), dataset=dataset, epoch=generation,
                  promotedAt=promoted_at or datetime.now(timezone.utc).isoformat())
    _verified(root)
    with root.opened(parts) as base:
        try:
            os.close(_walk(base, ['generations', sha], False))
        except FileNotFoundError:
            raise ReceiveError('Generation %s does not exist.' % sha, stage='verifying-content')
        control = _walk(base, ['control'], True)
        try:
            _atomic_file(control, 'manifest-%s.json' % latest['manifestSha'], manifest_bytes)
        finally:
            os.close(control)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp, dir_fd=base)
        os.symlink('generations/' + sha, tmp, dir_fd=base)
        _fsync_dir(base)
        try:
            os.rename(tmp, 'current', src_dir_fd=base, dst_dir_fd=base)
        except OSError as error:
            os.unlink(tmp, dir_fd=base)
            raise ReceiveError('Cannot replace current: %s' % error, stage='verifying-content')
        _fsync_dir(base)
        # F6: current is the only committed pointer; advertise it AFTER the swap, derived from readlink.
        committed = re.fullmatch(r'generations/([a-f0-9]{64})', os.readlink('current', dir_fd=base))
        if not committed:
            raise ReceiveError('current does not point at a generation after the swap.', stage='verifying-content')
        latest['contentSha'] = committed.group(1)
        control = _walk(base, ['control'], True)
        try:
            _atomic_file(control, 'latest.json', encoded(latest))
            _atomic_file(control, 'current', (latest['contentSha'] + '\n').encode())
        finally:
            os.close(control)


def current_generation(root, dataset_rel_parts):
    """Return the manifest sha that current points at, or None when there is no valid current."""
    try:
        with root.opened(_parts(dataset_rel_parts), create=False) as base:
            target = os.readlink('current', dir_fd=base)
    except (FileNotFoundError, NotADirectoryError, OSError):
        return None
    match = re.fullmatch(r'generations/([a-f0-9]{64})', target)
    return match.group(1) if match else None


def pointer_ahead(root, dataset_rel_parts):
    """True when control/latest.json's contentSha disagrees with readlink(current) (current is truth)."""
    committed = current_generation(root, dataset_rel_parts)
    advertised = None
    try:
        with root.opened(_parts(dataset_rel_parts), create=False) as base:
            control = _walk(base, ['control'], False)
            try:
                fd = os.open('latest.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=control)
            finally:
                os.close(control)
        with os.fdopen(fd, 'rb') as stream:
            advertised = json.loads(stream.read(BINDING_MAX + 1)).get('contentSha')
    except (OSError, ValueError, AttributeError, ReceiveError):
        advertised = None
    return advertised != committed


def write_binding(root, dataset_rel_parts, record):
    """Write binding.json atomically (temp file, fsync, rename, fsync directory)."""
    parts = _parts(dataset_rel_parts)
    if not isinstance(record, dict):
        raise ReceiveError('Binding record must be an object.', stage='writing-binding')
    _verified(root)
    with root.opened(parts) as base:
        _atomic_file(base, 'binding.json', encoded(record))


def read_binding(root, dataset_rel_parts):
    """Return binding.json as a dict, or None when it is absent.  A damaged file raises ValueError."""
    try:
        with root.opened(_parts(dataset_rel_parts), create=False) as base:
            fd = os.open('binding.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=base)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as error:
        raise ValueError('binding.json cannot be opened: %s' % error)
    with os.fdopen(fd, 'rb') as stream:
        raw = stream.read(BINDING_MAX + 1)
    if len(raw) > BINDING_MAX:
        raise ValueError('binding.json is too large.')
    record = json.loads(raw)
    if not isinstance(record, dict):
        raise ValueError('binding.json must hold an object.')
    return record


# ---------------------------------------------------------------- evidence for check / status

def storage_check(mountpoint, uuid, dataset_rel, *, findmnt_json=default_findmnt_json, placement=None):
    """Return read-only evidence about one dataset's disk; it never raises on absence.
    {bound, mounted, uuidMatches, markerMatches, currentGeneration, placementAgrees, hostRunning, pointerAhead}.
    pointerAhead: control/latest.json's contentSha differs from readlink(current) (a promote crashed after the swap).
    mounted: mountpoint is itself a mount point.  uuidMatches: that filesystem has the UUID.
    bound: binding.json can be read.  markerMatches: the marker names this UUID and relative path.
    currentGeneration: read from the `current` symlink (the truth, not the marker).
    placementAgrees: the placement entry (a dict, or None) is live and names the same dataset and
    plan digest as the marker.  hostRunning is None: it is not known here."""
    out = dict(bound=False, mounted=False, uuidMatches=False, markerMatches=False,
               currentGeneration=None, placementAgrees=False, hostRunning=None, pointerAhead=False)
    rel = [p for p in str(dataset_rel).split('/')] if isinstance(dataset_rel, str) else []
    try:
        row = _one_row(findmnt_json(mountpoint))
        out['mounted'] = isinstance(mountpoint, str) and os.path.normpath(str(row.get('target'))) == os.path.normpath(mountpoint) \
            and os.path.normpath(mountpoint) != '/'
        out['uuidMatches'] = out['mounted'] and row.get('uuid') == uuid
    except (ReceiveError, OSError, ValueError, subprocess.SubprocessError, AttributeError):
        return out
    if not out['uuidMatches']:
        return out
    try:
        root = MountRoot(mountpoint, uuid, findmnt_json)
    except (ReceiveError, OSError):
        out['uuidMatches'] = False
        return out
    marker = None
    try:
        marker = read_binding(root, rel)
        out['bound'] = marker is not None
        if marker is not None:
            out['markerMatches'] = marker.get('filesystemUuid') == uuid and marker.get('relativePath') == dataset_rel
        out['currentGeneration'] = current_generation(root, rel)
        out['pointerAhead'] = pointer_ahead(root, rel)
    except (ReceiveError, OSError, ValueError):
        pass
    finally:
        root.close()
    out['placementAgrees'] = _placement_agrees(placement, marker)
    return out


def _placement_agrees(placement, marker):
    """True when a live placement entry and the marker name the same dataset (and digest, when both have one)."""
    if not isinstance(placement, dict) or not isinstance(marker, dict):
        return False
    if placement.get('state') not in PLACEMENT_LIVE_STATES or placement.get('dataset') != marker.get('dataset'):
        return False
    wanted = (placement.get('plan') or {}).get('digest')
    held = (marker.get('plan') or {}).get('digest')
    return wanted is None or held is None or wanted == held
