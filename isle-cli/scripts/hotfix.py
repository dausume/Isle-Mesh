#!/usr/bin/env python3
"""isle hotfix: named, idempotent, reviewable repairs of state that is already written on a device and that new code
does not rewrite.  Each fix has plan(deps) -> actions (shared by check and apply: apply executes exactly the list it
showed) and apply(deps, actions, backup_dir).  Everything replaced or removed is copied to
<backup_root>/<name>/<UTC stamp>/ first; a receipt is appended to <backup_root>/<name>/applied.json.

  isle hotfix list | status | check <name> | apply <name> [--yes] [--no-sync-proof] | help

Exit codes: 0 done / nothing to do, 3 (check) changes needed, 1 error or blocked, 2 usage.
Action kinds: rewrite, write, move-aside, remove-symlink (do something); kept, note (shown, never touched);
blocked (the fix cannot decide safely: nothing is applied).
"""
import difflib, hashlib, json, os, re, shutil, subprocess, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import storage_mount

DOING = ('rewrite', 'write', 'move-aside', 'remove-symlink')
SHARE_REL = 'usr/share/isle-mesh'
BACKUP_SUFFIX = re.compile(r'\.pre-hotfix-[^/]*\Z')
OLD_CLI = '/usr/bin/isle'


class Deps:
    """Real side effects and paths. Tests substitute temp dirs and fakes with the same attributes and methods."""
    backup_root = Path('/var/lib/isle-mesh/hotfix')
    unit_dir = Path(os.environ.get('ISLE_STORAGE_UNIT_DIR') or '/etc/systemd/system')
    state_dir = Path(os.environ.get('ISLE_STORAGE_STATE_DIR') or '/var/lib/isle-mesh/storage')
    old_cli = Path(OLD_CLI)
    cli = Path(storage_mount.ISLE_CLI)
    share_root = Path('/usr/share/isle-mesh')
    dpkg_info = Path('/var/lib/dpkg/info')
    sync_timeout = 1800

    def say(self, line=''): print(line, flush=True)
    def is_root(self): return os.geteuid() == 0
    def now(self): return datetime.now(timezone.utc)
    def confirm(self, prompt):
        if not sys.stdin.isatty(): return False
        return input(prompt + ' [y/N] ').strip().lower() == 'y'

    def systemctl(self, args, timeout=60):
        """Exit code of `systemctl <args>`; 124 on timeout, 127 when systemctl is missing."""
        try: return subprocess.run(['systemctl'] + list(args), timeout=timeout).returncode
        except subprocess.TimeoutExpired: return 124
        except OSError: return 127


# ---------------------------------------------------------------- small helpers

def read_text(path):
    try: return Path(path).read_text()
    except (OSError, UnicodeDecodeError): return None

def md5_of(path):
    try:
        h = hashlib.md5()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''): h.update(chunk)
        return h.hexdigest()
    except OSError: return None

def write_atomic(path, text, mode=0o644):
    """Temp file in the same directory, chmod, os.replace: a reader never sees half a file."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + '.tmp-')
    try:
        with os.fdopen(fd, 'w') as out:
            out.write(text); out.flush(); os.fsync(out.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try: os.unlink(tmp)
        except OSError: pass
        raise

def action(kind, path, detail, **kw): return dict(kind=kind, path=str(path), detail=detail, **kw)
def doing(actions): return [a for a in actions if a['kind'] in DOING]
def blocked(actions): return [a for a in actions if a['kind'] == 'blocked']
def kept(actions): return [a for a in actions if a['kind'] == 'kept']

def backup_copy(path, backup_dir):
    """Copy path (a file or a symlink, kept as a symlink) to backup_dir/<path without the leading slash>."""
    dest = Path(backup_dir) / str(path).lstrip('/')
    dest.parent.mkdir(parents=True, exist_ok=True)
    if os.path.islink(path): os.symlink(os.readlink(path), dest)
    else: shutil.copy2(path, dest)
    return dest

def backup_move(path, backup_dir):
    dest = Path(backup_dir) / str(path).lstrip('/')
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dest))

def walk_files(root):
    """Every non-directory entry under root (symlinks not followed), sorted."""
    out = []
    for base, dirs, files in os.walk(str(root)):
        dirs.sort()
        out.extend(os.path.join(base, f) for f in sorted(files))
    return out

def dpkg_index(deps):
    """{relative path without leading slash: (package, md5)} for every md5sums entry under usr/share/isle-mesh/."""
    index = {}
    try: files = sorted(Path(deps.dpkg_info).glob('*.md5sums'))
    except OSError: return index
    for f in files:
        text = read_text(f)
        if text is None: continue
        pkg = f.name[:-len('.md5sums')]
        for line in text.splitlines():
            md5, _, rel = line.partition('  ')
            if rel.startswith(SHARE_REL + '/') and len(md5) == 32: index.setdefault(rel, (pkg, md5))
    return index

def share_rel(deps, path):
    return SHARE_REL + '/' + os.path.relpath(str(path), str(deps.share_root))


# ---------------------------------------------------------------- fix 1: storage-units

def hosted_datasets(deps):
    """[(dataset, rendered, problem)]: one row per binding state dir whose gate unit exists on this device.
    rendered = {unit file name: text}; problem = why the row cannot be trusted (then rendered is None)."""
    rows = []
    try: names = sorted(p.name for p in Path(deps.state_dir).iterdir() if p.is_dir())
    except OSError: return rows
    for ds in names:
        gate = 'isle-storage-%s.service' % ds
        if not (Path(deps.unit_dir) / gate).is_file(): continue      # not hosted here
        binding = Path(deps.state_dir) / ds / 'binding.json'
        try:
            b = json.loads(binding.read_text())
            if not isinstance(b, dict): raise ValueError('not an object')
            for key in ('dataset', 'mountpoint', 'relativePath', 'hostApp'):
                if not isinstance(b.get(key), str) or not b[key]: raise ValueError('no usable "%s"' % key)
            if b['dataset'] != ds: raise ValueError('names dataset %r, not %r' % (b['dataset'], ds))
            path = b['mountpoint'].rstrip('/') + '/' + b['relativePath']
            sync, timer = storage_mount.sync_unit_names(ds)
            stext, ttext = storage_mount.sync_timer_text(ds)
            rendered = {gate: storage_mount.unit_text(ds, b['mountpoint'], path, b['hostApp'], 'isle-' + b['hostApp']),
                        sync: stext, timer: ttext}
        except (OSError, ValueError) as exc:
            rows.append((ds, None, 'cannot use %s: %s' % (binding, exc))); continue
        rows.append((ds, rendered, None))
    return rows

def unit_lines(text): return [l.strip() for l in text.splitlines()]

def dropin_redundant(text, rendered):
    """True when every effective line of the drop-in is an empty reset of a key the rendered unit sets, or one of its lines."""
    lines = set(unit_lines(rendered))
    keys = {l.split('=', 1)[0] for l in lines if '=' in l and not l.startswith(('#', ';', '['))}
    for raw in unit_lines(text):
        if not raw or raw[0] in '#;' or (raw.startswith('[') and raw.endswith(']')): continue
        if raw in lines: continue
        if raw.endswith('=') and raw[:-1].strip() in keys: continue
        return False
    return True

def references(deps, planned, moved):
    """Files under unit_dir that name /usr/bin/isle once the planned rewrites are counted as done."""
    found = []
    for f in walk_files(deps.unit_dir):
        if f in moved or os.path.islink(f): continue
        text = planned.get(f)
        if text is None: text = read_text(f)
        if text and OLD_CLI in text: found.append(f)
    return found

def storage_plan(deps):
    acts, planned, moved = [], {}, set()
    unit_dir = Path(deps.unit_dir)
    for ds, rendered, problem in hosted_datasets(deps):
        if problem: acts.append(action('blocked', Path(deps.state_dir) / ds / 'binding.json', problem)); continue
        gate = 'isle-storage-%s.service' % ds
        for name, text in rendered.items():
            path = unit_dir / name
            if not path.is_file():
                if name != gate: acts.append(action('write', path, 'missing sync unit; written from this CLI', new=text)); planned[str(path)] = text
                continue
            old = read_text(path)
            if old is None: acts.append(action('blocked', path, 'cannot read the existing unit file'))
            elif old != text:
                n = sum(1 for l in difflib.ndiff(old.splitlines(), text.splitlines()) if l[:1] in '+-')
                acts.append(action('rewrite', path, 'unit text differs from what this CLI renders (%d changed line(s))' % n, old=old, new=text))
                planned[str(path)] = text
        for name in (gate, storage_mount.sync_unit_names(ds)[0]):
            ddir = unit_dir / (name + '.d')
            confs = sorted(ddir.glob('*.conf')) if ddir.is_dir() else []
            for conf in confs:
                text = read_text(conf)
                if text is not None and dropin_redundant(text, rendered[name]):
                    acts.append(action('move-aside', conf, 'drop-in adds nothing the rendered unit does not already say')); moved.add(str(conf))
                else:
                    acts.append(action('kept', conf, 'hand drop-in kept, not covered by the rendered unit'))
    # /usr/bin/isle: only a symlink to the real CLI is ours to remove, and only when nothing names it any more
    link = Path(deps.old_cli)
    if os.path.islink(link) and Path(deps.cli).exists() and os.path.realpath(link) == os.path.realpath(deps.cli):
        refs = references(deps, planned, moved)
        if refs: acts.append(action('kept', link, 'symlink to %s kept: still named by %s' % (deps.cli, ', '.join(os.path.basename(r) for r in refs))))
        else: acts.append(action('remove-symlink', link, 'symlink to %s that nothing under %s names any more' % (deps.cli, deps.unit_dir),
                                 target=os.readlink(link)))
    return acts

def storage_notes(deps):
    link = Path(deps.old_cli)
    if os.path.lexists(link) and not os.path.islink(link): return ['%s exists and is not a symlink; left alone' % link]
    if os.path.islink(link) and not (Path(deps.cli).exists() and os.path.realpath(link) == os.path.realpath(deps.cli)):
        return ['%s is a symlink to something other than %s; left alone' % (link, deps.cli)]
    return []

def storage_apply(deps, actions, backup_dir, opts=None):
    """Write the units, daemon-reload, prove sync can run, then (last) remove the symlink.
    NEVER start, stop or restart isle-storage-<dataset>.service (the gate): stopping it stops the hosted app.
    Enablement is not touched either; only the sync SERVICE (a oneshot) is started, and only for enabled timers."""
    opts, done, info = opts or {}, [], {}
    files = False
    for a in actions:
        if a['kind'] in ('rewrite', 'write'):
            if a['kind'] == 'rewrite': backup_copy(a['path'], backup_dir)
            write_atomic(a['path'], a['new'])
            files = True
        elif a['kind'] == 'move-aside':
            backup_move(a['path'], backup_dir)
            try: os.rmdir(os.path.dirname(a['path']))      # the .d dir, when that was its last file
            except OSError: pass
            files = True
        else: continue
        done.append(a)
    # opts['retry']: the last apply failed after the files were written (reload or sync proof), so finish that part again.
    if (files or opts.get('retry')) and deps.systemctl(['daemon-reload']) != 0:
        info['error'] = 'systemctl daemon-reload failed; the unit files were rewritten'
        info['done'] = done; return info
    if opts.get('no_sync_proof'): info['syncProof'] = 'skipped (--no-sync-proof)'
    else:
        proved = []
        for ds, rendered, problem in hosted_datasets(deps):
            if problem: continue
            sync, timer = storage_mount.sync_unit_names(ds)
            if deps.systemctl(['is-enabled', '--quiet', timer]) != 0: continue
            if deps.systemctl(['start', sync], timeout=deps.sync_timeout) != 0:
                info['error'] = 'units were rewritten; the sync service did not run: see journalctl -u %s' % sync
                info['done'] = done; info['syncProof'] = 'failed for ' + sync; return info
            proved.append(sync)
        info['syncProof'] = 'ran ' + ', '.join(proved) if proved else 'no enabled sync timer, nothing to run'
    for a in actions:
        if a['kind'] == 'remove-symlink':
            backup_copy(a['path'], backup_dir)
            os.unlink(a['path']); done.append(a)
    info['done'] = done
    return info


# ---------------------------------------------------------------- fix 2: hand-edit-backups

def backups_plan(deps):
    acts, index = [], dpkg_index(deps)
    for f in walk_files(deps.share_root):
        if not BACKUP_SUFFIX.search(os.path.basename(f)) or os.path.islink(f): continue
        neighbour = BACKUP_SUFFIX.sub('', f)
        entry = index.get(share_rel(deps, neighbour)) if os.path.isfile(neighbour) else None
        if entry and md5_of(neighbour) == entry[1]:
            acts.append(action('move-aside', f, 'the installed %s matches package %s again; the hand edit is gone' % (os.path.basename(neighbour), entry[0])))
        elif not os.path.isfile(neighbour):
            acts.append(action('kept', f, 'the file it backs up is not installed; this backup is the only copy'))
        else:
            acts.append(action('kept', f, 'the edited file is still installed; this backup is the way back until a release replaces it'))
    return acts

def backups_apply(deps, actions, backup_dir, opts=None):
    done = []
    for a in actions:
        if a['kind'] == 'move-aside': backup_move(a['path'], backup_dir); done.append(a)
    return {'done': done}


FIXES = {
    'storage-units': dict(
        name='storage-units',
        summary='Rewrite storage gate/sync units written by release 87 to the current text; drop hand drop-ins and the /usr/bin/isle symlink they needed.',
        why='2026-11-14 phase A: release 87 wrote units naming /usr/bin/isle (the CLI is /usr/local/bin/isle) and a gate that ran compose without -f/WorkingDirectory; '
            'one device was papered over by hand with a symlink and a drop-in. The source renders correct text now, but nothing rewrites units that already exist.',
        replacedBy='nothing: kept long term (any device that hosted a dataset under release 87 may still carry the old units)',
        plan=storage_plan, apply=storage_apply, notes=storage_notes),
    'hand-edit-backups': dict(
        name='hand-edit-backups',
        summary='Move aside *.pre-hotfix-* backups of hand-edited CLI files once a release has replaced the edited file.',
        why='2026-11-14 migration: CLI files under /usr/share/isle-mesh were hand-edited, leaving <file>.pre-hotfix-<date> beside them. '
            'Once the package md5 matches the installed file again the backup is clutter.',
        replacedBy='nothing: kept long term (cheap, and hand edits will happen again)',
        plan=backups_plan, apply=backups_apply, notes=lambda deps: []),
}


# ---------------------------------------------------------------- output

def show_actions(deps, actions, diffs=True):
    for a in actions:
        deps.say('  %-14s %s' % (a['kind'], a['path']))
        deps.say('                 ' + a['detail'])
        if diffs and a['kind'] == 'rewrite':
            for line in difflib.unified_diff(a['old'].splitlines(True), a['new'].splitlines(True), a['path'], a['path'] + ' (rendered by this CLI)'):
                deps.say('      ' + line.rstrip('\n'))

def receipts(deps, name):
    try:
        data = json.loads((Path(deps.backup_root) / name / 'applied.json').read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError): return []

def append_receipt(deps, name, record):
    d = Path(deps.backup_root) / name
    d.mkdir(parents=True, exist_ok=True)
    write_atomic(d / 'applied.json', json.dumps(receipts(deps, name) + [record], indent=2) + '\n')

def last_failure(deps, name):
    """The error of the newest receipt when that apply failed, else None. A failed apply may have written its files
    already (the plan is then empty), so it stays 'needed' until an apply ends ok."""
    rows = receipts(deps, name)
    return (rows[-1].get('error') or 'failed') if rows and rows[-1].get('result') == 'failed' else None

def state_of(deps, fix):
    acts = fix['plan'](deps)
    if blocked(acts): return 'blocked: ' + '; '.join(a['detail'] for a in blocked(acts)), acts
    if doing(acts): return 'needed', acts
    failed = last_failure(deps, fix['name'])
    if failed: return 'needed: the last apply failed (%s); apply again to finish it' % failed, acts
    ok = [r for r in receipts(deps, fix['name']) if r.get('result') == 'ok']
    return ('applied ' + ok[-1].get('when', '?') if ok else 'not-needed'), acts

def hand_edits(deps):
    """(differing files, backup-named files) under share_root; read-only, unreadable files are skipped."""
    diff, backups = [], []
    for rel, (pkg, md5) in sorted(dpkg_index(deps).items()):
        path = Path(deps.share_root) / rel[len(SHARE_REL) + 1:]
        if os.path.islink(path) or not os.path.isfile(path): continue
        got = md5_of(path)
        if got is not None and got != md5: diff.append((str(path), pkg))
    for f in walk_files(deps.share_root):
        if BACKUP_SUFFIX.search(os.path.basename(f)): backups.append(f)
    return diff, backups


# ---------------------------------------------------------------- commands

def cmd_list(deps):
    for name, fix in sorted(FIXES.items()):
        deps.say(name); deps.say('  summary:     ' + fix['summary']); deps.say('  why:         ' + fix['why'])
        deps.say('  replaced by: ' + (fix['replacedBy'] or 'nothing: kept long term'))
    return 0

def cmd_status(deps):
    for name, fix in sorted(FIXES.items()):
        state, acts = state_of(deps, fix)
        deps.say('%-18s %s' % (name, state))
        show_actions(deps, acts, diffs=False)
        for note in fix['notes'](deps): deps.say('                 note: ' + note)
    deps.say('')
    deps.say('Installed files that differ from their package (hand edits):')
    diff, backups = hand_edits(deps)
    for path, pkg in diff: deps.say('  differs   %s (package %s)' % (path, pkg))
    for path in backups: deps.say('  backup    %s' % path)
    if not diff and not backups: deps.say('  none')
    return 0

def prepare(deps, name):
    fix = FIXES.get(name)
    if not fix: deps.say('unknown hotfix %r (try: isle hotfix list)' % name); return None, None, 2
    acts = fix['plan'](deps)
    deps.say('Hotfix %s: %s' % (name, fix['summary']))
    if acts: show_actions(deps, acts)
    if blocked(acts): deps.say('Blocked: nothing can be applied until this is resolved.'); return fix, acts, 1
    return fix, acts, 0

def cmd_check(deps, name):
    fix, acts, code = prepare(deps, name)
    if not fix or code: return code
    failed = last_failure(deps, name)
    if not doing(acts) and failed:
        deps.say('The last apply failed (%s). Finish it with: sudo isle hotfix apply %s' % (failed, name)); return 3
    if not doing(acts): deps.say('Nothing to do.'); return 0
    deps.say('%d change(s) needed. Apply with: sudo isle hotfix apply %s' % (len(doing(acts)), name)); return 3

def cmd_apply(deps, name, yes, opts):
    if not deps.is_root(): deps.say('This changes the device. Type: sudo isle hotfix apply ' + name); return 1
    fix, acts, code = prepare(deps, name)
    if not fix or code: return code
    failed = last_failure(deps, name)
    if not doing(acts) and not failed: deps.say('Nothing to do.'); return 0
    if failed:
        deps.say('The last apply failed (%s); this run finishes it.' % failed); opts = dict(opts or {}, retry=True)
    if not yes and not deps.confirm('Proceed?'): deps.say('Cancelled; nothing was changed.'); return 1
    when = deps.now()
    backup_dir = Path(deps.backup_root) / name / when.strftime('%Y%m%dT%H%M%SZ')
    try:
        info = fix['apply'](deps, acts, backup_dir, opts)
        left = [] if info.get('error') else [a for a in doing(fix['plan'](deps))]
        error = info.get('error') or ('after applying, the plan still has changes: ' + '; '.join('%s %s' % (a['kind'], a['path']) for a in left) if left else None)
    except OSError as exc:
        info, error = {'done': []}, 'failed: %s' % exc
    record = dict(when=when.strftime('%Y-%m-%dT%H:%M:%SZ'), actions=[dict(kind=a['kind'], path=a['path'], detail=a['detail']) for a in info.get('done', [])],
                  kept=[dict(path=a['path'], detail=a['detail']) for a in kept(acts)], backupDir=str(backup_dir) if backup_dir.exists() else None,
                  syncProof=info.get('syncProof'), result='failed' if error else 'ok')
    if error: record['error'] = error
    try: append_receipt(deps, name, record)
    except OSError as exc: deps.say('warning: cannot write the receipt: %s' % exc)
    if error: deps.say('FAILED: ' + error); return 1
    deps.say('Applied %d action(s). %s' % (len(info['done']), 'Sync proof: %s. ' % info['syncProof'] if info.get('syncProof') else '') +
             ('Backup: %s' % backup_dir if backup_dir.exists() else ''))
    return 0

HELP = """isle hotfix - named, idempotent repairs of state already written on this device
  isle hotfix list                          every fix: summary, why it exists, what replaces it
  isle hotfix status                        per fix the state on this device; installed files that differ from their package
  isle hotfix check <name>                  read-only: every action it would take, with diffs. Exit 0 nothing to do, 3 changes needed, 1 error/blocked
  sudo isle hotfix apply <name> [--yes] [--no-sync-proof]
                                            show the list, ask, back up to /var/lib/isle-mesh/hotfix/<name>/<stamp>/, apply, verify, write a receipt
                                            (--no-sync-proof: storage-units skips starting the sync service of each enabled timer)
Also usable as a step of `isle release run` (step kind "hotfix", field name)."""

def main(argv, deps=None):
    deps = deps or Deps()
    cmd = argv[0] if argv else 'help'
    if cmd in ('help', '-h', '--help'): deps.say(HELP); return 0
    rest = argv[1:]
    if cmd in ('list', 'status') and not rest: return cmd_list(deps) if cmd == 'list' else cmd_status(deps)
    if cmd == 'check' and len(rest) == 1 and not rest[0].startswith('-'): return cmd_check(deps, rest[0])
    if cmd == 'apply' and rest and not rest[0].startswith('-'):
        flags = rest[1:]
        if all(f in ('--yes', '--no-sync-proof') for f in flags):
            return cmd_apply(deps, rest[0], '--yes' in flags, {'no_sync_proof': '--no-sync-proof' in flags})
    deps.say('usage: isle hotfix list | status | check <name> | apply <name> [--yes] [--no-sync-proof] | help'); return 2

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
