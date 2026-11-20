#!/usr/bin/env python3
"""Placement record + manifest publication, all against temp directories."""
import datetime, hashlib, io, json, os, signal, stat, sys, tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import storage_placement as sp
import storage_snapshot as ss
import repository_lock

FAILS = []
PLAN = dict(id='a' * 32, revision=2, digest='b' * 64)


def check(name, ok):
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok: FAILS.append(name)


def raises(kind, fn, *a, **kw):
    try: fn(*a, **kw)
    except kind: return True
    except Exception: return False
    return False


def make(tmp):
    root = Path(tmp); repo = root / 'apt' / 'repo'; repo.mkdir(parents=True)
    (repo / 'Packages').write_text('index'); (repo / 'sub').mkdir(); (repo / 'sub' / 'x.deb').write_bytes(b'deb')
    return root, repo, root / 'placement.json'


def _args(kw):
    args = dict(dataset='apt-public-repo', device='member-b', domain='apt.isle', source_path='/etc/isle-mesh/apt/repo', plan=PLAN)
    args.update(kw)
    return args


def authorize(path, **kw):
    return sp.authorize(path=path, **_args(kw))


def tr(path, a, b, **kw):
    kw.setdefault('by', 'tester'); kw.setdefault('operation', 'test-op')
    return sp.transition('apt-public-repo', a, b, path=path, **kw)


def fresh(path, state='authorized', dataset='apt-public-repo'):
    """A new entry walked to `state` along the allowed edges."""
    path = Path(path)
    if path.exists(): path.unlink()
    authorize(path, dataset=dataset)
    walk = {'authorized': [], 'migrated': [('authorized', 'migrated')],
            'cutover': [('authorized', 'migrated'), ('migrated', 'cutover')],
            'hosting': [('authorized', 'migrated'), ('migrated', 'cutover'), ('cutover', 'hosting')],
            'recovery-needed': [('authorized', 'migrated'), ('migrated', 'recovery-needed')]}[state]
    for a, b in walk:
        sp.transition(dataset, a, b, by='setup', operation='setup', path=path, bump_generation=(b == 'cutover'))
    return path


def record_tests(root, placement):
    check('missing record reads empty', sp.read(root / 'none.json') == {'datasets': {}})
    bad = root / 'bad.json'; bad.write_text('{nope')
    check('malformed record raises ValueError', raises(ValueError, sp.read, bad))
    bad.write_text('[]')
    check('non-object record raises ValueError', raises(ValueError, sp.read, bad))
    check('hosting_for absent is None', sp.hosting_for('apt-public-repo', placement) is None)
    check('transition without entry refused', raises(ValueError, tr, placement, 'authorized', 'migrated'))
    check('refused transition wrote nothing', not placement.exists())
    entry = authorize(placement)
    check('authorize returns entry in authorized state', entry['device'] == 'member-b' and entry['plan'] == PLAN and entry['state'] == 'authorized')
    check('authorize entry schema', set(entry) == {'dataset', 'domain', 'device', 'sourcePath', 'state', 'generation', 'plan', 'history', 'addresses', 'sourceDevice'}
          and entry['generation'] == 1 and entry['addresses'] == {'source': None, 'target': None})
    check('authorize history first row', len(entry['history']) == 1 and entry['history'][0]['from'] is None and entry['history'][0]['to'] == 'authorized'
          and set(entry['history'][0]) == {'from', 'to', 'at', 'by', 'operation'})
    check('record file mode 0644', stat.S_IMODE(placement.stat().st_mode) == 0o644)
    check('hosting_for returns entry with state and generation', sp.hosting_for('apt-public-repo', placement)['state'] == 'authorized'
          and sp.hosting_for('apt-public-repo', placement)['generation'] == 1)
    check('no temp files left', [p.name for p in root.iterdir() if p.name.startswith('tmp')] == [])
    check('second authorize refused', raises(ValueError, authorize, placement))
    again = authorize(placement, device='core-a', replace=True)
    check('authorize replace=True overwrites, keeps history, continues generation', again['device'] == 'core-a' and again['generation'] == 2
          and len(again['history']) == 2 and again['history'][1]['from'] == 'authorized')
    other = authorize(placement, dataset='other-set')
    check('other dataset coexists', set(sp.read(placement)['datasets']) == {'apt-public-repo', 'other-set'} and other)
    check('transition unknown dataset refused', raises(ValueError, sp.transition, 'nope-set', 'authorized', 'migrated', by='t', operation='o', path=placement))


def transition_tests(root):
    p = root / 'tr.json'
    now = datetime.datetime(2026, 11, 9, 12, 0, 0)
    fresh(p)
    e = tr(p, 'authorized', 'migrated', now=now, by='alice', operation='mark-migrated')
    check('authorized->migrated', e['state'] == 'migrated' and e['generation'] == 1)
    check('history appended with by/operation/at', e['history'][-1] == {'from': 'authorized', 'to': 'migrated', 'at': '2026-11-09T12:00:00Z', 'by': 'alice', 'operation': 'mark-migrated'})
    e = tr(p, 'migrated', 'cutover', bump_generation=True, expect_generation=1)
    check('migrated->cutover bumps generation', e['state'] == 'cutover' and e['generation'] == 2)
    e = tr(p, 'cutover', 'migrated', expect_generation=2)
    check('cutover->migrated (rollback) keeps generation', e['state'] == 'migrated' and e['generation'] == 2)
    e = tr(p, 'migrated', 'cutover', bump_generation=True)
    check('second cutover bumps again', e['generation'] == 3)
    e = tr(p, 'cutover', 'hosting', expect_generation=3)
    check('cutover->hosting', e['state'] == 'hosting' and e['generation'] == 3)
    check('history length tracks every change', len(sp.hosting_for('apt-public-repo', p)['history']) == 6)
    fresh(p, 'cutover')
    check('cutover->recovery-needed', tr(p, 'cutover', 'recovery-needed')['state'] == 'recovery-needed')
    check('recovery-needed->cutover', tr(p, 'recovery-needed', 'cutover')['state'] == 'cutover')
    tr(p, 'cutover', 'recovery-needed')
    check('recovery-needed->migrated', tr(p, 'recovery-needed', 'migrated')['state'] == 'migrated')
    check('migrated->recovery-needed', tr(p, 'migrated', 'recovery-needed')['state'] == 'recovery-needed')
    allowed = sp.TRANSITIONS
    bad = [(a, b) for a in sp.STATES for b in sp.STATES if (a, b) not in allowed]
    refused = 0
    for a, b in bad:
        fresh(p, a)
        before = p.read_bytes()
        refused += raises(ValueError, tr, p, a, b) and p.read_bytes() == before
    check('every disallowed transition refused, file untouched (%d)' % len(bad), refused == len(bad))
    fresh(p, 'migrated')
    before = p.read_bytes()
    check('CAS: stale from_state refused naming the actual state', raises_msg(tr, p, 'authorized', 'migrated', needle='state migrated'))
    check('CAS refusal wrote nothing', p.read_bytes() == before)
    check('generation mismatch refused', raises(ValueError, tr, p, 'migrated', 'cutover', bump_generation=True, expect_generation=5) and p.read_bytes() == before)
    check('migrated->cutover without bump refused', raises(ValueError, tr, p, 'migrated', 'cutover'))
    check('bump on another edge refused', raises(ValueError, tr, p, 'migrated', 'recovery-needed', bump_generation=True))
    check('transition needs by and operation', raises(ValueError, tr, p, 'migrated', 'recovery-needed', by='') and raises(ValueError, tr, p, 'migrated', 'recovery-needed', operation=' '))
    e = tr(p, 'migrated', 'recovery-needed', addresses={'source': '10.0.0.1', 'target': '10.0.0.2'})
    check('addresses recorded', e['addresses'] == {'source': '10.0.0.1', 'target': '10.0.0.2'})
    e = tr(p, 'recovery-needed', 'migrated', addresses={'target': None})
    check('addresses merge', e['addresses'] == {'source': '10.0.0.1', 'target': None})
    check('bad addresses refused', raises(ValueError, tr, p, 'migrated', 'recovery-needed', addresses={'x': '1'}))
    lock = Path(str(p) + '.lock')
    check('lock file exists, mode 0644', lock.exists() and stat.S_IMODE(lock.stat().st_mode) == 0o644)
    check('transition blocks while lock held', blocks_on_lock(p))
    check('lock released afterwards', tr(p, 'migrated', 'recovery-needed')['state'] == 'recovery-needed')


def raises_msg(fn, *a, needle, **kw):
    try: fn(*a, **kw)
    except ValueError as exc: return needle in str(exc)
    return False


def blocks_on_lock(p):
    import fcntl
    fd = os.open(str(p) + '.lock', os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        got = with_alarm(lambda: bool(tr(p, 'migrated', 'recovery-needed')), 1)
    finally:
        os.close(fd)
    return got is False and sp.hosting_for('apt-public-repo', p)['state'] == 'migrated'


def validation_tests(root):
    p = root / 'v.json'
    for label, kw in [('dataset', dict(dataset='Bad_Name')), ('device', dict(device='-x')),
                      ('domain without .isle', dict(domain='apt.example')),
                      ('relative source path', dict(source_path='etc/repo')),
                      ('traversal source path', dict(source_path='/etc/../x')),
                      ('plan not dict', dict(plan='x')),
                      ('plan id not hex', dict(plan=dict(PLAN, id='zz'))),
                      ('plan digest short', dict(plan=dict(PLAN, digest='ab'))),
                      ('plan revision not int', dict(plan=dict(PLAN, revision='2'))),
                      ('plan revision bool', dict(plan=dict(PLAN, revision=True)))]:
        check('rejects bad ' + label, raises(ValueError, authorize, p, **kw))
    check('nothing written on rejection', not p.exists())


def manifest_tests(root, repo):
    now = datetime.datetime(2026, 11, 9, 12, 0, 0)
    latest = sp.publish_manifest(repo, device='core-a', plan_id=PLAN['id'], item_id='item-1', now=now)
    sha = latest['manifestSha']
    published = repo / 'snapshot' / 'manifests' / (sha + '.json')
    raw = published.read_bytes()
    check('manifest named by sha of its bytes', hashlib.sha256(raw).hexdigest() == sha)
    doc = json.loads(raw)
    check('binding block present', doc['binding']['dataset'] == 'apt-public-repo' and doc['binding']['sourceDevice'] == 'core-a'
          and doc['binding']['planId'] == PLAN['id'] and doc['binding']['itemId'] == 'item-1'
          and doc['binding']['lockPath'] == str(repo.parent / '.publish.lock'))
    check('latest.json shape', json.loads((repo / 'snapshot' / 'latest.json').read_text()).keys() >= {'manifestSha', 'contentSha', 'at', 'dataset', 'totalBytes', 'entries'}
          and latest['entries'] == 3)
    check('manifest excludes snapshot dir', not any(e['path'].startswith('snapshot') for e in doc['entries']))
    check('verify accepts published manifest', ss.verify(str(repo), published, sha)['contentVerified'])
    kw = dict(device='core-a', plan_id=PLAN['id'], item_id='item-1', now=now)
    check('republish identical document is idempotent', sp.publish_manifest(repo, **kw)['manifestSha'] == sha)
    check('latest.json at is the injected time', latest['at'] == '2026-11-09T12:00:00Z')
    published.write_bytes(raw + b' ')
    check('differing file of same name refused', raises(ValueError, sp.publish_manifest, repo, **kw))
    published.write_bytes(raw)
    check('lock released after publish', lock_free(repo))
    check('hidden content makes publish fail', hidden_fails(repo))


def object_tests(root):
    repo = root / 'objrepo'; (repo / 'by-hash' / 'SHA256').mkdir(parents=True)
    (repo / 'pkg_1.0_amd64.deb').write_bytes(b'deb bytes'); (repo / 'Packages').write_text('index one')
    (repo / 'by-hash' / 'SHA256' / 'abc').write_text('hashed'); (repo / 'index.html').write_text('<html>')
    check('immutable_path rule', all(sp.immutable_path(p) for p in ('by-hash/SHA256/x', 'dists/by-hash/x', 'a/b_1_all.deb', 'pkg_1.0_amd64.deb'))
          and not any(sp.immutable_path(p) for p in ('Packages', 'index.html', 'isle-root.crt', 'InRelease', 'catalog/a/1/f', 'runtime/x', 'rollouts/r',
                                                    'x.tar', 'x.tar.gz', 'a/catalog/x', 'x.tar.bz2', 'plain.deb')))
    l1 = sp.publish_manifest(repo, device='core-a', now=datetime.datetime(2026, 11, 9, 1))
    objs = repo / 'snapshot' / 'objects'
    sha = lambda b: hashlib.sha256(b).hexdigest()
    check('immutable file hardlinked', os.stat(repo / 'pkg_1.0_amd64.deb').st_ino == os.stat(objs / sha(b'deb bytes')).st_ino
          and os.stat(repo / 'by-hash' / 'SHA256' / 'abc').st_nlink == 2)
    check('mutable file copied (new inode)', os.stat(repo / 'Packages').st_ino != os.stat(objs / sha(b'index one')).st_ino
          and os.stat(repo / 'Packages').st_nlink == 1)
    check('objects named by their sha', all(sha((objs / n).read_bytes()) == n for n in os.listdir(objs)))
    check('no temp objects left', not [n for n in os.listdir(objs) if n.startswith('.')])
    check('latest.json has contentSha', len(l1['contentSha']) == 64 and l1['contentSha'] != l1['manifestSha'])
    (repo / 'Packages').write_text('index two!')  # in place, as a careless writer would
    check('in-place rewrite leaves old object intact', (objs / sha(b'index one')).read_bytes() == b'index one')
    l2 = sp.publish_manifest(repo, device='core-a', now=datetime.datetime(2026, 11, 9, 2))
    check('second publish accepts linked files', l2['manifestSha'] != l1['manifestSha'] and (objs / sha(b'index two!')).exists())
    (objs / sha(b'deb bytes')).unlink(); (objs / sha(b'deb bytes')).write_bytes(b'wrong size here')
    check('existing object of other size refused, named', raises(ValueError, sp.publish_manifest, repo, device='core-a') )
    (objs / sha(b'deb bytes')).unlink(); os.link(repo / 'pkg_1.0_amd64.deb', objs / sha(b'deb bytes'))
    hardening_tests(root)


def hardening_tests(root):
    sha = lambda b: hashlib.sha256(b).hexdigest()
    # 1. overwrite-at-same-name control
    repo = root / 'tamper'; repo.mkdir(); (repo / 'Packages').write_text('index body')
    sp.publish_manifest(repo, device='core-a', now=datetime.datetime(2026, 11, 9, 1))
    obj = repo / 'snapshot' / 'objects' / sha(b'index body')
    obj.write_bytes(b'index BODY')  # same size, different bytes
    (repo / 'newfile').write_text('brand new')
    mans = sorted(os.listdir(repo / 'snapshot' / 'manifests'))
    needle = 'snapshot object %s does not match its name; repository integrity check failed' % sha(b'index body')
    check('tampered same-size object refused with message', raises_msg(sp.publish_manifest, repo, device='core-a', needle=needle))
    check('tampered object untouched, nothing else published', obj.read_bytes() == b'index BODY'
          and not (repo / 'snapshot' / 'objects' / sha(b'brand new')).exists()
          and sorted(os.listdir(repo / 'snapshot' / 'manifests')) == mans)
    # 4. eligible_for_hardlink
    r = root / 'elig'; (r / 'by-hash' / 'SHA256').mkdir(parents=True); (r / 'catalog' / 'n' / '1').mkdir(parents=True)
    h = sha(b'x')
    files = {'a.tar': b'tar', 'by-hash/SHA256/anyname': b'bh', 'p_1.2-3_amd64.deb': b'deb', 'catalog/n/1/plain.bin': b'cat',
             'catalog/n/1/' + h + '.bin': b'x', 'runtime/img.tar': b'rt'}
    (r / 'runtime').mkdir()
    for n, b in files.items(): (r / n).write_bytes(b)
    el = lambda n, d=None: sp.eligible_for_hardlink(r, n, d or sha(files[n]))
    check('tar under non-content-addressed name copied', not el('a.tar')[0] and not el('runtime/img.tar')[0])
    check('catalog descriptor not named by its sha copied; runtime manifest copied', not el('catalog/n/1/plain.bin')[0] and not el('runtime/img.tar')[0])
    check('by-hash file linked', el('by-hash/SHA256/anyname')[0])
    check('versioned deb linked', el('p_1.2-3_amd64.deb')[0])
    check('catalog file named by sha linked', el('catalog/n/1/' + h + '.bin', h)[0])
    check('catalog file not named by sha copied', not el('catalog/n/1/plain.bin')[0])
    check('mutable path class not eligible', not sp.eligible_for_hardlink(r, 'Packages', h)[0])
    fd = os.open(r, os.O_RDONLY | os.O_DIRECTORY)
    try: check('eligible accepts a dir fd', sp.eligible_for_hardlink(fd, 'p_1.2-3_amd64.deb', h)[0])
    finally: os.close(fd)
    (r / 'link.deb').symlink_to('p_1.2-3_amd64.deb')
    check('symlink never eligible', not sp.eligible_for_hardlink(r, 'link.deb', h)[0])
    (r / 'runtime' / 'manifest.json').write_bytes(b'rtm'); (r / 'catalog' / 'n' / '1' / 'release.json').write_bytes(b'desc')
    check('runtime manifest and catalog descriptor copied, never linked', not sp.eligible_for_hardlink(r, 'runtime/manifest.json', sha(b'rtm'))[0]
          and not sp.eligible_for_hardlink(r, 'catalog/n/1/release.json', sha(b'desc'))[0])
    r2 = root / 'elig2'; r2.mkdir(); (r2 / 'plain.tar').write_bytes(b'tar'); (r2 / 'q_1_all.deb').write_bytes(b'debq')
    sp.publish_manifest(r2, device='core-a')
    o = r2 / 'snapshot' / 'objects'
    check('publish: odd-named tar copied, deb linked', os.stat(r2 / 'plain.tar').st_nlink == 1
          and os.stat(r2 / 'q_1_all.deb').st_ino == os.stat(o / sha(b'debq')).st_ino)
    check('immutable_path still exported', callable(sp.immutable_path))


def gc_tests(root):
    repo = root / 'gcrepo'; repo.mkdir()
    shas = []
    for i in range(4):
        (repo / 'data.txt').write_text('gen %d' % i)
        shas.append(sp.publish_manifest(repo, device='core-a', now=datetime.datetime(2026, 11, 9, i))['manifestSha'])
        os.utime(repo / 'snapshot' / 'manifests' / (shas[-1] + '.json'), (1000 + i, 1000 + i))
    objs = repo / 'snapshot' / 'objects'
    h = lambda i: hashlib.sha256(('gen %d' % i).encode()).hexdigest()
    check('four distinct manifests', len(set(shas)) == 4 and len(os.listdir(objs)) == 4)
    r = sp.gc(repo, {shas[0]}, keep_floor=2)
    check('gc keeps pin + newest floor', set(r['kept']) == {shas[0], shas[2], shas[3]} and r['removedManifests'] == [shas[1]])
    check('gc removes only unreferenced objects', r['removedObjects'] == [h(1)]
          and (objs / h(0)).exists() and (objs / h(3)).exists() and (objs / h(2)).exists())
    check('nothing referenced by a kept manifest removed', all(
        (objs / e['sha256']).exists() for s in r['kept'] for e in json.loads((repo / 'snapshot' / 'manifests' / (s + '.json')).read_text())['entries'] if e['kind'] == 'file'))
    check('gc without pins and floor 1 keeps latest only', sp.gc(repo, set(), keep_floor=1)['kept'] == [shas[3]])
    check('gc rejects bad pin', raises(ValueError, sp.gc, repo, {'zz'}))
    check('gc releases lock', lock_free(repo))
    sp._geteuid = lambda: 1000
    try: check('cli gc needs root', raises(PermissionError, sp.main, ['gc', '--repo', str(repo)]))
    finally: sp._geteuid = os.geteuid
    sp._geteuid = lambda: 0
    try:
        out = io.StringIO(); pl = root / 'gc-placement.json'
        with redirect_stdout(out): sp.main(['gc', '--repo', str(repo), '--placement', str(pl)])
        check('cli gc derives pins (none: latest only kept up to floor 3)', len(json.loads(out.getvalue())['kept']) == 1)
        check('cli rejects --pin', raises(SystemExit, sp.main, ['gc', '--repo', str(repo), '--pin', shas[3]]))
    finally: sp._geteuid = os.geteuid


def derive_tests(root):
    repo = root / 'derive'; repo.mkdir(); (repo / 'd').write_text('x')
    pl = root / 'derive-placement.json'
    S = lambda c: c * 64
    def put(entries): pl.write_text(json.dumps({'datasets': entries}))
    mine = dict(sourcePath=str(repo), acceptedManifest=S('1'), rollbackManifest=S('2'), pending=None,
                plan=dict(id='a' * 32, revision=1, digest=S('b'), snapshotSha256=S('3')))
    put({'a': mine, 'other': dict(mine, sourcePath='/elsewhere', acceptedManifest=S('9')), 'nosrc': {'acceptedManifest': S('8')}})
    pins, complete, pending = sp.derive_pins(repo, pl)
    check('derive_pins: accepted, rollback, plan snapshot; other source skipped; absent sourcePath counts',
          pins == {S('1'), S('2'), S('3'), S('8')} and complete and not pending)
    put({'a': dict(mine, pending=dict(action='cutover', manifestSha=S('4')))})
    pins, complete, pending = sp.derive_pins(repo, pl)
    check('derive_pins: pending flagged and its manifestSha pinned', pending and S('4') in pins and complete)
    put({'a': mine})
    plan = dict(items=[dict(kind='storage', storage=dict(source=dict(snapshotSha256=S('5')))),
                       dict(kind='app'), dict(kind='storage', storage=dict(source=dict(snapshotSha256=None)))])
    pins, complete, pending = sp.derive_pins(repo, pl, [plan])
    check('derive_pins: plan storage item snapshot pinned', S('5') in pins and complete)
    check('derive_pins: malformed plan is incomplete', sp.derive_pins(repo, pl, [None])[1] is False
          and sp.derive_pins(repo, pl, [dict(items=[dict(kind='storage')])])[1] is False)
    pl.write_text('{nope')
    check('derive_pins: unreadable placement is incomplete', sp.derive_pins(repo, pl)[1] is False)
    # CLI-path refusals
    sp.publish_manifest(repo, device='core-a', now=datetime.datetime(2026, 11, 9, 1))
    snap = repo / 'snapshot'; before = sorted(os.listdir(snap / 'manifests'))
    put({'a': dict(mine, pending=dict(action='cutover'))})
    check('gc refuses while pending', raises_msg(sp.gc_derived, repo, pl, needle='a storage transition is pending; no garbage collection')
          and sorted(os.listdir(snap / 'manifests')) == before)
    pl.write_text('{nope')
    check('gc refuses on unreadable placement', raises_msg(sp.gc_derived, repo, pl, needle='incomplete') and sorted(os.listdir(snap / 'manifests')) == before)
    put({'a': mine})
    check('gc proceeds with sound authority', isinstance(sp.gc_derived(repo, pl, [plan])['kept'], list))
    # takenAt ordering beats mtime
    r2 = root / 'order'; r2.mkdir(); shas = []
    for i in range(3):
        (r2 / 'f').write_text('v%d' % i)
        shas.append(sp.publish_manifest(r2, device='core-a', now=datetime.datetime(2026, 11, 9, i))['manifestSha'])
    for i, sha in enumerate(shas):  # mtime runs backwards: newest takenAt gets the oldest mtime
        os.utime(r2 / 'snapshot' / 'manifests' / (sha + '.json'), (5000 - i, 5000 - i))
    # make latest point at the oldest so only ordering decides the rest
    (r2 / 'snapshot' / 'latest.json').write_text(json.dumps({'manifestSha': shas[0]}))
    res = sp.gc(r2, set(), keep_floor=2)
    check('gc orders by takenAt not mtime; latest.json manifest always kept', set(res['kept']) == {shas[0], shas[2]} and res['removedManifests'] == [shas[1]])
    # CLI aliases
    sp._geteuid = lambda: 0
    cpath = root / 'alias.json'
    err = io.StringIO()
    from contextlib import redirect_stderr
    try:
        with patch.object(sp, 'PLACEMENT', cpath), redirect_stdout(io.StringIO()), redirect_stderr(err):
            sp.main(['authorize-placement', '--dataset', 'al-set', '--device', 'core-a', '--domain', 'a.isle', '--source-path', '/x',
                     '--plan-id', 'a' * 32, '--plan-revision', '1', '--plan-digest', 'b' * 64])
            sp.main(['mark-migrated', '--dataset', 'al-set', '--digest', 'b' * 64])
            sp.transition('al-set', 'migrated', 'cutover', by='t', operation='cutover', path=cpath, bump_generation=True)
            sp.main(['accept-placement', '--dataset', 'al-set', '--generation', '2'])
    finally: sp._geteuid = os.geteuid
    e = sp.read(cpath)['datasets']['al-set']
    check('alias verbs work, print one deprecation line each to stderr',
          e['state'] == 'hosting' and err.getvalue().count('deprecated') == 2 and 'use authorize' in err.getvalue() and 'use accept' in err.getvalue())
    check('operation strings are authorize / accept', e['history'][0]['operation'] == 'authorize' and e['history'][-1]['operation'] == 'accept')


def gc_refusal_tests(root):
    repo = root / 'gcref'; repo.mkdir(); (repo / 'd').write_text('one')
    s1 = sp.publish_manifest(repo, device='core-a', now=datetime.datetime(2026, 11, 9, 1))['manifestSha']
    (repo / 'd').write_text('two')
    sp.publish_manifest(repo, device='core-a', now=datetime.datetime(2026, 11, 9, 2))
    snap = repo / 'snapshot'
    def listing(): return sorted(os.listdir(snap / 'objects')), sorted(os.listdir(snap / 'manifests'))
    before = listing()
    check('gc refuses pins=None', raises_msg(sp.gc, repo, None, keep_floor=1, needle='no pins') and listing() == before)
    check('gc refuses incomplete authority', raises_msg(sp.gc, repo, set(), keep_floor=1, authority_complete=False,
          needle='reference enumeration incomplete; no garbage collection') and listing() == before)
    latest = (snap / 'latest.json').read_bytes()
    (snap / 'latest.json').write_bytes(b'{nope')
    check('gc refuses unreadable latest.json', raises_msg(sp.gc, repo, set(), keep_floor=1, needle='latest.json') and listing() == before)
    (snap / 'latest.json').unlink()
    check('gc refuses missing latest.json', raises_msg(sp.gc, repo, set(), keep_floor=1, needle='latest.json') and listing() == before)
    (snap / 'latest.json').write_bytes(latest)
    bad = snap / 'manifests' / ('c' * 64 + '.json'); bad.write_bytes(b'garbage')
    check('gc refuses when any manifest fails to parse', raises_msg(sp.gc, repo, set(), keep_floor=1, needle='unreadable snapshot manifest')
          and sorted(os.listdir(snap / 'objects')) == before[0])
    bad.unlink()
    check('gc proceeds once authority is sound', len(sp.gc(repo, set(), keep_floor=1)['removedManifests']) == 1)


def lock_free(repo):
    with repository_lock.publication_lock(repo): return True


def hidden_fails(repo):
    (repo / '.stray').write_text('x')
    try: return raises(ValueError, sp.publish_manifest, repo, device='core-a')
    finally: (repo / '.stray').unlink()


def after_tests(root, repo, placement):
    out = io.StringIO()
    with redirect_stdout(out):
        r = sp.after_publication(repo, path=root / 'none.json', device='core-a')
    check('after_publication without record does nothing', r['published'] is False and out.getvalue() == '')
    before = set((repo / 'snapshot' / 'manifests').iterdir())
    r = r0 = sp.after_publication(repo, path=placement, device='core-a')
    check('after_publication with record publishes', r['published'] is True and (repo / 'snapshot' / 'manifests' / (r['manifestSha'] + '.json')).exists())
    check('after_publication binds plan id', json.loads((repo / 'snapshot' / 'manifests' / (r['manifestSha'] + '.json')).read_text())['binding']['planId'] == PLAN['id'])
    bad = root / 'badrec.json'; bad.write_text('{')
    out = io.StringIO()
    with redirect_stdout(out):
        r = sp.after_publication(repo, path=bad, device='core-a')
    check('malformed record is caught and reported', r['published'] is False and '[storage] manifest not published:' in out.getvalue())
    out = io.StringIO()
    with redirect_stdout(out):
        r = sp.after_publication(repo, path=placement, device=None) if not Path(sp.DEVICE_NAME).exists() else {'published': False}
    check('missing device-name is caught, not raised', r['published'] is False)
    check('after_publication publishes in authorized state', sp.hosting_for('apt-public-repo', placement)['state'] == 'authorized' and r0['published'])
    for st in ('migrated', 'cutover', 'recovery-needed'):
        fresh(placement, st)
        check('after_publication publishes in %s state' % st, sp.after_publication(repo, path=placement, device='core-a')['published'] is True)
    fresh(placement, 'hosting')
    check('after_publication publishes in hosting state', sp.after_publication(repo, path=placement, device='core-a')['published'] is True)
    check('after_publication completes while the lock is held (no deadlock)', no_deadlock(repo, placement))
    check('publish_manifest locked=True completes while the lock is held', no_deadlock_locked(repo))


def with_alarm(fn, seconds=3):
    def boom(*_): raise TimeoutError('deadlock')
    old = signal.signal(signal.SIGALRM, boom); signal.alarm(seconds)
    try: return fn()
    except TimeoutError: return False
    finally: signal.alarm(0); signal.signal(signal.SIGALRM, old)


def no_deadlock(repo, placement):
    def run():
        with repository_lock.publication_lock(repo):  # the real nesting: publisher holds the flock
            return sp.after_publication(repo, path=placement, device='core-a')['published'] is True
    return with_alarm(run)


def no_deadlock_locked(repo):
    def run():
        with repository_lock.publication_lock(repo):
            return bool(sp.publish_manifest(repo, device='core-a', locked=True)['manifestSha'])
    return with_alarm(run)


def publisher_hook_tests(root, repo):
    import catalog_publish, runtime_publish
    src = root / 'rel.json'; src.write_text('{}')
    with patch.object(catalog_publish, '_publish', return_value=('n', {})) as pub, \
         patch.object(catalog_publish.storage_placement, 'after_publication') as hook:
        res = catalog_publish.publish(src, repo)
    check('catalog publish calls hook once with apt dataset', hook.call_count == 1 and hook.call_args.kwargs['dataset'] == sp.DATASET_APT)
    check('catalog publish result unchanged', res == ('n', {}))
    with patch.object(runtime_publish, 'REPO', repo), patch.object(runtime_publish, 'publish_runtime'), patch.object(runtime_publish.storage_placement, 'after_publication') as hook, \
         patch('runtime_publish.os.geteuid', return_value=0), patch.object(Path, 'read_text', return_value='core'):
        runtime_publish.main(str(src))
    check('runtime publish calls hook with apt dataset', hook.call_count == 1 and hook.call_args.kwargs['dataset'] == sp.DATASET_APT)


def cli_tests(root, repo, placement):
    auth = ['authorize', '--dataset', 'cli-set', '--device', 'core-a', '--domain', 'a.isle', '--source-path', '/x',
            '--plan-id', 'a' * 32, '--plan-revision', '1', '--plan-digest', 'b' * 64]
    mark = ['mark-migrated', '--dataset', 'cli-set', '--digest', 'b' * 64]
    acc = ['accept', '--dataset', 'cli-set', '--generation', '2']
    sp._geteuid = lambda: 1000
    check('non-root refused: accept', raises(PermissionError, sp.main, acc))
    check('non-root refused: mark-migrated', raises(PermissionError, sp.main, mark))
    check('non-root refused: authorize', raises(PermissionError, sp.main, auth))
    check('non-root refused: publish-manifest', raises(PermissionError, sp.main, ['publish-manifest', '--repo', str(repo)]))
    sp._geteuid = lambda: 0
    path = root / 'cli-placement.json'
    try:
        with patch.object(sp, 'PLACEMENT', path), redirect_stdout(io.StringIO()):
            refused = raises(ValueError, sp.main, mark)
            code = sp.main(auth); again = raises(ValueError, sp.main, auth)
            bad_digest = raises(ValueError, sp.main, mark[:-1] + ['c' * 64])
            early = raises(ValueError, sp.main, acc)
            code2 = sp.main(mark); status = sp.main(['status'])
            twice = raises(ValueError, sp.main, mark)
            sp.transition('cli-set', 'migrated', 'cutover', by='t', operation='cutover', path=path, bump_generation=True)
            wrong_gen = raises(ValueError, sp.main, ['accept', '--dataset', 'cli-set', '--generation', '1'])
            code3 = sp.main(acc)
        check('cli mark-migrated before authorize refused', refused)
        check('cli mark-migrated digest mismatch refused', bad_digest)
        check('cli accept before cutover refused', early)
        check('cli mark-migrated flips authorized to migrated', code == 0 and code2 == 0 and status == 0 and twice
              and sp.read(path)['datasets']['cli-set']['history'][1]['operation'] == 'mark-migrated')
        check('cli accept generation mismatch refused', wrong_gen)
        entry = sp.read(path)['datasets']['cli-set']
        check('cli accept flips cutover to hosting', code3 == 0 and entry['state'] == 'hosting' and entry['generation'] == 2
              and entry['history'][-1]['operation'] == 'accept')
        check('cli authorize refuses existing entry', again)
        standalone = io.StringIO()
        with redirect_stdout(standalone):
            sp.main(['publish-manifest', '--repo', str(repo), '--device', 'core-a'])
        check('cli publish-manifest works with no record', json.loads(standalone.getvalue())['entries'] == 3 and not (root / 'none-placement.json').exists())
        with patch.object(sp, 'PLACEMENT', path), redirect_stdout(io.StringIO()):
            sp.main(['after-publication', '--repo', str(repo), '--dataset', 'cli-set'])
    finally:
        sp._geteuid = os.geteuid
    check('error exits 1 with storage: prefix', cli_error_exit(root))


def cli_error_exit(root):
    import subprocess
    r = subprocess.run([sys.executable, str(Path(__file__).with_name('storage_placement.py')), 'publish-manifest', '--repo', str(root / 'x')],
                       capture_output=True, text=True)
    return r.returncode == 1 and r.stderr.startswith('storage: ')


def view_tests(root):
    path = root / 'view-placement.json'
    dev = root / 'device-name'; dev.write_text('pol-core\n')
    sp.authorize('apt-public-repo', 'member-b', 'apt.isle', '/etc/isle-mesh/apt/repo', PLAN, path=path,
                 addresses={'source': '10.0.0.1', 'target': None}, source_device='pol-core')
    entry = sp.read(path)['datasets']['apt-public-repo']
    check('entry records sourceDevice', entry['sourceDevice'] == 'pol-core')
    check('sourceDevice validated', raises(ValueError, sp.authorize, 'x-set', 'member-b', 'x.isle', '/p', PLAN, path=path, source_device='Bad Name'))
    doc = sp.view_document(path, dev, now=datetime.datetime(2026, 11, 9, tzinfo=datetime.timezone.utc))
    row = doc['datasets']['apt-public-repo']
    check('view header', doc['schemaVersion'] == 1 and doc['writtenBy'] == 'pol-core' and doc['writtenAt'] == '2026-11-09T00:00:00Z'
          and set(doc) == {'schemaVersion', 'writtenAt', 'writtenBy', 'datasets'})
    check('view row fields', set(row) == {'dataset', 'domain', 'device', 'sourceDevice', 'state', 'generation', 'plan', 'pending', 'addresses'}
          and row['sourceDevice'] == 'pol-core' and row['pending'] is None and row['plan'] == PLAN
          and row['addresses'] == {'source': '10.0.0.1', 'target': None} and row['state'] == 'authorized')
    claims = json.loads(sp.claims_path(path).read_text())
    check('placement-claims mirror unchanged', claims == {'apt.isle': {'device': 'member-b', 'dataset': 'apt-public-repo', 'state': 'authorized'}})
    out = root / 'view-out.json'
    code = sp.main(['view-export', '--placement', str(path), '--out', str(out), '--device-file', str(dev)])
    check('view-export --out writes the document', code == 0 and 'apt-public-repo' in json.loads(out.read_text())['datasets'])
    buf = io.StringIO()
    with redirect_stdout(buf):
        sp.main(['view-export', '--placement', str(path), '--device-file', str(dev)])
    calls = []
    class Done: returncode = 0
    sp.post_view(doc, run=lambda argv, **kw: (calls.append((argv, kw)), Done())[1])
    argv, kw = calls[0]
    check('post_view: docker exec -i into the backend, document on stdin, tmp+fsync+replace+0644 code',
          argv[:4] == ['docker', 'exec', '-i', 'prf-isle-backend'] and '-u' not in argv and json.loads(kw['input'])['writtenBy'] == 'pol-core'
          and 'os.replace' in argv[argv.index('-c') + 1] and 'os.fsync' in argv[argv.index('-c') + 1] and '0o644' in argv[argv.index('-c') + 1]
          and argv[-1] == 'isle-storage-placement-view.json')
    class Bad: returncode = 1; stderr = 'no such container'; stdout = ''
    check('post_view: docker failure raises words', raises(ValueError, sp.post_view, doc, run=lambda *a, **k: Bad()))
    check('view-export prints the document', json.loads(buf.getvalue())['writtenBy'] == 'pol-core')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as tmp:
        root, repo, placement = make(tmp)
        record_tests(root, placement)
        transition_tests(root)
        validation_tests(root)
        manifest_tests(root, repo)
        object_tests(root)
        gc_tests(root)
        gc_refusal_tests(root)
        derive_tests(root)
        after_tests(root, repo, placement)
        publisher_hook_tests(root, repo)
        cli_tests(root, repo, placement)
        view_tests(root)
    print('%d failure(s)' % len(FAILS))
    sys.exit(1 if FAILS else 0)
