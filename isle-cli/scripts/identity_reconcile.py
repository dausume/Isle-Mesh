#!/usr/bin/env python3
"""Guarded canonical-identity reconciliation for an EXISTING member (handoff 2026-11-09, item A).

CLI 205's membership preflight refuses to act when the two saved names disagree:
  /etc/isle-mesh/device-name              the canonical Isle identity (agent mounts it, core reports it)
  /etc/isle-mesh/agent/remote/hostname    the registration name saved at the original join
Its repair only fills a MISSING device-name; it never resolves a conflict. Live case: pol-core kept
its October join name in the second file and the release-85 update stopped at the preflight.

This operation resolves exactly that conflict, and nothing else:
  - the caller states the EXPECTED old value and the canonical identity explicitly; a different
    saved value, a different device-name, a missing device-name or a hosting credential that names
    another device all refuse (fail closed, nothing written);
  - the old file is copied beside the original (mtime preserved) and a receipt is journaled before
    anything is overwritten; the rewrite is atomic; the preflight re-reads both files afterwards;
  - MAC, DHCP lease, discovery, interface, NetworkManager profile, certificates, enrollment credential
    and the OS hostname are never touched (the OS hostname is NOT an Isle identity);
  - it never joins, rejoins, renames a device at the core or guesses from the OS hostname.
Read-only: `isle identity status`, `isle identity plan ...`. Mutation runs only through the shared
operation journal: `sudo isle identity reconcile --expected-legacy <old> --canonical <device-name>`,
then retry the operation that was refused: `sudo isle operation retry <id>`.
"""
import hashlib, json, os, re, shutil, subprocess, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

STATE = Path('/etc/isle-mesh')
LEGACY = 'agent/remote/hostname'
RECEIPTS = 'agent/remote/identity-reconcile-receipts.jsonl'
PRESERVED = ('agent/agent.mode', 'agent/remote/mac-address', 'agent/remote/interface.conf',
             'agent/remote/discovery.json', 'agent/remote/nm-profile.uuid', 'ca/isle-root.crt',
             'ca/member-enrollment.json')
LABEL = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z')
CORE_DEVICES = 'https://api.polari.isle/api/islemesh/devices'


def now():
    return datetime.now(timezone.utc).isoformat()


def _read(path):
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return None


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _label(value, what):
    if not isinstance(value, str) or not LABEL.fullmatch(value):
        raise ValueError('%s must be a lowercase DNS label (got %r)' % (what, value))
    return value


def status(state=STATE):
    """Read-only: both saved names and whether the preflight would accept them."""
    device_name = _read(state / 'device-name')
    legacy = _read(state / LEGACY)
    return {'deviceName': device_name, 'registrationName': legacy,
            'agree': device_name is not None and legacy is not None and device_name == legacy,
            'mode': _read(state / 'agent/agent.mode'),
            'words': ('consistent' if device_name is not None and device_name == legacy else
                      'canonical device-name missing: restore it from this registration; never guessed'
                      if device_name is None else
                      'no saved registration name' if legacy is None else
                      'saved registration %r disagrees with canonical %r' % (legacy, device_name))}


def core_knows(name, fetch=None):
    """Advisory only: does the core's devices door list this name? Never blocks the decision."""
    try:
        raw = (fetch or _fetch_core)()
        rows = json.loads(raw).get('devices', [])
        return 'known' if any(r.get('name') == name for r in rows) else 'absent'
    except Exception as exc:  # noqa: BLE001 - advisory words, never a crash
        return 'unreachable: %s' % type(exc).__name__


def _fetch_core():
    return subprocess.run(['curl', '--http1.1', '--fail', '--silent', '--show-error', '--max-time', '10',
                           CORE_DEVICES], capture_output=True, text=True, timeout=15, check=True).stdout


def other_references(state, legacy):
    """Advisory: other files under the state root that still carry the legacy name (any case)."""
    found = []
    needle = legacy.lower()
    for path in sorted(state.rglob('*')):
        rel = path.relative_to(state).as_posix()
        if not path.is_file() or rel == LEGACY or rel == RECEIPTS or '.pre-reconcile-' in rel \
                or rel.startswith('operations/') or _secret_like(path, rel):
            continue
        try:
            if needle in path.read_text(errors='replace').lower():
                found.append(rel)
        except OSError:
            continue
    return found


def _secret_like(path, rel):
    """Never read key material or owner-only files for an advisory name scan (A2 review, pol-core)."""
    lowered = rel.lower()
    if any(part in lowered for part in ('/keys', 'private', 'secret', 'token')) \
            or lowered.endswith(('.key', '.pem', '.p12', '.pfx', '.jks')):
        return True
    try:
        return (path.stat().st_mode & 0o077) == 0
    except OSError:
        return True


def plan(expected_legacy, canonical, state=STATE, core=True, fetch=None):
    """Read-only decision. Raises ValueError when a guard refuses; returns the plan otherwise."""
    canonical = _label((canonical or '').strip(), 'canonical identity')
    expected = (expected_legacy or '').strip().lower()
    _label(expected, 'expected legacy name')
    if expected == canonical:
        raise ValueError('expected legacy name equals the canonical identity; nothing to reconcile')
    mode = _read(state / 'agent/agent.mode')
    if mode not in ('core', 'remote'):
        raise ValueError('No configured core/member mode; identity reconciliation applies to an existing member only')
    device_name = _read(state / 'device-name')
    if device_name is None:
        raise ValueError('Canonical device identity missing: restore /etc/isle-mesh/device-name from this member registration; this operation never creates an identity')
    if device_name != canonical:
        raise ValueError('Canonical identity mismatch: /etc/isle-mesh/device-name says %r, not %r; refusing' % (device_name, canonical))
    legacy_path = state / LEGACY
    legacy = _read(legacy_path)
    if legacy is None:
        raise ValueError('No saved registration name at %s; nothing to reconcile' % legacy_path)
    enrollment = 'no hosting credential'
    credential = state / 'ca/member-enrollment.json'
    if credential.is_file():
        if os.geteuid() != 0:
            enrollment = 'administrator validation required at apply'
        else:
            try:
                device = json.loads(credential.read_text()).get('device')
            except (OSError, ValueError) as exc:
                raise ValueError('Hosting credential unreadable: %s' % exc)
            if device != canonical:
                raise ValueError('Hosting credential names device %r, not %r; the credential is never rewritten here (pair again with sudo isle certs pair after correcting registration)' % (device, canonical))
            enrollment = 'agrees'
    preserved = [p for p in PRESERVED if (state / p).exists()]
    result = {'ok': True, 'path': str(legacy_path), 'legacy': legacy, 'canonical': canonical,
              'enrollment': enrollment, 'preserved': preserved,
              'coreKnowsCanonical': core_knows(canonical, fetch) if core else 'not checked',
              'otherReferences': other_references(state, legacy) if legacy.lower() != canonical else []}
    if legacy == canonical:
        result.update(action='none', words='saved registration already equals the canonical identity')
        return result
    if legacy.lower() != expected:
        raise ValueError('Saved registration name is %r, not the expected %r; refusing to overwrite an unexpected value' % (legacy, expected))
    result.update(action='rewrite', words='rewrite %s from %r to %r after backing it up' % (LEGACY, legacy, canonical))
    return result


def apply(expected_legacy, canonical, state=STATE, operation_dir=None, core=True, fetch=None):
    if os.geteuid() != 0:
        raise PermissionError('Identity reconciliation requires administrator authorization: sudo isle identity reconcile ...')
    decision = plan(expected_legacy, canonical, state, core, fetch)
    receipt = {'kind': 'identity-reconcile', 'at': now(), 'device': decision['canonical'],
               'operation': os.environ.get('ISLE_OPERATION_ID', ''), 'enrollment': decision['enrollment'],
               'coreKnowsCanonical': decision['coreKnowsCanonical'], 'preserved': decision['preserved'],
               'otherReferences': decision['otherReferences']}
    legacy_path = Path(decision['path'])
    if decision['action'] == 'none':
        receipt.update(action='none', file=str(legacy_path), words=decision['words'])
        _journal(state, operation_dir, receipt)
        return receipt
    backup = legacy_path.with_name('hostname.pre-reconcile-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    if backup.exists():
        raise FileExistsError('Backup already exists: %s' % backup)
    before = {p: (_sha(state / p), (state / p).stat().st_mtime_ns) for p in decision['preserved']}
    shutil.copy2(legacy_path, backup)          # bytes + mtime of the legacy registration kept beside it
    receipt.update(action='rewrite', file=str(legacy_path), legacy=decision['legacy'], backup=str(backup),
                   backupSha256=_sha(backup))
    _journal(state, operation_dir, dict(receipt, stage='backup-written'))
    fd, tmp = tempfile.mkstemp(dir=legacy_path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            out.write(decision['canonical'] + '\n'); out.flush(); os.fsync(out.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, legacy_path)
        directory = os.open(legacy_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    from member_preflight import identity
    try:
        agreed = identity(state)
        if agreed != decision['canonical']:
            raise RuntimeError('Preflight re-read %r after the rewrite' % (agreed,))
        after = {p: (_sha(state / p), (state / p).stat().st_mtime_ns) for p in decision['preserved']}
        if after != before:
            raise RuntimeError('A preserved file changed during reconciliation: %s'
                               % ', '.join(sorted(p for p in before if before[p] != after.get(p))))
    except (ValueError, RuntimeError, OSError) as exc:
        # Fail loudly AND leave the member as it was: the legacy bytes come back from the backup
        # (kept), and the failure is journaled with the backup path (A1 review, pol-core).
        restored = 'not restored'
        try:
            shutil.copy2(backup, legacy_path)
            restored = 'restored from backup' if _read(legacy_path) == decision['legacy'] else 'restore mismatch'
        except OSError as restore_exc:
            restored = 'restore failed: %s' % restore_exc
        receipt.update(stage='failed', error='%s: %s' % (type(exc).__name__, exc), legacyFile=restored,
                       words='reconciliation failed after the rewrite; legacy value %s; backup kept at %s' % (restored, backup))
        _journal(state, operation_dir, receipt)
        raise RuntimeError('%s; legacy value %s; backup kept at %s' % (exc, restored, backup)) from exc
    receipt.update(newSha256=_sha(legacy_path), stage='complete', preflight=agreed,
                   words='registration name now %r; legacy kept at %s' % (agreed, backup))
    _journal(state, operation_dir, receipt)
    return receipt


def _journal(state, operation_dir, receipt):
    log = state / RECEIPTS
    with log.open('a') as out:
        out.write(json.dumps(receipt, sort_keys=True) + '\n'); out.flush(); os.fsync(out.fileno())
    try: log.chmod(0o644)
    except OSError: pass
    if operation_dir:
        target = Path(operation_dir) / 'identity-receipt.json'
        target.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')


def _options(args):
    if len(args) != 4 or args[0] != '--expected-legacy' or args[2] != '--canonical':
        raise ValueError('Usage: --expected-legacy <saved-registration-name> --canonical <device-name>')
    return args[1], args[3]


def main(argv):
    if not argv:
        raise ValueError('Usage: isle identity status | isle identity plan --expected-legacy <old> --canonical <name> | sudo isle identity reconcile --expected-legacy <old> --canonical <name>')
    action, rest = argv[0], argv[1:]
    if action == 'status':
        if rest: raise ValueError('status takes no options')
        print(json.dumps(status())); return 0
    if action == 'plan':
        expected, canonical = _options(rest)
        print(json.dumps(plan(expected, canonical))); return 0
    if action == 'reconcile':
        expected, canonical = _options(rest)
        ident = os.environ.get('ISLE_OPERATION_ID', '')
        if os.geteuid() != 0 or not re.fullmatch('[a-f0-9]{32}', ident):
            raise PermissionError('Use the shared operation: sudo isle identity reconcile --expected-legacy <old> --canonical <name>')
        print(json.dumps(apply(expected, canonical, operation_dir=os.environ.get('ISLE_OPERATION_DIR') or None)))
        return 0
    raise ValueError('Unknown identity action: ' + action)


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except (ValueError, OSError, RuntimeError, PermissionError, subprocess.SubprocessError) as exc:
        print('identity: ' + str(exc), file=sys.stderr); sys.exit(1)
