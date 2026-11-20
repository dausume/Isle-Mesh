#!/usr/bin/env python3
"""Unprivileged tests for hotfix.py: fake Deps over temp dirs, no systemd, no sudo, no network."""
import hashlib, json, os, subprocess, sys, tempfile, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hotfix as hf
import storage_mount as sm

RESULTS = []
def test(fn):
    try:
        fn(); RESULTS.append((fn.__name__, None)); print('PASS ' + fn.__name__)
    except Exception:
        RESULTS.append((fn.__name__, traceback.format_exc())); print('FAIL ' + fn.__name__ + '\n' + traceback.format_exc())
    return fn

DS, APP, MP, REL = 'tank', 'board-engines', '/mnt/pol-ssd', 'isle/tank'
DSPATH = MP + '/' + REL
GATE = 'isle-storage-%s.service' % DS
SYNC, TIMER = sm.sync_unit_names(DS)

def tree_hash(root):
    h = hashlib.sha256()
    for base, dirs, files in os.walk(str(root)):
        dirs.sort()
        for d in dirs: h.update(('D' + os.path.relpath(os.path.join(base, d), str(root))).encode())
        for f in sorted(files):
            p = os.path.join(base, f); h.update(('F' + os.path.relpath(p, str(root))).encode())
            h.update(os.readlink(p).encode() if os.path.islink(p) else Path(p).read_bytes())
    return h.hexdigest()

class Fake(hf.Deps):
    """Everything under one temp root; systemctl is recorded, never run."""
    def __init__(self, root):
        r = Path(root)
        self.root = r
        self.backup_root, self.unit_dir, self.state_dir = r / 'backup', r / 'etc-systemd', r / 'state'
        self.old_cli, self.cli = r / 'usr-bin-isle', r / 'usr-local-bin-isle'
        self.share_root, self.dpkg_info = r / 'share' / 'isle-mesh', r / 'dpkg'
        for d in (self.unit_dir, self.state_dir, self.share_root, self.dpkg_info): d.mkdir(parents=True, exist_ok=True)
        self.said, self.calls, self.enabled, self.fail = [], [], {TIMER}, set()
        self.root_user, self.answer, self.asked = True, True, 0
        self.clock = hf.datetime(2026, 11, 16, 12, 0, 0, tzinfo=hf.timezone.utc)
    def say(self, line=''): self.said.append(line)
    def is_root(self): return self.root_user
    def now(self):
        self.clock = self.clock.replace(second=self.clock.second + 1); return self.clock
    def confirm(self, prompt): self.asked += 1; return self.answer
    def systemctl(self, args, timeout=60):
        self.calls.append(list(args))
        if args[0] == 'is-enabled': return 0 if args[-1] in self.enabled else 1
        return 1 if args[-1] in self.fail or args[0] in self.fail else 0
    def out(self): return '\n'.join(self.said)

def old_gate():
    return sm.unit_text(DS, MP, DSPATH, APP, 'isle-' + APP).replace('/usr/local/bin/isle', '/usr/bin/isle') \
        .replace('WorkingDirectory=/etc/isle-mesh/apps/%s\n' % APP, '').replace(' -f docker-compose.yml -f isle-overlay.yml', '')

def world(t, gate=None, sync=True, binding=True, link=True, dropins=()):
    f = Fake(t)
    (f.state_dir / DS).mkdir()
    if binding is True: (f.state_dir / DS / 'binding.json').write_text(json.dumps(
        {'dataset': DS, 'mountpoint': MP, 'relativePath': REL, 'hostApp': APP, 'schemaVersion': 2}))
    elif binding: (f.state_dir / DS / 'binding.json').write_text(binding)
    if gate is not False: (f.unit_dir / GATE).write_text(old_gate() if gate is None else gate)
    if sync:
        s, tm = sm.sync_timer_text(DS)
        (f.unit_dir / SYNC).write_text(s.replace('/usr/local/bin/isle', '/usr/bin/isle')); (f.unit_dir / TIMER).write_text(tm)
    f.cli.write_text('#!/bin/sh\n')
    if link: f.old_cli.symlink_to(f.cli)
    for unit, name, text in dropins:
        d = f.unit_dir / (unit + '.d'); d.mkdir(exist_ok=True); (d / name).write_text(text)
    return f

def apply(f, *flags): return hf.main(['apply', 'storage-units', '--yes'] + list(flags), f)
def started(f): return [c for c in f.calls if c[0] in ('start', 'stop', 'restart', 'enable', 'disable', 'reload', 'try-restart')]

@test
def old_units_are_rewritten_backed_up_and_the_gate_is_never_touched():
    with tempfile.TemporaryDirectory() as t:
        f = world(t); old = (f.unit_dir / GATE).read_text()
        assert apply(f) == 0, f.out()
        assert (f.unit_dir / GATE).read_text() == sm.unit_text(DS, MP, DSPATH, APP, 'isle-' + APP)
        assert (f.unit_dir / SYNC).read_text() == sm.sync_timer_text(DS)[0] and (f.unit_dir / TIMER).read_text() == sm.sync_timer_text(DS)[1]
        assert oct((f.unit_dir / GATE).stat().st_mode & 0o777) == '0o644'
        bk = list((f.backup_root / 'storage-units').glob('2*'))
        assert len(bk) == 1 and bk[0].name.endswith('Z') and len(bk[0].name) == 16
        assert (bk[0] / str(f.unit_dir / GATE).lstrip('/')).read_text() == old
        assert f.calls[0] == ['daemon-reload']
        assert not [c for c in f.calls if GATE in ' '.join(c)], f.calls          # the gate is never started/stopped/restarted/enabled
        assert not started(f) or started(f) == [['start', SYNC]], f.calls
        assert not [c for c in f.calls if c[0] in ('enable', 'disable', 'stop', 'restart')], f.calls
        rec = json.loads((f.backup_root / 'storage-units' / 'applied.json').read_text())
        assert len(rec) == 1 and rec[0]['result'] == 'ok' and rec[0]['backupDir'] == str(bk[0]) and rec[0]['actions'], rec
        assert not f.old_cli.is_symlink()                                         # nothing named it any more
        assert (bk[0] / str(f.old_cli).lstrip('/')).is_symlink()

@test
def a_second_apply_has_nothing_to_do_and_writes_no_receipt():
    with tempfile.TemporaryDirectory() as t:
        f = world(t); assert apply(f) == 0
        f.calls.clear(); f.said.clear(); before = tree_hash(t)
        assert apply(f) == 0 and 'Nothing to do.' in f.out() and f.calls == [] and tree_hash(t) == before
        assert hf.main(['check', 'storage-units'], f) == 0

@test
def sync_proof_only_for_enabled_timers_and_its_failure_is_reported():
    with tempfile.TemporaryDirectory() as t:
        f = world(t); f.enabled = set(); assert apply(f) == 0
        assert not [c for c in f.calls if c[0] == 'start'] and ['is-enabled', '--quiet', TIMER] in f.calls
    with tempfile.TemporaryDirectory() as t:
        f = world(t); assert apply(f) == 0 and ['start', SYNC] in f.calls
    with tempfile.TemporaryDirectory() as t:
        f = world(t); f.fail = {SYNC}; assert apply(f) == 1
        assert 'units were rewritten; the sync service did not run: see journalctl -u ' + SYNC in f.out()
        assert (f.unit_dir / GATE).read_text() == sm.unit_text(DS, MP, DSPATH, APP, 'isle-' + APP)
        assert f.old_cli.is_symlink()                                              # removal happens only after the proof
        rec = json.loads((f.backup_root / 'storage-units' / 'applied.json').read_text())
        assert rec[-1]['result'] == 'failed'
        f.fail = set(); f.said.clear()                                             # a rerun finishes the symlink, proving again
        assert apply(f) == 0 and not f.old_cli.is_symlink() and ['start', SYNC] in f.calls

@test
def a_failed_reload_leaves_the_fix_needed_and_a_rerun_finishes_it():
    with tempfile.TemporaryDirectory() as t:
        f = world(t, link=False); f.fail = {'daemon-reload'}; assert apply(f) == 1
        assert 'daemon-reload failed' in f.out() and not [c for c in f.calls if c[0] == 'start']
        assert (f.unit_dir / GATE).read_text() == sm.unit_text(DS, MP, DSPATH, APP, 'isle-' + APP)   # files are written: the plan is empty now
        f.said.clear(); assert hf.main(['check', 'storage-units'], f) == 3 and 'The last apply failed' in f.out()
        f.said.clear(); assert hf.main(['status'], f) == 0 and 'needed: the last apply failed' in f.out()
        f.fail = set(); f.calls.clear(); f.said.clear()
        assert apply(f) == 0 and ['daemon-reload'] in f.calls and ['start', SYNC] in f.calls
        assert not [c for c in started(f) if c[-1] == GATE]
        rec = json.loads((f.backup_root / 'storage-units' / 'applied.json').read_text())
        assert [r['result'] for r in rec] == ['failed', 'ok']
        f.calls.clear(); f.said.clear(); assert apply(f) == 0 and 'Nothing to do.' in f.out() and f.calls == []

@test
def no_sync_proof_skips_and_records_it():
    with tempfile.TemporaryDirectory() as t:
        f = world(t); assert apply(f, '--no-sync-proof') == 0
        assert not [c for c in f.calls if c[0] in ('start', 'is-enabled')]
        assert 'skipped' in json.loads((f.backup_root / 'storage-units' / 'applied.json').read_text())[0]['syncProof']

@test
def redundant_dropin_is_moved_aside_and_a_hand_one_is_kept():
    gate = sm.unit_text(DS, MP, DSPATH, APP, 'isle-' + APP)
    exec_start = [l for l in gate.splitlines() if l.startswith('ExecStart=')][0]
    redundant = '# hand fix\n[Service]\nExecStart=\n%s\nWorkingDirectory=\n' % exec_start
    with tempfile.TemporaryDirectory() as t:
        f = world(t, dropins=[(GATE, 'fix.conf', redundant), (GATE, 'mine.conf', '[Service]\nEnvironment=FOO=1\n')])
        assert hf.main(['check', 'storage-units'], f) == 3
        assert 'hand drop-in kept, not covered by the rendered unit' in f.out() and 'mine.conf' in f.out()
        assert apply(f) == 0
        d = f.unit_dir / (GATE + '.d')
        assert not (d / 'fix.conf').exists() and (d / 'mine.conf').read_text() == '[Service]\nEnvironment=FOO=1\n'
        bk = next((f.backup_root / 'storage-units').glob('2*'))
        assert (bk / str(d / 'fix.conf').lstrip('/')).read_text() == redundant
        assert 'hand drop-in kept' in f.out()
    with tempfile.TemporaryDirectory() as t:       # the .d dir goes with its last file
        f = world(t, dropins=[(GATE, 'fix.conf', redundant)])
        assert apply(f) == 0 and not (f.unit_dir / (GATE + '.d')).exists()
    assert hf.dropin_redundant('[Service]\nExecStart=\n', sm.sync_timer_text(DS)[0])
    assert not hf.dropin_redundant('[Service]\nBogus=\n', sm.sync_timer_text(DS)[0])

@test
def the_symlink_is_kept_while_a_kept_dropin_names_it_and_a_regular_file_is_left_alone():
    with tempfile.TemporaryDirectory() as t:
        f = world(t, dropins=[(GATE, 'mine.conf', '[Service]\nExecStartPost=/usr/bin/isle storage check\n')])
        assert apply(f) == 0 and f.old_cli.is_symlink() and 'mine.conf' in f.out() and 'still named by' in f.out()
        assert 'remove-symlink' not in f.out()
    with tempfile.TemporaryDirectory() as t:
        f = world(t, link=False); f.old_cli.write_text('regular')
        assert apply(f) == 0 and f.old_cli.read_text() == 'regular'
        assert any('not a symlink' in n for n in hf.FIXES['storage-units']['notes'](f))
        hf.main(['status'], f); assert 'not a symlink' in f.out()
    with tempfile.TemporaryDirectory() as t:       # a symlink to some other file is not ours
        f = world(t, link=False); other = Path(t) / 'other'; other.write_text('x'); f.old_cli.symlink_to(other)
        assert apply(f) == 0 and f.old_cli.is_symlink()
    with tempfile.TemporaryDirectory() as t:       # an unrelated unit naming /usr/bin/isle also keeps it
        f = world(t); (f.unit_dir / 'other.service').write_text('ExecStart=/usr/bin/isle x\n')
        assert apply(f) == 0 and f.old_cli.is_symlink() and 'other.service' in f.out()

@test
def a_damaged_binding_blocks_and_a_device_without_units_is_not_needed():
    with tempfile.TemporaryDirectory() as t:
        f = world(t, binding='{not json'); before = tree_hash(t)
        assert hf.main(['check', 'storage-units'], f) == 1 and 'binding.json' in f.out()
        assert apply(f) == 1 and f.calls == [] and tree_hash(t) == before
        f.said.clear(); hf.main(['status'], f); assert 'blocked:' in f.out()
    with tempfile.TemporaryDirectory() as t:
        f = world(t, binding='{"dataset": "other"}'); assert hf.main(['check', 'storage-units'], f) == 1
    with tempfile.TemporaryDirectory() as t:       # a state dir but no gate unit: this device does not host it
        f = world(t, gate=False, sync=False, binding='{broken', link=False)
        assert hf.main(['check', 'storage-units'], f) == 0 and 'Nothing to do.' in f.out()
        f.said.clear(); hf.main(['status'], f); assert 'not-needed' in f.out()
    with tempfile.TemporaryDirectory() as t:       # no state at all
        f = Fake(t); assert hf.main(['check', 'storage-units'], f) == 0

@test
def check_exit_codes_and_check_writes_nothing():
    with tempfile.TemporaryDirectory() as t:
        f = world(t); before = tree_hash(t)
        assert hf.main(['check', 'storage-units'], f) == 3
        out = f.out()
        assert '--- ' + str(f.unit_dir / GATE) in out and '+WorkingDirectory=' in out and '-ExecStartPre=/usr/bin/isle' in out, out
        assert 'remove-symlink' in out and tree_hash(t) == before and f.calls == []
        assert hf.main(['check', 'nope'], f) == 2 and hf.main(['check'], f) == 2 and hf.main(['bogus'], f) == 2
        assert hf.main(['apply', 'storage-units', '--what'], f) == 2 and hf.main(['list', 'x'], f) == 2
        assert apply(f) == 0 and hf.main(['check', 'storage-units'], f) == 0
    with tempfile.TemporaryDirectory() as t:       # a missing sync unit beside an existing gate is written
        f = world(t, sync=False); assert hf.main(['check', 'storage-units'], f) == 3
        assert apply(f) == 0 and (f.unit_dir / TIMER).is_file() and (f.unit_dir / SYNC).is_file()

@test
def apply_asks_unless_yes_and_refuses_without_root():
    with tempfile.TemporaryDirectory() as t:
        f = world(t); f.answer = False; before = tree_hash(t)
        assert hf.main(['apply', 'storage-units'], f) == 1 and f.asked == 1 and tree_hash(t) == before
        f.answer = True; assert hf.main(['apply', 'storage-units'], f) == 0 and f.asked == 2
        g = world(Path(t) / 'two') if (Path(t) / 'two').mkdir() is None else None
        g.root_user = False; assert hf.main(['apply', 'storage-units', '--yes'], g) == 1 and g.calls == []

# ---- hand-edit-backups
def dpkg_world(t, installed, listed, name='storage_receive.py'):
    f = Fake(t)
    rel = 'isle-cli/scripts/' + name
    p = f.share_root / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(installed)
    (p.parent / (name + '.pre-hotfix-20261114')).write_text('the original\n')
    if listed is not None:
        (f.dpkg_info / 'isle-mesh-cli.md5sums').write_text('%s  usr/share/isle-mesh/%s\n' % (hashlib.md5(listed.encode()).hexdigest(), rel))
    return f, p

@test
def backups_are_moved_when_the_package_file_is_back_and_kept_otherwise():
    with tempfile.TemporaryDirectory() as t:
        f, p = dpkg_world(t, 'package text\n', 'package text\n'); bak = p.parent / (p.name + '.pre-hotfix-20261114')
        assert hf.main(['check', 'hand-edit-backups'], f) == 3
        assert hf.main(['apply', 'hand-edit-backups', '--yes'], f) == 0 and not bak.exists() and p.read_text() == 'package text\n'
        moved = next((f.backup_root / 'hand-edit-backups').glob('2*')) / str(bak).lstrip('/')
        assert moved.read_text() == 'the original\n'
        assert hf.main(['apply', 'hand-edit-backups', '--yes'], f) == 0 and 'Nothing to do.' in f.out()
    with tempfile.TemporaryDirectory() as t:
        f, p = dpkg_world(t, 'hand edited\n', 'package text\n'); bak = p.parent / (p.name + '.pre-hotfix-20261114')
        assert hf.main(['check', 'hand-edit-backups'], f) == 0 and bak.exists() and 'still installed' in f.out()
        assert hf.main(['apply', 'hand-edit-backups', '--yes'], f) == 0 and bak.exists()
    with tempfile.TemporaryDirectory() as t:       # no package lists it
        f, p = dpkg_world(t, 'x\n', None); bak = p.parent / (p.name + '.pre-hotfix-20261114')
        assert hf.main(['apply', 'hand-edit-backups', '--yes'], f) == 0 and bak.exists() and 'still installed' in f.out()
    with tempfile.TemporaryDirectory() as t:       # another package's listing does not match this path
        f, p = dpkg_world(t, 'x\n', 'x\n', name='a.py'); (f.dpkg_info / 'isle-mesh-cli.md5sums').write_text('0' * 32 + '  usr/share/isle-mesh/other\n')
        assert hf.main(['check', 'hand-edit-backups'], f) == 0

@test
def status_lists_hand_edited_files_and_backups_and_says_none_when_clean():
    with tempfile.TemporaryDirectory() as t:
        f, p = dpkg_world(t, 'hand edited\n', 'package text\n')
        (f.share_root / 'clean.txt').write_text('ok\n')
        (f.dpkg_info / 'other.md5sums').write_text('%s  usr/share/isle-mesh/clean.txt\n%s  usr/bin/elsewhere\n' % (hashlib.md5(b'ok\n').hexdigest(), '0' * 32))
        assert hf.main(['status'], f) == 0
        out = f.out()
        assert 'Installed files that differ from their package (hand edits):' in out
        assert 'differs   %s (package isle-mesh-cli)' % p in out and 'backup    %s.pre-hotfix-20261114' % p in out and 'clean.txt' not in out
        assert 'hand-edit-backups' in out and 'storage-units' in out
    with tempfile.TemporaryDirectory() as t:
        f = Fake(t); hf.main(['status'], f); assert 'differ from their package (hand edits):\n  none' in f.out()

@test
def status_shows_applied_after_a_receipt_and_list_names_every_fix():
    with tempfile.TemporaryDirectory() as t:
        f = world(t); apply(f); f.said.clear(); hf.main(['status'], f)
        assert 'applied 2026-11-16T12:00' in f.out(), f.out()
        f.said.clear(); assert hf.main(['list'], f) == 0
        assert all(n in f.out() for n in hf.FIXES) and 'replaced by:' in f.out() and 'kept long term' in f.out()
        assert all(fx['why'] and fx['summary'] for fx in hf.FIXES.values())

@test
def unreadable_files_do_not_break_status():
    with tempfile.TemporaryDirectory() as t:
        f, p = dpkg_world(t, 'x\n', 'x\n'); p.chmod(0)
        try: assert hf.main(['status'], f) == 0
        finally: p.chmod(0o644)

@test
def hotfix_sh_refuses_apply_without_root_and_routes_the_rest():
    sh = str(Path(__file__).resolve().parent / 'hotfix.sh')
    if os.geteuid() != 0:
        p = subprocess.run(['bash', sh, 'apply', 'storage-units'], capture_output=True, text=True)
        assert p.returncode == 1 and 'sudo isle hotfix apply storage-units' in p.stdout, p
    p = subprocess.run(['bash', sh, 'nonsense'], capture_output=True, text=True); assert p.returncode == 2
    p = subprocess.run(['bash', sh, 'help'], capture_output=True, text=True); assert p.returncode == 0 and 'isle hotfix' in p.stdout

failed = [n for n, e in RESULTS if e]
print('\n%d passed, %d failed' % (len(RESULTS) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
