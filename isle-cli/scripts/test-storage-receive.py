#!/usr/bin/env python3
"""Checks for storage_receive.py, storage_mount.py and the storage_cli status/check forms.
All work happens in temp directories.  Prints PASS/FAIL per check; exits nonzero on any failure."""
import collections
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import storage_cli
import storage_mount as sm
import storage_receive as sr
from storage_snapshot import encoded

FAILED = []
COUNT = [0]
UUID = '3bd28d7c-ba1e-4e6e-9a9f-23f684f4adf8'
REL = ['isle', 'datasets', 'apt-public-repo']
Vfs = collections.namedtuple('Vfs', 'f_bavail f_frsize f_blocks')
BIG = lambda fd: Vfs(10 ** 12, 1, 10 ** 12)


def check(name, ok):
    COUNT[0] += 1
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok:
        FAILED.append(name)


def raises(name, exc, func, *args, **kwargs):
    """Check that func raises exc; return the exception (or None)."""
    try:
        func(*args, **kwargs)
    except exc as error:
        check(name, True)
        return error
    except Exception as error:
        check(name + ' (wrong exception %r)' % error, False)
        return None
    check(name, False)
    return None


class Env:
    """A temp directory acting as the mount root, with a fake findmnt that can flip its uuid."""

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix='stm4-')
        self.mount = os.path.join(os.path.realpath(self.tmp), 'mnt')
        os.mkdir(self.mount)
        self.uuid = UUID

    def findmnt(self, path):
        return {'filesystems': [{'target': self.mount, 'uuid': self.uuid}]}

    def root(self):
        return sr.MountRoot(self.mount, UUID, self.findmnt)

    def dataset(self, *more):
        return os.path.join(self.mount, *REL, *more)

    def close(self):
        for base, dirs, files in os.walk(self.tmp):
            for name in dirs + files:
                with contextlib.suppress(OSError):
                    os.chmod(os.path.join(base, name), 0o755 if name in dirs else 0o644)
        shutil.rmtree(self.tmp, ignore_errors=True)


def make_manifest(files, dirs=()):
    """Return (document, published bytes) for a dict of path -> bytes, with a binding block."""
    entries = [dict(path=d, kind='directory') for d in dirs]
    entries += [dict(path=p, kind='file', size=len(b), sha256=hashlib.sha256(b).hexdigest()) for p, b in files.items()]
    doc = dict(schemaVersion=1, kind='isle-content-snapshot', totalBytes=sum(len(b) for b in files.values()),
               entries=sorted(entries, key=lambda e: e['path']))
    published = dict(doc, binding=dict(dataset='apt-public-repo', sourceDevice='isle-core', takenAt='t',
                                       lockPath='/l', planId=None, itemId=None))
    return doc, encoded(published)


FILES = {'Packages': b'package index', 'runtime/image.tar': b'archive bytes', 'runtime/deep/note.txt': b'note'}
DIRS = ['runtime', 'runtime/deep', 'empty']


def fetcher(files, log=None, corrupt=None):
    by_sha = {hashlib.sha256(b).hexdigest(): b for b in files.values()}

    def fetch(entry, sink):
        if log is not None:
            log.append(entry['sha256'])
        data = by_sha[entry['sha256']]
        sink.write(corrupt(entry, data) if corrupt else data)
    return fetch


def do_receive(env, files=FILES, dirs=DIRS, op='op1', fetch=None, **kwargs):
    doc, raw = make_manifest(files, dirs)
    root = env.root()
    try:
        parts = sr.receive(root, REL, doc, fetch or fetcher(files), op_id=op, statvfs=BIG, **kwargs)
    finally:
        root.close()
    return doc, raw, parts


# ------------------------------------------------------------------ paths

def path_checks():
    check('good path segments', sr.validate_manifest_path('a/b.c/d-e') == ['a', 'b.c', 'd-e'])
    check('single segment', sr.validate_manifest_path('Packages') == ['Packages'])
    cases = [('absolute', '/etc/passwd'), ('dotdot', 'a/../b'), ('leading dotdot', '../x'), ('dot segment', 'a/./b'),
             ('backslash', 'a\\b'), ('question mark', 'a?b'), ('hash', 'a#b'),
             ('control char', 'a\x01b'), ('newline', 'a\nb'), ('DEL', 'a\x7fb'), ('empty string', ''),
             ('empty segment', 'a//b'), ('trailing slash', 'a/'), ('NUL', 'a\x00b'),
             ('segment of 201 bytes', 'a/' + 'x' * 201), ('multibyte segment over 200 bytes', 'é' * 101),
             ('total over 4096', '/'.join(['x' * 100] * 41)), ('non-string', None), ('lone surrogate', 'a\ud800')]
    for name, value in cases:
        raises('path refused: ' + name, ValueError, sr.validate_manifest_path, value)
    check('segment of exactly 200 bytes accepted', len(sr.validate_manifest_path('x' * 200)[0]) == 200)
    check("'%' is a literal file-name character: a dpkg epoch file validates (94 such entries in the real apt repo)",
          sr.validate_manifest_path('bsdutils_1%3a2.39.3-9ubuntu6.6_amd64.deb') == ['bsdutils_1%3a2.39.3-9ubuntu6.6_amd64.deb'])
    check("'%' never decodes into a forbidden or traversal character: a%2eb and a%2f..%2fb stay one literal segment",
          sr.validate_manifest_path('a%2eb') == ['a%2eb'] and sr.validate_manifest_path('a%2f..%2fb') == ['a%2f..%2fb'])


def manifest_checks():
    doc, _ = make_manifest(FILES, DIRS)
    check('valid manifest accepted', len(sr.validate_manifest(doc)) == len(FILES) + len(DIRS))
    bad = dict(doc, totalBytes=1)
    raises('totalBytes mismatch refused', ValueError, sr.validate_manifest, bad)
    raises('wrong kind refused', ValueError, sr.validate_manifest, dict(doc, kind='x'))
    dup = dict(doc, entries=doc['entries'] + [doc['entries'][0]])
    raises('duplicate path refused', ValueError, sr.validate_manifest, dup)
    unsafe = dict(doc, entries=[dict(path='../x', kind='file', size=0, sha256='0' * 64)], totalBytes=0)
    raises('unsafe path in manifest refused', ValueError, sr.validate_manifest, unsafe)
    two = dict(doc, entries=[dict(path='a', kind='file', size=1, sha256='a' * 64), dict(path='b', kind='file', size=2, sha256='a' * 64)], totalBytes=3)
    raises('one sha two sizes refused', ValueError, sr.validate_manifest, two)


# ------------------------------------------------------------------ mount root

def mount_checks():
    env = Env()
    try:
        raises('mountpoint / refused', sr.ReceiveError, sr.MountRoot, '/', UUID, env.findmnt)
        raises('relative mountpoint refused', sr.ReceiveError, sr.MountRoot, 'mnt', UUID, env.findmnt)
        env.uuid = 'other'
        err = raises('wrong uuid refused at open', sr.ReceiveError, env.root)
        check('uuid refusal is retryable', err is not None and err.retryable)
        env.uuid = UUID
        link = os.path.join(env.tmp, 'link')
        os.symlink(env.mount, link)
        raises('symlinked mountpoint refused', sr.ReceiveError, sr.MountRoot, link, UUID, env.findmnt)
        wrong = lambda p: {'filesystems': [{'target': '/', 'uuid': UUID}]}
        raises('mount target mismatch refused', sr.ReceiveError, sr.MountRoot, env.mount, UUID, wrong)
        two = lambda p: {'filesystems': [{'target': env.mount, 'uuid': UUID}] * 2}
        raises('two findmnt rows refused', sr.ReceiveError, sr.MountRoot, env.mount, UUID, two)
        root = env.root()
        root.verify()
        check('verify passes on good mount', True)
        env.uuid = 'flipped'
        raises('verify fails after uuid flip', sr.ReceiveError, root.verify)
        env.uuid = UUID
        root.reopen()
        root.verify()
        check('reopen re-verifies', root.fd is not None)
        fd = root.dir_fd(['a', 'b'])
        check('dir_fd creates 0755 dirs', os.path.isdir(os.path.join(env.mount, 'a', 'b'))
              and (os.stat(os.path.join(env.mount, 'a')).st_mode & 0o777) == 0o755)
        os.close(fd)
        os.symlink(env.tmp, os.path.join(env.mount, 'sneaky'))
        raises('dir_fd refuses a symlink component', sr.ReceiveError, root.dir_fd, ['sneaky', 'x'])
        raises('dir_fd refuses ..', sr.ReceiveError, root.dir_fd, ['..'])
        raises('dir_fd create=False on missing raises', FileNotFoundError, root.dir_fd, ['nope'], False)
        root.close()
        raises('closed root fails verify', sr.ReceiveError, root.verify)
    finally:
        env.close()


# ------------------------------------------------------------------ receive

def receive_checks():
    env = Env()
    try:
        log = []
        doc, raw, parts = do_receive(env, fetch=fetcher(FILES, log))
        csha = sr.content_sha(doc)
        check('contentSha differs from manifestSha', csha != sr.manifest_sha(raw))
        check('generation named by contentSha', parts == REL + ['generations', csha])
        gen = env.dataset('generations', csha)
        got = {p: Path(gen, p).read_bytes() for p in FILES}
        check('all files received intact', got == FILES)
        check('directory entries created incl. empty', Path(gen, 'empty').is_dir() and Path(gen, 'runtime', 'deep').is_dir())
        store = env.dataset('store')
        check('store holds one file per sha', sorted(os.listdir(store)) == sorted(hashlib.sha256(b).hexdigest() for b in FILES.values()))
        check('generation files are hard links of store', all(os.stat(os.path.join(gen, p)).st_nlink == 2 for p in FILES))
        check('fetch called once per file', len(log) == 3)
        check('staging emptied', os.listdir(env.dataset('staging')) == [])
        check('no current before promote', not os.path.lexists(env.dataset('current')))

        root = env.root()
        check('verify_generation passes (against contentSha)', sr.verify_generation(root, REL, csha, raw) == csha)
        check('verify_generation refuses wrong contentSha', raises('verify wrong contentSha', sr.ReceiveError, sr.verify_generation, root, REL, 'a' * 64, raw) is not None)
        raises('verify_generation refuses manifest of other content', sr.ReceiveError, sr.verify_generation, root, REL, csha,
               make_manifest({'x': b'y'})[1])

        # second generation reuses store entries
        files2 = dict(FILES, **{'runtime/new.txt': b'brand new'})
        log2 = []
        doc2, raw2, parts2 = do_receive(env, files2, DIRS, op='op2', fetch=fetcher(files2, log2))
        check('second generation fetches only the new file', log2 == [hashlib.sha256(b'brand new').hexdigest()])
        check('second generation is a different directory', parts2 != parts and sr.content_sha(doc2) != csha)
        check('store inode shared across generations', os.stat(os.path.join(gen, 'Packages')).st_ino
              == os.stat(env.dataset('generations', sr.content_sha(doc2), 'Packages')).st_ino)
        check('old generation still verifies', sr.verify_generation(root, REL, csha, raw) == csha)
        check('new generation verifies', sr.verify_generation(root, REL, sr.content_sha(doc2), raw2) == sr.content_sha(doc2))

        # plan_receive
        store_fd = root.dir_fd(REL + ['store'])
        plan = sr.plan_receive(make_manifest(files2)[0], store_fd)
        check('plan_receive: all reused', plan['toFetch'] == [] and len(plan['reuse']) == 4 and plan['bytesToFetch'] == 0)
        plan = sr.plan_receive(make_manifest({'z': b'zzz', 'y': b'yyyyy'})[0], store_fd)
        check('plan_receive: bytes and largest', plan['bytesToFetch'] == 8 and plan['largestFile'] == 5 and len(plan['toFetch']) == 2)
        plan = sr.plan_receive(make_manifest({'z': b'zzz', 'zz': b'zzz'})[0], store_fd)
        check('plan_receive: equal sha fetched once', len(plan['toFetch']) == 1 and len(plan['reuse']) == 1 and plan['bytesToFetch'] == 3)
        wrong_size = os.path.join(env.dataset('store'), hashlib.sha256(b'q').hexdigest())
        Path(wrong_size).write_bytes(b'qq')
        plan = sr.plan_receive(make_manifest({'q': b'q'})[0], store_fd)
        check('plan_receive: wrong-size store entry not reused', len(plan['toFetch']) == 1)
        check('plan_receive without store', len(sr.plan_receive(make_manifest(FILES)[0], None)['toFetch']) == 3)
        os.close(store_fd)
        root.close()
    finally:
        env.close()


def dedupe_check():
    env = Env()
    try:
        files = {'a': b'same', 'b': b'same', 'c': b'other'}
        log = []
        doc, raw, parts = do_receive(env, files, (), fetch=fetcher(files, log))
        check('equal content fetched once, linked twice', len(log) == 2
              and os.stat(env.dataset('generations', sr.content_sha(doc), 'a')).st_ino == os.stat(env.dataset('generations', sr.content_sha(doc), 'b')).st_ino)
    finally:
        env.close()


def failure_checks():
    env = Env()
    try:
        flip = lambda e, d: d[:-1] + b'X'
        err = raises('sha mismatch raises', sr.ReceiveError, do_receive, env, fetch=fetcher(FILES, corrupt=flip))
        check('sha mismatch is retryable, stage named', err is not None and err.retryable and err.stage == 'receiving-content')
        staging = env.dataset('staging')
        check('staging file gone after mismatch', not os.path.isdir(staging) or all(not os.listdir(os.path.join(staging, d)) for d in os.listdir(staging)))
        check('nothing linked into store after mismatch', os.listdir(env.dataset('store')) == [])
        grow = lambda e, d: d + b'extra'
        err = raises('size overflow raises', sr.ReceiveError, do_receive, env, op='o2', fetch=fetcher(FILES, corrupt=grow))
        check('size overflow retryable', err is not None and err.retryable)
        short = lambda e, d: d[:-1]
        err = raises('short file (size mismatch) raises', sr.ReceiveError, do_receive, env, op='o3', fetch=fetcher(FILES, corrupt=short))
        check('size mismatch retryable', err is not None and err.retryable)
        check('still nothing in store', os.listdir(env.dataset('store')) == [])

        def boom(entry, sink):
            raise OSError('connection reset')
        err = raises('fetch exception wrapped', sr.ReceiveError, do_receive, env, op='o4', fetch=boom)
        check('fetch exception retryable', err is not None and err.retryable)

        # capacity
        doc, raw = make_manifest(FILES, DIRS)
        root = env.root()
        tiny = lambda fd: Vfs(1000, 1, 10 ** 9)
        err = raises('capacity refusal', sr.ReceiveError, sr.receive, root, REL, doc, fetcher(FILES), op_id='o5', statvfs=tiny)
        check('capacity refusal not retryable', err is not None and not err.retryable and 'space' in str(err))
        exact = lambda fd: Vfs(sum(len(b) for b in FILES.values()) + sr.HEADROOM_MIN, 1, 0)
        sr.receive(root, REL, doc, fetcher(FILES), op_id='o6', statvfs=exact)
        check('capacity exactly sufficient passes', True)
        check('headroom is 5 percent with a 64 MiB floor', sr.headroom(0) == 64 * 2 ** 20 and sr.headroom(10 ** 12) == 5 * 10 ** 10)
        root.close()

        # bad inputs
        raises('bad op id refused', sr.ReceiveError, do_receive, env, op='../x')
        bad = dict(doc, totalBytes=0)
        root = env.root()
        raises('invalid manifest refused by receive', sr.ReceiveError, sr.receive, root, REL, bad, fetcher(FILES), op_id='o7', statvfs=BIG)
        root.close()
    finally:
        env.close()


def mount_flip_check():
    env = Env()
    try:
        files = {'a': b'one', 'b': b'two', 'c': b'three'}
        doc, raw, parts = do_receive(env, files, ())
        csha = sr.content_sha(doc)
        root = env.root()
        sr.promote(root, REL, csha, raw, dataset='apt-public-repo', generation=1, op_id='p1')
        root.close()
        files2 = {'a': b'one', 'b': b'two', 'c': b'three', 'd': b'four', 'e': b'five'}
        doc2, raw2 = make_manifest(files2)
        calls = []

        def flipping(entry, sink):
            calls.append(entry['path'])
            sink.write(next(b for b in files2.values() if hashlib.sha256(b).hexdigest() == entry['sha256']))
            env.uuid = 'unplugged'
        root = env.root()
        err = raises('mount flip mid-receive aborts', sr.ReceiveError, sr.receive, root, REL, doc2, flipping, op_id='m2', statvfs=BIG)
        check('mount flip is retryable and names the stage', err is not None and err.retryable and err.stage == 'receiving-content')
        check('aborted after the first fetch', len(calls) == 1)
        env.uuid = UUID
        check('previous current untouched', sr.current_generation(root, REL) == csha)
        root.close()
    finally:
        env.close()


def symlink_checks():
    env = Env()
    try:
        root = env.root()
        os.makedirs(env.dataset())
        outside = os.path.join(env.tmp, 'outside')
        os.mkdir(outside)
        os.symlink(outside, env.dataset('store'))
        doc, raw = make_manifest(FILES, DIRS)
        raises('symlinked store dir refused', sr.ReceiveError, sr.receive, root, REL, doc, fetcher(FILES), op_id='s1', statvfs=BIG)
        check('nothing written through the symlink', os.listdir(outside) == [])
        os.unlink(env.dataset('store'))
        # a symlink parent inside the generation
        doc3, raw3 = make_manifest({'sub/f': b'x'}, ['sub'])
        gdir = env.dataset('generations', sr.content_sha(doc3))
        os.makedirs(gdir)
        os.symlink(outside, os.path.join(gdir, 'sub'))
        raises('symlink parent inside generation refused', sr.ReceiveError, sr.receive, root, REL, doc3, fetcher({'f': b'x'}), op_id='s2', statvfs=BIG)
        check('nothing written through generation symlink', os.listdir(outside) == [])
        root.close()
    finally:
        env.close()


# ------------------------------------------------------------------ verify, promote, binding

def verify_checks():
    env = Env()
    try:
        doc, raw, parts = do_receive(env)
        csha = sr.content_sha(doc)
        gen = env.dataset('generations', csha)
        root = env.root()
        store_fd = root.dir_fd(REL + ['store'])
        inv = sr.inventory_generation(root, parts, store_fd)
        check('inventory_generation equals the manifest content', inv == doc and sr.content_sha(inv) == csha)
        foreign = os.path.join(env.tmp, 'foreign-source')
        Path(foreign).write_bytes(b'foreign bytes')
        os.link(foreign, os.path.join(gen, 'extra-link'))
        err = raises('foreign hardlinked file refused by inventory_generation', sr.ReceiveError, sr.inventory_generation, root, parts, store_fd)
        check('refusal names the multi-link file', err is not None and 'extra-link' in str(err))
        os.unlink(os.path.join(gen, 'extra-link'))
        os.symlink('/etc/passwd', os.path.join(gen, 'link'))
        raises('symlink refused by inventory_generation', sr.ReceiveError, sr.inventory_generation, root, parts, store_fd)
        os.unlink(os.path.join(gen, 'link'))
        os.mkfifo(os.path.join(gen, 'fifo'))
        raises('special file refused by inventory_generation', sr.ReceiveError, sr.inventory_generation, root, parts, store_fd)
        os.unlink(os.path.join(gen, 'fifo'))
        Path(gen, '.hidden').write_text('x')
        raises('hidden file refused by inventory_generation', sr.ReceiveError, sr.inventory_generation, root, parts, store_fd)
        os.unlink(os.path.join(gen, '.hidden'))
        os.close(store_fd)
        check('generation verifies again after cleanup', sr.verify_generation(root, REL, csha, raw) == csha)

        # tamper: replace the file by a distinct inode with other bytes
        victim = os.path.join(gen, 'Packages')
        os.unlink(victim)
        Path(victim).write_bytes(b'tampered!!!!!')
        raises('tampered file fails verify_generation', sr.ReceiveError, sr.verify_generation, root, REL, csha, raw)
        os.unlink(victim)
        os.link(os.path.join(env.dataset('store'), hashlib.sha256(b'package index').hexdigest()), victim)
        os.chmod(os.path.join(env.dataset('store'), hashlib.sha256(b'archive bytes').hexdigest()), 0o644)
        with open(os.path.join(env.dataset('store'), hashlib.sha256(b'archive bytes').hexdigest()), 'wb') as handle:
            handle.write(b'archive BYTES')  # tamper in place through the shared inode
        raises('in-place tamper of a store file fails verify', sr.ReceiveError, sr.verify_generation, root, REL, csha, raw)
        root.close()
    finally:
        env.close()


def promote_checks():
    env = Env()
    try:
        doc, raw, parts = do_receive(env)
        csha, msha = sr.content_sha(doc), sr.manifest_sha(raw)
        root = env.root()
        check('current absent before promote', sr.current_generation(root, REL) is None)
        sr.promote(root, REL, csha, raw, dataset='apt-public-repo', generation=3, op_id='p1', promoted_at='2026-11-09T12:00:00+00:00')
        check('current_generation reads back contentSha', sr.current_generation(root, REL) == csha)
        check('current is a relative symlink', os.readlink(env.dataset('current')) == 'generations/' + csha)
        check('served through current', Path(env.dataset('current'), 'Packages').read_bytes() == b'package index')
        latest = json.loads(Path(env.dataset('control', 'latest.json')).read_text())
        check('control/latest.json consistent with current', latest == dict(contentSha=csha, manifestSha=msha, dataset='apt-public-repo',
              epoch=3, promotedAt='2026-11-09T12:00:00+00:00') and latest['contentSha'] == sr.current_generation(root, REL))
        check('control/current is one line with the contentSha', Path(env.dataset('control', 'current')).read_text() == csha + '\n')
        check('pointerAhead false right after promote', sr.pointer_ahead(root, REL) is False)
        check('raw manifest copied under its manifestSha', Path(env.dataset('control', 'manifest-%s.json' % msha)).read_bytes() == raw)
        check('no temp files left', not [n for n in os.listdir(env.dataset()) if '.tmp-' in n]
              and not [n for n in os.listdir(env.dataset('control')) if '.tmp-' in n])

        files2 = dict(FILES, extra=b'more')
        doc2, raw2, parts2 = do_receive(env, files2, DIRS, op='op2', fetch=fetcher(files2))
        csha2 = sr.content_sha(doc2)
        sr.promote(root, REL, csha2, raw2, dataset='apt-public-repo', generation=4, op_id='p2')
        check('second promote replaces current', sr.current_generation(root, REL) == csha2)
        # crash simulation: swap happened, latest still names the previous generation
        stale = Path(env.dataset('control', 'latest.json'))
        stale.write_text(json.dumps(dict(contentSha=csha, manifestSha=msha)) )
        check('stale latest.json -> pointerAhead True', sr.pointer_ahead(root, REL) is True)
        sr.promote(root, REL, csha2, raw2, dataset='apt-public-repo', generation=4, op_id='p2b')
        check('next promote repairs pointerAhead', sr.pointer_ahead(root, REL) is False
              and Path(env.dataset('control', 'current')).read_text() == csha2 + '\n')
        check('latest.json follows promote', json.loads(Path(env.dataset('control', 'latest.json')).read_text())['contentSha'] == csha2)
        check('old generation kept', os.path.isdir(env.dataset('generations', csha)))

        raises('promote refuses missing generation', sr.ReceiveError, sr.promote, root, REL, 'c' * 64,
               make_manifest({'q': b'q'})[1], dataset='d', generation=1)
        raises('promote refuses manifest of another content', sr.ReceiveError, sr.promote, root, REL, csha2, raw, dataset='d', generation=1)
        check('failed promotes leave current alone', sr.current_generation(root, REL) == csha2)
        raises('promote refuses bad generation epoch', sr.ReceiveError, sr.promote, root, REL, csha2, raw2, dataset='d', generation=-1)
        os.symlink('elsewhere', env.dataset('current.tmp-p3'))
        sr.promote(root, REL, csha, raw, dataset='d', generation=5, op_id='p3')
        check('stale temp symlink replaced', sr.current_generation(root, REL) == csha)
        os.unlink(env.dataset('current'))
        os.mkdir(env.dataset('current'))
        raises('current as a real directory refused', sr.ReceiveError, sr.promote, root, REL, csha2, raw2, dataset='d', generation=6, op_id='p4')
        check('failed rename leaves no temp link', not os.path.lexists(env.dataset('current.tmp-p4')))
        root.close()
    finally:
        env.close()


def binding_checks():
    env = Env()
    try:
        root = env.root()
        check('read_binding absent is None', sr.read_binding(root, REL) is None)
        record = {'schemaVersion': 2, 'dataset': 'apt-public-repo', 'filesystemUuid': UUID, 'relativePath': '/'.join(REL),
                  'currentGeneration': 'ab' * 32}
        sr.write_binding(root, REL, record)
        check('binding round trip', sr.read_binding(root, REL) == record)
        sr.write_binding(root, REL, dict(record, currentGeneration='cd' * 32))
        check('binding replaced atomically', sr.read_binding(root, REL)['currentGeneration'] == 'cd' * 32
              and not [n for n in os.listdir(env.dataset()) if '.tmp-' in n])
        Path(env.dataset('binding.json')).write_text('{broken')
        raises('damaged binding raises ValueError', ValueError, sr.read_binding, root, REL)
        os.unlink(env.dataset('binding.json'))
        os.symlink('/etc/hostname', env.dataset('binding.json'))
        raises('symlinked binding refused', ValueError, sr.read_binding, root, REL)
        os.unlink(env.dataset('binding.json'))
        raises('non-object binding refused', sr.ReceiveError, sr.write_binding, root, REL, [])
        root.close()
    finally:
        env.close()


# ------------------------------------------------------------------ evidence

def storage_check_checks():
    env = Env()
    rel = '/'.join(REL)
    try:
        empty = sr.storage_check(env.mount, UUID, rel, findmnt_json=env.findmnt)
        check('empty mount: bound, marker, generation, placement all false', empty == dict(
            bound=False, mounted=True, uuidMatches=True, markerMatches=False, currentGeneration=None,
            placementAgrees=False, hostRunning=None, pointerAhead=False))
        check('missing mountpoint reports false', sr.storage_check(os.path.join(env.tmp, 'nope'), UUID, rel,
              findmnt_json=lambda p: {'filesystems': []})['mounted'] is False)
        check('findmnt failure never raises', sr.storage_check(env.mount, UUID, rel, findmnt_json=lambda p: 1 / 0 if False else (_ for _ in ()).throw(OSError('x')))['mounted'] is False)
        check('root mountpoint is not mounted evidence', sr.storage_check('/', UUID, rel, findmnt_json=lambda p: {'filesystems': [{'target': '/', 'uuid': UUID}]})['mounted'] is False)
        env.uuid = 'wrong'
        wrong = sr.storage_check(env.mount, UUID, rel, findmnt_json=env.findmnt)
        check('wrong uuid: mounted but uuidMatches false', wrong['mounted'] and not wrong['uuidMatches'] and not wrong['bound'])
        env.uuid = UUID

        doc, raw, parts = do_receive(env)
        csha = sr.content_sha(doc)
        root = env.root()
        record = {'schemaVersion': 2, 'dataset': 'apt-public-repo', 'filesystemUuid': UUID, 'relativePath': rel,
                  'currentGeneration': csha, 'plan': {'digest': 'dd' * 32}}
        sr.write_binding(root, REL, record)
        sr.promote(root, REL, csha, raw, dataset='apt-public-repo', generation=1)
        root.close()
        entry = dict(dataset='apt-public-repo', state='migrated', plan={'digest': 'dd' * 32})
        full = sr.storage_check(env.mount, UUID, rel, findmnt_json=env.findmnt, placement=entry)
        check('after receive+promote everything true', full == dict(bound=True, mounted=True, uuidMatches=True, markerMatches=True,
              currentGeneration=csha, placementAgrees=True, hostRunning=None, pointerAhead=False))
        check('placement without entry disagrees', sr.storage_check(env.mount, UUID, rel, findmnt_json=env.findmnt)['placementAgrees'] is False)
        check('placement with other digest disagrees', sr.storage_check(env.mount, UUID, rel, findmnt_json=env.findmnt,
              placement=dict(entry, plan={'digest': 'ee' * 32}))['placementAgrees'] is False)
        check('placement recovery-needed disagrees', sr.storage_check(env.mount, UUID, rel, findmnt_json=env.findmnt,
              placement=dict(entry, state='recovery-needed'))['placementAgrees'] is False)
        check('placement for other dataset disagrees', sr.storage_check(env.mount, UUID, rel, findmnt_json=env.findmnt,
              placement=dict(entry, dataset='other'))['placementAgrees'] is False)
        root = env.root()
        sr.write_binding(root, REL, dict(record, filesystemUuid='someone-else'))
        root.close()
        check('marker for another uuid does not match', sr.storage_check(env.mount, UUID, rel, findmnt_json=env.findmnt)['markerMatches'] is False)
        check('currentGeneration comes from the symlink, not the marker',
              sr.storage_check(env.mount, UUID, rel, findmnt_json=env.findmnt)['currentGeneration'] == csha)
    finally:
        env.close()


def real_findmnt_check():
    if shutil.which('findmnt') is None:
        print('SKIP real findmnt probe (findmnt absent)')
        return
    env = Env()
    try:
        try:
            data = sr.default_findmnt_json(env.mount)
        except Exception as error:
            print('SKIP real findmnt probe (%s)' % error)
            return
        row = data['filesystems'][0]
        check('real findmnt returns a target row', row['target'].startswith('/'))
        raises('real findmnt: a plain directory is not the expected disk', sr.ReceiveError, sr.MountRoot, env.mount, UUID)
    finally:
        env.close()


def progress_check():
    env = Env()
    old = sr.PROGRESS_STEP
    try:
        sr.PROGRESS_STEP = 10
        files = {'a': b'x' * 6, 'b': b'y' * 6, 'c': b'z' * 6, 'd': b'w' * 3}
        seen = []
        do_receive(env, files, (), progress=lambda *a: seen.append(a))
        check('progress fires per step and once at the end', [s[0] for s in seen] == [12, 21, 21] or [s[0] for s in seen][-1] == 21 and len(seen) >= 2)
        check('progress reports totals', seen[-1] == (21, 21, 4, 4))
    finally:
        sr.PROGRESS_STEP = old
        env.close()


# ------------------------------------------------------------------ mount unit, cli

def mount_unit_checks():
    check('mount unit name escapes dash', sm.mount_unit_name('/mnt/pol-ssd') == 'mnt-pol\\x2dssd.mount')
    check('mount unit name nested', sm.mount_unit_name('/run/media/u/disk') == 'run-media-u-disk.mount')
    check('mount unit name root', sm.mount_unit_name('/') == '-.mount')
    check('mount unit name collapses slashes', sm.mount_unit_name('//mnt//x/') == 'mnt-x.mount')
    check('mount unit name leading dot escaped', sm.mount_unit_name('/.hid') == '\\x2ehid.mount')
    raises('mount unit name refuses relative', ValueError, sm.mount_unit_name, 'mnt/x')
    text = sm.unit_text('apt-public-repo', '/mnt/pol-ssd', '/mnt/pol-ssd/isle/datasets/apt-public-repo', 'isle-content-host', 'isle-isle-content-host')
    lines = text.splitlines()
    unit = 'mnt-pol\\x2dssd.mount'
    for want in ('RequiresMountsFor=/mnt/pol-ssd', 'BindsTo=' + unit, 'After=' + unit,
                 'AssertPathExists=/mnt/pol-ssd/isle/datasets/apt-public-repo/binding.json',
                 'ExecStartPre=/usr/local/bin/isle storage check --dataset apt-public-repo',
                 'WorkingDirectory=/etc/isle-mesh/apps/isle-content-host',
                 'Environment=ISLE_DATASET_PATH=/mnt/pol-ssd/isle/datasets/apt-public-repo',
                 'ExecStart=/usr/bin/docker compose -p isle-isle-content-host -f docker-compose.yml -f isle-overlay.yml up -d --no-build --pull never',
                 'ExecStop=/usr/bin/docker compose -p isle-isle-content-host -f docker-compose.yml -f isle-overlay.yml stop'):
        check('unit has ' + want.split('=')[0] + ' line', want in lines)
    # Run the unit's compose lines the way systemd would (WorkingDirectory + Environment), with a fake docker that
    # fails like compose does when it finds no configuration or an unset ISLE_DATASET_PATH.
    import subprocess as _sp, tempfile as _tf, shlex as _sx
    kv = dict(l.split('=', 1) for l in lines if '=' in l and not l.startswith('['))
    with _tf.TemporaryDirectory() as tmp:
        appdir = Path(tmp) / 'app'
        appdir.mkdir()
        for f in ('docker-compose.yml', 'isle-overlay.yml'):
            (appdir / f).write_text('services: {}\n')
        fake = Path(tmp) / 'docker'
        fake.write_text('#!/bin/sh\nfor f in $(echo "$@" | grep -o "\\-f [^ ]*" | cut -d" " -f2); do [ -f "$f" ] || exit 14; done\n'
                        'echo "$@" | grep -q -- "-f " || exit 14\n[ -n "$ISLE_DATASET_PATH" ] || exit 15\nexit 0\n')
        fake.chmod(0o755)
        env_k, env_v = kv['Environment'].split('=', 1)
        codes = []
        for key in ('ExecStart', 'ExecStop'):
            argv = _sx.split(kv[key])
            argv[0] = str(fake)
            codes.append(_sp.run(argv, cwd=str(appdir), env={'PATH': '/usr/bin:/bin', env_k: env_v}).returncode)
        check('unit compose lines run from WorkingDirectory with both files and the bind parameter set', codes == [0, 0])
    deploy = (HERE / 'app-deploy.sh').read_text()
    check('unit compose matches app-deploy (same APPS_DIR, project isle-<app>, both -f files, offline flags)',
          'APPS_DIR=' + sm.APPS_DIR in deploy and 'docker compose -p "isle-$NAME"' in deploy
          and '-f docker-compose.yml -f isle-overlay.yml up -d' in deploy and '(--no-build --pull never)' in deploy)
    raises('unit refuses newline injection', ValueError, sm.unit_text, 'apt', '/mnt/x\nExecStart=evil', '/mnt/x/d', 'a', 'p')
    raises('unit refuses dataset path outside mount', ValueError, sm.unit_text, 'apt', '/mnt/x', '/mnt/y/d', 'a', 'p')
    raises('unit refuses percent specifier', ValueError, sm.unit_text, 'apt', '/mnt/x', '/mnt/x/%h', 'a', 'p')
    raises('unit refuses bad dataset', ValueError, sm.unit_text, '../x', '/mnt/x', '/mnt/x/d', 'a', 'p')
    check('overlay_bind shape', sm.overlay_bind('ISLE_DATASET_PATH', '/mnt/pol-ssd/isle/d') == {
        'type': 'bind', 'source': '/mnt/pol-ssd/isle/d', 'target': '/data', 'read_only': True, 'bind': {'create_host_path': False}})
    raises('overlay_bind refuses relative source', ValueError, sm.overlay_bind, 'ISLE_DATASET_PATH', 'rel/x')
    raises('overlay_bind refuses bad param', ValueError, sm.overlay_bind, 'lower', '/x')


def cli_checks():
    tmp = tempfile.mkdtemp(prefix='stm4cli-')
    # `status` asks the core for the placement view and reads this machine's operation journal.  A test reaches
    # neither: on a device that hosts the dataset both answered and the first check below failed (pol, 2026-11-18).
    import operations
    real_view, real_latest = storage_cli._placement_view, operations.latest
    storage_cli._placement_view = lambda dataset, http: (None, 'unreachable')
    operations.latest = lambda name: None
    try:
        def run(*argv):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = storage_cli.main(list(argv))
            return code, json.loads(out.getvalue())
        code, data = run('status', '--dataset', 'apt-public-repo', '--root', tmp)
        check('status with no copies', code == 0 and data['placement'] is None and data['operation'] is None
              and data['receipt'] is None and data['mount']['bound'] is False)
        folder = os.path.join(tmp, 'apt-public-repo')
        os.makedirs(folder)
        Path(folder, 'binding.json').write_text(json.dumps({'currentGeneration': 'ab' * 32}))
        Path(folder, 'receipts.jsonl').write_text(json.dumps({'ok': False, 'at': '1'}) + '\nnot json\n' + json.dumps(
            {'ok': True, 'at': '2', 'plan': {'digest': 'dd'}, 'target': {'device': 'pol-core'}}) + '\n')
        code, data = run('status', '--dataset', 'apt-public-repo', '--root', tmp)
        check('status reads binding and last receipt', data['receipt'] == {'ok': True, 'digest': 'dd', 'at': '2'}
              and data['mount']['bound'] and data['mount']['currentGeneration'] == 'ab' * 32 and data['device'] == 'pol-core'
              and data['source'] == 'root-filesystem-copies')
        code, data = run('status', '--dataset', '../etc', '--root', tmp)
        check('status refuses bad dataset', code == 1 and not data['ok'])
        code, data = run('check', '--dataset', 'apt-public-repo', '--mountpoint', os.path.join(tmp, 'absent'), '--uuid', UUID,
                         '--path', 'isle/datasets/apt-public-repo')
        check('dataset check on absent disk reports false and exits 1', code == 1 and data['result']['mounted'] is False)
    finally:
        storage_cli._placement_view, operations.latest = real_view, real_latest
        shutil.rmtree(tmp, ignore_errors=True)


def sync_timer_checks():
    service, timer = sm.sync_timer_text('apt-public-repo')
    check('sync service: oneshot root ExecStart', 'Type=oneshot' in service and 'User=root' in service
          and 'ExecStart=/usr/local/bin/isle storage sync --dataset apt-public-repo\n' in service)
    check('sync timer: directives', all(d in timer for d in ('OnBootSec=5min', 'OnUnitActiveSec=10min', 'Persistent=true',
                                                              'Unit=isle-storage-sync@apt-public-repo.service', 'WantedBy=timers.target')))
    # phase A 2026-11-14: units named /usr/bin/isle, which no install creates (203/EXEC on pol). The entry point the
    # units name must be the one install-node-services.sh writes, and no storage unit may name /usr/bin/isle.
    installer = (HERE.parent.parent / 'install-node-services.sh').read_text()
    check('units name the CLI entry point the installer creates (%s)' % sm.ISLE_CLI,
          'cat > %s <<' % sm.ISLE_CLI in installer and '/usr/bin/isle' not in (HERE / 'storage_mount.py').read_text().replace('# The system CLI', '').split('ISLE_CLI =', 1)[1])
    check('sync unit names', sm.sync_unit_names('apt') == ('isle-storage-sync@apt.service', 'isle-storage-sync@apt.timer'))
    raises('sync timer refuses bad dataset', ValueError, sm.sync_timer_text, '../x')
    tmp = tempfile.mkdtemp(prefix='stm4c-unit-')
    try:
        enabled = lambda name: name.endswith('.timer')
        check('sync_timer_installed false with no files', sm.sync_timer_installed('apt', tmp, enabled) is False)
        Path(tmp, 'isle-storage-sync@apt.service').write_text(service)
        check('sync_timer_installed false with only the service', sm.sync_timer_installed('apt', tmp, enabled) is False)
        Path(tmp, 'isle-storage-sync@apt.timer').write_text(timer)
        check('sync_timer_installed true with both files and enabled', sm.sync_timer_installed('apt', tmp, enabled) is True)
        check('sync_timer_installed false when not enabled', sm.sync_timer_installed('apt', tmp, lambda name: False) is False)
        seen = []
        sm.sync_timer_installed('apt', tmp, lambda name: seen.append(name) or True)
        check('sync_timer_installed asks about the timer unit', seen == ['isle-storage-sync@apt.timer'])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


for group in (path_checks, manifest_checks, mount_checks, receive_checks, dedupe_check, failure_checks, mount_flip_check,
              symlink_checks, verify_checks, promote_checks, binding_checks, storage_check_checks, real_findmnt_check,
              progress_check, mount_unit_checks, sync_timer_checks, cli_checks):
    group()
print('%d failed' % len(FAILED) if FAILED else 'all %d checks passed' % COUNT[0])
sys.exit(1 if FAILED else 0)
