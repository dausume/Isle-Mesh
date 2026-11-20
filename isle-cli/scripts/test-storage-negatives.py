#!/usr/bin/env python3
"""Independent negative-control suite for the storage migration code (docs/STORAGE-MIGRATION-CONTRACT.md section 8).

Run from isle-cli/:  python3 scripts/test-storage-negatives.py

Every test asserts two things: (a) the operation refuses (exception / Refusal / exit code / receipt ok:false, with the
words the contract names where it names them) and (b) nothing was written or changed: a snapshot of the relevant fake
state (target disk tree, placement bytes, router and pin tables, receipts, DNS writes recorded by the fake io, source
tree) is compared before and after.  The fakes are pol's (test-storage-migrate.py Scenario, test-storage-core.py World /
FakeIo, test-storage-sync.py Scenario, test-storage-witness.py Fake/Env, test-storage-ownership.py shell harnesses);
they are loaded, not copied.  The assertions are independent.

CENSUS (docs section 8, counted by ZCensus at the end of this file, which fails if this text drifts)
  section 8 controls ............................. 107   (Disk 10, Content 9, Plan 12, Ownership 15, Receipts 13, v3 + v3.2 48)
  executed in this file .......................... 100
  skipped, needs root / loop device .............. 2     (unplugged-while-serving BindsTo; hand-run compose up with the
                                                          disk absent) and 4 root-only variants of executed controls
                                                          (*__loopdev: wrong UUID on a real mount, unplug mid-copy with real
                                                          st_dev, root-fs fallback by unmounting, symlink in the path).
                                                          test-storage-negatives-loopdev.sh runs those four for real; it
                                                          prints SKIP for the two that need systemd units or docker.
  NOT testable at this layer ..................... 5
     removal of the source before acceptance, plan edit that omits the source item, removal of the last infrastructure
     provider, client-supplied protectedRoles ignored: the removal-check route and plan validation are Isle App backend
     code (this repository holds only the client removal_guard.py); identical witness re-post idempotent vs a different
     witness for the same (dataset, G, device) rejected: the witness door is Isle App backend code (the core-side
     relay duplicate handling is exercised by test_identical_receipt_repost_idempotent).
  Executed with a stated limit: the router mDNS scan control runs the shell function that skips managed names
  (openwrt-router managed-names.sh) and checks both router scripts call it; a real router is not exercised.  The
  backend halves of the receipt door, the 403 claim door and CA issuance HTTP status are reached through their core-side
  evidence readers (mark-migrated, accept, cert-authority.issue PermissionError).
FINDINGS: tests that fail on pol's branch are kept failing on purpose; see the report that accompanies this file.
"""
import collections
import contextlib
import copy
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

LOOP = 'needs root/loop device: see test-storage-negatives-loopdev.sh'
NOTEST = 'not testable at this layer: '


def load_exec(name, filename, cut=None):
    """Execute pol's test module (optionally only the text before `cut`) so its fakes can be reused."""
    source = (HERE / filename).read_text()
    if cut is not None:
        source = source[:source.index(cut)]
    module = types.ModuleType(name)
    module.__file__ = str(HERE / filename)
    exec(compile(source, str(HERE / filename), 'exec'), module.__dict__)
    return module


import storage_contract as sc
import storage_core as core
import storage_migrate as sm
import storage_placement as sp
import storage_receive as sr
import storage_snapshot as ssnap
import storage_cli
import storage_mount

with contextlib.redirect_stdout(io.StringIO()):
    PM = load_exec('pol_migrate', 'test-storage-migrate.py')
    PC = load_exec('pol_core', 'test-storage-core.py')
    PO = load_exec('pol_owner', 'test-storage-ownership.py')
    SY = load_exec('pol_sync', 'test-storage-sync.py', cut='\nfor group in (current_checks')
    WI = load_exec('pol_witness', 'test-storage-witness.py', cut='# ---- happy path')
    RC = load_exec('pol_receive', 'test-storage-receive.py', cut='\nfor group in (path_checks')


# ------------------------------------------------------------------ snapshots

def tree(root):
    """{relative path: ('d',) | ('l', target) | ('f', size, sha256, nlink)} for everything below root (lstat, no follow)."""
    out = {}
    root = str(root)
    if not os.path.lexists(root):
        return out
    for base, dirs, files in os.walk(root):
        for name in dirs + files:
            path = os.path.join(base, name)
            rel = os.path.relpath(path, root)
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode):
                out[rel] = ('l', os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                out[rel] = ('d',)
            elif stat.S_ISREG(info.st_mode):
                with open(path, 'rb') as handle:
                    out[rel] = ('f', info.st_size, hashlib.sha256(handle.read()).hexdigest(), info.st_nlink)
            else:
                out[rel] = ('s', stat.S_IFMT(info.st_mode))
    return out


def only_dirs(snapshot):
    return all(v == ('d',) for v in snapshot.values())


def mig_snapshot(s):
    """Everything a pol migrate/sync Scenario could change, except the operation journal dir."""
    return dict(disk=tree(s.mount), units=tree(getattr(s, 'units', '/nonexistent')), state=tree(s.state),
                deploys=list(getattr(s, 'deploys', [])), posts=len(s.http.posts),
                systemctl=[list(c) for c in getattr(s, 'systemctl_calls', [])])


def mig_go(s, capacity=None, fstatvfs=None, systemctl=None, argv=None):
    """pol's Scenario.go with the capacity / systemctl seams exposed."""
    argv = argv or ['storage', 'migrate', '--dataset', s.req_dataset, '--plan', PM.PLAN_ID, '--revision', '2',
                    '--item', PM.ITEM_ID, '--digest', s.digest]
    env = {'ISLE_OPERATION_DIR': s.opdir, 'ISLE_STORAGE_STATE_DIR': s.state, 'ISLE_STORAGE_UNIT_DIR': s.units,
           'ISLE_ENROLLMENT_FILE': s.enroll, 'ISLE_AGENT_ADDRESS': PM.MY_ADDR}
    deps = sm.Deps(identity=lambda: s.identity, findmnt_json=s.findmnt, run=s.run_findmnt,
                   statvfs=capacity or os.statvfs, fstatvfs=fstatvfs or PM.BIG, deploy=s.deploy, lease_path=s.lease,
                   sleep=s.sleep, monotonic=lambda: s.clock[0], systemctl=systemctl or s.systemctl, probe_address=lambda: None)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        s.rc = sm.run(argv, env=env, http=s.http, now=PM.NOW, stage_hook=s.hook, deps=deps)
    s.output = buffer.getvalue()
    path = Path(s.opdir) / 'storage-receipt.json'
    s.receipt = json.loads(path.read_text()) if path.exists() else None
    return s


@contextlib.contextmanager
def mig(**kwargs):
    s = PM.Scenario(**kwargs)
    try:
        yield s
    finally:
        s.close()


@contextlib.contextmanager
def sync_scn(**kwargs):
    s = SY.Scenario(**kwargs)
    try:
        yield s
    finally:
        s.close()


def core_snapshot(w):
    io_ = w.io
    claims = io_.placement_path.parent / 'placement-claims.json'
    return dict(placement=w.raw(), claims=claims.read_bytes() if claims.exists() else b'', router=dict(io_.router),
                pins=dict(io_.pins), registered=list(io_.registered), pin_log=list(io_.pin_log))


class MigAsserts:
    def assert_refused(self, s, stage, words=None, retryable=None):
        r = s.receipt or {}
        self.assertEqual(s.rc, 1, s.output)
        self.assertIs(r.get('ok'), False, r)
        self.assertEqual(r.get('failedStage'), stage, r.get('words'))
        if words:
            self.assertIn(words.lower(), r.get('words', '').lower())
        if retryable is not None:
            self.assertIs(r.get('retryable'), retryable)

    def assert_no_success_anywhere(self, s):
        self.assertFalse(any(p[1].get('ok') is True for p in s.http.posts), 'a success receipt was posted')
        self.assertFalse(s.deploys, 'a deploy happened')

    def assert_not_promoted(self, s):
        self.assertFalse(s.disk_has('current'), 'current exists')
        self.assertFalse(s.disk_has('binding.json'), 'binding.json exists')
        self.assertFalse(os.path.exists(s.units) and os.listdir(s.units), 'a unit was installed')
        self.assertFalse((Path(s.state) / PM.DATASET / 'binding.json').exists(), 'root-fs binding copy exists')


class CoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.w = PC.World(self.tmp.name)
        self.stderr = contextlib.redirect_stderr(io.StringIO())
        self.stderr.__enter__()
        self.addCleanup(self.stderr.__exit__, None, None, None)

    def refuse(self, call, words=None, snapshot=True, w=None):
        w = w or self.w
        before = core_snapshot(w)
        code = call()
        self.assertEqual(code, 1, w.io.said())
        if words:
            self.assertIn(words, w.io.said())
        if snapshot:
            after = core_snapshot(w)
            self.assertEqual(after, before, 'state changed by a refused operation')
        return code


LOOP_CONTROL_NAMES = []


# ================================================================== Disk and mount

class DiskAndMount(MigAsserts, unittest.TestCase):

    def test_wrong_disk_uuid_mismatch(self):
        with mig() as s:
            s.fs_uuid = '00000000-dead-beef-0000-000000000000'
            before = mig_snapshot(s)
            mig_go(s)
            self.assert_refused(s, 'preflight-storage', 'UUID', )
            self.assertEqual(mig_snapshot(s), before)
            self.assert_no_success_anywhere(s)
        env = RC.Env()
        try:
            env.uuid = 'some-other-filesystem'
            before = tree(env.mount)
            with self.assertRaises(sr.ReceiveError) as caught:
                env.root()
            self.assertTrue(caught.exception.retryable)
            self.assertIn('UUID', str(caught.exception))
            self.assertEqual(tree(env.mount), before)
        finally:
            env.close()
        with sync_scn() as s:                     # the replica sync refuses the same way
            s.fs_uuid = 'ffffffff-0000-0000-0000-000000000000'
            before = tree(s.mount)
            s.go()
            self.assertEqual(s.rc, 1)
            self.assertIn('UUID', s.receipt['words'])
            self.assertEqual(tree(s.mount), before)
            self.assertEqual(s.current(), 'generations/' + s.seed.content_sha)
            self.assertFalse(s.http.posts)

    def test_unplugged_mid_copy_uuid_or_st_dev_changes(self):
        with mig() as s:
            original, fetched = s.http.stream_to, []

            def unplug_after_first(url, sink, resolve=None, expected_size=None):
                original(url, sink, resolve, expected_size)
                fetched.append(url)
                s.fs_uuid = 'unplugged-and-replaced'
            s.http.stream_to = unplug_after_first
            mig_go(s)
            self.assert_refused(s, 'receiving-content', 'UUID', retryable=True)
            self.assertEqual(len(fetched), 1, 'the copy did not stop at the first mount check')
            self.assert_not_promoted(s)
            self.assert_no_success_anywhere(s)
            self.assertIsNot(s.receipt.get('contentVerified'), True)
            self.assertFalse(os.listdir(os.path.join(s.dataset_path, 'staging')), 'staging not cleaned')
        env = RC.Env()                            # st_dev change on the held mount root descriptor
        try:
            files = {'a': b'one', 'b': b'two', 'c': b'three'}
            doc, raw = RC.make_manifest(files)
            root = env.root()
            by_sha = {hashlib.sha256(v).hexdigest(): v for v in files.values()}

            def swap_device(entry, sink):
                sink.write(by_sha[entry['sha256']])
                root.st_dev += 1                  # what the descriptor would report on a different device
            with self.assertRaises(sr.ReceiveError) as caught:
                sr.receive(root, RC.REL, doc, swap_device, op_id='mid', statvfs=RC.BIG)
            self.assertTrue(caught.exception.retryable)
            self.assertIn('st_dev', str(caught.exception))
            self.assertIsNone(sr.current_generation(root, RC.REL))
            gen = env.dataset('generations', sr.content_sha(doc))
            self.assertEqual(len([e for e in tree(gen) if tree(gen)[e][0] == 'f']), 1, 'copy continued after st_dev change')
            root.close()
        finally:
            env.close()

    @unittest.skip(LOOP)
    def test_unplugged_mid_copy_uuid_or_st_dev_changes__loopdev(self):
        """Skeleton (loopdev script does this): loop device with ext4, MountRoot + sr.receive of a large manifest in a
        thread, `losetup -d`/umount -l while the fetch callback sleeps, then assert ReceiveError(retryable) with
        'st_dev'/'UUID' words, no `current`, no binding.json, store holds only verified objects."""

    @unittest.skip(LOOP)
    def test_wrong_disk_uuid_mismatch__loopdev(self):
        """Skeleton: mkfs.ext4 a sparse file (UUID A), mount it on a temp mountpoint through a loop device, build
        sr.MountRoot(mountpoint, UUID_B) with the REAL findmnt and assert ReceiveError(retryable) naming both UUIDs,
        and that sm.Migration stage 2 (storage_preflight.inspect with the real findmnt) refuses with 'UUID'; the mounted
        filesystem is byte-for-byte unchanged (tree hash before/after)."""

    @unittest.skip(LOOP)
    def test_root_filesystem_fallback__loopdev(self):
        """Skeleton: with the loop disk mounted, `umount` it, then run storage_preflight.inspect(dataset path, UUID) and
        sr.MountRoot against the now-empty mountpoint (real findmnt answers the root fs) and assert refusal with 'root
        filesystem' / UUID words and that nothing was created below the mountpoint."""

    @unittest.skip(LOOP)
    def test_symlink_in_the_path__loopdev(self):
        """Skeleton: on a real loop mount make <mount>/isle a symlink to a directory on another filesystem and assert
        storage_preflight.inspect, sr.MountRoot.dir_fd and sr.receive all refuse and the target directory stays empty."""

    @unittest.skip(LOOP)
    def test_unplugged_while_serving_bindsto_stops_the_host(self):
        """Skeleton: install isle-storage-<ds>.service via storage_mount.unit_text on a loop mount, start it with a
        stub ExecStart, `umount`/`losetup -d` the disk, assert `systemctl is-active` becomes inactive (BindsTo=<mount unit>)
        within a few seconds and the unit is not restarted (restart: no).  Needs root and systemd."""

    def test_insufficient_capacity_including_staging_and_store_delta(self):
        tiny = lambda p: types.SimpleNamespace(f_bavail=1, f_frsize=1, f_flag=0, f_blocks=10)
        with mig() as s:                           # stage 2: plan total + 5 % headroom
            before = mig_snapshot(s)
            mig_go(s, capacity=tiny)
            self.assert_refused(s, 'preflight-storage', 'Insufficient')
            self.assertEqual(mig_snapshot(s), before)
        with mig() as s:                           # stage 3: total + LARGEST FILE (staging) + headroom
            total = s.doc['totalBytes']
            enough_for_total_only = lambda fd: PM.Vfs(total + sr.HEADROOM_MIN, 1, 0)
            mig_go(s, fstatvfs=enough_for_total_only)
            self.assert_refused(s, 'fetching-manifest', 'Insufficient space')
            snap = tree(s.mount)
            self.assertTrue(only_dirs(snap), 'a file was written despite insufficient capacity: %r' % snap)
            self.assertFalse(s.disk_has('store'))
            self.assert_no_success_anywhere(s)
        env = RC.Env()                             # during the copy: remaining bytes + headroom
        try:
            doc, raw = RC.make_manifest(RC.FILES, RC.DIRS)
            root = env.root()
            nothing_free = lambda fd: PM.Vfs(0, 1, 0)
            with self.assertRaises(sr.ReceiveError) as caught:
                sr.receive(root, RC.REL, doc, RC.fetcher(RC.FILES), op_id='cap', statvfs=nothing_free)
            self.assertFalse(caught.exception.retryable)
            self.assertIn('Insufficient space', str(caught.exception))
            written = {k: v for k, v in tree(env.mount).items() if v[0] != 'd'}
            self.assertEqual(written, {}, 'bytes were written with no free space')
            root.close()
        finally:
            env.close()
        with sync_scn() as s:                      # store delta of a later generation
            before = tree(s.mount)
            env_ = {'ISLE_OPERATION_DIR': s.opdir, 'ISLE_STORAGE_STATE_DIR': s.state}
            deps = SY.ss.Deps(identity=lambda: SY.DEVICE, findmnt_json=s.findmnt, fstatvfs=lambda fd: PM.Vfs(sr.HEADROOM_MIN, 1, 0),
                              lease_path=s.lease)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = SY.ss.run(['storage', 'sync', '--dataset', SY.DATASET], env=env_, http=s.http, now=lambda: s.clock,
                               stage_hook=None, deps=deps)
            receipt = json.loads((Path(s.opdir) / 'storage-receipt.json').read_text())
            self.assertEqual(rc, 1)
            self.assertIn('Insufficient', receipt['words'])
            self.assertEqual(s.current(), 'generations/' + s.seed.content_sha)
            self.assertEqual({k: v for k, v in tree(s.mount).items() if v[0] != 'd'},
                             {k: v for k, v in before.items() if v[0] != 'd'}, 'files appeared for an unaffordable delta')

    def test_root_filesystem_fallback(self):
        root_row = lambda p: {'filesystems': [{'target': '/', 'uuid': PM.UUID, 'fstype': 'ext4', 'options': 'rw,relatime', 'source': '/dev/root'}]}
        self.assertFalse(os.path.lexists('/isle'), 'test host has /isle; cannot assert the negative')
        with mig(mountpoint='/') as s:             # (a) the plan names /
            before = tree(os.path.join(s.tmp, 'mnt'))
            mig_go(s)
            self.assert_refused(s, 'preflight-storage', '/')
            self.assertEqual(tree(os.path.join(s.tmp, 'mnt')), before)
            self.assertFalse(os.path.lexists('/isle'), 'a dataset dir was created on the real root fs')
            self.assert_no_success_anywhere(s)
        with mig() as s:                           # (b) the directory is not a mount point: findmnt answers /
            s.findmnt = root_row
            before = mig_snapshot(s)
            mig_go(s)
            self.assert_refused(s, 'preflight-storage', 'root filesystem')
            self.assertEqual(mig_snapshot(s), before)
        with sync_scn() as s:                      # (c) sync with the disk gone
            s.findmnt = root_row
            before = tree(s.mount)
            s.go()
            self.assertEqual(s.rc, 1)
            self.assertEqual(tree(s.mount), before)
            self.assertEqual(s.current(), 'generations/' + s.seed.content_sha)
        env = RC.Env()                             # (d) the evidence readers
        try:
            before = tree(env.mount)
            seen = sr.storage_check(env.mount, PM.UUID, '/'.join(RC.REL), findmnt_json=root_row)
            self.assertFalse(seen['mounted'])
            self.assertFalse(seen['uuidMatches'])
            self.assertEqual(tree(env.mount), before)
            with self.assertRaises(ValueError) as caught:
                storage_cli.check_path(env.mount, mount_of=lambda path: {'target': '/', 'source': '/dev/root', 'uuid': 'x'})
            self.assertIn('root filesystem', str(caught.exception))
        finally:
            env.close()

    def test_relative_dataset_dir_present_on_root_fs(self):
        root_row = lambda p: {'filesystems': [{'target': '/', 'uuid': 'root-fs-uuid', 'fstype': 'ext4', 'options': 'rw', 'source': '/dev/root'}]}
        with mig() as s:
            os.makedirs(os.path.join(s.dataset_path, 'store'))
            Path(s.dataset_path, 'leftover').write_bytes(b'created on the root fs while the disk was unplugged')
            s.findmnt = root_row                   # the disk is not mounted: everything below is on /
            before = mig_snapshot(s)
            mig_go(s)
            self.assertEqual(s.rc, 1, s.output)
            self.assertIs(s.receipt['ok'], False)
            self.assertEqual(s.receipt['failedStage'], 'preflight-storage')
            self.assertTrue(re.search('UUID|root filesystem', s.receipt['words']), s.receipt['words'])
            self.assertEqual(mig_snapshot(s), before)
            self.assert_no_success_anywhere(s)

    def test_mount_not_a_systemd_unit(self):
        with mig() as s:
            real = s.systemctl

            def no_mount_unit(args):               # every query about a mount unit says: no such unit
                if args and args[0] in ('is-active', 'show', 'status', 'cat', 'list-units', 'list-unit-files'):
                    return 1
                return real(args)
            before = mig_snapshot(s)
            mig_go(s, systemctl=no_mount_unit)
            self.assert_refused(s, 'preflight-storage')
            self.assertIn('systemd', s.receipt['words'].lower())
            self.assertEqual(mig_snapshot(s), before)
            self.assert_no_success_anywhere(s)

    def test_marker_present_but_placement_disagrees(self):
        env = RC.Env()
        try:
            files = {'a': b'one', 'b': b'two'}
            doc, raw, parts = RC.do_receive(env, files, ())
            root = env.root()
            sr.promote(root, RC.REL, sr.content_sha(doc), raw, dataset='apt-public-repo', generation=3, op_id='p')
            marker = dict(dataset='apt-public-repo', filesystemUuid=PM.UUID, mountpoint=env.mount,
                          relativePath='/'.join(RC.REL), plan=dict(digest='b' * 64), currentGeneration=sr.content_sha(doc))
            sr.write_binding(root, RC.REL, marker)
            root.close()
            before = tree(env.mount)
            rel = '/'.join(RC.REL)
            live = dict(state='migrated', dataset='apt-public-repo', plan=dict(digest='b' * 64))
            self.assertTrue(sr.storage_check(env.mount, PM.UUID, rel, findmnt_json=env.findmnt, placement=live)['placementAgrees'])
            for label, placement in (('other digest', dict(live, plan=dict(digest='c' * 64))),
                                     ('other dataset', dict(live, dataset='some-other-set')),
                                     ('recovery-needed', dict(live, state='recovery-needed')),
                                     ('no placement', None)):
                seen = sr.storage_check(env.mount, PM.UUID, rel, findmnt_json=env.findmnt, placement=placement)
                self.assertTrue(seen['bound'] and seen['markerMatches'], label)
                self.assertFalse(seen['placementAgrees'], label)
            self.assertEqual(tree(env.mount), before, 'storage check is not read-only')
        finally:
            env.close()
        with mig() as s:                           # the migrate launch gate (stage 7) refuses on the same disagreement
            def corrupt_marker(label, info):
                if label == 'deploying-host':
                    path = Path(s.dataset_path, 'binding.json')
                    record = json.loads(path.read_text())
                    record['plan']['digest'] = 'c' * 64
                    path.write_text(json.dumps(record))
            s.hook_extra = corrupt_marker
            mig_go(s)
            self.assert_refused(s, 'deploying-host', 'storage check fails')
            self.assertEqual(s.deploys, [])
            self.assert_no_success_anywhere(s)
            self.assertIsNot(s.receipt.get('hostDeployed'), True)

    @unittest.skip(LOOP)
    def test_hand_run_compose_up_with_disk_absent(self):
        """Skeleton: with the loop disk unmounted run `docker compose -f <release compose> -f isle-overlay.yml up -d` for a
        bind with create_host_path:false (storage_mount.overlay_bind) and assert docker exits non-zero, that
        <mountpoint>/<relativePath> was NOT created on the root fs, and no container is running.  Needs docker + root."""

    def test_each_launch_path_runs_storage_check(self):
        gate = re.compile(r'isle storage check|storage_cli\.py[" ]+check(?![-\w])|storage\.sh check|storage check')
        paths = {'app deploy': ['app-deploy.sh'], 'app reconcile': ['reconcile.sh', 'runtime_reconcile.py', 'app_overlay.py'],
                 'update apply': ['update.py', 'update.sh']}
        for label, names in paths.items():
            with self.subTest(launch_path=label):
                texts = [(HERE / n).read_text() for n in names if (HERE / n).exists()]
                self.assertTrue(any(gate.search(text) for text in texts),
                                '%s: none of %s runs storage check before compose up' % (label, ', '.join(names)))
        unit = storage_mount.unit_text('apt-public-repo', '/mnt/pol-ssd', '/mnt/pol-ssd/isle/datasets/apt-public-repo',
                                       'isle-content-host', 'isle-isle-content-host')
        pre = [l for l in unit.splitlines() if l.startswith('ExecStartPre=')]
        self.assertEqual(len(pre), 1)
        self.assertTrue(gate.search(pre[0]), 'the mount-gate unit does not run storage check')
        argv = pre[0].split('=', 1)[1].split()
        self.assertEqual(argv[1:3], ['storage', 'check'])
        with self.subTest(launch_path='systemd unit command line'):
            usage = io.StringIO()
            with contextlib.redirect_stderr(usage), contextlib.redirect_stdout(io.StringIO()):
                try:
                    code = storage_cli.main(['check'] + argv[3:])
                except SystemExit as exit_:
                    code = exit_.code
            self.assertNotEqual(code, 2, 'the unit ExecStartPre line is a usage error and can never pass: ' + usage.getvalue().strip()[-90:])


def sync_go(s, hook=None, fstatvfs=None):
    env = {'ISLE_OPERATION_DIR': s.opdir, 'ISLE_STORAGE_STATE_DIR': s.state}
    deps = SY.ss.Deps(identity=lambda: SY.DEVICE, findmnt_json=s.findmnt, fstatvfs=fstatvfs or SY.BIG, lease_path=s.lease)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        s.rc = SY.ss.run(['storage', 'sync', '--dataset', SY.DATASET], env=env, http=s.http, now=lambda: s.clock,
                         stage_hook=hook, deps=deps)
    path = Path(s.opdir) / 'storage-receipt.json'
    s.receipt = json.loads(path.read_text()) if path.exists() else None
    return s


def served_state(s):
    """What a reader of the replica sees: current link, control files, binding, the serving generation's bytes."""
    base = Path(s.dataset_path)
    strip = lambda rows: {k: v[:3] for k, v in rows.items()}          # nlink legitimately grows when a new generation links the store
    return dict(current=os.readlink(base / 'current'), control=strip(tree(base / 'control')),
                binding=(base / 'binding.json').read_bytes(), generation=strip(tree(base / 'generations' / s.seed.content_sha)),
                served=strip(tree(base / 'current')))


EMPTY = hashlib.sha256(b'').hexdigest()


# ================================================================== Content and paths

class ContentAndPaths(MigAsserts, unittest.TestCase):

    def test_tampered_manifest(self):
        with mig() as s:
            s.served_manifest = s.raw + b' '
            before = mig_snapshot(s)
            mig_go(s)
            self.assert_refused(s, 'fetching-manifest', 'snapshotSha256')
            self.assertTrue(only_dirs(tree(s.mount)), tree(s.mount))
            self.assertFalse(s.disk_has('store'))
            self.assertEqual(mig_snapshot(s)['units'], before['units'])
            self.assert_no_success_anywhere(s)
        with mig() as s:                          # same length, one byte flipped
            flipped = bytearray(s.raw)
            flipped[10] ^= 0x01
            s.served_manifest = bytes(flipped)
            mig_go(s)
            self.assert_refused(s, 'fetching-manifest')
            self.assertTrue(only_dirs(tree(s.mount)))
        with sync_scn() as s:
            s.served_manifest = s.pub.raw + b' '
            before = tree(s.mount)
            s.go()
            self.assertEqual(s.rc, 1)
            self.assertIn('does not hash to the snapshotSha256', s.receipt['words'])
            self.assertEqual(tree(s.mount), before)
            self.assertEqual(s.http.fetched_objects(), [])

    def test_tampered_file(self):
        with mig() as s:
            victim = hashlib.sha256(PM.FILES['a/one.deb']).hexdigest()
            s.http.corrupt.add(victim)
            mig_go(s)
            self.assert_refused(s, 'receiving-content', 'sha256', retryable=True)
            self.assertFalse(os.path.lexists(os.path.join(s.dataset_path, 'store', victim)), 'the corrupt object was stored')
            self.assert_not_promoted(s)
            self.assert_no_success_anywhere(s)
            self.assertEqual(os.listdir(os.path.join(s.dataset_path, 'staging')), [])
        env = RC.Env()
        try:
            doc, raw, parts = RC.do_receive(env)
            csha = sr.content_sha(doc)
            gen = env.dataset('generations', csha)
            victim = os.path.join(gen, 'Packages')
            os.unlink(victim)
            Path(victim).write_bytes(b'tampered bytes')   # a distinct inode with other bytes
            before = tree(env.mount)
            root = env.root()
            with self.assertRaises(sr.ReceiveError):
                sr.verify_generation(root, RC.REL, csha, raw)
            root.close()
            self.assertEqual(tree(env.mount), before, 'verification changed the disk')
        finally:
            env.close()

    def test_manifest_path_escape(self):
        # '%' is a literal file-name character (dpkg epoch files: name_1%3a2.0_amd64.deb). Nothing percent-decodes a
        # manifest path locally, so an encoded '..' stays one literal segment and can never traverse.
        self.assertEqual(sr.validate_manifest_path('a%2e%2e/b'), ['a%2e%2e', 'b'])
        self.assertEqual(sr.validate_manifest_path('%2e%2e'), ['%2e%2e'])
        self.assertEqual(sr.validate_manifest_path('pool/bsdutils_1%3a2.39_amd64.deb'), ['pool', 'bsdutils_1%3a2.39_amd64.deb'])
        bad = ['/etc/passwd', '../escape', 'a/../../escape', '..', 'a\\b', 'a//b', 'a/', 'a?b', 'a#b', 'a\x00b', 'a\nb']
        for path in bad:
            with self.subTest(path=path):
                with mig(manifest_extra=[dict(path=path, kind='file', size=0, sha256=EMPTY)]) as s:
                    before_top = set(os.listdir(s.tmp))
                    mig_go(s)
                    self.assert_refused(s, 'fetching-manifest', 'not acceptable')
                    self.assertTrue(only_dirs(tree(s.mount)), tree(s.mount))
                    self.assertEqual(set(os.listdir(s.tmp)) - before_top - {'state'}, set(), 'something was created beside the mount')
                    self.assert_no_success_anywhere(s)
                env = RC.Env()
                try:
                    doc = dict(schemaVersion=1, kind='isle-content-snapshot', totalBytes=0,
                               entries=[dict(path=path, kind='file', size=0, sha256=EMPTY)])
                    before = tree(env.mount)
                    root = env.root()
                    with self.assertRaises(sr.ReceiveError):
                        sr.receive(root, RC.REL, doc, lambda e, sink: None, op_id='esc', statvfs=RC.BIG)
                    root.close()
                    self.assertEqual(tree(env.mount), before)
                    self.assertEqual(set(os.listdir(env.tmp)), {'mnt'})
                finally:
                    env.close()

    def test_symlink_or_special_file_at_the_receiver_or_symlink_parent(self):
        env = RC.Env()
        try:
            root = env.root()
            os.makedirs(env.dataset())
            outside = os.path.join(env.tmp, 'outside')
            os.mkdir(outside)
            os.symlink(outside, env.dataset('store'))          # symlinked store directory
            doc, raw = RC.make_manifest(RC.FILES, RC.DIRS)
            before = tree(env.tmp)
            with self.assertRaises(sr.ReceiveError):
                sr.receive(root, RC.REL, doc, RC.fetcher(RC.FILES), op_id='s1', statvfs=RC.BIG)
            self.assertEqual(os.listdir(outside), [])
            self.assertEqual(tree(env.tmp), before)
            os.unlink(env.dataset('store'))
            doc3, raw3 = RC.make_manifest({'sub/f': b'x'}, ['sub'])   # symlink parent inside the generation
            gdir = env.dataset('generations', sr.content_sha(doc3))
            os.makedirs(gdir)
            os.symlink(outside, os.path.join(gdir, 'sub'))
            before = tree(env.tmp)
            with self.assertRaises(sr.ReceiveError):
                sr.receive(root, RC.REL, doc3, RC.fetcher({'f': b'x'}), op_id='s2', statvfs=RC.BIG)
            self.assertEqual(os.listdir(outside), [])
            self.assertEqual({k for k in tree(env.tmp) if k.startswith('outside')}, {k for k in before if k.startswith('outside')})
            root.close()
        finally:
            env.close()
        env = RC.Env()
        try:                                                   # special file / symlink / hidden in a received generation
            doc, raw, parts = RC.do_receive(env)
            csha = sr.content_sha(doc)
            gen = env.dataset('generations', csha)
            root = env.root()
            store = root.dir_fd(RC.REL + ['store'])
            for label, make in (('fifo', lambda p: os.mkfifo(p)), ('symlink', lambda p: os.symlink('/etc/passwd', p)),
                                ('hidden file', lambda p: Path(p).write_text('x'))):
                path = os.path.join(gen, '.hidden' if label == 'hidden file' else 'planted')
                make(path)
                before = tree(env.mount)
                with self.assertRaises(sr.ReceiveError, msg=label):
                    sr.inventory_generation(root, parts, store)
                self.assertEqual(tree(env.mount), before, label)
                os.unlink(path)
            os.close(store)
            root.close()
        finally:
            env.close()
        with mig() as s:                                       # the dataset directory itself is a symlink off the disk
            outside = os.path.join(s.tmp, 'elsewhere')
            os.mkdir(outside)
            os.symlink(outside, os.path.join(s.mount, 'isle'))
            before = mig_snapshot(s)
            mig_go(s)
            self.assert_refused(s, 'preflight-storage', 'symlink')
            self.assertEqual(os.listdir(outside), [])
            self.assertEqual(mig_snapshot(s), before)

    def test_interrupted_sync_keeps_the_previous_generation_serving(self):
        with sync_scn() as s:
            def reset(url, sink, resolve=None, expected_size=None):
                sink.write(b'part')
                raise SY.HttpError('connection reset by peer', None)
            s.http.stream_to = reset
            before = served_state(s)
            store_before = set(tree(os.path.join(s.dataset_path, 'store')))
            s.go()
            self.assertEqual(s.rc, 1)
            self.assertIs(s.receipt['ok'], False)
            self.assertIs(s.receipt['retryable'], True)
            self.assertEqual(served_state(s), before)
            self.assertTrue(store_before <= set(tree(os.path.join(s.dataset_path, 'store'))))
            self.assertEqual((Path(s.dataset_path) / 'current' / 'Packages').read_bytes(), SY.V1['Packages'])
            self.assertFalse(s.http.posts)

    def test_promotion_only_after_generation_reinventory(self):
        with mig() as s:
            def tamper(label, info):
                if label == 'verifying-content':
                    victim = os.path.join(s.dataset_path, 'generations', s.content_sha, 'Packages')
                    os.unlink(victim)
                    Path(victim).write_bytes(b'not what the manifest says')
            s.hook_extra = tamper
            mig_go(s)
            self.assert_refused(s, 'verifying-content', 'differs')
            self.assertIs(s.receipt['contentVerified'], False)
            self.assert_not_promoted(s)
            self.assertFalse(s.disk_has('control', 'latest.json'))
            self.assert_no_success_anywhere(s)
        with sync_scn() as s:
            before = served_state(s)

            def tamper(label, info):
                if label == 'verifying-content':
                    victim = os.path.join(s.dataset_path, 'generations', s.pub.content_sha, 'Packages')
                    os.unlink(victim)
                    Path(victim).write_bytes(b'not what the manifest says')
            sync_go(s, hook=tamper)
            self.assertEqual(s.rc, 1)
            self.assertEqual(s.receipt['failedStage'], 'verifying-content')
            self.assertEqual(served_state(s), before)
            self.assertFalse(s.http.posts)

    def test_missing_trust_ca_unreadable(self):
        url, resolve = 'https://apt.isle/snapshot/latest.json', ('apt.isle', '127.0.0.1')
        with tempfile.TemporaryDirectory() as tmp:
            absent = os.path.join(tmp, 'isle-root.crt')
            if shutil.which('curl'):
                with self.assertRaises(sm.HttpError) as caught:
                    sm.CurlHttp(ca=absent).get_bytes(url, resolve=resolve, max_bytes=100)
                self.assertIn('curl exit', str(caught.exception))
            unreadable = os.path.join(tmp, 'unreadable.crt')
            Path(unreadable).write_text('x')
            os.chmod(unreadable, 0)
            if shutil.which('curl') and os.geteuid() != 0:
                with self.assertRaises(sm.HttpError):
                    sm.CurlHttp(ca=unreadable).get_bytes(url, resolve=resolve, max_bytes=100)
            self.assertEqual(os.listdir(tmp).count('isle-root.crt'), 0, 'a CA file was created')
            os.chmod(unreadable, 0o600)
        self.assertEqual(sm.CA_FILE, '/etc/isle-mesh/ca/isle-root.crt')
        with mig() as s:                           # the migrate stage that cannot reach the plan with no trust
            s.routes[s.plan_url] = sm.HttpError('curl exit 77: error setting certificate file', None)
            before = mig_snapshot(s)
            mig_go(s)
            self.assert_refused(s, 'fetching-plan', retryable=True)
            self.assertEqual(mig_snapshot(s), before)
            self.assert_no_success_anywhere(s)

    def test_fetch_with_redirect_refused(self):
        seen = []

        class Popen(PM.FakePopen):
            def __init__(self, argv, **kw):
                seen.append(argv)
                super().__init__(argv, **kw)
        curl = sm.CurlHttp(popen=Popen)
        argv = curl._argv('https://apt.isle/x', ('apt.isle', '10.1.2.3'), 30)
        self.assertEqual(argv[argv.index('--max-redirs') + 1], '0')
        for flag in ('-L', '--location', '--location-trusted', '-k', '--insecure'):
            self.assertNotIn(flag, argv)
        class Redirecting(PM.FakePopen):
            def __init__(self, argv, **kw):
                super().__init__(argv, **kw)
                self.stderr = types.SimpleNamespace(read=lambda: b'curl: (47) Maximum (0) redirects followed')
                self.code = 47
        with self.assertRaises(sm.HttpError) as caught:
            sm.CurlHttp(popen=Redirecting).get_bytes('https://apt.isle/x')
        self.assertIn('curl exit 47', str(caught.exception))
        with mig() as s:                           # a 3xx body served in place of the manifest never passes
            s.served_manifest = b'<html><head><title>302 Found</title></head></html>'
            mig_go(s)
            self.assert_refused(s, 'fetching-manifest')
            self.assertTrue(only_dirs(tree(s.mount)))
            self.assert_no_success_anywhere(s)
        with mig() as s:
            s.routes[s.plan_url] = sm.HttpError('curl exit 47: Maximum (0) redirects followed', None)
            before = mig_snapshot(s)
            mig_go(s)
            self.assert_refused(s, 'fetching-plan')
            self.assertEqual(mig_snapshot(s), before)

    def test_fetch_by_bare_ip_or_with_k_impossible(self):
        calls = []
        curl = sm.CurlHttp(popen=lambda *a, **k: calls.append(a) or PM.FakePopen(*a, **k),
                           run=lambda *a, **k: calls.append(a))
        for url, resolve in (('https://10.1.2.3/x', None), ('https://[::1]/x', None), ('https://0x7f000001/x', None),
                             ('http://apt.isle/x', None), ('https://u:p@apt.isle/x', None), ('https://apt.isle:8443/x', None),
                             ('https://apt.isle/x#frag', None), ('https://example.com/x', None),
                             ('https://apt.isle/x', ('apt.isle', 'evil.example')), ('https://apt.isle/x', ('other.isle', '10.1.1.1'))):
            with self.subTest(url=url, resolve=resolve):
                with self.assertRaises(ValueError):
                    curl.get_bytes(url, resolve=resolve)
                if resolve is None:
                    with self.assertRaises(ValueError):
                        curl.post_json(url, {'a': 1}, 'T')
        self.assertEqual(calls, [], 'curl was started for a refused URL')
        good = curl._argv('https://apt.isle/x', ('apt.isle', '10.1.2.3'), 5)
        for flag in ('-k', '--insecure', '--proxy-insecure', '--cacert-native'):
            self.assertNotIn(flag, good)
        self.assertTrue(good[-1].startswith('https://apt.isle/'))
        text = (HERE / 'storage_migrate.py').read_text() + (HERE / 'storage_witness.py').read_text() + (HERE / 'storage_sync.py').read_text()
        self.assertNotRegex(text, r"'-k'|'--insecure'|\"-k\"")


class OpsCase(unittest.TestCase):
    """operations.py with the journal root in a temp dir and a stand-in storage.sh (pol's test-operations pattern)."""

    def setUp(self):
        try:
            import operations
            import member_preflight
        except ImportError as error:                     # pragma: no cover
            self.skipTest('operations module unavailable: %s' % error)
        self.op = operations
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.marker = self.root / 'child-ran.log'
        for patcher in (mock.patch.object(operations, 'ROOT', self.root / 'ops'), mock.patch.object(operations, 'SCRIPTS', self.root),
                        mock.patch('os.geteuid', return_value=0),
                        mock.patch.object(member_preflight, 'repair', return_value={'device': 'fixture', 'mode': 'core'}),
                        mock.patch.object(member_preflight, 'check', return_value={'device': 'fixture', 'mode': 'core'})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.child('exit 0')

    def child(self, body):
        (self.root / 'storage.sh').write_text('echo "$@" >> "%s"\n%s\n' % (self.marker, body))

    def ran(self):
        return self.marker.read_text().splitlines() if self.marker.exists() else []

    def hold(self, name):
        handle = (self.op.ROOT / name).open('a+')
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(handle.close)
        return handle


DIGEST_VECTOR = PM.VECTOR


# ================================================================== Plan and request

class PlanAndRequest(MigAsserts, OpsCase):

    def test_stale_plan_revision_bumped(self):
        with mig() as s:
            s.routes[s.plan_url]['revision'] = 3
            before = mig_snapshot(s)
            mig_go(s)
            self.assert_refused(s, 'fetching-plan')
            self.assertEqual(mig_snapshot(s), before)
            self.assert_no_success_anywhere(s)
        w = PC.World(tempfile.mkdtemp(dir=self.temp.name))
        before = core_snapshot(w)
        code = w.run('authorize', '--dataset', PC.DATASET, '--plan', PC.PLAN_ID, '--revision', PC.REVISION + 1,
                     '--item', PC.VECTOR['item']['id'], '--digest', PC.DIGEST)     # digest reviewed for the old revision
        self.assertEqual(code, 1)
        self.assertIn('changed since it was reviewed', w.io.said())
        self.assertEqual(core_snapshot(w), before)
        self.assertIsNone(w.entry())

    def test_inactive_superseded_or_deferred_plan(self):
        with mig() as s:
            s.routes[s.plan_url]['active'] = False
            before = mig_snapshot(s)
            mig_go(s)
            self.assert_refused(s, 'fetching-plan', 'not active')
            self.assertEqual(mig_snapshot(s), before)
        with mig(item_changes={'state': 'deferred'}) as s:
            before = mig_snapshot(s)
            mig_go(s)
            self.assert_refused(s, 'fetching-plan', 'not planned')
            self.assertEqual(mig_snapshot(s), before)
        with mig() as s:                                  # superseded: the service now serves another active revision
            s.routes[s.plan_url] = dict(s.routes[s.plan_url], revision=3, active=True)
            before = mig_snapshot(s)
            mig_go(s)
            self.assert_refused(s, 'fetching-plan')
            self.assertEqual(mig_snapshot(s), before)
        with tempfile.TemporaryDirectory() as tmp:
            w = PC.World(tmp)
            w.io.plan['active'] = False
            before = core_snapshot(w)
            self.assertEqual(w.authorize(), 1)
            self.assertIn('not active', w.io.said())
            self.assertEqual(core_snapshot(w), before)
        with tempfile.TemporaryDirectory() as tmp:
            w = PC.World(tmp)
            w.io.plan = PC.make_plan(state='deferred')
            before = core_snapshot(w)
            self.assertEqual(w.authorize(), 1)
            self.assertEqual(core_snapshot(w), before)

    def test_changes_to_app_originDevice_state_hostingRelease_each_change_the_digest(self):
        item = copy.deepcopy(DIGEST_VECTOR['item'])
        release = dict(DIGEST_VECTOR['hostingRelease'])
        base = sc.storage_digest(DIGEST_VECTOR['planId'], DIGEST_VECTOR['revision'], item, release)
        self.assertEqual(base, DIGEST_VECTOR['expectedDigest'])
        changes = {'app': lambda i: i.__setitem__('app', 'some-other-app'),
                   'originDevice': lambda i: (i.__setitem__('originDevice', 'someone-else'), i['storage']['source'].__setitem__('device', 'someone-else')),
                   'state': lambda i: i.__setitem__('state', 'existing'),
                   'hostingRelease': lambda i: i['hostingRelease'].__setitem__('manifestSha256', 'f' * 64)}
        for field, mutate in changes.items():
            with self.subTest(field=field):
                changed = copy.deepcopy(item)
                changed['hostingRelease'] = dict(release)
                mutate(changed)
                digest = sc.storage_digest(DIGEST_VECTOR['planId'], DIGEST_VECTOR['revision'], changed, changed['hostingRelease'])
                self.assertNotEqual(digest, base, field)
                with mig() as s:                           # the reviewed digest no longer matches what the service serves
                    mutate(s.routes[s.plan_url]['items'][0])
                    before = mig_snapshot(s)
                    mig_go(s)
                    self.assert_refused(s, 'fetching-plan')
                    self.assertEqual(mig_snapshot(s), before)
                    self.assert_no_success_anywhere(s)

    def test_release_manifest_sha_differs_from_the_pin(self):
        cat = PM.API + '/api/islemesh/catalog/isle-content-host'
        with mig() as s:
            s.routes[cat]['entry']['install_plan']['release']['sha256'] = 'e' * 64
            mig_go(s)
            self.assert_refused(s, 'deploying-host', 'differs from the pinned')
            self.assertEqual(s.deploys, [])
            self.assertIsNot(s.receipt.get('hostDeployed'), True)
            self.assertIsNot(s.receipt.get('readinessVerified'), True)
            self.assert_no_success_anywhere(s)
        with mig() as s:
            del s.routes[cat]
            mig_go(s)
            self.assert_refused(s, 'deploying-host', 'not published')
            self.assertEqual(s.deploys, [])

    def test_dataset_differs_from_the_items_dataset(self):
        with mig(req_dataset='other-set') as s:
            before = mig_snapshot(s)
            mig_go(s)
            self.assert_refused(s, 'fetching-plan', 'other-set')
            self.assertEqual(mig_snapshot(s), before)
            self.assert_no_success_anywhere(s)
        with tempfile.TemporaryDirectory() as tmp:
            w = PC.World(tmp)
            before = core_snapshot(w)
            code = w.run('authorize', '--dataset', 'other-set', '--plan', PC.PLAN_ID, '--revision', PC.REVISION,
                         '--item', PC.VECTOR['item']['id'], '--digest', PC.DIGEST)
            self.assertEqual(code, 1)
            self.assertEqual(core_snapshot(w), before)

    def test_wrong_device_plan_names_econ_core_runs_on_pol(self):
        with mig() as s:                                            # the plan names econ-core, this agent is pol-core
            s.item['device'] = s.item['storage']['target']['device'] = 'econ-core'
            s.digest = sc.storage_digest(PM.PLAN_ID, 2, s.item, s.item['hostingRelease'])
            s.routes[s.view_url]['plan']['digest'] = s.digest
            before = mig_snapshot(s)
            mig_go(s)
            self.assert_refused(s, 'fetching-plan', 'econ-core')
            self.assertEqual(mig_snapshot(s), before)
            self.assert_no_success_anywhere(s)
        with mig() as s:
            s.identity = 'econ-core'                                # same plan, the wrong machine runs it
            before = mig_snapshot(s)
            mig_go(s)
            self.assert_refused(s, 'fetching-plan', 'pol-core')
            self.assertEqual(mig_snapshot(s), before)

    def test_old_plan_vector_still_validates_byte_for_byte(self):
        raw = (HERE / 'fixtures' / 'storage-digest-vector.json').read_bytes()
        vector = sc.load_vector(HERE / 'fixtures' / 'storage-digest-vector.json')
        item, release = copy.deepcopy(vector['item']), dict(vector['hostingRelease'])
        self.assertEqual(sc.storage_digest(vector['planId'], vector['revision'], item, release), vector['expectedDigest'])
        self.assertEqual((HERE / 'fixtures' / 'storage-digest-vector.json').read_bytes(), raw)   # validation did not touch it
        self.assertEqual(item, vector['item'])
        flipped = copy.deepcopy(item)
        flipped['storage']['source']['totalBytes'] += 1
        self.assertNotEqual(sc.storage_digest(vector['planId'], vector['revision'], flipped, release), vector['expectedDigest'])
        with mig() as s:                                            # and a one-field drift of that plan is refused
            s.routes[s.plan_url]['items'][0]['storage']['source']['totalBytes'] += 1
            before = mig_snapshot(s)
            mig_go(s)
            self.assert_refused(s, 'fetching-plan', 'changed since')
            self.assertEqual(mig_snapshot(s), before)

    def test_retry_reuses_the_normalized_request(self):
        args = ['storage', 'sync', '--dataset', 'd1']
        self.assertEqual(self.op.request(self.op.request(args)), args)
        self.child('exit 3')
        self.assertEqual(self.op.execute(list(args)), 3)
        journal = self.op.latest('storage-d1')
        self.assertEqual(journal['request'], args)
        self.child('exit 0')
        self.assertEqual(self.op.execute(args=['storage', 'sync', '--dataset', 'a-different-dataset'], ident=journal['id']), 0)
        self.assertEqual(self.ran(), ['sync --dataset d1', 'sync --dataset d1'], 'the retry did not reuse the stored request')
        self.assertEqual(self.op.read(journal['id'])['request'], args)
        for bad in (['storage', 'sync', '--dataset', 'd1', '--extra'], ['storage', 'sync', 'd1', '--dataset'],
                    ['storage', 'cutover', '--generation', '2', '--dataset', 'd1']):
            with self.assertRaises(ValueError, msg=bad):
                self.op.execute(bad)
        self.assertEqual(self.ran(), ['sync --dataset d1', 'sync --dataset d1'])

    def test_dataset_lock_taken_before_side_effects(self):
        (self.op.ROOT).mkdir(parents=True, exist_ok=True)
        self.hold('app-storage-d1.lock')
        with self.assertRaises(RuntimeError) as caught:
            self.op.execute(['storage', 'sync', '--dataset', 'd1'])
        self.assertIn('already running', str(caught.exception))
        self.assertEqual(self.ran(), [], 'the child started although the dataset lock was held')
        self.op.execute(['storage', 'sync', '--dataset', 'other-set'])   # another dataset is not blocked
        self.assertEqual(self.ran(), ['sync --dataset other-set'])

    def test_migrate_stage_6_refused_while_a_device_update_runs_the_copy_is_not(self):
        with mig() as s:
            os.makedirs(os.path.dirname(s.lease))
            holder = open(s.lease, 'a+')
            fcntl.flock(holder, fcntl.LOCK_EX)
            try:
                mig_go(s)
            finally:
                holder.close()
            self.assert_refused(s, 'writing-binding', 'device update in progress on pol-core; retry after it completes', retryable=True)
            self.assertIs(s.receipt['contentVerified'], True)       # stages 3-5 ran beside the update
            self.assert_not_promoted(s)
            self.assertEqual(s.deploys, [])
            self.assert_no_success_anywhere(s)
        with mig() as s:                                             # what a device update would see, stage by stage
            os.makedirs(os.path.dirname(s.lease))
            seen = {}

            def probe(label, info):
                handle = open(s.lease, 'a+')
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    seen[label] = 'free'
                except BlockingIOError:
                    seen[label] = 'held'
                finally:
                    handle.close()
            s.hook_extra = probe
            mig_go(s)
            self.assertEqual(s.rc, 0, s.output)
            self.assertEqual([seen[k] for k in ('receiving-content', 'verifying-content')], ['free', 'free'])
            self.assertEqual([seen[k] for k in ('deploying-host', 'verifying-readiness')], ['held', 'held'])

    def test_rollback_refused_beside_a_copy(self):
        (self.op.ROOT).mkdir(parents=True, exist_ok=True)
        self.hold('app-storage-apt-public-repo.lock')               # a migrate/sync copy is running for the dataset
        with self.assertRaises(RuntimeError):
            self.op.execute(['storage', 'rollback', '--dataset', 'apt-public-repo', '--generation', '4'])
        self.assertEqual(self.ran(), [])
        with self.assertRaises(RuntimeError):
            self.op.execute(['storage', 'cutover', '--dataset', 'apt-public-repo', '--generation', '2'])
        self.assertEqual(self.ran(), [])
        # the core refuses it too, from the placement alone, while a reconcile-address operation is pending
        with tempfile.TemporaryDirectory() as tmp:
            w = PC.World(tmp)
            w.cut_over()
            w.io.leases['pol-core'] = '10.10.0.202'
            w.io.serve['10.10.0.202'] = {'current': PC.CONTENT, 'ok': True}
            w.io.register_effective = False
            w.run('reconcile-address', '--dataset', PC.DATASET, '--generation', 4, op='a' * 32)
            self.assertEqual(w.entry()['pending']['action'], 'reconcile-address')
            before = core_snapshot(w)
            self.assertEqual(w.run('rollback', '--dataset', PC.DATASET, '--generation', w.entry()['generation'], op='f' * 32), 1)
            self.assertEqual(core_snapshot(w), before)

    def test_nested_publication_lock_does_not_deadlock(self):
        import repository_lock
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / 'apt' / 'repo'
            repo.mkdir(parents=True)
            (repo / 'Packages').write_text('index')
            placement = root / 'placement.json'
            PO.entry_at(placement, 'migrated')

            def alarm(signum, frame):
                raise TimeoutError('deadlock: after-publication waited on the lock its caller holds')
            old = signal.signal(signal.SIGALRM, alarm)
            signal.alarm(5)
            try:
                with repository_lock.publication_lock(repo):
                    probe = open(str(repo.parent / '.publish.lock'), 'a+') if (repo.parent / '.publish.lock').exists() else None
                    if probe is not None:
                        with self.assertRaises(BlockingIOError):         # the lock really is held by the publisher
                            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        probe.close()
                    result = sp.after_publication(repo, path=placement, device='core-a')
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old)
            self.assertIs(result['published'], True)
            self.assertEqual(len(os.listdir(repo / 'snapshot' / 'manifests')), 1)
            with repository_lock.publication_lock(repo):                  # and the lock was released afterwards
                pass


# ================================================================== Ownership and DNS

class CaCase(unittest.TestCase):
    """The certificate authority fixtures of test-storage-ownership.py (AuthorityPlacement), without its tests."""
    setUpClass = classmethod(PO.base.EnrollmentTest.setUpClass.__func__)
    tearDownClass = classmethod(PO.base.EnrollmentTest.tearDownClass.__func__)
    setUp = PO.AuthorityPlacement.setUp
    place = PO.AuthorityPlacement.place
    issue = PO.AuthorityPlacement.issue

    def refuse_issue(self, words, device='pol-core', domain=PO.DOMAIN):
        before = tree(self.core.state)
        with self.assertRaisesRegex(PermissionError, words):
            self.issue(device, domain)
        self.assertEqual(tree(self.core.state), before, 'the authority state changed on a refusal')


class DnsFenceCase(unittest.TestCase):
    setUp = PO.DnsRegisterFence.setUp
    run_script = PO.DnsRegisterFence.run_script
    intent_file = PO.DnsRegisterFence.intent_file
    wrote = PO.DnsRegisterFence.wrote


class DnsReconcileCase(unittest.TestCase):
    CATALOG, LEASES = PO.DnsReconcile.CATALOG, PO.DnsReconcile.LEASES
    setUp = PO.DnsReconcile.setUp
    run_script = PO.DnsReconcile.run_script
    isle_calls = PO.DnsReconcile.isle_calls


class HostsCase(unittest.TestCase):
    setUp = PO.HostsReconcile.setUp
    run_script = PO.HostsReconcile.run_script
    block = PO.HostsReconcile.block


class OwnershipAndDns(MigAsserts, CoreCase):

    def test_migrate_with_no_authorized_entry_cert_and_every_dns_path_refused(self):
        with mig() as s:                                   # (1) the target agent: no entry in the placement view
            del s.routes[s.view_url]
            before = mig_snapshot(s)
            mig_go(s)
            self.assert_refused(s, 'fetching-plan', 'placement view')
            self.assertEqual(mig_snapshot(s), before)
            self.assert_no_success_anywhere(s)
        # (2) the core: nothing to cut over or mark, nothing written
        self.refuse(lambda: self.w.mark(), None)
        self.refuse(lambda: self.w.cutover(g=1), None)
        self.assertEqual(self.w.raw(), b'')
        self._no_entry_cert()
        self._no_entry_dns()

    def _no_entry_cert(self):
        class Case(CaCase):
            def runTest(inner):
                inner.refuse_issue('belongs to the core')                # no placement-claims entry at all
                inner.place('migrated', device='someone-else')
                inner.refuse_issue('placed on another device')
        self._run_case(Case)

    def _no_entry_dns(self):
        class Case(DnsFenceCase):
            def runTest(inner):
                entry = PO.entry_at(inner.placement, 'authorized')
                before = inner.placement.read_bytes()
                for args in (('register', PO.DOMAIN, PO.TARGET), ('unregister', PO.DOMAIN)):
                    done = inner.run_script(*args)
                    inner.assertNotEqual(done.returncode, 0, args)
                    inner.assertIn('managed name: refused outside a storage intent', done.stderr)
                inner.assertFalse(inner.wrote())
                inner.assertEqual(inner.placement.read_bytes(), before)
        self._run_case(Case)

    def _run_case(self, case_class):
        suite = unittest.TestSuite([case_class()])
        result = unittest.TestResult()
        suite.run(result)
        problems = result.failures + result.errors
        self.assertEqual(problems, [], '\n'.join(t for _, t in problems))

    def test_cert_issued_for_the_wrong_device(self):
        class Case(CaCase):
            def runTest(inner):
                inner.place('migrated')
                inner.refuse_issue('placed on another device', device='other')
                inner.place('cutover')
                inner.refuse_issue('placed on another device', device='other')
        self._run_case(Case)

    def test_recovery_needed_issues_no_cert(self):
        class Case(CaCase):
            def runTest(inner):
                inner.place('recovery-needed')
                for device in ('pol-core', 'other'):
                    inner.refuse_issue('frozen by its storage placement', device=device)
        self._run_case(Case)

    def test_member_to_member_claim_still_403_without_an_entry(self):
        class Case(CaCase):
            def runTest(inner):
                inner.authority.write(inner.authority.claims, {'board.isle': 'pol-core'})
                inner.refuse_issue('belongs to another member', device='other', domain='board.isle')
                inner.place('migrated', domain='board.isle', device='pol-core')    # an entry for another name changes nothing
                inner.place('migrated', domain=PO.DOMAIN, device='pol-core')
                inner.refuse_issue('belongs to another member', device='other', domain='board.isle')
        self._run_case(Case)

    def test_host_not_ready_in_60_s(self):
        with mig() as s:
            s.readiness_down = True
            mig_go(s)
            self.assert_refused(s, 'verifying-readiness', '60 seconds', retryable=True)
            self.assertGreaterEqual(s.clock[0], 60)
            self.assertIsNot(s.receipt.get('readinessVerified'), True)
            self.assertFalse(any(p[1].get('ok') is True for p in s.http.posts), 'a success receipt was posted')
        # the core then refuses to mark that receipt migrated and leaves the placement alone
        w = self.w
        self.assertEqual(w.authorize(), 0)
        row = w.receipts()
        row['receipt']['readinessVerified'] = False
        (w.io.state_dir / PC.DATASET / 'inbound-receipts.jsonl').write_text(json.dumps(row) + '\n')
        self.refuse(lambda: w.mark(), 'readinessVerified')
        self.assertEqual(w.entry()['state'], 'authorized')

    def test_stage_7_never_publishes_dns(self):
        with mig() as s:
            mig_go(s)
            self.assertEqual(s.rc, 0, s.output)
            self.assertEqual(s.deploys, [('isle-content-host', ['--staged', '--bind', 'ISLE_DATASET_PATH=' + s.dataset_path])])
            self.assertEqual([p[0] for p in s.http.posts], [PM.API + '/api/islemesh/storage/receipt'])
            urls = [c[0] for c in s.http.calls]
            self.assertFalse([u for u in urls if re.search(r'dns|router|register', u)], urls)
            self.assertFalse([c for c in s.systemctl_calls if any('dns' in a for a in c)])
        text = (HERE / 'app-deploy.sh').read_text()
        staged = text.split('if [ "$STAGED" = 1 ]; then\n    echo "staged: domain not published', 1)
        self.assertEqual(len(staged), 2, 'staged branch not found')
        self.assertNotIn('dns_publication', staged[1].split('else', 1)[0])
        # and the non-staged path refuses a managed domain owned elsewhere (nothing written)
        with tempfile.TemporaryDirectory() as tmp:
            view = Path(tmp, 'view.json')
            view.write_text(json.dumps({'view': {'dataset': PC.DATASET, 'domain': PO.DOMAIN, 'state': 'cutover', 'device': 'pol-core'}}))
            before = tree(tmp)
            with self.assertRaises(ValueError):
                storage_cli.domain_guard(PO.DOMAIN, 'isle-core', [view.read_text()])
            self.assertEqual(tree(tmp), before)

    def test_lost_response_after_the_cutover_cas_rerun_continues(self):
        w = self.w
        w.cut_over()
        done = core_snapshot(w)
        self.assertEqual(w.cutover(), 0)                   # same operation, same G: the response was lost
        self.assertIn('already cut over', w.io.said())
        self.assertEqual(core_snapshot(w), done, 'a rerun repeated a committed step')
        self.refuse(lambda: w.cutover(g=1, op='9' * 32), None)    # a stale caller is still refused

    def test_dns_does_not_move_auto_revert_state_back_to_migrated(self):
        w = self.w
        w.migrated()
        before = core_snapshot(w)
        w.io.register_effective = False
        self.assertEqual(w.cutover(), 1)
        entry = w.entry()
        self.assertEqual(entry['state'], 'migrated')
        self.assertFalse(entry.get('pending'))
        after = core_snapshot(w)
        self.assertEqual(after['router'], before['router'])
        self.assertEqual(after['pins'], before['pins'])
        receipt = json.loads((w.opdir / 'storage-receipt.json').read_text())
        self.assertIs(receipt['ok'], False)
        self.assertIs(receipt['cutover']['rolledBack'], True)
        self.assertEqual(w.io.registered[-1], (PC.DOMAIN, PC.CORE_ADDR))

    def test_old_source_not_serving_on_revert_recovery_needed_rolledback_false(self):
        w = self.w
        w.migrated()
        w.io.register_effective = False
        w.io.serve[PC.CORE_ADDR]['ok'] = False
        self.assertEqual(w.cutover(), 1)
        entry = w.entry()
        self.assertEqual(entry['state'], 'recovery-needed')
        self.assertFalse(entry.get('pending'))
        receipt = json.loads((w.opdir / 'storage-receipt.json').read_text())
        self.assertIs(receipt['cutover']['rolledBack'], False)
        self.assertIs(receipt['cutover']['recoveryNeeded'], True)
        self.assertEqual(w.io.router[PC.DOMAIN], PC.CORE_ADDR, 'the router moved to the target although nothing verified')
        claims = json.loads((w.io.placement_path.parent / 'placement-claims.json').read_text())
        self.assertEqual(claims[PC.DOMAIN]['state'], 'recovery-needed')
        before = core_snapshot(w)            # every automated writer is frozen: a plain cutover/accept/reconcile is refused
        w.io.serve[PC.CORE_ADDR]['ok'] = True
        self.refuse(lambda: w.cutover(g=entry['generation'], op='7' * 32), None)
        self.refuse(lambda: w.run('reconcile-address', '--dataset', PC.DATASET, '--generation', entry['generation'], op='8' * 32), None)

    def test_dns_reconcile_does_not_reclaim_the_domain_for_the_core_after_cutover(self):
        class Case(DnsReconcileCase):
            def runTest(inner):
                for state in ('cutover', 'hosting'):
                    PO.entry_at(inner.placement, state)
                    before = inner.placement.read_bytes()
                    inner.log.unlink() if inner.log.exists() else None
                    done = inner.run_script(PO.CORE)         # the router still answers the core's address
                    inner.assertEqual(done.returncode, 0, done.stderr)
                    calls = inner.isle_calls()
                    inner.assertFalse([c for c in calls if 'dns register' in c or 'dns unregister' in c], calls)
                    inner.assertTrue(all(c.startswith('storage reconcile-address') for c in calls), calls)
                    inner.assertEqual(inner.placement.read_bytes(), before)
        self._run_case(Case)

    def test_unmanaged_127_0_0_1_apt_isle_line_is_gone(self):
        text = (HERE / 'apt-repo.sh').read_text()
        self.assertNotIn('tee -a /etc/hosts', text)
        self.assertNotRegex(text, r'echo\s+"?127\.0\.0\.1\s+apt\.isle')

        class Case(HostsCase):
            def runTest(inner):
                inner.hosts.write_text('127.0.0.1 localhost\n')
                PO.entry_at(inner.placement, 'cutover')
                inner.assertEqual(inner.run_script().returncode, 0)
                inner.assertNotIn('apt.isle', inner.block())
                inner.assertIn('127.0.0.1 localhost', inner.block())
        self._run_case(Case)

    def test_core_registry_push_omits_the_domain(self):
        class Case(PO.AptRepoAndPush):
            def runTest(inner):
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / 'placement.json'
                    for state in ('cutover', 'hosting'):
                        PO.entry_at(path, state)
                        done = inner.run_push(tmp, path.read_text(), inner.REGISTRY)    # asserts registry.json untouched
                        inner.assertEqual(done.returncode, 0, done.stderr)
                        apps = json.loads(done.stdout)['registry']['apps']
                        inner.assertNotIn('apt', apps, state)
                        inner.assertIn('who', apps)
                    done = inner.run_push(tmp, '{broken', inner.REGISTRY)          # an unreadable record pushes nothing
                    inner.assertNotEqual(done.returncode, 0)
                    inner.assertEqual(done.stdout, '')
        self._run_case(Case)

    def test_cutover_with_a_stale_replica_refused(self):
        w = self.w
        w.migrated()
        w.io.serve[PC.TARGET_ADDR]['current'] = 'd' * 64
        self.refuse(lambda: w.cutover(), 'run sync first')
        self.assertEqual((w.io.registered, w.io.pin_log), ([], []))
        self.assertEqual(w.entry()['state'], 'migrated')

    def test_rollback_from_hosting_refused(self):
        w = self.w
        w.cut_over()
        w.witness()
        self.assertEqual(w.accept(4), 0)
        self.assertEqual(w.entry()['state'], 'hosting')
        self.refuse(lambda: w.run('rollback', '--dataset', PC.DATASET, '--generation', 5), 'new plan')
        self.assertEqual(w.entry()['state'], 'hosting')

    def test_generation_mismatch_stale_generation_refused(self):
        w = self.w
        w.migrated()
        self.refuse(lambda: w.cutover(g=1), 'generation 2')
        self.refuse(lambda: w.cutover(g=3), 'generation 2')
        w.cut_over()
        for verb in ('rollback', 'accept', 'reconcile-address'):
            with self.subTest(verb=verb):
                self.refuse(lambda: w.run(verb, '--dataset', PC.DATASET, '--generation', 3, op='6' * 32), None)


# ================================================================== Receipts and witness

def real_poster_io(tmp, members):
    path = Path(tmp) / 'members.json'
    path.write_text(json.dumps(members))
    io_ = core.RealIo.__new__(core.RealIo)
    io_.state_dir = Path(tmp)
    return io_, path


class ReceiptsAndWitness(CoreCase):

    def test_forged_receipt_digest_device_poster(self):
        w = self.w
        w.authorize()
        row = w.receipts()                                         # (digest) a receipt for a digest the entry never had
        row['receipt']['plan']['digest'] = '9' * 64
        (w.io.state_dir / PC.DATASET / 'inbound-receipts.jsonl').write_text(json.dumps(row) + '\n')
        self.refuse(lambda: w.mark(), 'no stored migrate receipt')
        w.receipts(device='econ-core')                             # (device) receipt names another device
        self.refuse(lambda: w.mark(), 'econ-core')
        w.receipts()                                               # (poster) presented credential does not verify
        w.io.poster_ok = False
        self.refuse(lambda: w.mark(), 'poster')
        self.assertEqual(w.entry()['state'], 'authorized')
        with tempfile.TemporaryDirectory() as tmp:                 # the real verifier: full-hash equality and device equality
            target_hash, other_hash = hashlib.sha256(b'token-target').hexdigest(), hashlib.sha256(b'token-other').hexdigest()
            io_, members = real_poster_io(tmp, {'target': target_hash, 'other': other_hash})
            receipt = {'kind': 'isle.storage.migrate', 'target': {'device': 'target'}}
            forged = [('fingerprint of another member', {'subject': 'target', 'fingerprint': other_hash}),
                      ('prefix only', {'subject': 'target', 'fingerprint': target_hash[:16]}),
                      ('authenticated as another member', {'subject': 'other', 'fingerprint': other_hash}),
                      ('unknown subject', {'subject': 'nobody', 'fingerprint': target_hash})]
            before = tree(tmp)
            with mock.patch.object(core, 'MEMBERS', members):
                for label, poster in forged:
                    self.assertFalse(io_.verify_poster({'receipt': receipt, 'posterPresented': poster}), label)
            self.assertEqual(tree(tmp), before)

    def test_receipt_digest_differs_from_the_stored_plan(self):
        w = self.w
        w.authorize()
        row = w.receipts()
        row['receipt']['plan']['digest'] = '5' * 64              # the receipt is for a digest the stored plan never had
        (w.io.state_dir / PC.DATASET / 'inbound-receipts.jsonl').write_text(json.dumps(row) + '\n')
        self.refuse(lambda: w.run('mark-migrated', '--dataset', PC.DATASET, '--digest', '5' * 64), None)
        self.refuse(lambda: w.run('mark-migrated', '--dataset', PC.DATASET, '--digest', PC.DIGEST), 'no stored migrate receipt')
        self.assertEqual(w.entry()['state'], 'authorized')
        self.assertEqual(w.entry()['plan']['digest'], PC.DIGEST)

    def _witness_verb_refused(self, device):
        fake = WI.Fake()
        env = WI.Env(fake, me=device)
        before = tree(env.t / 'state')
        code = env.run()
        self.assertEqual(code, 1)
        self.assertIs(env.receipt['ok'], False)
        self.assertIn('third device', env.receipt['words'])
        self.assertEqual(fake.posts, [])
        self.assertEqual(tree(env.t / 'state'), before)
        self.assertEqual(fake.fetched, [], 'content was fetched by a device that may not witness')

    def test_witness_from_the_source(self):
        self._witness_verb_refused(WI.SOURCE)
        w = self.w
        w.cut_over()
        w.witness(device=w.entry()['sourceDevice'], id='by-source')
        self.refuse(lambda: w.accept(4), None)
        self.assertEqual(w.entry()['state'], 'cutover')

    def test_witness_posted_by_the_target(self):
        self._witness_verb_refused(WI.TARGET)
        w = self.w
        w.cut_over()
        w.witness(device='pol-core', id='by-target')
        self.refuse(lambda: w.accept(4), 'target')
        self.assertEqual(w.entry()['state'], 'cutover')

    def test_witness_json_naming_a_device_other_than_the_authenticated_one(self):
        w = self.w
        w.cut_over()
        with tempfile.TemporaryDirectory() as tmp:
            honest, attacker = hashlib.sha256(b'third-token').hexdigest(), hashlib.sha256(b'attacker-token').hexdigest()
            io_, members = real_poster_io(tmp, {'third': honest, 'attacker': attacker})
            w.io.verify_poster = io_.verify_poster
            with mock.patch.object(core, 'MEMBERS', members):
                # authenticated as `attacker`, but the witness JSON says it was made by the third device
                w.witness(device='third', id='forged', posterPresented={'subject': 'attacker', 'fingerprint': attacker})
                self.refuse(lambda: w.accept(4), 'poster')
                self.assertEqual(w.entry()['state'], 'cutover')
                # a witness whose JSON carries no device at all verifies for nobody
                row = w.witness(id='nodevice', posterPresented={'subject': 'third', 'fingerprint': honest})
                self.refuse(lambda: w.accept(4), None)
                self.assertEqual(w.entry()['state'], 'cutover')

    def test_replayed_or_stale_witness(self):
        w = self.w
        w.cut_over()
        w.witness(epoch=3, id='old-epoch')                            # an older generation
        self.refuse(lambda: w.accept(4), None)
        w.witness(contentSha='9' * 64, id='old-sha')                  # another (older) content
        self.refuse(lambda: w.accept(4), None)
        good = w.witness(id='w-good')
        self.assertEqual(w.accept(4), 0)                              # control: the fresh one is what it takes
        w2 = PC.World(tempfile.mkdtemp(dir=self.tmp.name))            # replay after drift: the witness binds the old epoch
        w2.cut_over()
        w2.witness()
        w2.io.leases['pol-core'] = '10.10.0.201'
        w2.io.serve['10.10.0.201'] = {'current': PC.CONTENT, 'ok': True}
        self.assertEqual(w2.run('reconcile-address', '--dataset', PC.DATASET, '--generation', 4), 0)
        before = core_snapshot(w2)
        self.assertEqual(w2.accept(6), 1)
        self.assertEqual(core_snapshot(w2), before)
        self.assertEqual(w2.entry()['state'], 'cutover')

    def test_oversize_witness_download_refused(self):
        fake = WI.Fake(files={}, big=[('huge/a.iso', 100 * sm.MiB), ('huge/b.iso', 200 * sm.MiB)])
        env = WI.Env(fake)
        self.assertEqual(env.run(), 1)
        self.assertIn('bounded', env.receipt['words'])
        self.assertEqual(fake.posts, [])
        self.assertFalse([u for u in fake.fetched if 'huge' in u], 'an oversize file was downloaded')
        rows = [json.loads(l) for l in (env.t / 'state' / WI.DATASET / 'witnesses.jsonl').read_text().splitlines()]
        self.assertTrue(rows and all(r['ok'] is False and r['posted'] is False for r in rows), 'a refusal was recorded as a witness')
        class Big:
            def __init__(self, argv, **kw):
                self.stdout = io.BytesIO(b'x' * 100)
                self.stderr = io.BytesIO(b'10.9.9.9')
            def wait(self): return 0
            def kill(self): pass
        with self.assertRaises(sm.HttpError):
            WI.sw.WitnessCurl(popen=Big).get_bytes_addr('https://apt.isle/x', max_bytes=10)

    def test_accept_with_no_witness(self):
        w = self.w
        w.cut_over()
        self.refuse(lambda: w.accept(4), 'no witness')
        self.assertEqual(w.entry()['state'], 'cutover')
        self.assertNotIn('acceptedWitness', w.entry() if w.entry().get('acceptedWitness') else {})
        self.assertIsNone(w.entry().get('acceptedWitness'))

    @unittest.skip(NOTEST + 'removal-check is a route of the Isle App backend (POST/GET /api/islemesh/removal-check); this repo has only the client removal_guard.py. Skeleton: PUT a plan removing the source item while placement is cutover and assert reasons contain PHASE_A_SOURCE_RETAINED and the plan is not saved.')
    def test_removal_of_the_source_before_acceptance(self):
        pass

    @unittest.skip(NOTEST + 'backend plan-save validation (removal-check on plan edit); not in this repository. Skeleton: save a plan revision that merely omits the storage source item and assert it is refused like an explicit removal.')
    def test_plan_edit_that_omits_the_source_item(self):
        pass

    @unittest.skip(NOTEST + 'backend removal-check provider count; not in this repository. Skeleton: remove the last infrastructure provider of apt.isle and assert refusal.')
    def test_removal_of_the_last_infrastructure_provider(self):
        pass

    @unittest.skip(NOTEST + 'backend request handling of protectedRoles; not in this repository. Skeleton: post a plan whose items carry protectedRoles: [] and assert protection is still derived from the presence of the storage block.')
    def test_client_supplied_protectedRoles_ignored(self):
        pass

    def test_uninstall_or_disable_of_the_cores_private_compose_not_undone_by_the_next_publish(self):
        text = (HERE / 'apt-repo.sh').read_text()
        marker = 'APT_PLACED=$(python3 -c '
        self.assertIn(marker, text)
        code = text.split(marker, 1)[1].split("' \"${ISLE_PLACEMENT", 1)[0].lstrip("'")
        self.assertLess(text.index('hosting replica lags until sync'), text.index('isle app deploy apt --compose'),
                        'the private compose redeploy is reachable before the placement check')
        self.assertRegex(text, r'\[ "\$APT_PLACED" = "cutover" \] \|\| \[ "\$APT_PLACED" = "hosting" \]')
        with tempfile.TemporaryDirectory() as tmp:
            placement = Path(tmp) / 'placement.json'
            for state, redeploys in (('cutover', False), ('hosting', False), ('migrated', True), ('authorized', True)):
                PO.entry_at(placement, state)
                before = placement.read_bytes()
                out = subprocess.run([sys.executable, '-c', code, str(placement)], capture_output=True, text=True)
                self.assertEqual(out.stdout.strip() in ('cutover', 'hosting'), not redeploys, state + ': ' + out.stdout + out.stderr)
                self.assertEqual(placement.read_bytes(), before)


# ================================================================== v3 additions

class V3Additions(CoreCase):

    # ---- Content
    def test_real_manifest_receive_verify_positive(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'repo'
            (repo / 'dists').mkdir(parents=True)
            (repo / 'dists' / 'InRelease').write_bytes(b'signed release')
            (repo / 'Packages').write_bytes(b'package index')
            (repo / 'pkg_1.0_all.deb').write_bytes(b'deb bytes')
            latest = sp.publish_manifest(repo, device='core-a', now=datetime(2026, 11, 9, 12))
            raw = (repo / 'snapshot' / 'manifests' / (latest['manifestSha'] + '.json')).read_bytes()
            doc = json.loads(raw)
            objects = repo / 'snapshot' / 'objects'
            env = RC.Env()
            try:
                def from_objects(entry, sink):
                    sink.write((objects / entry['sha256']).read_bytes())
                root = env.root()
                parts = sr.receive(root, RC.REL, doc, from_objects, op_id='real', statvfs=RC.BIG)
                self.assertEqual(sr.verify_generation(root, RC.REL, sr.content_sha(doc), raw), sr.content_sha(doc))
                self.assertEqual(latest['contentSha'], sr.content_sha(doc))
                root.close()
            finally:
                env.close()
            env = RC.Env()                                    # the twin: one flipped object byte is refused, nothing promoted
            try:
                victim = (objects / hashlib.sha256(b'package index').hexdigest())
                def flipped(entry, sink):
                    data = (objects / entry['sha256']).read_bytes()
                    sink.write(data[:-1] + b'X' if entry['sha256'] == victim.name else data)
                root = env.root()
                with self.assertRaises(sr.ReceiveError):
                    sr.receive(root, RC.REL, doc, flipped, op_id='bad', statvfs=RC.BIG)
                self.assertIsNone(sr.current_generation(root, RC.REL))
                self.assertFalse(os.path.lexists(env.dataset('store', victim.name)))
                root.close()
            finally:
                env.close()

    def test_old_InRelease_promote_old_by_hash_fetch_still_served(self):
        old_hash = hashlib.sha256(b'old packages index').hexdigest()
        by_hash = 'dists/by-hash/SHA256/' + old_hash
        v1 = {'dists/InRelease': b'release one', by_hash: b'old packages index'}
        v2 = {'dists/InRelease': b'release two', by_hash: b'old packages index', 'dists/by-hash/SHA256/new': b'new packages index'}
        SY.DIRS.extend(['dists', 'dists/by-hash', 'dists/by-hash/SHA256'])
        self.addCleanup(lambda: [SY.DIRS.remove(d) for d in ('dists', 'dists/by-hash', 'dists/by-hash/SHA256')])
        with sync_scn(seed=v1, publish=v2) as s:
            good_stream = s.http.stream_to
            s.http.stream_to = lambda url, sink, resolve=None, expected_size=None: sink.write(b'corrupt')
            before = served_state(s)
            s.go()
            self.assertEqual(s.rc, 1)
            self.assertEqual(served_state(s), before)
            self.assertEqual((Path(s.dataset_path) / 'current' / by_hash).read_bytes(), b'old packages index')
            self.assertEqual((Path(s.dataset_path) / 'current' / 'dists/InRelease').read_bytes(), b'release one')
            s.http.stream_to = good_stream                        # the retry promotes; the old by-hash name still answers
            s.go()
            self.assertEqual(s.rc, 0, s.output)
            self.assertEqual((Path(s.dataset_path) / 'current' / by_hash).read_bytes(), b'old packages index')
            self.assertEqual((Path(s.dataset_path) / 'current' / 'dists/InRelease').read_bytes(), b'release two')
            self.assertEqual((Path(s.dataset_path) / 'generations' / s.seed.content_sha / 'dists/InRelease').read_bytes(), b'release one')

    def test_manifest_source_publish_retry_of_the_approved_snapshot_succeeds_from_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'repo'
            repo.mkdir()
            (repo / 'Packages').write_bytes(b'approved index')
            approved = sp.publish_manifest(repo, device='core-a', now=datetime(2026, 11, 9, 1))
            raw = (repo / 'snapshot' / 'manifests' / (approved['manifestSha'] + '.json')).read_bytes()
            doc = json.loads(raw)
            with open(repo / 'Packages', 'r+b') as handle:            # the publisher rewrites in place after approval
                handle.write(b'REWRITTEN idx!')
            sp.publish_manifest(repo, device='core-a', now=datetime(2026, 11, 9, 2))
            objects = repo / 'snapshot' / 'objects'
            env = RC.Env()
            try:
                root = env.root()
                sr.receive(root, RC.REL, doc, lambda e, sink: sink.write((objects / e['sha256']).read_bytes()), op_id='retry', statvfs=RC.BIG)
                self.assertEqual(sr.verify_generation(root, RC.REL, sr.content_sha(doc), raw), sr.content_sha(doc))
                root.close()
            finally:
                env.close()
            tampered = objects / hashlib.sha256(b'approved index').hexdigest()
            tampered.write_bytes(b'APPROVED INDEX')                    # same size, other bytes: the object store is damaged
            manifests = sorted(os.listdir(repo / 'snapshot' / 'manifests'))
            (repo / 'Packages').write_bytes(b'approved index')         # the publisher is back to the approved content
            with self.assertRaises(ValueError) as caught:
                sp.publish_manifest(repo, device='core-a', now=datetime(2026, 11, 9, 3))
            self.assertIn('does not match its name', str(caught.exception))
            self.assertEqual(sorted(os.listdir(repo / 'snapshot' / 'manifests')), manifests)
            self.assertEqual(tampered.read_bytes(), b'APPROVED INDEX')
            env = RC.Env()
            try:
                root = env.root()
                with self.assertRaises(sr.ReceiveError):
                    sr.receive(root, RC.REL, doc, lambda e, sink: sink.write(tampered.read_bytes()), op_id='retry2', statvfs=RC.BIG)
                self.assertIsNone(sr.current_generation(root, RC.REL))
                root.close()
            finally:
                env.close()

    def test_foreign_hardlink_in_a_generation_refused(self):
        env = RC.Env()
        try:
            doc, raw, parts = RC.do_receive(env)
            csha = sr.content_sha(doc)
            gen = env.dataset('generations', csha)
            foreign = os.path.join(env.tmp, 'foreign')
            Path(foreign).write_bytes(b'foreign bytes')
            os.link(foreign, os.path.join(gen, 'extra-link'))
            root = env.root()
            before = tree(env.mount)
            with self.assertRaises(sr.ReceiveError) as caught:
                sr.verify_generation(root, RC.REL, csha, raw)
            self.assertIn('extra-link', str(caught.exception))
            self.assertEqual(tree(env.mount), before)
            root.close()
        finally:
            env.close()
        with mig() as s:
            def plant(label, info):
                if label == 'verifying-content':
                    foreign = os.path.join(s.tmp, 'foreign')
                    Path(foreign).write_bytes(b'foreign bytes')
                    os.link(foreign, os.path.join(s.dataset_path, 'generations', s.content_sha, 'planted'))
            s.hook_extra = plant
            mig_go(s)
            self.assertEqual(s.rc, 1)
            self.assertEqual(s.receipt['failedStage'], 'verifying-content')
            self.assertFalse(s.disk_has('current'))
            self.assertFalse(s.disk_has('binding.json'))
            self.assertEqual(s.deploys, [])

    # ---- Locks
    def test_update_refused_during_critical_stages_allowed_during_the_copy_message_names_the_dataset(self):
        class Case(OpsCase):
            def runTest(inner):
                inner.child('exit 0')
                inner.op.ROOT.mkdir(parents=True, exist_ok=True)
                ident = 'f' * 32                                      # a running storage journal, as pol's test-operations builds it
                path = inner.op.path_for(ident)
                path.mkdir(parents=True)
                inner.op.atomic(path / 'status.json', dict(id=ident, name='storage-d9', request=[], state='running',
                                                           createdAt=inner.op.now(), attempt=1, history=[]))
                running = (path / 'lock').open('a+')
                fcntl.flock(running, fcntl.LOCK_EX | fcntl.LOCK_NB)
                inner.addCleanup(running.close)
                (inner.root / 'reconcile.sh').write_text('echo update >> "%s"\n' % inner.marker)
                with (inner.op.ROOT / 'app-device-update.lock').open('a+') as exclusive:        # critical stage: lease held
                    fcntl.flock(exclusive, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    with inner.assertRaises(RuntimeError) as caught:
                        inner.op.execute(['reconcile', 'apply', 'device'])
                    inner.assertEqual(str(caught.exception), 'device update refused: storage operation in progress for dataset d9')
                inner.assertEqual(inner.ran(), [])
                inner.assertEqual(inner.op.execute(['reconcile', 'apply', 'device']), 0)     # during the copy: no lease held
                inner.assertEqual(inner.ran(), ['update'])
        suite = unittest.TestSuite([Case()])
        result = unittest.TestResult()
        suite.run(result)
        problems = result.failures + result.errors
        self.assertEqual(problems, [], '\n'.join(t for _, t in problems))

    # ---- DNS
    def test_dns_register_sh_refuses_a_managed_name_outside_an_intent(self):
        class Case(DnsFenceCase):
            def runTest(inner):
                PO.entry_at(inner.placement, 'migrated', PO.pending())
                before = inner.placement.read_bytes()
                for state in ('migrated',):
                    done = inner.run_script('register', PO.DOMAIN, PO.TARGET)
                    inner.assertNotEqual(done.returncode, 0)
                    inner.assertIn('managed name: refused outside a storage intent', done.stderr)
                inner.assertFalse(inner.wrote())
                inner.assertEqual(inner.placement.read_bytes(), before)
        OwnershipAndDns._run_case(self, Case)

    def test_dns_reconcile_does_not_write_while_pending(self):
        class Case(DnsReconcileCase):
            def runTest(inner):
                PO.entry_at(inner.placement, 'cutover', PO.pending(address=PO.TARGET))
                before = inner.placement.read_bytes()
                inner.run_script(PO.CORE)
                PO.entry_at(inner.placement, 'recovery-needed')
                inner.run_script(PO.CORE)
                inner.assertEqual(inner.isle_calls(), [])
        OwnershipAndDns._run_case(self, Case)

    def test_retry_with_a_different_action_or_digest_does_not_resume_a_pending_transition(self):
        w = self.w
        PC.crashed_cutover(w, False)
        for label, change in (('digest', lambda p: p.__setitem__('digest', '7' * 64)), ('action', lambda p: p.__setitem__('action', 'rollback'))):
            with self.subTest(label):
                raw = json.loads(w.raw())
                original = copy.deepcopy(raw)
                change(raw['datasets'][PC.DATASET]['pending'])
                w.io.placement_path.write_text(json.dumps(raw))
                before = core_snapshot(w)
                self.assertEqual(w.cutover(g=3, op='c' * 32), 1)
                self.assertEqual(core_snapshot(w), before)
                self.assertEqual(w.entry()['state'], 'migrated')
                w.io.placement_path.write_text(json.dumps(original))

    def test_router_mDNS_scan_skips_managed_names(self):
        lib = HERE.parent.parent / 'openwrt-router' / 'scripts' / 'lib' / 'managed-names.sh'
        join = HERE.parent.parent / 'openwrt-router' / 'scripts' / 'router-setup' / 'configure-join-protocol.sh'
        vlan = HERE.parent.parent / 'openwrt-router' / 'scripts' / 'utilities' / 'manage-vlan-domains.sh'
        with tempfile.TemporaryDirectory() as tmp:
            names = Path(tmp) / 'isle-managed-names'
            names.write_text('apt.isle\n')
            before = names.read_bytes()
            run = lambda name: subprocess.run(['sh', '-c', '. %s; isle_managed_skip %s %s' % (lib, name, names)], capture_output=True, text=True)
            self.assertEqual(run('apt.isle').returncode, 0, 'a managed name was not skipped')
            self.assertEqual(run('other.isle').returncode, 1)
            self.assertEqual(run('pt.isle').returncode, 1)
            self.assertEqual(names.read_bytes(), before)
        for script in (join, vlan):
            self.assertIn('isle_managed_skip', script.read_text(), script.name)

    def test_core_app_redeploy_does_not_publish_a_managed_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            view = Path(tmp) / 'view.json'
            view.write_text(json.dumps({'view': {'dataset': PC.DATASET, 'domain': PO.DOMAIN, 'state': 'cutover', 'device': 'pol-core'}}))
            before = tree(tmp)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = storage_cli.main(['guard-domain', '--domain', PO.DOMAIN, '--device', 'isle-core', '--view', str(view),
                                         '--local', str(Path(tmp) / 'absent.json')])
            self.assertEqual(code, 1)
            self.assertIn('owned by pol-core', out.getvalue())
            self.assertEqual(tree(tmp), before)
            for state in ('hosting',):
                view.write_text(json.dumps({'view': {'domain': PO.DOMAIN, 'state': state, 'device': 'pol-core'}}))
                with self.assertRaises(ValueError):
                    storage_cli.domain_guard(PO.DOMAIN, 'isle-core', [view.read_text()])

    def test_cutover_refused_without_the_sync_timer(self):
        w = self.w
        w.migrated()
        w.io.timer = False
        self.refuse(lambda: w.cutover(), 'sync trigger not installed')
        self.assertEqual((w.io.registered, w.io.pin_log), ([], []))
        self.assertEqual(w.entry()['state'], 'migrated')

    # ---- CA
    def test_cert_renewal_of_the_cores_own_cert_still_works(self):
        class Case(CaCase):
            def runTest(inner):
                text = (HERE / 'certs.sh').read_text()
                body = text.split("<<'PYCLAIM' || return 1\n", 1)[1].split('\nPYCLAIM', 1)[0]
                claims = inner.authority.directory / 'claims.json'
                body = body.replace('/etc/isle-mesh/ca/enrollment/claims.json', str(claims))
                inner.place('hosting')
                inner.issue()                                         # the member now holds a placement grant
                done = subprocess.run([sys.executable, '-c', body, PO.DOMAIN], capture_output=True, text=True)
                inner.assertEqual(done.returncode, 0, done.stderr)    # the core still renews its own cert
                claims.write_text(json.dumps({'board.isle': 'pol-core'}))
                before = claims.read_bytes()
                done = subprocess.run([sys.executable, '-c', body, 'board.isle'], capture_output=True, text=True)
                inner.assertNotEqual(done.returncode, 0, 'the core issued a name claimed by a member')
                inner.assertEqual(claims.read_bytes(), before)
        OwnershipAndDns._run_case(self, Case)

    def test_the_core_itself_is_refused(self):
        # the denylist holds the core itself and nothing else; no list of movable names exists any more
        w = self.w
        self.assertEqual(core.core_itself(), {'api.polari.isle'})
        self.assertFalse(hasattr(core, 'HOSTABLE') or hasattr(w.io, 'hostable_path'))
        with mock.patch.object(core, 'API', 'https://' + PO.DOMAIN):
            self.refuse(lambda: w.authorize(), 'the core itself')
        self.assertIsNone(w.entry())

        class Case(CaCase):
            def runTest(inner):
                inner.place('migrated')
                inner.refuse_issue('belongs to the core', device='pol-core', domain='auth.polari.isle')
        OwnershipAndDns._run_case(self, Case)

    def test_source_not_the_current_owner_refused_at_authorize(self):
        w = self.w
        w.io.registry = {PC.DOMAIN: 'econ-core'}
        self.refuse(lambda: w.authorize(), 'does not currently own')
        self.assertIsNone(w.entry())
        w.io.registry = {}
        self.refuse(lambda: w.authorize(), None)
        self.assertIsNone(w.entry())

    # ---- Receipts and witness
    def test_identical_receipt_repost_idempotent(self):
        w = self.w
        w.authorize()
        w.io.http = {'receipts': [PC._api_row('receipt', PC._migrate_body(), 'pol-core', 'r1')]}
        w.run('relay')
        log = w.io.state_dir / PC.DATASET / 'inbound-receipts.jsonl'
        first = log.read_bytes()
        self.assertEqual(len(first.splitlines()), 1)
        w.io.http['receipts'][0]['seen'] = 9                              # the door counted a re-post
        before = core_snapshot(w)
        w.run('relay')
        self.assertEqual(log.read_bytes(), first)
        self.assertEqual(core_snapshot(w), before)
        self.assertEqual(w.io.mark_calls, [(PC.DATASET, PC.DIGEST)], 'a re-posted receipt triggered mark-migrated again')

    def test_receipt_for_another_plan_or_revision_refused(self):
        w = self.w
        w.authorize()
        for label, change in (('revision', lambda r: r['plan'].__setitem__('revision', PC.REVISION + 1)),
                              ('plan id', lambda r: r['plan'].__setitem__('id', 'e' * 32))):
            with self.subTest(label):
                w = PC.World(tempfile.mkdtemp(dir=self.tmp.name))
                w.authorize()
                row = w.receipts()
                change(row['receipt'])
                (w.io.state_dir / PC.DATASET / 'inbound-receipts.jsonl').write_text(json.dumps(row) + '\n')
                self.refuse(lambda: w.mark(), None, w=w)
                self.assertEqual(w.entry()['state'], 'authorized')

    def test_witness_outside_the_freshness_window(self):
        w = self.w
        w.cut_over()
        cut = [h for h in w.entry()['history'] if h['to'] == 'cutover'][-1]['at']
        base = datetime.strptime(cut, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        for label, delta in (('after 24 h', timedelta(hours=25)), ('before cutover - 120 s', timedelta(seconds=-300))):
            with self.subTest(label):
                w.witness(at=(base + delta).strftime('%Y-%m-%dT%H:%M:%SZ'), id='w-' + label)
                self.refuse(lambda: w.accept(4), None)
                self.assertEqual(w.entry()['state'], 'cutover')

    def test_witness_epoch_mismatch(self):
        w = self.w
        w.cut_over()
        for epoch in (3, 5, 0):
            with self.subTest(epoch=epoch):
                w.witness(epoch=epoch, id='e%d' % epoch)
                self.refuse(lambda: w.accept(4), None)
                self.assertEqual(w.entry()['state'], 'cutover')

    def test_no_retire_source_emitted(self):
        self.assertNotIn('retire-source', sc.VERBS)
        self.assertNotIn('retire', ' '.join(sc.VERBS))
        with self.assertRaises(ValueError):
            sc.parse_request(['storage', 'retire-source', '--dataset', 'apt-public-repo'])
        item = copy.deepcopy(PM.VECTOR['item'])
        action = sc.planned_action(PM.VECTOR['planId'], PM.VECTOR['revision'], item, PM.VECTOR['expectedDigest'], dict(PM.VECTOR['hostingRelease']), core_device='isle-core')
        self.assertNotIn('retire', json.dumps(action))
        for name in ('storage_contract.py', 'storage_core.py', 'storage_migrate.py', 'storage_sync.py', 'storage_witness.py', 'storage_cli.py'):
            code = re.findall(r"['\"]retire-source['\"]", (HERE / name).read_text())
            self.assertEqual(code, [], name)


# ================================================================== v3.2 additions (same class: V3Additions)

def v3(fn):
    setattr(V3Additions, fn.__name__, fn)
    return fn


def make_repo(tmp, files):
    repo = Path(tmp) / 'repo'
    repo.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_bytes(data)
    return repo


def receive_from_objects(repo, raw, op_id='obj'):
    doc = json.loads(raw)
    objects = repo / 'snapshot' / 'objects'
    env = RC.Env()
    try:
        root = env.root()
        sr.receive(root, RC.REL, doc, lambda e, sink: sink.write((objects / e['sha256']).read_bytes()), op_id=op_id, statvfs=RC.BIG)
        return sr.verify_generation(root, RC.REL, sr.content_sha(doc), raw) == sr.content_sha(doc)
    finally:
        env.close()


@v3
def test_snapshot_then_the_publisher_rewrites_bootstrap_CA_index_in_place_prior_objects_still_hash_and_receive(self):
    with tempfile.TemporaryDirectory() as tmp:
        files = {'isle-root.crt': b'CA ONE', 'index.html': b'<html>one</html>', 'Packages': b'package index one', 'bootstrap.sh': b'#!/bin/sh one\n'}
        repo = make_repo(tmp, files)
        approved = sp.publish_manifest(repo, device='core-a', now=datetime(2026, 11, 9, 1))
        manifest = repo / 'snapshot' / 'manifests' / (approved['manifestSha'] + '.json')
        raw = manifest.read_bytes()
        objects = repo / 'snapshot' / 'objects'
        before = tree(objects)
        for name, data in files.items():                                 # in place, same inode, same length
            with open(repo / name, 'r+b') as handle:
                handle.write(b'X' * len(data))
        self.assertEqual(tree(objects), before, 'rewriting the source changed the snapshot objects')
        for name in os.listdir(objects):
            self.assertEqual(hashlib.sha256((objects / name).read_bytes()).hexdigest(), name)
        try:
            verified = ssnap.verify(str(repo), manifest, approved['manifestSha']).get('contentVerified') is True
        except ValueError:
            verified = False
        self.assertFalse(verified, 'the rewritten source still verifies as the approved snapshot')
        self.assertTrue(receive_from_objects(repo, raw), 'the approved snapshot no longer receives from snapshot/objects')


@v3
def test_equal_content_files_with_distinct_inodes_accepted(self):
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, {'a.txt': b'same bytes', 'b/b.txt': b'same bytes', 'c.txt': b'other'})
        self.assertNotEqual(os.stat(repo / 'a.txt').st_ino, os.stat(repo / 'b/b.txt').st_ino)
        latest = sp.publish_manifest(repo, device='core-a', now=datetime(2026, 11, 9, 1))      # no exception
        self.assertEqual(os.stat(repo / 'a.txt').st_nlink, 1)
        self.assertEqual(os.stat(repo / 'b/b.txt').st_nlink, 1)
        names = os.listdir(repo / 'snapshot' / 'objects')
        self.assertEqual(sorted(names), sorted({hashlib.sha256(b'same bytes').hexdigest(), hashlib.sha256(b'other').hexdigest()}))
        raw = (repo / 'snapshot' / 'manifests' / (latest['manifestSha'] + '.json')).read_bytes()
        self.assertTrue(receive_from_objects(repo, raw))
        os.link(repo / 'a.txt', repo / 'a-hardlink.txt')                                       # the twin: a real second link is refused
        before = tree(repo / 'snapshot')
        with self.assertRaises(ValueError):
            sp.publish_manifest(repo, device='core-a', now=datetime(2026, 11, 9, 2))
        self.assertEqual(tree(repo / 'snapshot'), before)


@v3
def test_an_existing_objects_sha_is_never_overwritten(self):
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, {'Packages': b'index body'})
        sp.publish_manifest(repo, device='core-a', now=datetime(2026, 11, 9, 1))
        obj = repo / 'snapshot' / 'objects' / hashlib.sha256(b'index body').hexdigest()
        info = os.stat(obj)
        sp.publish_manifest(repo, device='core-a', now=datetime(2026, 11, 9, 2))              # nothing changed: object untouched
        again = os.stat(obj)
        self.assertEqual((again.st_ino, again.st_mtime_ns), (info.st_ino, info.st_mtime_ns))
        obj.write_bytes(b'index BODY')                                                         # damaged in place, same size
        (repo / 'new').write_bytes(b'brand new')
        before = tree(repo / 'snapshot')
        with self.assertRaises(ValueError) as caught:
            sp.publish_manifest(repo, device='core-a', now=datetime(2026, 11, 9, 3))
        self.assertIn('does not match its name', str(caught.exception))
        self.assertEqual(tree(repo / 'snapshot'), before)
        self.assertEqual(obj.read_bytes(), b'index BODY', 'the damaged object was replaced instead of reported')


@v3
def test_a_foreign_extra_link_still_refused(self):
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, {'Packages': b'index', 'a.txt': b'content'})
        sp.publish_manifest(repo, device='core-a', now=datetime(2026, 11, 9, 1))
        foreign = Path(tmp) / 'foreign-link'
        os.link(repo / 'a.txt', foreign)
        before = tree(repo / 'snapshot')
        with self.assertRaises(ValueError) as caught:
            sp.publish_manifest(repo, device='core-a', now=datetime(2026, 11, 9, 2))
        self.assertIn('hardlink', str(caught.exception).lower())
        self.assertEqual(tree(repo / 'snapshot'), before)
        with self.assertRaises(ValueError):
            ssnap.inventory(str(repo))


def _four_publications(tmp):
    repo = make_repo(tmp, {'data.txt': b'gen 0'})
    shas = []
    for i in range(4):
        (repo / 'data.txt').write_bytes(('gen %d' % i).encode())
        shas.append(sp.publish_manifest(repo, device='core-a', now=datetime(2026, 11, 9, i))['manifestSha'])
        os.utime(repo / 'snapshot' / 'manifests' / (shas[-1] + '.json'), (1000 + i, 1000 + i))
    return repo, shas


@v3
def test_four_publications_during_a_slow_copy_keep_its_source_objects(self):
    with tempfile.TemporaryDirectory() as tmp:
        repo, shas = _four_publications(tmp)
        objects = repo / 'snapshot' / 'objects'
        before = tree(repo / 'snapshot')
        for refused_call in (lambda: sp.gc(repo, None, keep_floor=1), lambda: sp.gc(repo, set(), keep_floor=1, authority_complete=False)):
            with self.assertRaises(ValueError):
                refused_call()
            self.assertEqual(tree(repo / 'snapshot'), before)
        result = sp.gc(repo, {shas[0]}, keep_floor=1)                   # the copy's approved snapshot is pinned
        self.assertIn(shas[0], result['kept'])
        raw = (repo / 'snapshot' / 'manifests' / (shas[0] + '.json')).read_bytes()
        self.assertTrue((objects / hashlib.sha256(b'gen 0').hexdigest()).exists())
        self.assertTrue(receive_from_objects(repo, raw), 'the slow copy lost its source objects')
        self.assertFalse((objects / hashlib.sha256(b'gen 1').hexdigest()).exists(), 'an unpinned object survived')


@v3
def test_gc_holds_the_publication_lock_pins_live_references_and_removes_only_the_unpinned(self):
    import repository_lock
    with tempfile.TemporaryDirectory() as tmp:
        repo, shas = _four_publications(tmp)
        before = tree(repo / 'snapshot')
        done = {}

        def run():
            done['result'] = sp.gc(repo, {shas[0]}, keep_floor=1)
        with repository_lock.publication_lock(repo):                    # a publisher holds the lock
            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            thread.join(0.7)
            self.assertTrue(thread.is_alive(), 'gc ran without the publication lock')
            self.assertEqual(tree(repo / 'snapshot'), before)
        thread.join(10)
        self.assertFalse(thread.is_alive())
        kept = set(done['result']['kept'])
        self.assertIn(shas[0], kept)
        self.assertIn(shas[3], kept)
        for sha in shas:
            exists = (repo / 'snapshot' / 'manifests' / (sha + '.json')).exists()
            self.assertEqual(exists, sha in kept)
        with repository_lock.publication_lock(repo):                    # gc released it
            pass


@v3
def test_recover_from_a_pending_in_migrated_cutover_and_hosting_only_to_a_state_that_verifies(self):
    w = self.w
    PC.crashed_cutover(w, False)                                         # pending in migrated
    w.io.ops['c' * 32] = {'state': 'interrupted'}
    self.refuse(lambda: w.run('recover', '--dataset', PC.DATASET, '--generation', 3, '--to', 'hosting', op='d' * 32), None)
    self.refuse(lambda: w.run('recover', '--dataset', PC.DATASET, '--generation', 2, '--to', 'migrated', op='d' * 32), None)
    self.assertEqual(w.entry()['pending']['action'], 'cutover')
    with tempfile.TemporaryDirectory() as tmp:                           # pending in hosting (reconcile-address)
        h = PC.World(tmp)
        h.cut_over()
        h.witness()
        self.assertEqual(h.accept(4), 0)
        h.io.leases['pol-core'] = '10.10.0.200'
        h.io.serve['10.10.0.200'] = {'current': PC.CONTENT, 'ok': True}
        h.io.crash_after_register = True
        with self.assertRaises(PC.Crash):
            h.run('reconcile-address', '--dataset', PC.DATASET, '--generation', 5, op='b' * 32)
        h.io.crash_after_register = False
        h.io.ops['b' * 32] = {'state': 'interrupted'}
        before = core_snapshot(h)
        self.assertEqual(h.run('recover', '--dataset', PC.DATASET, '--generation', 6, '--to', 'migrated', op='d' * 32), 1)
        self.assertEqual(core_snapshot(h), before)


@v3
def test_recover_from_recovery_needed_by_generation_alone(self):
    w = self.w
    w.migrated()
    w.io.register_effective = False
    w.io.serve[PC.CORE_ADDR]['ok'] = False
    w.cutover()
    entry = w.entry()
    self.assertEqual(entry['state'], 'recovery-needed')
    self.assertFalse(entry.get('pending'))
    g = entry['generation']
    w.io.serve[PC.CORE_ADDR]['ok'] = True
    w.io.register_effective = True
    self.refuse(lambda: w.run('recover', '--dataset', PC.DATASET, '--generation', g - 1, '--to', 'migrated', op='d' * 32), None)
    self.refuse(lambda: w.run('recover', '--dataset', PC.DATASET, '--generation', g, '--to', 'hosting', op='d' * 32), None)
    w.io.serve[PC.CORE_ADDR]['ok'] = False                              # the chosen state cannot be verified
    self.refuse(lambda: w.run('recover', '--dataset', PC.DATASET, '--generation', g + 1, '--to', 'migrated', op='d' * 32), None)
    self.assertEqual(w.entry()['state'], 'recovery-needed')


@v3
def test_lost_response_right_after_the_pending_write(self):
    w = self.w
    w.migrated()
    router_before = dict(w.io.router)

    def die_before_dns(domain, address, intent_path):
        raise PC.Crash()
    w.io.register_dns = die_before_dns
    with self.assertRaises(PC.Crash):
        w.cutover(op='c' * 32)
    entry = w.entry()
    self.assertEqual((entry['state'], entry['generation'], entry['pending']['action']), ('migrated', 3, 'cutover'))
    self.assertEqual(w.io.router, router_before, 'DNS moved before the crash point')
    del w.io.register_dns
    self.refuse(lambda: w.cutover(g=3, op='9' * 32), 'generation 3')           # another operation does not take the pending
    self.refuse(lambda: w.cutover(g=2, op='9' * 32), None)
    self.assertEqual(w.cutover(g=2, op='c' * 32), 0)                           # the owner resumes it
    self.assertEqual((w.entry()['state'], w.entry()['generation']), ('cutover', 4))


@v3
def test_crash_after_the_dns_write_before_the_cas_router_re_read(self):
    w = self.w
    PC.crashed_cutover(w, True)                                                # router already shows the target
    self.assertEqual(w.io.router[PC.DOMAIN], PC.TARGET_ADDR)
    w.io.ops['c' * 32] = {'state': 'interrupted'}
    self.refuse(lambda: w.run('recover', '--dataset', PC.DATASET, '--generation', 2, '--to', 'cutover', op='d' * 32), None)
    self.refuse(lambda: w.run('recover', '--dataset', PC.DATASET, '--generation', 3, '--to', 'hosting', op='d' * 32), None)
    self.assertEqual(w.entry()['state'], 'migrated')
    w.io.registered.clear()
    self.assertEqual(w.run('recover', '--dataset', PC.DATASET, '--generation', 3, '--to', 'migrated', op='d' * 32), 0)
    self.assertEqual(w.io.router[PC.DOMAIN], PC.CORE_ADDR)                     # reverted from what the router said, not assumed
    self.assertEqual(w.entry()['state'], 'migrated')


@v3
def test_crash_after_the_cas_before_the_receipt_receipt_from_stored_state(self):
    w = self.w
    w.migrated()
    original, crashed = core.Ctx.write_receipt, []

    def die_once(ctx, kind, ok, words, **extra):
        if kind == 'isle.storage.cutover' and ok and not crashed:
            crashed.append(1)
            raise PC.Crash()
        return original(ctx, kind, ok, words, **extra)
    with mock.patch.object(core.Ctx, 'write_receipt', die_once):
        with self.assertRaises(PC.Crash):
            w.cutover()
        self.assertEqual((w.entry()['state'], w.entry()['generation']), ('cutover', 4))
        stale = json.loads((w.opdir / 'storage-receipt.json').read_text())            # whatever an earlier verb left in the journal dir
        self.assertNotEqual(stale['kind'], 'isle.storage.cutover')
        done = core_snapshot(w)
        self.refuse(lambda: w.cutover(g=1, op='9' * 32), None)
        self.assertEqual(w.cutover(), 0)                                        # the same operation reruns
    self.assertEqual(core_snapshot(w), done, 'the rerun repeated a committed step')
    receipt = json.loads((w.opdir / 'storage-receipt.json').read_text())
    self.assertEqual((receipt['kind'], receipt['ok'], receipt['epoch']), ('isle.storage.cutover', True, 4))
    self.assertEqual((receipt['cutover'].get('newAddress'), receipt['cutover'].get('previousAddress'), receipt['cutover'].get('previousPin')),
                     (PC.TARGET_ADDR, PC.CORE_ADDR, '127.0.0.1'), 'contract 2.3 step 8: the receipt carries previous and new address and pin')


@v3
def test_recover_refuses_a_live_operation(self):
    w = self.w
    PC.crashed_cutover(w, False)
    w.io.ops['c' * 32] = {'state': 'running'}
    self.refuse(lambda: w.run('recover', '--dataset', PC.DATASET, '--generation', 3, '--to', 'migrated', op='d' * 32), 'c' * 32)
    self.assertEqual(w.entry()['pending']['operation'], 'c' * 32)


@v3
def test_concurrent_recover_vs_writer_one_wins_no_steal(self):
    w = self.w
    PC.crashed_cutover(w, False)
    w.io.ops['c' * 32] = {'state': 'interrupted'}
    self.assertEqual(w.run('recover', '--dataset', PC.DATASET, '--generation', 3, '--to', 'migrated', op='d' * 32), 0)
    self.assertEqual(w.entry()['state'], 'migrated')
    self.assertFalse(w.entry().get('pending'))
    won = core_snapshot(w)
    self.refuse(lambda: w.cutover(g=3, op='c' * 32), None)                     # the stale writer lost
    self.refuse(lambda: w.run('recover', '--dataset', PC.DATASET, '--generation', 3, '--to', 'migrated', op='e' * 32), None)
    self.assertEqual(core_snapshot(w), won)


@v3
def test_resume_with_the_right_action_digest_epoch_but_the_wrong_operation_id_refused(self):
    w = self.w
    PC.crashed_cutover(w, False)
    for op in ('9' * 32, '0' * 32):
        self.refuse(lambda: w.cutover(g=3, op=op), 'generation 3')
        self.refuse(lambda: w.cutover(g=2, op=op), None)
    self.assertEqual(w.entry()['pending']['operation'], 'c' * 32)
    self.assertEqual(w.entry()['state'], 'migrated')


@v3
def test_cutover_revert_and_rollback_intents_resume_and_recover(self):
    w = self.w                                                                 # rollback: DNS written, then a crash
    w.cut_over()
    original = w.io.register_dns

    def write_then_die(domain, address, intent_path):
        original(domain, address, intent_path)
        raise PC.Crash()
    w.io.register_dns = write_then_die
    with self.assertRaises(PC.Crash):
        w.run('rollback', '--dataset', PC.DATASET, '--generation', 4, op='b' * 32)
    self.assertEqual(w.entry()['pending']['action'], 'rollback')
    w.io.register_dns = original
    self.refuse(lambda: w.run('rollback', '--dataset', PC.DATASET, '--generation', 5, op='9' * 32), None)
    w.io.ops['b' * 32] = {'state': 'interrupted'}
    self.assertEqual(w.run('recover', '--dataset', PC.DATASET, '--generation', 5, '--to', 'migrated', op='d' * 32), 0)
    self.assertEqual((w.entry()['state'], w.io.router[PC.DOMAIN]), ('migrated', PC.CORE_ADDR))
    with tempfile.TemporaryDirectory() as tmp:                                 # rollback resumed by its own operation
        r = PC.World(tmp)
        r.cut_over()
        orig = r.io.register_dns
        r.io.register_dns = lambda d, a, i: (orig(d, a, i), (_ for _ in ()).throw(PC.Crash()))
        with self.assertRaises(PC.Crash):
            r.run('rollback', '--dataset', PC.DATASET, '--generation', 4, op='b' * 32)
        r.io.register_dns = orig
        self.assertEqual(r.run('rollback', '--dataset', PC.DATASET, '--generation', 4, op='b' * 32), 0)
        self.assertEqual(r.entry()['state'], 'migrated')
    with tempfile.TemporaryDirectory() as tmp:                                 # cutover-revert: crash during the revert
        v = PC.World(tmp)
        v.migrated()
        calls, orig = [], v.io.register_dns
        v.io.register_effective = False

        def second_dies(d, a, i):
            calls.append(a)
            orig(d, a, i)
            if len(calls) == 2:
                raise PC.Crash()
        v.io.register_dns = second_dies
        with self.assertRaises(PC.Crash):
            v.cutover(op='c' * 32)
        self.assertEqual(v.entry()['pending']['action'], 'cutover-revert')
        v.io.register_dns, v.io.register_effective = orig, True
        gen = v.entry()['generation']
        self.refuse(lambda: v.cutover(g=gen, op='9' * 32), None)
        v.io.ops['c' * 32] = {'state': 'interrupted'}
        self.assertEqual(v.run('recover', '--dataset', PC.DATASET, '--generation', gen, '--to', 'migrated', op='d' * 32), 0)
        self.assertEqual((v.entry()['state'], v.io.router[PC.DOMAIN]), ('migrated', PC.CORE_ADDR))


@v3
def test_a_stale_holder_of_an_older_epoch_fails_its_CAS_after_any_pending_write(self):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'placement.json'
        entry = PO.entry_at(path, 'migrated')
        g = entry['generation']
        sp.write_pending(PC.DATASET, PO.pending(), expect_generation=g, path=path, by='t')
        after_write = path.read_bytes()
        self.assertEqual(sp.hosting_for(PC.DATASET, path)['generation'], g + 1)
        stale = (lambda: sp.transition(PC.DATASET, 'migrated', 'cutover', by='t', operation='x', path=path, expect_generation=g, bump_generation=True),
                 lambda: sp.write_pending(PC.DATASET, PO.pending(op='op2'), expect_generation=g, path=path, by='t'),
                 lambda: sp.clear_pending(PC.DATASET, 'migrated', expect_generation=g, path=path, by='t'))
        for call in stale:
            with self.assertRaises(ValueError):
                call()
            self.assertEqual(path.read_bytes(), after_write)


@v3
def test_dns_register_sh_re_reads_the_intent_under_placement_lock_through_the_router_mutation(self):
    class Case(DnsFenceCase):
        def runTest(inner):
            entry = PO.entry_at(inner.placement, 'migrated', PO.pending(op='op1'))
            intent = inner.intent_file(entry)                                  # op1's intent file is valid when written...
            PO.entry_at(inner.placement, 'migrated', PO.pending(op='op2'))     # ...then recover hands the pending to op2
            before = inner.placement.read_bytes()
            done = inner.run_script('register', PO.DOMAIN, PO.TARGET, intent=intent, op='op1')
            inner.assertNotEqual(done.returncode, 0)
            inner.assertFalse(inner.wrote())
            inner.assertEqual(inner.placement.read_bytes(), before)
            fresh = inner.run_script('register', PO.DOMAIN, PO.TARGET, intent=inner.intent_file(PO.entry_at(inner.placement, 'migrated', PO.pending(op='op1'))), op='op1')
            inner.assertEqual(fresh.returncode, 0, fresh.stderr)
            inner.assertEqual(inner.locklog.read_text().split(), ['held'], 'the router was mutated without the placement lock')
    OwnershipAndDns._run_case(self, Case)


@v3
def test_stage_1_placement_view_migrated_with_the_same_digest_accepted_any_other_state_or_digest_refused(self):
    with mig() as s:
        s.routes[s.view_url]['state'] = 'migrated'
        mig_go(s)
        self.assertEqual(s.rc, 0, s.output)                                    # a re-run (host redeploy after a disk swap)
    for label, change in (('migrated other digest', {'state': 'migrated', 'plan': {'digest': 'f' * 64}}),
                          ('cutover', {'state': 'cutover'}), ('hosting', {'state': 'hosting'}),
                          ('recovery-needed', {'state': 'recovery-needed'}), ('unknown', {'state': 'who-knows'}),
                          ('another device', {'state': 'migrated', 'device': 'econ-core'})):
        with self.subTest(label), mig() as s:
            s.routes[s.view_url].update(change)
            before = mig_snapshot(s)
            mig_go(s)
            self.assertEqual(s.rc, 1)
            self.assertEqual(s.receipt['failedStage'], 'fetching-plan')
            self.assertEqual(mig_snapshot(s), before)
            self.assertEqual(s.deploys, [])


@v3
def test_pin_transition_under_the_intent_before_verification(self):
    w = self.w
    w.migrated()
    seen = []
    pin = w.io.hosts_pin

    def watch(domain, address):
        entry = w.entry()
        seen.append((entry['pending']['action'] if entry.get('pending') else None, w.io.router.get(domain), address))
        pin(domain, address)
    w.io.hosts_pin = watch
    self.assertEqual(w.cutover(), 0)
    self.assertEqual(seen[0], ('cutover', PC.CORE_ADDR, None), 'the pin moved without the intent or after DNS moved')
    with tempfile.TemporaryDirectory() as tmp:                                  # the transition itself failing refuses
        f = PC.World(tmp)
        f.migrated()
        real_pin, attempts = f.io.hosts_pin, []

        def read_only_once(domain, address):
            attempts.append(address)
            if len(attempts) == 1:
                raise ValueError('hosts file is read-only')
            real_pin(domain, address)
        f.io.hosts_pin = read_only_once
        before = core_snapshot(f)
        self.assertEqual(f.cutover(), 1)
        self.assertTrue(all(address == PC.CORE_ADDR for _, address in f.io.registered), 'DNS moved to the target although the pin could not move')
        self.assertEqual(f.io.router, before['router'])
        self.assertEqual(f.io.pins, before['pins'])
        self.assertEqual(f.entry()['state'], 'migrated')


@v3
def test_verification_by_resolve_to_the_router_derived_address_then_a_final_ordinary_resolution_check(self):
    w = self.w
    w.migrated()
    before = core_snapshot(w)
    original = w.io.fetch_current
    w.io.fetch_current = lambda domain, addr: 'd' * 64 if w.io.registered else original(domain, addr)   # target serves other bytes after DNS moved
    self.assertEqual(w.cutover(), 1)
    self.assertEqual(w.entry()['state'], 'migrated')
    self.assertEqual((w.io.router, w.io.pins), (before['router'], before['pins']))
    with tempfile.TemporaryDirectory() as tmp:
        f = PC.World(tmp)
        f.migrated()
        orig = f.io.https_check
        f.io.https_check = lambda domain, addr, paths: False if addr is None and f.io.registered else orig(domain, addr, paths)
        before = core_snapshot(f)
        self.assertEqual(f.cutover(), 1)                                        # by --resolve passed, ordinary resolution did not
        self.assertEqual(f.entry()['state'], 'migrated')
        self.assertEqual((f.io.router, f.io.pins), (before['router'], before['pins']))


@v3
def test_auto_revert_restores_the_pin_and_the_router_together(self):
    w = self.w
    w.migrated()
    before = core_snapshot(w)
    w.io.register_effective = False
    self.assertEqual(w.cutover(), 1)
    self.assertEqual(w.io.pin_log[0], (PC.DOMAIN, None))
    self.assertEqual(w.io.pin_log[-1], (PC.DOMAIN, '127.0.0.1'))
    self.assertEqual(w.io.registered[-1], (PC.DOMAIN, PC.CORE_ADDR))
    self.assertEqual((w.io.router, w.io.pins), (before['router'], before['pins']))
    self.assertFalse(w.entry().get('pending'))


@v3
def test_cutover_refused_while_a_reconcile_address_pending_exists(self):
    w = self.w
    w.cut_over()
    w.io.leases['pol-core'] = '10.10.0.202'
    w.io.serve['10.10.0.202'] = {'current': PC.CONTENT, 'ok': True}
    w.io.register_effective = False
    w.run('reconcile-address', '--dataset', PC.DATASET, '--generation', 4, op='a' * 32)
    self.assertEqual(w.entry()['pending']['action'], 'reconcile-address')
    g = w.entry()['generation']
    self.refuse(lambda: w.cutover(g=g, op='f' * 32), None)
    self.refuse(lambda: w.cutover(g=4, op='f' * 32), None)


@v3
def test_target_DHCP_address_changes_domain_follows_owner_and_epoch_unchanged(self):
    w = self.w
    w.migrated()
    self.refuse(lambda: w.run('reconcile-address', '--dataset', PC.DATASET, '--generation', 2), None)     # not in migrated
    w.cut_over()
    w.io.leases['pol-core'] = '10.10.0.201'
    w.io.serve['10.10.0.201'] = {'current': PC.CONTENT, 'ok': True}
    self.refuse(lambda: w.run('reconcile-address', '--dataset', PC.DATASET, '--generation', 3, op='a' * 32), None)
    before = w.entry()
    self.assertEqual(w.run('reconcile-address', '--dataset', PC.DATASET, '--generation', 4, op='a' * 32), 0)
    after = w.entry()
    self.assertEqual((after['state'], after['device'], after['domain'], after['plan']), (before['state'], before['device'], before['domain'], before['plan']))
    self.assertEqual(w.io.router[PC.DOMAIN], '10.10.0.201')
    self.assertEqual(after['generation'], before['generation'] + 2)


@v3
def test_concurrent_cutover_rollback_refuses(self):
    w = self.w
    w.cut_over()
    w.io.leases['pol-core'] = '10.10.0.203'
    w.io.serve['10.10.0.203'] = {'current': PC.CONTENT, 'ok': True}
    w.io.register_effective = False
    w.run('reconcile-address', '--dataset', PC.DATASET, '--generation', 4, op='a' * 32)
    g = w.entry()['generation']
    self.refuse(lambda: w.run('rollback', '--dataset', PC.DATASET, '--generation', g, op='f' * 32), None)
    self.refuse(lambda: w.run('rollback', '--dataset', PC.DATASET, '--generation', 4, op='f' * 32), None)
    self.refuse(lambda: w.cutover(g=g, op='f' * 32), None)


@v3
def test_pointerAhead_reported_when_latest_json_and_current_disagree_repaired_by_the_next_promote(self):
    env = RC.Env()
    try:
        doc, raw, parts = RC.do_receive(env, {'a': b'one', 'b': b'two'}, ())
        csha = sr.content_sha(doc)
        root = env.root()
        sr.promote(root, RC.REL, csha, raw, dataset='apt-public-repo', generation=3, op_id='p1')
        latest = env.dataset('control', 'latest.json')
        Path(latest).write_text(json.dumps(dict(contentSha='a' * 64, manifestSha='b' * 64, dataset='apt-public-repo', epoch=3)))
        before = tree(env.mount)
        self.assertTrue(sr.pointer_ahead(root, RC.REL))
        seen = sr.storage_check(env.mount, PM.UUID, '/'.join(RC.REL), findmnt_json=env.findmnt)
        self.assertTrue(seen['pointerAhead'])
        self.assertEqual(seen['currentGeneration'], csha, 'current is not the truth')
        self.assertEqual(tree(env.mount), before)
        sr.promote(root, RC.REL, csha, raw, dataset='apt-public-repo', generation=3, op_id='p2')
        self.assertFalse(sr.pointer_ahead(root, RC.REL))
        root.close()
    finally:
        env.close()
    with mig() as s:                                                            # the launch gate refuses on it too
        def rewind(label, info):
            if label == 'deploying-host':
                Path(s.dataset_path, 'control', 'latest.json').write_text(json.dumps(dict(contentSha='a' * 64)))
        s.hook_extra = rewind
        mig_go(s)
        self.assertEqual(s.rc, 1)
        self.assertEqual(s.receipt['failedStage'], 'deploying-host')
        self.assertIn('pointerAhead', s.receipt['words'])
        self.assertEqual(s.deploys, [])


@v3
def test_well_known_isle_storage_current_matches_readlink_current(self):
    env = RC.Env()
    try:
        doc, raw, parts = RC.do_receive(env, {'a': b'one'}, ())
        csha = sr.content_sha(doc)
        root = env.root()
        sr.promote(root, RC.REL, csha, raw, dataset='apt-public-repo', generation=3, op_id='p1')
        self.assertEqual(os.readlink(env.dataset('current')), 'generations/' + csha)
        self.assertEqual(Path(env.dataset('control', 'current')).read_text(), csha + '\n')
        root.close()
    finally:
        env.close()
    with mig() as s:                                                            # a host serving another current is not ready
        s.served_current = 'd' * 64
        mig_go(s)
        self.assertEqual(s.rc, 1)
        self.assertEqual(s.receipt['failedStage'], 'verifying-readiness')
        self.assertIn('contentSha', s.receipt['words'])
        self.assertIsNot(s.receipt.get('readinessVerified'), True)
        self.assertFalse(any(p[1].get('ok') is True for p in s.http.posts))


@v3
@unittest.skip(NOTEST + 'the receipt/witness doors are Isle App backend routes (POST /api/islemesh/storage/witness); not in this repo. Skeleton: post witness W for (dataset, G, device) twice with identical bytes (200, seen+1) and then a different body for the same triple (409).')
def test_identical_witness_re_post_idempotent_vs_a_different_witness_for_the_same_dataset_G_device_rejected(self):
    pass


@v3
def test_authorize_and_witness_hold_no_lease(self):
    w = self.w

    @contextlib.contextmanager
    def busy():
        raise core.DeviceUpdateBusy('device update in progress on isle-core; retry after it completes')
        yield
    w.io.lease = busy
    self.assertEqual(w.authorize(), 0, w.io.said())                             # authorize does not take the lease
    w.receipts()
    self.refuse(lambda: w.mark(), 'device update in progress')                  # the critical verbs do
    self.assertEqual(w.entry()['state'], 'authorized')
    text = (HERE / 'storage_witness.py').read_text()
    self.assertNotRegex(text, r'app-device-update\.lock|LOCK_SH|LOCK_EX|flock')
    fake = WI.Fake()
    env = WI.Env(fake)
    self.assertEqual(env.run(), 0)                                              # runs with no lease path at all


@v3
def test_isle_update_plan_takes_no_lock(self):
    text = (HERE / 'update.py').read_text()
    plan = text.split("if action=='plan' and len(args)<=1:", 1)[1].split("if action!='apply'", 1)[0]
    self.assertNotRegex(plan, r'flock|lock|operations|execute')
    import operations
    with self.assertRaises(ValueError):                                         # plan is not a journaled (locking) operation
        operations.request(['update', 'plan'])


# ================================================================== census

CENSUS_CLASSES = (DiskAndMount, ContentAndPaths, PlanAndRequest, OwnershipAndDns, ReceiptsAndWitness, V3Additions)
CENSUS_TOTAL, CENSUS_EXECUTED, CENSUS_LOOP, CENSUS_NOT_TESTABLE = 107, 100, 2, 5


class ZCensus(unittest.TestCase):
    def test_census_matches_the_header(self):
        total = executed = loop = notest = 0
        for cls in CENSUS_CLASSES:
            for name in dir(cls):
                if not name.startswith('test_') or name.endswith('__loopdev'):
                    continue
                method = getattr(cls, name)
                total += 1
                if getattr(method, '__unittest_skip__', False):
                    if getattr(method, '__unittest_skip_why__', '').startswith('needs root'):
                        loop += 1
                    else:
                        notest += 1
                else:
                    executed += 1
        self.assertEqual((total, executed, loop, notest), (CENSUS_TOTAL, CENSUS_EXECUTED, CENSUS_LOOP, CENSUS_NOT_TESTABLE),
                         'update the CENSUS_* constants and the header')


if __name__ == '__main__':
    unittest.main(verbosity=1)
