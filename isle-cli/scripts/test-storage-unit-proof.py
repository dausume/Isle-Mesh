#!/usr/bin/env python3
"""Checks for storage_unit_proof.py: the sync a unit starts proves the units and reports once; the core reads the
stored report; `isle storage units prove`.  Fake network, fake systemctl, temp folders (the Scenario of
test-storage-sync.py is loaded, not copied).  Prints PASS/FAIL per check; exits nonzero on any failure."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import storage_cli
import storage_core as core
import storage_mount
import storage_unit_proof as up
import storage_units

source = (HERE / 'test-storage-sync.py').read_text()
SY = types.ModuleType('pol_sync')
SY.__file__ = str(HERE / 'test-storage-sync.py')
exec(compile(source[:source.index('\nfor group in (current_checks')], SY.__file__, 'exec'), SY.__dict__)
ss, DATASET, DEVICE, V1 = SY.ss, SY.DATASET, SY.DEVICE, SY.V1

FAILED, COUNT = [], [0]
GATE, SERVICE = 'isle-storage-%s.service' % DATASET, 'isle-storage-sync@%s.service' % DATASET
TAKEN = 'a different receipt is already stored for this actionId and attempt'
DIGEST = 'd' * 64


def check(name, ok):
    COUNT[0] += 1
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok:
        FAILED.append(name)


class Unit(SY.Scenario):
    """A sync started by its unit: the trigger variable is set and systemd shows the service activating."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.trigger, self.active, self.fail, self.calls = 'unit', 'activating', None, []
        self.answers, self.bodies = [], []
        self.enrollment = os.path.join(self.tmp, 'enrollment.json')
        Path(self.enrollment).write_text(json.dumps({'token': 'T' * 64}))
        self.http.post_json = self.post

    def post(self, url, payload, token=None):
        self.http.posts.append(url)
        self.bodies.append((json.loads(json.dumps(payload)), token))
        return self.answers.pop(0) if self.answers else (201, '')

    def systemctl(self, args):
        self.calls.append(list(args))
        if args[0] == 'show':
            return 0, 'ActiveState=%s\n' % self.active
        return 1 if self.fail and list(args[:len(self.fail)]) == self.fail else 0

    def go(self, argv=None):
        env = {'ISLE_OPERATION_DIR': self.opdir, 'ISLE_STORAGE_STATE_DIR': self.state, 'ISLE_ENROLLMENT_FILE': self.enrollment}
        if self.trigger:
            env[up.TRIGGER] = self.trigger
        deps = ss.Deps(identity=lambda: DEVICE, findmnt_json=self.findmnt, fstatvfs=SY.BIG, lease_path=self.lease,
                       systemctl=self.systemctl)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            self.rc = ss.run(argv or ['storage', 'sync', '--dataset', DATASET], env=env, http=self.http,
                             now=lambda: self.clock, deps=deps)
        self.output = buffer.getvalue()
        self.receipt = json.loads((Path(self.opdir) / 'storage-receipt.json').read_text())
        path = Path(self.state) / DATASET / up.PROOF
        self.proof = json.loads(path.read_text()) if path.exists() else None
        self.status = json.loads((Path(self.state) / DATASET / 'sync-status.json').read_text())
        return self


@contextlib.contextmanager
def unit(**kwargs):
    s = Unit(**kwargs)
    try:
        yield s
    finally:
        s.close()


def device_checks():
    with unit(publish=V1) as s:
        s.go()
        r, body = s.receipt, (s.bodies or [({}, None)])[0][0]
        check('proof: the run ends 0 and says the units are proven', s.rc == 0 and 'Units proven: ' + GATE in s.output)
        check('proof: systemd is asked about the sync service, then the hosting unit is restarted and read active',
              s.calls == [['show', '-p', 'ActiveState', SERVICE], ['restart', GATE], ['is-active', '--quiet', GATE]])
        check('proof: one report to the core with the enrollment token', len(s.bodies) == 1 and s.bodies[0][1] == 'T' * 64
              and s.http.posts == [SY.API + '/api/islemesh/storage/receipt'])
        check('proof: the report is the sync receipt with device and unitProof',
              body.get('kind') == 'isle.storage.sync' and body.get('ok') is True and body.get('device') == DEVICE
              and body['unitProof'] == {'for': 't', 'device': DEVICE, 'gateUnit': GATE, 'syncUnit': SERVICE, 'syncByUnit': True,
                                        'gateStarted': True, 'ok': True, 'error': None})
        check('proof: the report carries what the door needs', body.get('actionId') == 'isle.storage.sync:%s@%s:%s' % (DATASET, DEVICE, DIGEST)
              and body['plan']['digest'] == DIGEST and body.get('epoch') == 5 and body.get('attempt') == 1 and 'T' * 64 not in json.dumps(body))
        check('proof: kept on the device', s.proof['posted'] is True and s.proof['for'] == 't' and s.proof['attempt'] == 1
              and s.proof['at'] == s.clock and r['posted'] is True)
        check('proof: status shows it', storage_cli.status_document(s.state, DATASET, is_enabled=lambda name: True)['unitProof']['gateStarted'] is True)
        s.calls, s.bodies = [], []
        s.go()
        check('proof: the next run of the unit proves nothing again', s.rc == 0 and not s.calls and not s.bodies
              and 'unitProof' not in s.receipt)
        with (Path(s.state) / DATASET / 'receipts.jsonl').open('a') as out:
            out.write(json.dumps(dict(kind='isle.storage.migrate', ok=True, at='t2', source=dict(device=SY.ORIGIN))) + '\n')
        s.answers = [(409, TAKEN), (201, '')]
        s.go()
        check('proof: a later migration is proven again, past the number the core holds',
              s.rc == 0 and [b[0]['attempt'] for b in s.bodies] == [1, 2] and s.proof['for'] == 't2' and s.proof['attempt'] == 2)
    with unit() as s:
        s.go()
        check('proof: a run that also fetched new content proves the same way', s.rc == 0 and s.proof['posted'] is True
              and s.receipt['replicaCurrent'] is False and len(s.bodies) == 1)
    for label, change in (('typed by hand', dict(trigger=None)), ('placement cutover', dict(state='cutover')),
                          ('placement hosting', dict(state='hosting'))):
        with unit(publish=V1, **({'state': change['state']} if 'state' in change else {})) as s:
            s.trigger = change.get('trigger', 'unit')
            s.go()
            check('no proof: %s -> the sync runs, nothing restarted, nothing posted' % label,
                  s.rc == 0 and not s.calls and not s.bodies and s.proof is None and 'unitProof' not in s.receipt)
    with unit(publish=V1) as s:
        s.active = 'inactive'
        s.go()
        check('not by systemd: the variable alone proves nothing, exit 1, hosting unit untouched, no report',
              s.rc == 1 and 'is not being run by systemd now' in s.output and s.calls == [['show', '-p', 'ActiveState', SERVICE]]
              and not s.bodies and s.proof['ok'] is False and s.proof['posted'] is False)
        check('not by systemd: the sync itself is still recorded as done', s.receipt['ok'] is True and s.status['error'] is None)
    for failing in (['restart', GATE], ['is-active']):
        with unit(publish=V1) as s:
            s.fail = failing
            s.go()
            check('hosting unit: systemctl %s fails -> exit 1, words name the unit, no report' % failing[0],
                  s.rc == 1 and 'units of %s not proven: systemctl %s' % (DATASET, failing[0]) in s.output and GATE in s.output
                  and not s.bodies and s.proof['gateStarted'] is False)
            s.fail, s.calls = None, []
            s.go()
            check('hosting unit: the next run tries again and reports (%s)' % failing[0], s.rc == 0 and len(s.bodies) == 1
                  and s.proof['posted'] is True)
    with unit(publish=V1) as s:
        s.answers = [(503, 'busy')]
        s.go()
        check('report not delivered: exit 1 with the words, proof kept as not posted',
              s.rc == 1 and 'the report did not reach the core: HTTP 503' in s.output and s.proof['posted'] is False
              and s.proof['gateStarted'] is True)
        s.go()
        check('report not delivered: the next run reports', s.rc == 0 and s.proof['posted'] is True and len(s.bodies) == 2)
    with unit(publish=V1) as s:
        os.unlink(s.enrollment)
        s.go()
        check('no enrollment file: not posted, exit 1', s.rc == 1 and not s.bodies and s.proof['posted'] is False)
    service, _ = storage_mount.sync_timer_text(DATASET)
    check('unit text: the sync service names itself as the trigger', 'Environment=%s=unit\n' % up.TRIGGER in service)


def row(kind, at, device='pol-core', digest=DIGEST, **extra):
    body = dict(kind=kind, ok=True, at=at, plan=dict(digest=digest), device=device, **extra)
    return {'receipt': body, 'posterPresented': {'subject': device, 'fingerprint': 'f' * 64}}


def proof_row(at, **change):
    block = dict(ok=True, syncByUnit=True, gateStarted=True)
    block.update({k: v for k, v in change.items() if k in block})
    return row('isle.storage.sync', at, unitProof=block, **{k: v for k, v in change.items() if k not in block})


def core_checks():
    entry = {'device': 'pol-core', 'plan': {'digest': DIGEST}}
    early, mid, late = '2026-11-09T12:00:00+00:00', '2026-11-09T12:10:00+00:00', '2026-11-09T12:20:00+00:00'
    yes = lambda r: True
    migrate = row('isle.storage.migrate', mid)
    check('core: nothing stored -> words', up.stored([], entry, yes) == 'no report that the units ran is stored')
    check('core: a migrate receipt alone -> words', up.stored([migrate], entry, yes) == 'no report that the units ran is stored')
    check('core: a proof after the migrate receipt -> held', up.stored([migrate, proof_row(late)], entry, yes) is None)
    check('core: the order of the rows does not matter', up.stored([proof_row(late), migrate], entry, yes) is None)
    check('core: a proof older than the last migrate receipt -> words',
          'older than the last migrate receipt' in up.stored([proof_row(early), migrate], entry, yes))
    check('core: a later migrate receipt voids an earlier proof',
          'older' in up.stored([migrate, proof_row(mid), row('isle.storage.migrate', late)], entry, yes))
    check('core: an ordinary sync receipt is no proof', up.stored([migrate, row('isle.storage.sync', late)], entry, yes) is not None)
    for label, change in (('hosting unit not started', dict(gateStarted=False)), ('not run by the unit', dict(syncByUnit=False)),
                          ('proof not ok', dict(ok=False)), ('another device', dict(device='econ-core')),
                          ('another plan digest', dict(digest='e' * 64))):
        check('core: %s -> not held' % label, up.stored([migrate, proof_row(late, **change)], entry, yes) is not None)
    check('core: a proof whose poster does not verify -> words name the poster',
          'could not be verified' in up.stored([migrate, proof_row(late)], entry, lambda r: False))
    check('core: a time without a zone is not used', up.stored([migrate, proof_row('2026-11-09T12:20:00')], entry, yes) is not None)
    with tempfile.TemporaryDirectory() as tmp:
        real = core.RealIo.__new__(core.RealIo)
        real.state_dir = Path(tmp)
        real.verify_poster = yes
        (Path(tmp) / 'ds').mkdir()
        (Path(tmp) / 'ds' / 'inbound-receipts.jsonl').write_text(json.dumps(migrate) + '\n' + json.dumps(proof_row(late)) + '\n')
        check('core: IO.unit_proof reads the relayed rows', real.unit_proof('ds', entry) is None
              and real.unit_proof('other', entry) == 'no report that the units ran is stored')
        full = dict(migrate, receipt=dict(migrate['receipt'], contentVerified=True, stagedDeploy=True, readinessVerified=True,
                                          plan=dict(digest=DIGEST, id='p', revision=1), target=dict(device='pol-core')))
        (Path(tmp) / 'ds' / 'inbound-receipts.jsonl').write_text(json.dumps(full) + '\n' + json.dumps(proof_row(late)) + '\n')
        placed = {'device': 'pol-core', 'plan': {'digest': DIGEST, 'id': 'p', 'revision': 1}}
        check('core: mark-migrated still takes the migrate receipt when a unit proof was stored after it',
              core.qualifying_receipt(real, placed, 'ds', DIGEST)['receipt']['kind'] == 'isle.storage.migrate')


def verb_checks():
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / DATASET
        said, started = [], []

        def start(args, code=0, write=None):
            started.append(list(args))
            if write is not None:
                (folder / up.PROOF).write_text(json.dumps(write))
            return code

        run = lambda **kw: up.run_now(DATASET, root=tmp, start=lambda a: start(a, **kw), out=said.append)
        check('prove: a device that does not host the dataset is told so, nothing started',
              run() == 1 and 'does not host it' in said[-1] and not started)
        folder.mkdir()
        (folder / 'binding.json').write_text(json.dumps({'dataset': DATASET}))
        (folder / 'receipts.jsonl').write_text(json.dumps(dict(kind='isle.storage.migrate', at='t')) + '\n')
        good = {'for': 't', 'ok': True, 'posted': True, 'gateUnit': GATE, 'attempt': 1, 'at': 'now'}
        check('prove: starts the sync unit and reports the proof', run(write=good) == 0 and started == [['start', SERVICE]]
              and 'Units of %s proven' % DATASET in said[-1] and GATE in said[-1])
        check('prove: a failed run -> exit 1 with the recorded reason',
              run(code=1, write={'for': 't', 'ok': False, 'posted': False, 'error': 'systemctl restart failed'}) == 1
              and 'systemctl restart failed' in said[-1] and 'journalctl -u ' + SERVICE in said[-1])
        check('prove: a proof for an earlier migration does not count',
              run(write=dict(good, **{'for': 'earlier'})) == 1 and 'no proof was made' in said[-1])
        (folder / up.PROOF).unlink()
        check('prove: the unit ran and made no proof -> exit 1, says when a proof is made',
              run() == 1 and 'only while the placement is `migrated`' in said[-1])
        check('prove: still running at the limit', run(code=124) == 1 and 'still running' in said[-1])
        out = []
        check('units prove: needs root', storage_units.main(['prove', '--dataset', DATASET], root=tmp, euid=lambda: 1000, out=out.append) == 1
              and 'sudo isle storage units prove --dataset ' + DATASET in out[-1])


for group in (device_checks, core_checks, verb_checks):
    group()
print('%d failed: %s' % (len(FAILED), FAILED) if FAILED else 'all %d checks passed' % COUNT[0])
sys.exit(1 if FAILED else 0)
