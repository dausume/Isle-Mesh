"""Typed storage-migration contract (stm-1): validation, digest and request shapes.
Pure functions only; nothing here touches disks, the network or the journal.
See docs/STORAGE-MIGRATION-CONTRACT.md (v2 decisions D4-D6).
The backend carries the same digest function; both check one shared test vector.
"""
import hashlib
import json
import re

DATASET = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')
DOMAIN = re.compile(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.){1,7}isle\Z')
HEX64 = re.compile(r'[a-f0-9]{64}\Z')
ITEM_ID = re.compile(r'[a-zA-Z0-9_.-]{1,80}\Z')
PLAN_ID = re.compile(r'[a-f0-9]{32}\Z')
APP_NAME = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')
LABEL = re.compile(r'^[a-z0-9-]{1,80}$')
DECIMAL = re.compile(r'[0-9]{1,12}\Z')

# The eight stages of the migrate operation on the target; cutover runs on the core.
STAGES = ('fetching-plan', 'preflight-storage', 'fetching-manifest', 'receiving-content',
          'verifying-content', 'writing-binding', 'deploying-host', 'verifying-readiness')
SYNC_STAGES = ('checking-placement', 'fetching-latest', 'fetching-manifest', 'receiving-content',
               'verifying-content', 'promoting')
CUTOVER_STAGES = ('checking-placement', 'registering-target', 'verifying-cutover', 'reverting',
                  'recovery-needed')
# Every label storage_core.py passes to ctx.stage(): the cutover verbs plus authorize, mark-migrated, recover, accept, reconcile-address.
CORE_STAGES = ('fetching-plan', 'checking-ownership', 'writing-placement', 'checking-placement', 'pushing-managed-names',
               'marking-migrated',
               'writing-intent', 'moving-pin', 'registering-target', 'verifying-cutover', 'committing', 'reverting',
               'recovery-needed', 'verifying-recovery', 'accepting')
WITNESS_STAGES = ('checking-placement', 'fetching-control', 'sampling-content', 'recording-witness')
ITEM_STATES = ('planned', 'existing', 'deferred')
KINDS = ('isle.storage.authorize', 'isle.storage.migrate', 'isle.storage.cutover')
VERBS = ('authorize', 'migrate', 'mark-migrated', 'cutover', 'rollback', 'recover', 'accept',
         'reconcile-address', 'sync', 'witness', 'units-apply')
UNIT_OVERRIDES = ('keep', 'remove')
RECOVER_TARGETS = ('migrated', 'cutover', 'hosting')
STATUSES = ('planned', 'blocked', 'running', 'succeeded', 'failed', 'interrupted')
RECEIPT_KINDS = ('isle.storage.migrate', 'isle.storage.sync', 'isle.storage.cutover', 'isle.storage.rollback',
                 'isle.storage.recover', 'isle.storage.witness', 'isle.storage.accept', 'isle.storage.authorize',
                 'isle.storage.mark-migrated', 'isle.storage.reconcile-address')
CUTOVER_RECEIPT_KINDS = ('isle.storage.cutover', 'isle.storage.rollback')
HEADROOM_PERCENT = 5
KILOBYTE = 1024


def _int(value, name, minimum=0):
    """Return value when it is a real integer (not a bool) at least minimum."""
    if type(value) is not int or value < minimum:
        raise ValueError('%s must be an integer of at least %d.' % (name, minimum))
    return value


def _text(value, name, limit, spaces=False):
    """Return value when it is a non-empty string within limit and without NUL."""
    if not isinstance(value, str) or not value or len(value) > limit or '\x00' in value:
        raise ValueError('%s must be a non-empty string of at most %d characters.' % (name, limit))
    if not spaces and re.search(r'\s', value):
        raise ValueError('%s must not contain whitespace.' % name)
    return value


def _domain(value):
    """Validate the served name: a strict DNS hostname of 2-8 labels ending in .isle, at most 253."""
    if not isinstance(value, str) or len(value) > 253 or not DOMAIN.match(value):
        raise ValueError('storage.domain must be a DNS hostname of 2-8 labels ending in .isle (at most 253 characters).')
    return value


def required_capacity(total_bytes):
    """Return ceil(total_bytes * 1.05) using integer arithmetic only."""
    return (total_bytes * (100 + HEADROOM_PERCENT) + 99) // 100


def _mapping(value, name):
    """Return value when it is a dict, otherwise raise."""
    if not isinstance(value, dict):
        raise ValueError('%s must be an object.' % name)
    return value


def _relative_path(value):
    """Validate the target relative path: relative, no parent traversal, at most 200."""
    _text(value, 'storage.target.relativePath', 200, spaces=True)
    if value.startswith('/'):
        raise ValueError('storage.target.relativePath must be relative and must not start with a slash.')
    if '..' in value.split('/'):
        raise ValueError('storage.target.relativePath must not contain parent traversal (..).')
    return value


def _hex_or_none(value, name):
    """Return value when it is 64 lowercase hex characters or None."""
    if value is not None and not (isinstance(value, str) and HEX64.match(value)):
        raise ValueError('%s must be 64 lowercase hex characters or null.' % name)
    return value


def _source(raw):
    """Rebuild the source sub-block from the whitelist."""
    raw = _mapping(raw, 'storage.source')
    sha = _hex_or_none(raw.get('snapshotSha256'), 'storage.source.snapshotSha256')
    taken = raw.get('snapshotTakenAt')
    if taken is not None:
        _text(taken, 'storage.source.snapshotTakenAt', 64)
    out = {'device': _text(raw.get('device'), 'storage.source.device', 100),
           'path': _text(raw.get('path'), 'storage.source.path', 200, spaces=True),
           'snapshotSha256': sha, 'snapshotTakenAt': taken,
           'totalBytes': _int(raw.get('totalBytes'), 'storage.source.totalBytes'),
           'entries': _int(raw.get('entries'), 'storage.source.entries')}
    # Optional keys appear only when set, so an absent key hashes like the older whitelist.
    content = _hex_or_none(raw.get('contentSha256'), 'storage.source.contentSha256')
    if content is not None:
        out['contentSha256'] = content
    apps = raw.get('apps', [])
    if (not isinstance(apps, list) or len(apps) > 8 or len(set(apps if all(isinstance(a, str) for a in apps) else [])) != len(apps)
            or any(not isinstance(a, str) or not APP_NAME.match(a) for a in apps)):
        raise ValueError('storage.source.apps must be a list of at most 8 unique names matching [a-z0-9][a-z0-9-]{0,63}.')
    if apps:
        out['apps'] = list(apps)
    return out


def _target(raw):
    """Rebuild the target sub-block from the whitelist."""
    raw = _mapping(raw, 'storage.target')
    mount = _text(raw.get('mountpoint'), 'storage.target.mountpoint', 200, spaces=True)
    if not mount.startswith('/') or '..' in mount.split('/'):
        raise ValueError('storage.target.mountpoint must be absolute without parent traversal.')
    return {'device': _text(raw.get('device'), 'storage.target.device', 100),
            'filesystemUuid': _text(raw.get('filesystemUuid'), 'storage.target.filesystemUuid', 200),
            'relativePath': _relative_path(raw.get('relativePath')),
            'capacityBytes': _int(raw.get('capacityBytes'), 'storage.target.capacityBytes'),
            'mountpoint': mount}


def validate_storage_block(block):
    """Return a new, whitelisted copy of a storage block or raise ValueError.
    Unknown keys at every level are silently dropped, never passed through."""
    block = _mapping(block, 'storage')
    if block.get('schemaVersion') != 1 or type(block.get('schemaVersion')) is not int:
        raise ValueError('storage.schemaVersion must be 1.')
    dataset = block.get('dataset')
    if not isinstance(dataset, str) or not DATASET.match(dataset):
        raise ValueError('storage.dataset must match [a-z0-9][a-z0-9-]{0,63}.')
    out = {'schemaVersion': 1, 'dataset': dataset,
           'domain': _domain(block.get('domain')),
           'source': _source(block.get('source')), 'target': _target(block.get('target'))}
    if out['target']['capacityBytes'] < required_capacity(out['source']['totalBytes']):
        raise ValueError('storage.target.capacityBytes must be at least storage.source.totalBytes plus 5 percent.')
    if block.get('sourceRemoval') != 'dependent':
        raise ValueError('storage.sourceRemoval must be "dependent" in version 1.')
    out['sourceRemoval'] = 'dependent'
    return out


def validate_storage_item(item):
    """Validate a plan item of kind storage and return its validated storage block."""
    item = _mapping(item, 'item')
    if item.get('kind') != 'storage':
        raise ValueError('Plan item kind must be "storage".')
    _text(item.get('app'), 'item.app', 100)
    if item.get('state') not in ITEM_STATES:
        raise ValueError('Plan item state must be one of %s.' % ', '.join(ITEM_STATES))
    block = validate_storage_block(item.get('storage'))
    if item.get('device') != block['target']['device']:
        raise ValueError('Plan item device must equal storage.target.device.')
    if item.get('originDevice') != block['source']['device']:
        raise ValueError('Plan item originDevice must equal storage.source.device.')
    return block


def _check_ids(plan_id, revision, item_id):
    """Validate the plan id, revision and item id shared by digest and requests."""
    if not isinstance(plan_id, str) or not PLAN_ID.match(plan_id):
        raise ValueError('Plan id must be 32 lowercase hex characters.')
    _int(revision, 'Revision')
    if not isinstance(item_id, str) or not ITEM_ID.match(item_id):
        raise ValueError('Item id must match [a-zA-Z0-9_.-]{1,80}.')


def validate_hosting_release(release):
    """Return a rebuilt {name, version, manifestSha256} or raise ValueError."""
    release = _mapping(release, 'hostingRelease')
    sha = release.get('manifestSha256')
    if not isinstance(sha, str) or not HEX64.match(sha):
        raise ValueError('hostingRelease.manifestSha256 must be 64 lowercase hex characters.')
    return {'name': _text(release.get('name'), 'hostingRelease.name', 100),
            'version': _text(release.get('version'), 'hostingRelease.version', 100),
            'manifestSha256': sha}


def storage_digest_payload(plan_id, revision, item, hosting_release):
    """Return the D6 execution tuple as a dict (the exact object that is hashed)."""
    item = _mapping(item, 'item')
    block = validate_storage_item(item)  # also checks kind, app, state, device agreement
    _check_ids(plan_id, revision, item.get('id'))
    # Hash only whitelisted values, so an unknown key can never make two honest
    # implementations (backend, CLI) disagree about the same plan.
    return {'planId': plan_id, 'revision': revision, 'itemId': item['id'],
            'device': item['device'], 'originDevice': item['originDevice'],
            'app': item['app'], 'state': item['state'], 'storage': block,
            'hostingRelease': validate_hosting_release(hosting_release)}


def storage_digest(plan_id, revision, item, hosting_release):
    """Return the D6 digest: sha256 of the canonical JSON execution tuple."""
    payload = storage_digest_payload(plan_id, revision, item, hosting_release)
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _summary(item, block):
    """Build the one-sentence description of what the migration will do."""
    src, tgt = block['source'], block['target']
    gib = src['totalBytes'] / KILOBYTE ** 3
    return ('mirror %.1f GiB (%d files) of %s onto %s filesystem %s… under %s, deploy %s, '
            'cut %s over, verify from a neighbour; source kept'
            % (gib, src['entries'], block['dataset'], tgt['device'], tgt['filesystemUuid'][:8],
               tgt['relativePath'], item.get('app'), block['domain']))


def _chain(chain):
    """Return the display chain rebuilt from its whitelist: a list of {kind, device, status}."""
    if chain is None:
        return []
    if not isinstance(chain, (list, tuple)):
        raise ValueError('chain must be a list.')
    return [{'kind': _text(_mapping(step, 'chain step').get('kind'), 'chain.kind', 100),
             'device': _text(step.get('device'), 'chain.device', 100),
             'status': _text(step.get('status'), 'chain.status', 100)} for step in chain]


def action_id(verb, dataset, device, digest):
    """Return the action id 'isle.storage.<verb>:<dataset>@<device>:<digest>'."""
    return 'isle.storage.%s:%s@%s:%s' % (verb, dataset, device, digest)


def planned_action(plan_id, revision, item, digest, hosting_release, journal_state=None,
                   kind='isle.storage.migrate', core_device=None, generation=None,
                   placement_state=None, chain=None):
    """Build the plannedAction of one of the three kinds for a validated storage item.
    The chain mirrors the backend: authorize (on core_device) -> migrate (on the target) -> cutover (on core_device).
    core_device is required for every kind; generation (decimal >= 1, the placement generation) is
    required for cutover. placement_state is shown beside the status; chain is a list of display-only
    {kind, device, status} steps and never grants approval. retire-source is out of Phase A."""
    if not isinstance(digest, str) or not HEX64.match(digest):
        raise ValueError('Digest must be 64 lowercase hex characters.')
    if digest != storage_digest(plan_id, revision, item, hosting_release):
        raise ValueError('Digest does not match the execution tuple.')
    if kind not in KINDS:
        raise ValueError('Unknown action kind: %r.' % (kind,))
    status = journal_state or 'planned'
    if status not in STATUSES:
        raise ValueError('Unknown journal state: %r.' % (status,))
    block = validate_storage_item(item)
    target, dataset = block['target']['device'], block['dataset']
    core = _text(core_device, 'core_device', 100)
    authorize_id = action_id('authorize', dataset, core, digest)
    migrate_id = action_id('migrate', dataset, target, digest)
    cutover_id = action_id('cutover', dataset, core, digest)
    base = {'kind': kind, 'dataset': dataset, 'planId': plan_id, 'revision': revision,
            'itemId': item['id'], 'digest': digest, 'status': status, 'execute': None,
            'placementState': placement_state, 'chain': _chain(chain)}
    if kind == 'isle.storage.authorize':
        base.update(id=authorize_id, device=core, dependsOn=[],
                    summary='authorize moving %s onto %s: the core records the approved plan digest'
                            % (dataset, target),
                    operatorCommand=None, undo=None)
        return base
    if kind == 'isle.storage.migrate':
        base.update(id=migrate_id, device=target, dependsOn=[authorize_id], summary=_summary(item, block),
                    operatorCommand='sudo isle ' + ' '.join(migrate_request(dataset, plan_id, revision, item['id'], digest)),
                    undo='sudo isle storage rollback --dataset %s --generation <G>   (EMERGENCY ONLY; '
                         'repoints the domain to the source; the source was never changed)' % dataset)
        return base
    gen = _int(generation, 'Generation', 1)
    base.update(id=cutover_id, device=core, dependsOn=[migrate_id],
                summary='cut %s over to %s: point the domain at the replica, verify, keep the source for rollback'
                        % (block['domain'], target),
                operatorCommand='sudo isle ' + ' '.join(cutover_request(dataset, gen)),
                undo='sudo isle storage rollback --dataset %s --generation %d   (EMERGENCY ONLY; '
                     'repoints the domain to the source; the source was never changed)' % (dataset, gen))
    return base


def migrate_request(dataset, plan_id, revision, item_id, digest):
    """Return the exact D5 argv for a migrate request (runs on the target)."""
    if not isinstance(dataset, str) or not DATASET.match(dataset):
        raise ValueError('Dataset must match [a-z0-9][a-z0-9-]{0,63}.')
    _check_ids(plan_id, revision, item_id)
    if not isinstance(digest, str) or not HEX64.match(digest):
        raise ValueError('Digest must be 64 lowercase hex characters.')
    return ['storage', 'migrate', '--dataset', dataset, '--plan', plan_id, '--revision', str(revision),
            '--item', item_id, '--digest', digest]


def _generation_request(verb, dataset, generation):
    if not isinstance(dataset, str) or not DATASET.match(dataset):
        raise ValueError('Dataset must match [a-z0-9][a-z0-9-]{0,63}.')
    return ['storage', verb, '--dataset', dataset, '--generation', str(_int(generation, 'Generation', 1))]


def cutover_request(dataset, generation):
    """Return the argv for a cutover request (runs on the core)."""
    return _generation_request('cutover', dataset, generation)


def rollback_request(dataset, generation):
    """Return the argv for a rollback request (runs on the core)."""
    return _generation_request('rollback', dataset, generation)


def sync_request(dataset):
    """Return the argv for a sync request (runs on the target)."""
    if not isinstance(dataset, str) or not DATASET.match(dataset):
        raise ValueError('Dataset must match [a-z0-9][a-z0-9-]{0,63}.')
    return ['storage', 'sync', '--dataset', dataset]


def parse_request(args):
    """Parse a storage argv (any section 2.1 verb) into a typed dict or raise ValueError."""
    if not isinstance(args, (list, tuple)) or not all(isinstance(a, str) for a in args):
        raise ValueError('Storage request must be a list of strings.')
    args = list(args)
    if len(args) < 2 or args[0] != 'storage' or args[1] not in VERBS:
        raise ValueError('Unknown storage request.')
    verb = args[1]
    if verb == 'units-apply':  # local to the hosting device: rewrites the dataset's systemd units, no receipt
        if len(args) not in (4, 6) or args[2] != '--dataset' or (len(args) == 6 and args[4] != '--overrides'):
            raise ValueError('Expected: storage units-apply --dataset <v> [--overrides keep|remove].')
        if not DATASET.match(args[3]):
            raise ValueError('Dataset must match [a-z0-9][a-z0-9-]{0,63}.')
        if len(args) == 6 and args[5] not in UNIT_OVERRIDES:
            raise ValueError('Overrides must be one of: ' + ', '.join(UNIT_OVERRIDES) + '.')
        return {'verb': verb, 'dataset': args[3], 'overrides': args[5] if len(args) == 6 else 'keep'}
    flags = {'migrate': ('--dataset', '--plan', '--revision', '--item', '--digest'),
             'authorize': ('--dataset', '--plan', '--revision', '--item', '--digest'),
             'mark-migrated': ('--dataset', '--digest'), 'recover': ('--dataset', '--generation', '--to'),
             'cutover': ('--dataset', '--generation'), 'rollback': ('--dataset', '--generation'),
             'accept': ('--dataset', '--generation'), 'reconcile-address': ('--dataset', '--generation'),
             'witness': ('--dataset', '--generation'), 'sync': ('--dataset',)}[verb]
    if len(args) != 2 + 2 * len(flags) or tuple(args[2::2]) != flags:
        raise ValueError('Expected: storage %s %s.' % (verb, ' '.join(f + ' <v>' for f in flags)))
    values = args[3::2]
    if not DATASET.match(values[0]):
        raise ValueError('Dataset must match [a-z0-9][a-z0-9-]{0,63}.')
    if verb == 'sync':
        return {'verb': verb, 'dataset': values[0]}
    if verb == 'mark-migrated':
        if not HEX64.match(values[1]):
            raise ValueError('Digest must be 64 lowercase hex characters.')
        return {'verb': verb, 'dataset': values[0], 'digest': values[1]}
    if verb in ('cutover', 'rollback', 'recover', 'accept', 'reconcile-address', 'witness'):
        if not DECIMAL.match(values[1]) or int(values[1]) < 1:
            raise ValueError('Generation must be a decimal integer of at least 1.')
        parsed = {'verb': verb, 'dataset': values[0], 'generation': int(values[1])}
        if verb == 'recover':
            if values[2] not in RECOVER_TARGETS:
                raise ValueError('Recover target must be one of: ' + ', '.join(RECOVER_TARGETS) + '.')
            parsed['to'] = values[2]
        return parsed
    dataset, plan_id, rev, item_id, digest = values
    if not DECIMAL.match(rev):
        raise ValueError('Revision must be a non-negative decimal integer.')
    if not HEX64.match(digest):
        raise ValueError('Digest must be 64 lowercase hex characters.')
    _check_ids(plan_id, int(rev), item_id)
    return {'verb': verb, 'dataset': dataset, 'plan': plan_id, 'revision': int(rev),
            'item': item_id, 'digest': digest}


def lock_name(parsed):
    """Return the operation lock file name 'app-storage-<dataset>.lock' for a parsed request."""
    return 'app-storage-%s.lock' % _parsed_dataset(parsed)


def journal_name(parsed):
    """Return the journal name 'storage-<dataset>' for a parsed request."""
    return 'storage-%s' % _parsed_dataset(parsed)


def _parsed_dataset(parsed):
    dataset = parsed.get('dataset') if isinstance(parsed, dict) else None
    if not isinstance(dataset, str) or not DATASET.match(dataset):
        raise ValueError('Parsed request has no valid dataset.')
    return dataset


def receipt_skeleton(kind, operation_id, attempt, plan, dataset, domain, hosting_release=None):
    """Return a section 2.6 receipt with every key present and nothing yet proven.
    The cutover and neighbourCheck keys exist only for cutover and rollback receipts."""
    if kind not in RECEIPT_KINDS:
        raise ValueError('Unknown receipt kind: %r.' % (kind,))
    _int(attempt, 'Attempt', 1)
    receipt = {'kind': kind, 'schemaVersion': 2, 'phase': 'A', 'operation': operation_id, 'attempt': attempt,
            'at': None, 'plan': dict(plan),
            'hostingRelease': None if hosting_release is None else validate_hosting_release(hosting_release), 'dataset': dataset, 'domain': domain,
            'target': {'device': None, 'filesystemUuid': None, 'mount': None, 'stDev': None,
                       'path': None, 'availableBytesBefore': 0, 'availableBytesAfter': 0},
            'source': {'device': None, 'snapshotSha256': None, 'totalBytes': 0, 'entries': 0,
                       'previousAddress': None},
            'stages': {}, 'contentVerified': False, 'hostDeployed': False, 'stagedDeploy': False,
            'readinessVerified': False, 'sourceRemovable': False,
            'mountsBefore': [], 'mountsAfter': [], 'words': '',
            'actionId': None, 'filesystemUuidObserved': None, 'mountSourceObserved': None,
            'digestRecomputed': False, 'generation': None}
    if kind in CUTOVER_RECEIPT_KINDS:
        receipt['cutover'] = {'done': False, 'at': None, 'previousAddress': None, 'rolledBack': False}
        receipt['neighbourCheck'] = 'pending'
    return receipt


def binding_record(block, snapshot_sha, plan, host_app, bound_at, current_generation=None, item_id=None):
    """Return the section 3 binding.json record proving a directory is the dataset's home.
    current_generation (the manifest sha of the promoted generation) defaults to snapshot_sha.
    item_id, when given, is carried as plan.itemId (the receipt door needs it for sync receipts)."""
    block = validate_storage_block(block)
    if not isinstance(snapshot_sha, str) or not HEX64.match(snapshot_sha):
        raise ValueError('Snapshot sha256 must be 64 lowercase hex characters.')
    generation = snapshot_sha if current_generation is None else current_generation
    if not isinstance(generation, str) or not HEX64.match(generation):
        raise ValueError('Current generation must be 64 lowercase hex characters.')
    plan_block = {'id': plan['id'], 'revision': plan['revision'], 'digest': plan['digest']}
    if item_id is not None:
        plan_block['itemId'] = _text(item_id, 'itemId', 80)
    return {'schemaVersion': 2, 'dataset': block['dataset'], 'currentGeneration': generation,
            'filesystemUuid': block['target']['filesystemUuid'],
            'relativePath': block['target']['relativePath'], 'snapshotSha256': snapshot_sha,
            'boundAt': bound_at,
            'plan': plan_block,
            'hostApp': host_app}


def load_vector(path):
    """Load the shared digest test vector from a JSON file (used by tests only)."""
    with open(path) as handle:
        return json.load(handle)
