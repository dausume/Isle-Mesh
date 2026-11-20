#!/usr/bin/env python3
"""Proof that a dataset's systemd units run on the hosting device (contract section 2.5, "Unit proof").

Enabling a unit shows nothing about whether it runs: a wrong path in its text is found only when systemd
starts it.  Migrate cannot start the hosting unit itself, because the unit's own `isle storage check` refuses
the placement state `authorized`.  So the first sync that the sync unit starts after `mark-migrated`
(placement `migrated`, the name still at the source) does two things more:

  1. restarts the hosting unit isle-storage-<dataset>.service through systemd, which is the path a boot takes
     (its own check, its own compose command), and reads that it is active;
  2. posts its sync receipt to the core with `device` and `unitProof`, once per migration.

Cutover on the core refuses until that receipt is stored (stored() below, called by storage_core).
`isle storage units prove --dataset D` starts the sync unit now instead of waiting for its timer.
"""
import contextlib
import json
from datetime import datetime
from pathlib import Path
import subprocess

import operations
import storage_migrate as sm
import storage_mount

TRIGGER = 'ISLE_STORAGE_TRIGGER'    # 'unit' in the sync service's own text (storage_mount.sync_timer_text)
PROOF = 'unit-proof.json'
START_SECONDS = 1800


def gate_unit(dataset):
    return 'isle-storage-%s.service' % dataset


def _read(path):
    with contextlib.suppress(OSError, ValueError):
        found = json.loads(Path(path).read_bytes())
        return found if isinstance(found, dict) else None
    return None


def migration_mark(folder):
    """What names the migration being proven: the time of the last migrate receipt this device recorded."""
    mark = None
    with contextlib.suppress(OSError):
        for line in (Path(folder) / 'receipts.jsonl').read_bytes().splitlines():
            with contextlib.suppress(ValueError, AttributeError):
                row = json.loads(line)
                if row.get('kind') == 'isle.storage.migrate' and isinstance(row.get('at'), str):
                    mark = row['at']
    return mark


def prove(sync):
    """The proof block of a sync the unit started, or None when this run has nothing to prove: not started by
    the unit, placement not `migrated`, or this migration already reported."""
    if sync.env.get(TRIGGER) != 'unit' or (sync.view or {}).get('state') != 'migrated':
        return None
    mark = migration_mark(sync.folder)
    held = _read(sync.folder / PROOF) or {}
    if held.get('for') == mark and held.get('posted') is True:
        return None
    service, _ = storage_mount.sync_unit_names(sync.dataset)
    gate = gate_unit(sync.dataset)
    block = {'for': mark, 'device': sync.device, 'gateUnit': gate, 'syncUnit': service,
             'syncByUnit': False, 'gateStarted': False, 'ok': False, 'error': None}
    try:
        _, shown = sync.deps.systemctl(['show', '-p', 'ActiveState', service])
    except (TypeError, ValueError):
        shown = ''
    if 'ActiveState=activating' not in str(shown).split():
        block['error'] = '%s is not being run by systemd now (%s)' % (service, str(shown).strip() or 'state not shown')
        return block
    block['syncByUnit'] = True
    for args in (['restart', gate], ['is-active', '--quiet', gate]):
        code = sync.deps.systemctl(args)
        if code != 0:
            block['error'] = 'systemctl %s failed (exit %s); see: journalctl -u %s' % (' '.join(args), code, gate)
            return block
    block.update(gateStarted=True, ok=True)
    return block


def after_sync(sync):
    """Called once a sync has finished without error.  Returns None, or the words of a proof that failed."""
    block = prove(sync)
    if block is None:
        return None
    receipt = sync.receipt
    receipt.update(device=sync.device, unitProof=block)
    if block['ok']:
        enrollment = sync.env.get('ISLE_ENROLLMENT_FILE') or sm.ENROLLMENT_FILE
        sm.post_receipt(sync.http, enrollment, receipt)
    kept = dict(block, at=receipt.get('at'), posted=receipt.get('posted') is True, attempt=receipt.get('attempt'),
                postError=receipt.get('postError'))
    with contextlib.suppress(OSError):
        operations.atomic(sync.folder / PROOF, kept)
        if sync.op_dir:
            operations.atomic(sync.op_dir / 'storage-receipt.json', receipt)
    if not block['ok']:
        return 'units of %s not proven: %s' % (sync.dataset, block['error'])
    if not kept['posted']:
        return 'units of %s ran, and the report did not reach the core: %s' % (sync.dataset, kept['postError'])
    receipt['words'] += ' Units proven: %s started by systemd; reported to the core.' % block['gateUnit']
    return None


def _when(text):
    with contextlib.suppress(ValueError, TypeError):
        return datetime.fromisoformat(text)
    return None


def stored(rows, entry, verify):
    """Core side.  None when the stored receipts hold a unit proof from the hosting device that is not older
    than its last migrate receipt; otherwise the words saying what is missing.  `rows` are the relayed rows,
    `verify(row)` checks the poster against the CA members."""
    digest, device = (entry.get('plan') or {}).get('digest'), entry.get('device')
    migrated, proofs, unverified = None, [], False
    for row in rows:
        receipt = row.get('receipt') if isinstance(row.get('receipt'), dict) else row
        if (receipt.get('plan') or {}).get('digest') != digest:
            continue
        at = _when(receipt.get('at'))
        if at is None or at.tzinfo is None:
            continue
        if receipt.get('kind') == 'isle.storage.migrate':
            migrated = at if migrated is None else max(migrated, at)
        elif receipt.get('kind') == 'isle.storage.sync':
            proof = receipt.get('unitProof') if isinstance(receipt.get('unitProof'), dict) else {}
            if (receipt.get('ok') is True and receipt.get('device') == device and proof.get('ok') is True
                    and proof.get('syncByUnit') is True and proof.get('gateStarted') is True):
                if verify(row):
                    proofs.append(at)
                else:
                    unverified = True
    if any(migrated is None or at >= migrated for at in proofs):
        return None
    if proofs:
        return 'the stored report that the units ran is older than the last migrate receipt'
    if unverified:
        return 'the poster of the report that the units ran could not be verified against the CA members'
    return 'no report that the units ran is stored'


def _start(args):
    try:
        return subprocess.run(['systemctl', *args], timeout=START_SECONDS).returncode
    except subprocess.TimeoutExpired:
        return 124
    except (OSError, subprocess.SubprocessError):
        return 127


def run_now(dataset, *, root, start=_start, out=print):
    """`isle storage units prove --dataset D`: start the sync unit now, then say what the proof record holds.
    Exit 0 only when the units are proven for the last migration recorded here."""
    service, _ = storage_mount.sync_unit_names(dataset)
    folder = Path(root) / dataset
    if _read(folder / 'binding.json') is None:
        out('No binding copy for %s under %s: this device does not host it.' % (dataset, folder))
        return 1
    code = start(['start', service])
    held = _read(folder / PROOF) or {}
    proven = held.get('posted') is True and held.get('ok') is True and held.get('for') == migration_mark(folder)
    if proven:
        out('Units of %s proven: %s ran, %s started by systemd, the core holds the report (attempt %s, %s).'
            % (dataset, service, held.get('gateUnit'), held.get('attempt'), held.get('at')))
        return 0
    if code != 0:
        status = _read(folder / 'sync-status.json') or {}
        why = held.get('error') or held.get('postError') or status.get('error') or 'no reason recorded'
        if code == 124:
            why = 'still running after %d minutes' % (START_SECONDS // 60)
        out('Units of %s not proven: %s ended with exit %s: %s. See: journalctl -u %s' % (dataset, service, code, why, service))
        return 1
    out('%s ran, and no proof was made: a proof is made only while the placement is `migrated` '
        '(after mark-migrated, before cutover). See: isle storage status --dataset %s' % (service, dataset))
    return 1
