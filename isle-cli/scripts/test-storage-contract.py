#!/usr/bin/env python3
"""Checks for storage_contract.py. Prints PASS/FAIL per check; exits nonzero on any failure."""
import copy
import hashlib
import json
import re
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import storage_contract as sc

VECTOR = sc.load_vector(HERE / 'fixtures' / 'storage-digest-vector.json')
FAILED = []


def check(name, ok):
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok:
        FAILED.append(name)


def refuses(name, func, *args):
    try:
        func(*args)
    except ValueError as error:
        check(name, bool(str(error)))
    else:
        check(name, False)


ITEM_ID = VECTOR['item']['id']
PLAN_ID = VECTOR['planId']


def block():
    return copy.deepcopy(VECTOR['item']['storage'])


def item(**changes):
    out = copy.deepcopy(VECTOR['item'])
    out.update(changes)
    return out


def release(**changes):
    return dict(VECTOR['hostingRelease'], **changes)


def digest_of(it=None, revision=None, rel=None):
    return sc.storage_digest(PLAN_ID, VECTOR['revision'] if revision is None else revision,
                             it or item(), rel or release())


def mutated(path, value):
    b = block()
    target = b
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return b


def vector_and_digest_changes():
    exp = VECTOR['expectedDigest']
    check('vector digest matches', digest_of() == exp)
    payload = sc.storage_digest_payload(PLAN_ID, VECTOR['revision'], item(), release())
    check('payload keys', set(payload) == {'planId', 'revision', 'itemId', 'device', 'originDevice', 'app', 'state', 'storage', 'hostingRelease'})
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    check('digest is sha256 of canonical payload', hashlib.sha256(raw.encode()).hexdigest() == exp)
    check('digest changes with revision', digest_of(revision=3) != exp)
    check('digest changes with app', digest_of(item(app='other-app')) != exp)
    check('digest changes with state', digest_of(item(state='existing')) != exp)
    check('digest changes with originDevice', digest_of(item(originDevice='x-core', storage=mutated(['source', 'device'], 'x-core'))) != exp)
    check('digest changes with device', digest_of(item(device='other-core', storage=mutated(['target', 'device'], 'other-core'))) != exp)
    check('digest changes with item id', digest_of(item(id='other-item')) != exp)
    for field, value in (('name', 'other'), ('version', '9.9'), ('manifestSha256', 'ef' * 32)):
        check('digest changes with hostingRelease.' + field, digest_of(rel=release(**{field: value})) != exp)
    cases = [('uuid', ['target', 'filesystemUuid'], 'x-uuid'), ('relativePath', ['target', 'relativePath'], 'isle/other'),
             ('snapshotSha256', ['source', 'snapshotSha256'], 'cd' * 32), ('capacityBytes', ['target', 'capacityBytes'], 21474836481)]
    for name, path, value in cases:
        check('digest changes with ' + name, digest_of(item(storage=mutated(path, value))) != exp)
    extra = item()
    extra['junk'] = 1
    check('unknown item keys do not change digest', digest_of(extra) == exp)
    check('unknown release keys do not change digest', digest_of(rel=release(junk=1)) == exp)
    refuses('digest refuses bad plan id', sc.storage_digest, 'XYZ', 1, item(), release())
    refuses('digest refuses negative revision', sc.storage_digest, PLAN_ID, -1, item(), release())
    refuses('digest refuses bad item id', sc.storage_digest, PLAN_ID, 1, item(id='a b'), release())
    refuses('digest refuses bad release sha', sc.storage_digest, PLAN_ID, 1, item(), release(manifestSha256='zz'))
    refuses('digest refuses empty release name', sc.storage_digest, PLAN_ID, 1, item(), release(name=''))
    refuses('digest refuses long release version', sc.storage_digest, PLAN_ID, 1, item(), release(version='v' * 101))
    refuses('digest refuses non-object release', sc.storage_digest, PLAN_ID, 1, item(), 'x')


def block_constraints():
    ok = sc.validate_storage_block(block())
    check('valid block accepted and equal', ok == block())
    check('returns a new dict', ok is not VECTOR['item']['storage'] and ok['source'] is not VECTOR['item']['storage']['source'])
    extra = block()
    extra['evil'] = 1
    extra['source']['evil'] = 2
    extra['target']['evil'] = 3
    check('unknown keys dropped at every level', sc.validate_storage_block(extra) == block())
    check('snapshotSha256 None accepted', sc.validate_storage_block(mutated(['source', 'snapshotSha256'], None))['source']['snapshotSha256'] is None)
    refuses('schemaVersion 2 refused', sc.validate_storage_block, mutated(['schemaVersion'], 2))
    refuses('schemaVersion True refused', sc.validate_storage_block, mutated(['schemaVersion'], True))
    refuses('non-object block refused', sc.validate_storage_block, [])
    refuses('uppercase dataset refused', sc.validate_storage_block, mutated(['dataset'], 'Apt'))
    refuses('dataset 65 chars refused', sc.validate_storage_block, mutated(['dataset'], 'a' * 65))
    refuses('domain over 100 refused', sc.validate_storage_block, mutated(['domain'], 'a' * 101))
    for bad in ('apt', 'apt.local', 'isle', '.isle', 'a..isle', '-a.isle', 'a-.isle', 'A.isle', 'a b.isle', 'a_b.isle', 'a.isle.', 'x' * 64 + '.isle',
                'a.b.c.d.e.f.g.h.isle', ('a' * 63 + '.') * 4 + 'isle', 'apt.isle\n'):
        refuses('domain %r refused' % bad[:20], sc.validate_storage_block, mutated(['domain'], bad))
    check('multi-label domain accepted', sc.validate_storage_block(mutated(['domain'], 'a.b.c.d.e.f.g.isle'))['domain'] == 'a.b.c.d.e.f.g.isle')
    check('63-char label accepted', sc.validate_storage_block(mutated(['domain'], 'a' * 63 + '.isle'))['domain'] == 'a' * 63 + '.isle')
    total = block()['source']['totalBytes']
    refuses('capacity below 1.05x refused', sc.validate_storage_block, mutated(['target', 'capacityBytes'], total * 105 // 100))
    check('capacity at ceil(1.05x) accepted', sc.validate_storage_block(mutated(['target', 'capacityBytes'], sc.required_capacity(total)))['target']['capacityBytes'] == sc.required_capacity(total))
    check('required_capacity rounds up', sc.required_capacity(1) == 2 and sc.required_capacity(100) == 105 and sc.required_capacity(0) == 0)
    refuses('empty domain refused', sc.validate_storage_block, mutated(['domain'], ''))
    refuses('leading slash relativePath refused', sc.validate_storage_block, mutated(['target', 'relativePath'], '/abs'))
    refuses('dotdot relativePath refused', sc.validate_storage_block, mutated(['target', 'relativePath'], 'a/../b'))
    refuses('relativePath over 200 refused', sc.validate_storage_block, mutated(['target', 'relativePath'], 'a' * 201))
    refuses('whitespace in uuid refused', sc.validate_storage_block, mutated(['target', 'filesystemUuid'], 'ab cd'))
    refuses('empty uuid refused', sc.validate_storage_block, mutated(['target', 'filesystemUuid'], ''))
    refuses('uuid over 200 refused', sc.validate_storage_block, mutated(['target', 'filesystemUuid'], 'a' * 201))
    refuses('short snapshotSha256 refused', sc.validate_storage_block, mutated(['source', 'snapshotSha256'], 'ab'))
    refuses('uppercase snapshotSha256 refused', sc.validate_storage_block, mutated(['source', 'snapshotSha256'], 'AB' * 32))
    refuses('capacity below total refused', sc.validate_storage_block, mutated(['target', 'capacityBytes'], 1))
    refuses('negative totalBytes refused', sc.validate_storage_block, mutated(['source', 'totalBytes'], -1))
    refuses('bool entries refused', sc.validate_storage_block, mutated(['source', 'entries'], True))
    refuses('sourceRemoval immediate refused', sc.validate_storage_block, mutated(['sourceRemoval'], 'immediate'))
    refuses('relative mountpoint refused', sc.validate_storage_block, mutated(['target', 'mountpoint'], 'mnt/x'))
    missing = block()
    del missing['target']
    refuses('missing target refused', sc.validate_storage_block, missing)


def item_checks():
    check('valid item returns block', sc.validate_storage_item(item()) == block())
    refuses('item kind not storage refused', sc.validate_storage_item, dict(item(), kind='app'))
    refuses('item device mismatch refused', sc.validate_storage_item, dict(item(), device='x-core'))
    refuses('item originDevice mismatch refused', sc.validate_storage_item, dict(item(), originDevice='x-core'))
    refuses('item without app refused', sc.validate_storage_item, {k: v for k, v in item().items() if k != 'app'})
    refuses('item app over 100 refused', sc.validate_storage_item, item(app='a' * 101))
    refuses('item bad state refused', sc.validate_storage_item, item(state='done'))
    check('item states accepted', all(sc.validate_storage_item(item(state=x)) == block() for x in ('planned', 'existing', 'deferred')))
    refuses('item without storage refused', sc.validate_storage_item, {'kind': 'storage', 'device': 'pol-core'})


def action_checks():
    d, pid = VECTOR['expectedDigest'], PLAN_ID
    rel = release()
    core = 'isle-core'
    act = sc.planned_action(pid, 2, item(), d, rel, core_device=core)
    auth = sc.planned_action(pid, 2, item(), d, rel, kind='isle.storage.authorize', core_device=core)
    check('authorize id, device core, no deps', auth['id'] == 'isle.storage.authorize:apt-public-repo@isle-core:' + d
          and auth['device'] == core and auth['dependsOn'] == [] and auth['execute'] is None)
    check('action id format', act['id'] == 'isle.storage.migrate:apt-public-repo@pol-core:' + d)
    check('action status default planned', act['status'] == 'planned' and act['execute'] is None and act['kind'] == 'isle.storage.migrate')
    check('migrate dependsOn authorize, device is target', act['dependsOn'] == [auth['id']] and act['device'] == 'pol-core')
    check('action status override', sc.planned_action(pid, 2, item(), d, rel, 'running', core_device=core)['status'] == 'running')
    refuses('action bad status refused', sc.planned_action, pid, 2, item(), d, rel, 'done', 'isle.storage.migrate', core)
    check('action summary text', act['summary'] == 'mirror 16.0 GiB (1234 files) of apt-public-repo onto pol-core filesystem 11112222… under isle/datasets/apt-public-repo, deploy isle-content-host, cut apt.isle over, verify from a neighbour; source kept')
    check('action operatorCommand', act['operatorCommand'] == 'sudo isle storage migrate --dataset apt-public-repo --plan %s --revision 2 --item apt-repo-to-pol-core --digest %s' % (pid, d))
    check('migrate undo emergency', act['undo'].startswith('sudo isle storage rollback --dataset apt-public-repo') and 'EMERGENCY ONLY' in act['undo'])
    refuses('action bad digest refused', sc.planned_action, pid, 2, item(), 'zz', rel, None, 'isle.storage.migrate', core)
    refuses('action digest of other tuple refused', sc.planned_action, pid, 2, item(), d, release(version='0'), None, 'isle.storage.migrate', core)
    refuses('action unknown kind refused', sc.planned_action, pid, 2, item(), d, rel, None, 'isle.storage.purge', core)
    refuses('retire-source kind refused', sc.planned_action, pid, 2, item(), d, rel, None, 'isle.storage.retire-source', core)
    check('KINDS are authorize, migrate, cutover', sc.KINDS == ('isle.storage.authorize', 'isle.storage.migrate', 'isle.storage.cutover'))
    for kind in ('isle.storage.authorize', 'isle.storage.migrate'):
        refuses(kind + ' without core_device refused', sc.planned_action, pid, 2, item(), d, rel, None, kind)
    mid = act['id']
    cut = sc.planned_action(pid, 2, item(), d, rel, kind='isle.storage.cutover', core_device=core, generation=3)
    check('cutover id and device', cut['id'] == 'isle.storage.cutover:apt-public-repo@isle-core:' + d and cut['device'] == core)
    check('cutover dependsOn migrate', cut['dependsOn'] == [mid] and cut['execute'] is None)
    check('cutover operatorCommand', cut['operatorCommand'] == 'sudo isle storage cutover --dataset apt-public-repo --generation 3')
    check('cutover undo emergency', 'EMERGENCY ONLY' in cut['undo'] and '--generation 3' in cut['undo'])
    refuses('cutover without core_device refused', sc.planned_action, pid, 2, item(), d, rel, None, 'isle.storage.cutover', None, 3)
    refuses('cutover without generation refused', sc.planned_action, pid, 2, item(), d, rel, None, 'isle.storage.cutover', core)
    refuses('cutover generation 0 refused', sc.planned_action, pid, 2, item(), d, rel, None, 'isle.storage.cutover', core, 0)
    check('action defaults placementState None, chain []', act['placementState'] is None and act['chain'] == [])
    chained = sc.planned_action(pid, 2, item(), d, rel, kind='isle.storage.cutover', core_device=core, generation=3,
                                placement_state='migrated', chain=[{'kind': 'isle.storage.witness', 'device': 'econ-core', 'status': 'planned', 'x': 1}])
    check('action placementState and chain', chained['placementState'] == 'migrated'
          and chained['chain'] == [{'kind': 'isle.storage.witness', 'device': 'econ-core', 'status': 'planned'}])
    refuses('chain step without device refused', sc.planned_action, pid, 2, item(), d, rel, None, 'isle.storage.migrate', core, None, None, [{'kind': 'k', 'status': 's'}])
    check('blocked status accepted', sc.planned_action(pid, 2, item(), d, rel, 'blocked', core_device=core)['status'] == 'blocked')
    check('every action has dependsOn list', all(isinstance(a['dependsOn'], list) for a in (auth, act, cut)))


def optional_source_checks():
    exp = VECTOR['expectedDigest']
    c, apps = 'ef' * 32, ['isle-content-host', 'apt2']

    def src(key, value):
        return mutated(['source', key], value)

    check('absent contentSha256/apps not emitted', 'contentSha256' not in sc.validate_storage_block(block())['source']
          and 'apps' not in sc.validate_storage_block(block())['source'])
    check('null contentSha256 and empty apps do not change digest',
          digest_of(item(storage=src('contentSha256', None))) == exp and digest_of(item(storage=src('apps', []))) == exp)
    check('contentSha256 emitted when set', sc.validate_storage_block(src('contentSha256', c))['source']['contentSha256'] == c)
    check('present contentSha256 changes digest', digest_of(item(storage=src('contentSha256', c))) != exp)
    check('apps emitted when set', sc.validate_storage_block(src('apps', apps))['source']['apps'] == apps)
    check('present apps changes digest', digest_of(item(storage=src('apps', apps))) != exp)
    check('8 apps accepted', len(sc.validate_storage_block(src('apps', ['a%d' % i for i in range(8)]))['source']['apps']) == 8)
    refuses('9 apps refused', sc.validate_storage_block, src('apps', ['a%d' % i for i in range(9)]))
    refuses('duplicate apps refused', sc.validate_storage_block, src('apps', ['a', 'a']))
    for bad in (['A'], ['-a'], ['a b'], [''], [1], ['a' * 65], 'abc', {'a': 1}):
        refuses('malformed apps %r refused' % (bad,), sc.validate_storage_block, src('apps', bad))
    refuses('uppercase contentSha256 refused', sc.validate_storage_block, src('contentSha256', 'AB' * 32))
    refuses('short contentSha256 refused', sc.validate_storage_block, src('contentSha256', 'ab'))
    refuses('non-string contentSha256 refused', sc.validate_storage_block, src('contentSha256', 5))


def backend_twin_checks():
    path = Path('/tmp/claude-1000/pol-stm2-wt/modules/islemesh/custom/islemesh_storage_plan.py')
    if not path.exists():
        print('PASS backend twin cross-check skipped (path absent)')
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location('islemesh_storage_plan', path)
    twin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(twin)
    full = copy.deepcopy(VECTOR['item'])
    full['hostingRelease'] = copy.deepcopy(VECTOR['hostingRelease'])
    variants = [('vector', {}),
                ('contentSha256+apps', {'contentSha256': 'ef' * 32, 'apps': ['isle-content-host', 'apt2']}),
                ('apps only', {'apps': ['apt2']})]
    for name, extra in variants:
        it = copy.deepcopy(full)
        it['storage']['source'].update(extra)
        cli = json.dumps(sc.storage_digest_payload(PLAN_ID, VECTOR['revision'], it, it['hostingRelease']), sort_keys=True, separators=(',', ':'), ensure_ascii=True)
        back = json.dumps(twin.storage_digest_payload(PLAN_ID, VECTOR['revision'], it), sort_keys=True, separators=(',', ':'), ensure_ascii=True)
        check('backend twin canonical payload byte-identical (%s)' % name, cli == back)
        check('backend twin validated block identical (%s)' % name, sc.validate_storage_block(it['storage']) == twin.validate_storage_block(it['storage']))
    check('backend twin digest equals vector', twin.storage_digest(PLAN_ID, VECTOR['revision'], full) == VECTOR['expectedDigest'])
    plan = {'id': PLAN_ID, 'revision': VECTOR['revision'], 'items': [full]}
    back_acts = twin.planned_actions(plan, 'isle-core')
    cli_acts = [sc.planned_action(PLAN_ID, VECTOR['revision'], full, VECTOR['expectedDigest'], full['hostingRelease'], 'blocked',
                                  kind=k, core_device='isle-core', generation=1)
                for k in ('isle.storage.authorize', 'isle.storage.migrate', 'isle.storage.cutover')]
    check('backend twin action kinds, ids, devices, dependsOn match',
          [(a['kind'], a['id'], a['device'], a['dependsOn']) for a in back_acts]
          == [(a['kind'], a['id'], a['device'], a['dependsOn']) for a in cli_acts])


def request_checks():
    d, pid = VECTOR['expectedDigest'], PLAN_ID
    argv = sc.migrate_request('apt-public-repo', pid, 2, 'apt-repo-to-pol-core', d)
    check('migrate argv exact', argv == ['storage', 'migrate', '--dataset', 'apt-public-repo', '--plan', pid, '--revision', '2', '--item', 'apt-repo-to-pol-core', '--digest', d])
    parsed = sc.parse_request(argv)
    check('migrate round trip', parsed == {'verb': 'migrate', 'dataset': 'apt-public-repo', 'plan': pid, 'revision': 2, 'item': 'apt-repo-to-pol-core', 'digest': d})
    check('sync round trip', sc.parse_request(['storage', 'sync', '--dataset', 'apt-public-repo']) == {'verb': 'sync', 'dataset': 'apt-public-repo'})
    check('sync_request exact', sc.sync_request('apt-public-repo') == ['storage', 'sync', '--dataset', 'apt-public-repo'])
    for verb, build in (('cutover', sc.cutover_request), ('rollback', sc.rollback_request)):
        a = build('apt-public-repo', 4)
        check(verb + ' argv exact', a == ['storage', verb, '--dataset', 'apt-public-repo', '--generation', '4'])
        check(verb + ' round trip', sc.parse_request(a) == {'verb': verb, 'dataset': 'apt-public-repo', 'generation': 4})
        refuses(verb + ' generation 0 refused', sc.parse_request, ['storage', verb, '--dataset', 'a', '--generation', '0'])
        refuses(verb + ' generation negative refused', sc.parse_request, ['storage', verb, '--dataset', 'a', '--generation', '-1'])
        refuses(verb + ' generation non-decimal refused', sc.parse_request, ['storage', verb, '--dataset', 'a', '--generation', '1.5'])
        refuses(verb + ' without generation refused', sc.parse_request, ['storage', verb, '--dataset', 'a'])
        refuses(verb + ' reordered flags refused', sc.parse_request, ['storage', verb, '--generation', '1', '--dataset', 'a'])
        refuses(verb + ' builder generation 0 refused', build, 'a', 0)
    refuses('old migrate form without dataset refused', sc.parse_request, ['storage', 'migrate', '--plan', pid, '--revision', '2', '--item', 'i', '--digest', d])
    lock, journal = sc.lock_name(parsed), sc.journal_name(parsed)
    check('lock name', lock == 'app-storage-apt-public-repo.lock')
    check('journal name', journal == 'storage-apt-public-repo')
    check('names agree across verbs', all(sc.lock_name(sc.parse_request(a)) == lock and sc.journal_name(sc.parse_request(a)) == journal
                                          for a in (sc.cutover_request('apt-public-repo', 1), sc.rollback_request('apt-public-repo', 1), sc.sync_request('apt-public-repo'))))
    refuses('lock name without dataset refused', sc.lock_name, {'verb': 'sync'})
    refuses('journal name with bad dataset refused', sc.journal_name, {'dataset': '../x'})
    bad_hex = list(argv)
    bad_hex[-1] = 'g' * 64
    refuses('parse refuses bad digest hex', sc.parse_request, bad_hex)
    refuses('parse refuses missing flag', sc.parse_request, argv[:4] + argv[6:])
    refuses('parse refuses extra arg', sc.parse_request, argv + ['--x'])
    neg = list(argv)
    neg[7] = '-1'
    refuses('parse refuses negative revision', sc.parse_request, neg)
    swapped = list(argv)
    swapped[4], swapped[6] = swapped[6], swapped[4]
    refuses('parse refuses reordered flags', sc.parse_request, swapped)
    refuses('parse refuses sync extra arg', sc.parse_request, ['storage', 'sync', '--dataset', 'a', 'b'])
    refuses('parse refuses bad dataset', sc.parse_request, ['storage', 'rollback', '--dataset', 'Bad', '--generation', '1'])
    refuses('parse refuses unknown verb', sc.parse_request, ['storage', 'purge'])
    refuses('parse refuses non-list', sc.parse_request, 'storage migrate')


def shape_checks():
    check('STAGES count 8', len(sc.STAGES) == 8 and len(set(sc.STAGES)) == 8)
    check('STAGES match label regex', all(sc.LABEL.match(s) for s in sc.STAGES))
    check('STAGES order', sc.STAGES[0] == 'fetching-plan' and sc.STAGES[-1] == 'verifying-readiness')
    check('CUTOVER_STAGES', sc.CUTOVER_STAGES == ('checking-placement', 'registering-target', 'verifying-cutover', 'reverting', 'recovery-needed')
          and all(sc.LABEL.match(s) for s in sc.CUTOVER_STAGES) and not set(sc.CUTOVER_STAGES) & set(sc.STAGES))
    plan = {'id': VECTOR['planId'], 'revision': 2, 'itemId': 'i', 'digest': VECTOR['expectedDigest']}
    r = sc.receipt_skeleton('isle.storage.migrate', 'a' * 32, 1, plan, 'apt-public-repo', 'apt.isle')
    keys = {'kind', 'schemaVersion', 'phase', 'hostingRelease', 'stagedDeploy', 'operation', 'attempt', 'at', 'plan', 'dataset', 'domain', 'target', 'source', 'stages',
            'contentVerified', 'hostDeployed', 'readinessVerified', 'sourceRemovable',
            'mountsBefore', 'mountsAfter', 'words', 'actionId', 'filesystemUuidObserved', 'mountSourceObserved',
            'digestRecomputed', 'generation'}
    check('receipt has exactly the expected keys', set(r) == keys)
    check('receipt defaults', r['stages'] == {} and r['sourceRemovable'] is False
          and not (r['contentVerified'] or r['hostDeployed'] or r['readinessVerified'] or r['stagedDeploy'])
          and r['phase'] == 'A' and r['hostingRelease'] is None
          and r['mountsBefore'] == [] and r['mountsAfter'] == []
          and r['actionId'] is None and r['filesystemUuidObserved'] is None and r['mountSourceObserved'] is None
          and r['digestRecomputed'] is False and r['generation'] is None)
    check('migrate receipt has no cutover/neighbourCheck', 'cutover' not in r and 'neighbourCheck' not in r)
    for kind in ('isle.storage.cutover', 'isle.storage.rollback'):
        rc = sc.receipt_skeleton(kind, 'a' * 32, 1, plan, 'd', 'x', release())
        check(kind + ' receipt keeps cutover block', rc['neighbourCheck'] == 'pending'
              and rc['cutover'] == {'done': False, 'at': None, 'previousAddress': None, 'rolledBack': False})
    check('receipt carries hostingRelease', sc.receipt_skeleton('isle.storage.migrate', 'a' * 32, 1, plan, 'd', 'x', release())['hostingRelease'] == release())
    check('witness receipt kind', 'isle.storage.witness' in sc.RECEIPT_KINDS)
    check('skeleton schemaVersion defaults to 2', r['schemaVersion'] == 2)
    check('binding record carries plan.itemId only when given',
          sc.binding_record(block(), 'ab' * 32, plan, 'h', 't', item_id='i')['plan'] == {'id': plan['id'], 'revision': 2, 'digest': plan['digest'], 'itemId': 'i'})
    refuses('binding refuses a blank itemId', sc.binding_record, block(), 'ab' * 32, plan, 'h', 't', None, '')
    emitted = set()
    for name in ('storage_core', 'storage_migrate', 'storage_sync', 'storage_witness'):
        emitted |= set(re.findall(r"isle\.storage\.([a-z-]+)", (HERE / (name + '.py')).read_text()))
    emitted -= {'sh'}
    kinds = {k.split('.')[-1] for k in sc.RECEIPT_KINDS}
    check('RECEIPT_KINDS covers every kind the drivers emit',
          {'migrate', 'sync', 'cutover', 'rollback', 'recover', 'witness', 'accept', 'authorize', 'mark-migrated',
           'reconcile-address'} <= kinds and emitted <= kinds and kinds <= emitted | {'sync'})
    def labels(name, pattern):
        text = (HERE / (name + '.py')).read_text()
        found = set()
        for call in re.findall(pattern, text):
            found |= set(re.findall(r"'([a-z0-9-]+)'", re.sub(r" if .*? else ", ' ', call)))  # drop ternary conditions
        return found
    core = labels('storage_core', r"ctx\.stage\(([^\n]*)\)")
    check('CORE_STAGES covers every ctx.stage label of storage_core', core and core <= set(sc.CORE_STAGES) and set(sc.CORE_STAGES) <= core)
    witness = labels('storage_witness', r"self\.begin\(([^\n]*)\)")
    check('WITNESS_STAGES covers every begin label of storage_witness', witness and witness <= set(sc.WITNESS_STAGES) and set(sc.WITNESS_STAGES) <= witness)
    check('migrate driver labels within STAGES', labels('storage_migrate', r"self\.begin\(([^\n]*)\)") <= set(sc.STAGES))
    check('sync driver labels within SYNC_STAGES', labels('storage_sync', r"self\.begin\(([^\n]*)\)") <= set(sc.SYNC_STAGES))
    check('all new stage labels match the label regex', all(sc.LABEL.match(x) for x in sc.CORE_STAGES + sc.WITNESS_STAGES))
    check('blocked is a status', 'blocked' in sc.STATUSES)
    refuses('receipt bad kind refused', sc.receipt_skeleton, 'other', 'a' * 32, 1, plan, 'd', 'x')
    refuses('receipt attempt 0 refused', sc.receipt_skeleton, 'isle.storage.sync', 'a' * 32, 0, plan, 'd', 'x')
    rec = sc.binding_record(block(), 'ab' * 32, plan, 'isle-content-host', '2026-11-09T12:00:00+00:00')
    check('binding record shape', rec['schemaVersion'] == 2 and rec['currentGeneration'] == 'ab' * 32 and set(rec) == {'schemaVersion', 'currentGeneration', 'dataset', 'filesystemUuid', 'relativePath', 'snapshotSha256', 'boundAt', 'plan', 'hostApp'}
          and rec['plan'] == {'id': plan['id'], 'revision': 2, 'digest': plan['digest']} and rec['filesystemUuid'] == block()['target']['filesystemUuid'])
    refuses('binding refuses bad sha', sc.binding_record, block(), 'nope', plan, 'h', 't')
    check('binding explicit generation', sc.binding_record(block(), 'ab' * 32, plan, 'h', 't', 'cd' * 32)['currentGeneration'] == 'cd' * 32)
    refuses('binding refuses bad generation', sc.binding_record, block(), 'ab' * 32, plan, 'h', 't', 'zz')
    check('fixture is plain JSON', json.loads((HERE / 'fixtures' / 'storage-digest-vector.json').read_text()) == VECTOR)


for group in (vector_and_digest_changes, block_constraints, item_checks, action_checks, optional_source_checks, backend_twin_checks, request_checks, shape_checks):
    group()
print('%d failed' % len(FAILED) if FAILED else 'all checks passed')
sys.exit(1 if FAILED else 0)
