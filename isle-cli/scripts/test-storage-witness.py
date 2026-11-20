#!/usr/bin/env python3
"""Checks for storage_witness.py (stm-8): fake network, temp dirs.  Prints PASS/FAIL; exits nonzero on failure."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import storage_contract as sc
import storage_migrate as sm
import storage_receive as sr
import storage_witness as sw
from storage_snapshot import encoded

FAILED, COUNT = [], [0]
DATASET, DOMAIN, GEN = 'apt-public-repo', 'apt.isle', 3
TARGET, SOURCE, ME = 'pol-core', 'isle-core', 'econ-core'
TADDR = '10.9.9.9'
DIGEST = 'a' * 64
NOW = lambda: '2026-11-09T12:00:00+00:00'
BASE = 'https://%s/.well-known/isle-storage/' % DOMAIN
VIEW_URL = '%s/api/islemesh/storage/placement?dataset=%s' % (sm.API, DATASET)
FILES = {'Packages': b'index', 'a/one.deb': b'one', 'b/two.deb': b'two two', 'c/three': b'3', 'z/last': b'last file'}


def check(name, ok):
    COUNT[0] += 1
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok:
        FAILED.append(name)


def manifest(files, big=()):
    entries = [dict(path=p, kind='file', size=len(b), sha256=hashlib.sha256(b).hexdigest()) for p, b in files.items()]
    entries += [dict(path=p, kind='file', size=size, sha256='f' * 64) for p, size in big]
    doc = dict(schemaVersion=1, kind='isle-content-snapshot', totalBytes=0, entries=sorted(entries, key=lambda e: e['path']))
    raw = encoded(dict(doc, binding=dict(dataset=DATASET)))
    return doc, raw


class Fake:
    def __init__(self, files=FILES, big=(), view=None, address=TADDR):
        self.files = dict(files)
        doc, self.raw = manifest(files, big)
        self.doc = doc
        self.csha, self.msha = sr.content_sha(doc), sr.manifest_sha(self.raw)
        self.view = dict(device=TARGET, sourceDevice=SOURCE, state='cutover', generation=GEN, domain=DOMAIN,
                         plan=dict(digest=DIGEST), addresses=dict(target=TADDR))
        self.view.update(view or {})
        self.latest = dict(contentSha=self.csha, manifestSha=self.msha, epoch=GEN)
        self.current = self.csha + '\n'
        self.address, self.view_ok, self.fetched, self.posts = address, True, [], []
        self.post_result = (201, 'ok')
        self.manifest_bytes = self.raw
        self.overrides = {}

    def get_json(self, url):
        if url == VIEW_URL and self.view_ok:
            return {'view': self.view}
        raise sm.HttpError('404', 404)

    def get_bytes_addr(self, url, max_bytes=None):
        self.fetched.append(url)
        if url in self.overrides:
            return self.overrides[url], self.address
        if url == BASE + 'latest.json':
            return json.dumps(self.latest).encode(), self.address
        if url == BASE + 'current':
            return self.current.encode(), self.address
        if url == BASE + 'manifest-%s.json' % self.latest['manifestSha']:
            return self.manifest_bytes, self.address
        path = url.split(DOMAIN + '/', 1)[1]
        if path in self.files:
            return self.files[path], self.address
        raise sm.HttpError('404', 404)

    def post_json(self, url, payload, token=None):
        self.posts.append((url, json.loads(json.dumps(payload)), token))
        if isinstance(self.post_result, Exception):
            raise self.post_result
        return self.post_result


class Env:
    def __init__(self, fake, me=ME):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        (t / 'op').mkdir()
        (t / 'enroll.json').write_text(json.dumps(dict(device=me, token='T' * 64)))
        self.env = dict(ISLE_OPERATION_DIR=str(t / 'op'), ISLE_STORAGE_STATE_DIR=str(t / 'state'),
                        ISLE_ENROLLMENT_FILE=str(t / 'enroll.json'))
        self.t, self.fake, self.me = t, fake, me
        self.dig = lambda d: [fake.address or TADDR]

    def run(self, generation=GEN, dataset=DATASET):
        out = sw.run(['storage', 'witness', '--dataset', dataset, '--generation', str(generation)], env=self.env,
                     http=self.fake, now=NOW, deps=sw.Deps(identity=lambda: self.me, dig=lambda d: self.dig(d)))
        path = self.t / 'op' / 'storage-receipt.json'
        self.receipt = json.loads(path.read_text()) if path.exists() else None
        return out


def refused(name, fake, me=ME, needle=None, **kw):
    e = Env(fake, me)
    code = e.run(**kw)
    text = (e.receipt or {}).get('words', '')
    check(name + ': exit 1', code == 1)
    check(name + ': receipt ok false', e.receipt is not None and e.receipt['ok'] is False)
    if needle:
        check(name + ': words mention ' + needle, needle in text)
    check(name + ': nothing posted', fake.posts == [])
    return e


# ---- happy path
f = Fake()
e = Env(f)
code = e.run()
r = e.receipt
check('happy: exit 0', code == 0)
check('happy: kind', r['kind'] == 'isle.storage.witness')
check('happy: ok', r['ok'] is True)
check('happy: device is this device', r['device'] == ME)
check('happy: generation', r['generation'] == GEN)
check('happy: epoch equals generation', isinstance(r.get('epoch'), int) and r['epoch'] == r['generation'] == GEN)
check('happy: contentSha', r['contentSha'] == f.csha)
check('happy: manifestSha', r['manifestSha'] == f.msha)
check('happy: actionId is the cutover id', r['actionId'] == 'isle.storage.cutover:%s@%s:%s' % (DATASET, TARGET, DIGEST))
check('happy: resolvedAddress', r['resolvedAddress'] == TADDR)
check('happy: route', r['route'] == [TADDR])
check('happy: at', r['at'] == NOW())
check('happy: 4 samples', len(r['sampled']) == 4)
check('happy: samples have path size sha256', all(set(s) == {'path', 'size', 'sha256'} for s in r['sampled']))
paths = [s['path'] for s in r['sampled']]
check('happy: small picks are first, middle, last', paths[:3] == ['Packages', 'b/two.deb', 'z/last'])
check('happy: content fetched by normal https name', all(u.startswith('https://' + DOMAIN) for u in f.fetched))
check('happy: at most 4 content files', len([u for u in f.fetched if '.well-known' not in u]) <= 4)
check('happy: posted', r['posted'] is True and len(f.posts) == 1)
check('happy: post url', f.posts[0][0] == sm.API + '/api/islemesh/storage/witness')
check('happy: bearer passed separately', f.posts[0][2] == 'T' * 64)
check('happy: token not in receipt', 'T' * 64 not in json.dumps(r))
lines = (e.t / 'state' / DATASET / 'witnesses.jsonl').read_text().splitlines()
check('happy: witnesses.jsonl has one line', len(lines) == 1 and json.loads(lines[0])['ok'] is True)
check('happy: no receipts.jsonl touched', not (e.t / 'state' / DATASET / 'receipts.jsonl').exists())

# ---- refusals
refused('source device', Fake(), me=SOURCE, needle='third device')
refused('target device', Fake(), me=TARGET, needle='third device')
fv = Fake(); fv.view_ok = False
refused('view absent', fv, needle='failing closed')
for st in ('hosting', 'migrated', 'authorized'):
    refused('state ' + st, Fake(view=dict(state=st)), needle='not cutover')
refused('generation mismatch (view ahead)', Fake(view=dict(generation=GEN + 2)), needle='current is %d' % (GEN + 2))
refused('generation mismatch (caller)', Fake(), generation=GEN - 1, needle='generation moved')
fc = Fake(); fc.current = 'b' * 64 + '\n'
refused('current != latest.contentSha', fc, needle='current')
fe = Fake(); fe.latest['epoch'] = GEN + 1
ee = Env(fe)
check('latest.epoch recorded, not enforced', ee.run() == 0 and ee.receipt['promotedAtEpoch'] == GEN + 1 and ee.receipt['epoch'] == GEN)
fe0 = Fake(); fe0.latest.pop('epoch')
ee0 = Env(fe0)
check('latest.epoch absent: still ok, promotedAtEpoch None', ee0.run() == 0 and ee0.receipt['promotedAtEpoch'] is None)
refused('source device via legacy source_device key', Fake(view=dict(sourceDevice=None, source_device=SOURCE)), me=SOURCE, needle='third device')
fpd = Fake(); fpd.view.pop('plan'); fpd.view['planDigest'] = DIGEST
epd = Env(fpd)
check('planDigest fallback binds the action', epd.run() == 0 and epd.receipt['actionId'].endswith(':' + DIGEST))
fnp = Fake(); fnp.view.pop('plan')
refused('no plan digest at all', fnp, needle='plan digest')
fm = Fake(); fm.manifest_bytes = fm.raw + b' '
refused('manifest sha mismatch', fm, needle='manifestSha')
fcs = Fake(); fcs.latest['contentSha'] = 'c' * 64; fcs.current = 'c' * 64 + '\n'
refused('content sha mismatch', fcs, needle='contentSha')
fs = Fake(); fs.files['a/one.deb'] = b'ONE'
fs2 = Fake(files=dict(FILES, **{'b/two.deb': b'tamper'}))
fs2.files['b/two.deb'] = b'tamper'
fs3 = Fake(); fs3.files['b/two.deb'] = b'bad bytes'
e3 = refused('sampled file sha mismatch', fs3, needle='b/two.deb')
check('sampled mismatch: nothing posted but recorded', (e3.t / 'state' / DATASET / 'witnesses.jsonl').exists())
fo = Fake(files={}, big=[('huge/a.iso', 100 * sm.MiB), ('huge/b.iso', 200 * sm.MiB)])
refused('oversize-only manifest', fo, needle='bounded')
fw = Fake(address='10.1.1.1')
refused('resolved address != target address', fw, needle='not the target address')
fx = Fake()
fx.overrides[BASE + 'latest.json'] = b'not json'
refused('latest.json not JSON', fx, needle='JSON')
ff = Fake(); ff.latest['epoch'] = GEN
bad = sw.Env if False else None
code = sw.run(['storage', 'witness', '--dataset', DATASET], env={}, http=Fake(), now=NOW, deps=sw.Deps(identity=lambda: ME))
check('bad argv: exit 2', code == 2)

# ---- address missing from the view: refused
fn = Fake(address='10.7.7.7'); fn.view.pop('addresses')
refused('no view target address', fn, needle='placement view carries no target address; witness cannot bind the route')
fn2 = Fake(address='10.7.7.7'); fn2.view['addresses'] = {}
refused('empty view addresses', fn2, needle='no target address')

# ---- POST failure recorded, op succeeds
fp = Fake(); fp.post_result = (500, 'boom')
ep = Env(fp)
check('post 500: exit 0', ep.run() == 0)
check('post 500: posted false + postError', ep.receipt['posted'] is False and 'HTTP 500' in ep.receipt['postError'] and ep.receipt['ok'])
fp2 = Fake(); fp2.post_result = sm.HttpError('curl exit 7 ' + 'T' * 64)
ep2 = Env(fp2)
check('post transport error: exit 0, token scrubbed', ep2.run() == 0 and ep2.receipt['posted'] is False and 'T' * 64 not in ep2.receipt['postError'])
fp3 = Fake(); ep3 = Env(fp3); (ep3.t / 'enroll.json').unlink()
check('no enrollment file: exit 0, posted false', ep3.run() == 0 and ep3.receipt['posted'] is False and 'postError' in ep3.receipt)

# ---- determinism of the bounded pick
many = {'f%02d' % i: bytes([i]) * (i + 1) for i in range(30)}
doc, _ = manifest(many)
cands = sorted(doc['entries'], key=lambda x: x['path'])
a1 = sw.bounded_pick(cands, GEN, ME); a2 = sw.bounded_pick(cands, GEN, ME)
check('bounded pick: same inputs same index', a1 == a2)
check('bounded pick matches the contract formula', a1 == int(hashlib.sha256(('%d:%s' % (GEN, ME)).encode()).hexdigest(), 16) % 30)
picks = {sw.bounded_pick(cands, GEN, 'dev-%d' % i) for i in range(20)}
check('bounded pick: different devices spread', len(picks) > 1)
s1 = sw.choose_samples(doc, GEN, ME); s2 = sw.choose_samples(doc, GEN, ME)
check('choose_samples deterministic', s1 == s2 and len(s1) == 4)
big_doc, _ = manifest({'s': b'x'}, big=[('b1', 10 * sm.MiB), ('b2', 100 * sm.MiB)])
picked = sw.choose_samples(big_doc, GEN, ME)
check('bounded entry never above 64 MiB', all(p['size'] <= 64 * sm.MiB for p in picked))
check('one small entry only: deduped', len(sw.choose_samples(manifest({'only': b'1'})[0], GEN, ME)) == 1)

# ---- stage labels
stages = []
orig = sw.operations.stage
sw.operations.stage = lambda ident, label: stages.append((ident, label))
fs_ = Fake(); es = Env(fs_); es.env['ISLE_OPERATION_ID'] = 'op1'
es.run()
sw.operations.stage = orig
check('stage labels in order', [s[1] for s in stages] == list(sw.STAGES))
check('stage labels carry the op id', all(s[0] == 'op1' for s in stages))
check('stage labels are valid tokens', all(__import__('re').fullmatch(r'[a-z0-9-]{1,80}', s[1]) for s in stages))

# ---- curl address plumbing
class P:
    def __init__(self, argv, **kw):
        self.argv = argv
        import io
        self.stdout, self.stderr = io.BytesIO(b'body'), io.BytesIO(b'10.9.9.9')
    def wait(self): return 0
    def kill(self): pass
c = sw.WitnessCurl(popen=P)
body, addr = c.get_bytes_addr('https://apt.isle/x', max_bytes=100)
check('curl: body and remote_ip split', (body, addr) == (b'body', '10.9.9.9'))
check('curl: write-out to stderr and no --resolve', '%{stderr}%{remote_ip}' in c._argv('https://apt.isle/x', None, 5) and '--resolve' not in c._argv('https://apt.isle/x', None, 5))
try:
    c.get_bytes_addr('https://apt.isle/x', max_bytes=2); big = False
except sm.HttpError:
    big = True
check('curl: oversize body refused', big)
check('storage_migrate CurlHttp argv untouched', '--write-out' not in sm.CurlHttp()._argv('https://apt.isle/x', None, 5))

print('%d checks, %d failed' % (COUNT[0], len(FAILED)))
sys.exit(1 if FAILED else 0)
