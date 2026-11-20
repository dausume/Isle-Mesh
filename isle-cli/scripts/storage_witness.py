#!/usr/bin/env python3
"""isle storage witness --dataset D --generation G (contract section 6; slice stm-8).

A root journaled operation on a THIRD device: through normal DNS it fetches the control files and the
manifest of the cutover dataset, samples three small entries plus one bounded entry, records the address
it connected to and the dig route, and POSTs the receipt to /api/islemesh/storage/witness.  It holds the
dataset lock (operations.py) and no device-update lease.  Sampling proves routing and content, nothing more.
Every outside effect is injected: http (get_json / get_bytes_addr / post_json), identity, dig and now.
"""
import contextlib
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import quote

import operations
import storage_contract as sc
import storage_receive as sr
import storage_migrate as sm
from storage_migrate import HttpError, API, MiB, MANIFEST_MAX, ENROLLMENT_FILE, STATE_DIR, default_now

SMALL_MAX = MiB
BOUNDED_MAX = 64 * MiB
STAGES = ('checking-placement', 'fetching-control', 'sampling-content', 'recording-witness')


class Refusal(Exception):
    def __init__(self, message, stage):
        super().__init__(message)
        self.stage = stage


class WitnessCurl(sm.CurlHttp):
    """CurlHttp that also reports the address curl connected to (--write-out to stderr, never mixed into the body)."""

    def _argv(self, url, resolve, max_time):
        argv = super()._argv(url, resolve, max_time)
        return argv[:-1] + ['--write-out', '%{stderr}%{remote_ip}', argv[-1]]

    def get_bytes_addr(self, url, max_bytes=None):
        process = self.popen(self._argv(url, None, 120), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        chunks, total = [], 0
        try:
            while True:
                block = process.stdout.read(MiB)
                if not block:
                    break
                total += len(block)
                if max_bytes is not None and total > max_bytes:
                    raise HttpError('Response is larger than the allowed %d bytes.' % max_bytes)
                chunks.append(block)
        except BaseException:
            process.kill()
            process.wait()
            raise
        stderr = process.stderr.read()
        code = process.wait()
        if code != 0:
            raise self._failure(code, stderr)
        return b''.join(chunks), stderr.decode('utf-8', 'replace').strip()


def default_dig(domain):
    result = subprocess.run(['dig', '+short', domain], capture_output=True, text=True, timeout=30)
    found = []
    for line in result.stdout.split():
        with contextlib.suppress(ValueError):
            ipaddress.ip_address(line)
            found.append(line)
    return found


class Deps:
    def __init__(self, **overrides):
        self.identity = sm.default_identity
        self.dig = default_dig
        for key, value in overrides.items():
            if not hasattr(self, key):
                raise TypeError('Unknown dependency: ' + key)
            setattr(self, key, value)


def bounded_pick(candidates, generation, device):
    """Deterministic index into the sorted candidates from (G, device)."""
    return int(hashlib.sha256(('%d:%s' % (generation, device)).encode()).hexdigest(), 16) % len(candidates)


def choose_samples(document, generation, device):
    files = sorted((e for e in document.get('entries', []) if isinstance(e, dict) and e.get('kind') == 'file'),
                   key=lambda e: e['path'])
    for e in files:
        sr.validate_manifest_path(e['path'])
        if type(e.get('size')) is not int or e['size'] < 0:
            raise ValueError('Manifest entry %r has no valid size.' % e['path'])
    small = [e for e in files if e['size'] <= SMALL_MAX]
    picks = []
    for index in ([0, len(small) // 2, len(small) - 1] if small else []):
        if small[index] not in picks:
            picks.append(small[index])
    candidates = [e for e in files if e['size'] <= BOUNDED_MAX]
    if not candidates:
        raise ValueError('No manifest entry of at most 64 MiB to sample (no eligible bounded entry).')
    start = bounded_pick(candidates, generation, device)
    for step in range(len(candidates)):  # a collision with a small pick advances to the next unpicked entry
        chosen = candidates[(start + step) % len(candidates)]
        if chosen not in picks:
            picks.append(chosen)
            break
    return picks


class Witness:
    def __init__(self, req, env, http, now, deps):
        self.req, self.env, self.http, self.now, self.deps = req, env, http, now, deps
        self.op_id = env.get('ISLE_OPERATION_ID') or 'direct-%d' % os.getpid()
        self.op_dir = Path(env['ISLE_OPERATION_DIR']) if env.get('ISLE_OPERATION_DIR') else None
        self.state_dir = Path(env.get('ISLE_STORAGE_STATE_DIR') or STATE_DIR)
        self.enrollment = Path(env.get('ISLE_ENROLLMENT_FILE') or ENROLLMENT_FILE)
        self.stages = []
        self.current = STAGES[0]
        self.receipt = dict(kind='isle.storage.witness', schemaVersion=1, operation=self.op_id,
                            dataset=req['dataset'], device=None, generation=req['generation'], epoch=req['generation'],
                            contentSha=None, manifestSha=None, promotedAtEpoch=None, actionId=None, resolvedAddress=None,
                            route=[], at=None, sampled=[], ok=False, posted=False, words='')

    def begin(self, label):
        self.current = label
        self.stages.append(label)
        if self.env.get('ISLE_OPERATION_ID'):
            operations.stage(self.env['ISLE_OPERATION_ID'], label)

    def fetch(self, url, max_bytes, addresses):
        try:
            raw, address = self.http.get_bytes_addr(url, max_bytes=max_bytes)
        except HttpError as error:
            raise Refusal('Cannot fetch %s: %s' % (url, error), self.current)
        if address:
            addresses.add(address)
        return raw

    def check_placement(self):
        r, req = self.receipt, self.req
        self.begin('checking-placement')
        try:
            device = self.deps.identity()
        except (ValueError, OSError) as error:
            raise Refusal('This device\'s identity is not usable: %s' % error, self.current)
        r['device'] = device
        try:
            view = self.http.get_json('%s/api/islemesh/storage/placement?dataset=%s' % (API, quote(req['dataset'])))
        except HttpError as error:
            raise Refusal('No placement view for dataset %s: failing closed (%s).' % (req['dataset'], error), self.current)
        inner = view.get('view') if isinstance(view, dict) else None
        view = inner if isinstance(inner, dict) else view
        if not isinstance(view, dict) or not view.get('state'):
            raise Refusal('No placement view for dataset %s: failing closed.' % req['dataset'], self.current)
        source = view.get('sourceDevice') or view.get('source_device')
        if device in (view.get('device'), source):
            raise Refusal('A witness must be a third device: %s is the %s of %s.' % (
                device, 'target' if device == view.get('device') else 'source', req['dataset']), self.current)
        if view.get('state') != 'cutover':
            raise Refusal('Placement of %s is %s, not cutover.' % (req['dataset'], view.get('state')), self.current)
        if view.get('generation') != req['generation']:
            raise Refusal('generation moved, re-read: current is %s.' % view.get('generation'), self.current)
        domain = view.get('domain')
        try:
            sc._domain(domain)
            sm.check_url('https://%s/' % domain)
        except ValueError as error:
            raise Refusal('The placement view has no usable domain: %s' % error, self.current)
        plan = view.get('plan')
        digest = plan.get('digest') if isinstance(plan, dict) else None
        if not isinstance(digest, str):
            digest = view.get('planDigest')
        if not isinstance(digest, str):
            raise Refusal('The placement view has no plan digest to bind the cutover action to.', self.current)
        r['actionId'] = sc.action_id('cutover', req['dataset'], view['device'], digest)
        return view, domain

    def fetch_control(self, view, domain):
        r, req = self.receipt, self.req
        self.begin('fetching-control')
        base = 'https://%s/.well-known/isle-storage/' % domain
        addresses = set()
        try:
            latest = json.loads(self.fetch(base + 'latest.json', 16 * MiB, addresses))
        except ValueError:
            raise Refusal('latest.json is not JSON.', self.current)
        current = self.fetch(base + 'current', 4096, addresses).decode('utf-8', 'replace').strip()
        if not isinstance(latest, dict):
            raise Refusal('latest.json is not an object.', self.current)
        if current != latest.get('contentSha'):
            raise Refusal('current (%s) is not latest.contentSha (%s): committed and advertised disagree.'
                          % (current[:12], str(latest.get('contentSha'))[:12]), self.current)
        # latest.epoch is informational (the generation seen at the last promote); G is bound from the placement view.
        r['promotedAtEpoch'] = latest.get('epoch') if type(latest.get('epoch')) is int else None
        msha, csha = latest.get('manifestSha'), latest.get('contentSha')
        if not (isinstance(msha, str) and sc.HEX64.match(msha) and isinstance(csha, str) and sc.HEX64.match(csha)):
            raise Refusal('latest.json has no valid manifestSha/contentSha.', self.current)
        raw = self.fetch(base + 'manifest-%s.json' % msha, MANIFEST_MAX, addresses)
        if sr.manifest_sha(raw) != msha:
            raise Refusal('The manifest bytes do not match latest.manifestSha.', self.current)
        try:
            document = json.loads(raw)
            if sr.content_sha(document) != csha:
                raise ValueError
        except ValueError:
            raise Refusal('The manifest content does not match latest.contentSha.', self.current)
        r.update(contentSha=csha, manifestSha=msha)
        self.addresses = addresses
        return document

    def sample(self, view, domain, document):
        r = self.receipt
        self.begin('sampling-content')
        if len(self.addresses) > 1:
            raise Refusal('The name %s was served from several addresses: %s.' % (domain, ', '.join(sorted(self.addresses))), self.current)
        address = next(iter(self.addresses), None)
        r['resolvedAddress'] = address
        try:
            r['route'] = list(self.deps.dig(domain))
        except (OSError, subprocess.SubprocessError) as error:
            r['route'] = []
            r['routeError'] = str(error)
        expected = (view.get('addresses') or {}).get('target') if isinstance(view.get('addresses'), dict) else None
        if not expected:
            raise Refusal('placement view carries no target address; witness cannot bind the route', self.current)
        if address != expected:
            raise Refusal('%s resolved to %s, not the target address %s.' % (domain, address, expected), self.current)
        try:
            picks = choose_samples(document, self.req['generation'], r['device'])
        except (ValueError, KeyError) as error:
            raise Refusal(str(error), self.current)
        for entry in picks:
            url = sm.object_url(domain, entry['path'])
            raw = self.fetch(url, entry['size'], self.addresses)
            digest = hashlib.sha256(raw).hexdigest()
            if len(raw) != entry['size'] or digest != entry.get('sha256'):
                raise Refusal('Sampled file %s does not match the manifest (size %d, sha256 %s…).'
                              % (entry['path'], len(raw), digest[:12]), self.current)
            r['sampled'].append(dict(path=entry['path'], size=entry['size'], sha256=digest))
        if len(self.addresses) > 1:
            raise Refusal('Sampled files came from several addresses: %s.' % ', '.join(sorted(self.addresses)), self.current)

    def finish(self, error):
        r = self.receipt
        self.begin('recording-witness')
        r.update(ok=error is None, at=self.now())
        if error is None:
            r['words'] = ('Witness %s saw %s served from %s at generation %d: control files and %d sampled files '
                          'match the manifest.' % (r['device'], r['dataset'], r['resolvedAddress'],
                                                   r['generation'], len(r['sampled'])))
        else:
            r.update(failedStage=error.stage if isinstance(error, Refusal) else self.current)
            r['words'] = 'Witness of %s refused at %s: %s' % (r['dataset'], r['failedStage'], error)
        self._write()
        if r['device'] is not None and error is not None and r['failedStage'] == 'checking-placement':
            return  # nothing was observed on the served name: nothing recorded outside the journal
        self._record()
        if error is None:
            self._post()
            self._write()

    def _write(self):
        if self.op_dir:
            operations.atomic(self.op_dir / 'storage-receipt.json', self.receipt)

    def _record(self):
        try:
            folder = self.state_dir / self.req['dataset']
            folder.mkdir(parents=True, exist_ok=True)
            with (folder / 'witnesses.jsonl').open('ab') as out:
                out.write(json.dumps(self.receipt, sort_keys=True).encode() + b'\n')
                out.flush()
                os.fsync(out.fileno())
        except OSError as error:
            self.receipt['recordError'] = str(error)

    def _post(self):
        r, token = self.receipt, None
        try:
            token = json.loads(self.enrollment.read_text()).get('token')
            if not isinstance(token, str) or not token:
                raise ValueError('the enrollment file has no token')
            status, text = self.http.post_json(API + '/api/islemesh/storage/witness', r, token)
            r['posted'] = 200 <= status < 300
            if not r['posted']:
                r['postError'] = 'HTTP %d %s' % (status, str(text)[:200])
        except (OSError, ValueError, HttpError) as error:
            r['posted'] = False
            message = str(error)
            r['postError'] = message.replace(token, '***') if token else message

    def execute(self):
        error = None
        try:
            view, domain = self.check_placement()
            document = self.fetch_control(view, domain)
            self.sample(view, domain, document)
        except Refusal as caught:
            error = caught
        except (OSError, ValueError, subprocess.SubprocessError) as caught:
            error = Refusal('%s: %s' % (type(caught).__name__, caught), self.current)
        self.finish(error)
        if error is not None:
            print('storage witness: ' + self.receipt['words'], file=sys.stderr)
            return 1
        print(self.receipt['words'])
        return 0


def run(argv, *, env=os.environ, http=None, now=None, deps=None):
    argv = list(argv)
    if argv[:1] == ['witness']:
        argv = ['storage'] + argv
    try:
        req = sc.parse_request(argv)
        if req['verb'] != 'witness':
            raise ValueError('Only the witness verb runs here.')
    except ValueError as error:
        print('storage witness: %s' % error, file=sys.stderr)
        return 2
    return Witness(req, env, http or WitnessCurl(), now or default_now, deps or Deps()).execute()


if __name__ == '__main__':
    sys.exit(run(sys.argv[1:]))
