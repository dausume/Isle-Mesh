#!/usr/bin/env python3
"""isle backup: device-backups of this machine.

A device-backup is one folder <home>/<device>/<id>/ with one archive per item and device-backup.json.

  isle backup plan [--json]          read-only: what one would hold, what is left out and why, where it would go
  sudo isle backup create --yes      journaled: write one, re-read every file, then keep two (release, long-term)
  sudo isle backup list [--json]     the device-backups of this machine: slot, time, complete or not
  sudo isle backup verify <id>       re-read every file of one against its device-backup.json
  sudo isle backup release-hold      lower a write gate that an interrupted device-backup left up
  sudo isle backup restore <id> --drill
                                     unpack one into a scratch folder beside it, compare every file with its
                                     archive, open every database in an instance's data, remove the scratch
                                     folder.  Nothing of the machine is changed; the result is kept beside the
                                     device-backup (drill.json).
  sudo isle backup restore <id> --apply [--yes] [--item <name>]...
                                     put the data of the Polari instances back from one that passed a drill
                                     (device_backup_apply.py).  Without --yes it says what it would do.
  sudo isle backup before list [--json]
  sudo isle backup before drop <item> --yes
                                     the data an instance had before a restore, kept beside its data; removing it
  sudo isle backup before undo --yes
                                     put a restore that did not finish back as it was (the next --apply --yes
                                     does the same before anything else)

Home: the partition this machine records with the role `backup` (isle storage partition list).  A machine that
records no such partition keeps them in its state folder.
Two are kept per device.  `release` is the newest.  When a new one is complete, the previous one becomes
`long-term` if the long-term one is 7 days old or older (or there is none); otherwise the previous one is deleted.
Nothing is encrypted, nothing is sent anywhere, and nothing is deleted before the new one has been re-read.
Also a step of `isle release run` (step kind "backup").
Exit codes: 0 done, 1 refused or failed, 2 usage.
"""
import fcntl, hashlib, json, os, re, shutil, subprocess, sys, tempfile, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import operations
import device_backup_drill as drill
import device_backup_items as items_module
from device_backup_items import FOLDER, Gate, HOLD_PREFIX, ItemFailed

KIND = 'isle.device-backup'
MANIFEST = 'device-backup.json'
DRILL, DRILL_KIND, SCRATCH = 'drill.json', 'isle.device-backup-drill', 'drill-'
SLOTS = ('release', 'long-term')
LONG_TERM_AGE = timedelta(days=7)
ID = re.compile(r'[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\Z')
MiB = 1024 * 1024
PACKAGES = ('isle-mesh-cli', 'isle-app-store', 'polari-complete-offline')
USAGE = 'Usage: isle backup plan [--json] | sudo isle backup create --yes | sudo isle backup list [--json]\n' \
        '       sudo isle backup verify <id> | sudo isle backup release-hold | sudo isle backup restore <id> --drill\n' \
        '       sudo isle backup restore <id> --apply [--yes] [--item <name>]... | sudo isle backup before list [--json]\n' \
        '       sudo isle backup before drop <item> --yes | sudo isle backup before undo --yes'


class Refused(Exception):
    """Nothing was written."""


class Deps:
    """Everything that touches this machine; a test replaces it."""
    settings = Path('/etc/isle-mesh')
    state = Path('/var/lib/isle-mesh')
    polari_dir = Path('/etc/isle-mesh/polari')
    lock_file = operations.ROOT / 'app-device-backup.lock'
    hold_limit = 1800       # seconds an instance may take to flush before the item fails
    poll_interval = 5
    reserve = 256 * MiB     # kept free on the home beyond twice the size of what is copied
    stop_grace = 90         # seconds a container gets to stop before a restore replaces its data
    start_limit = 300       # seconds an instance may take to answer after a restore started it

    def say(self, line=''): print(line, flush=True)
    def is_root(self): return os.geteuid() == 0
    def now(self): return datetime.now(timezone.utc)
    def time(self): return time.monotonic()
    def sleep(self, seconds): time.sleep(seconds)
    def operation(self): return os.environ.get('ISLE_OPERATION_ID') or None
    def operation_dir(self): return os.environ.get('ISLE_OPERATION_DIR') or None
    def free(self, path): return shutil.disk_usage(path).free

    def device(self):
        import names
        return names.current_device()

    def roles_io(self):
        import storage_roles
        return storage_roles.Io()

    def http(self):
        import storage_migrate
        return storage_migrate.CurlHttp()

    def docker(self, args, timeout=60):
        try:
            done = subprocess.run(['docker'] + list(args), capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError):
            return 127, ''
        return done.returncode, done.stdout

    def versions(self):
        try:
            done = subprocess.run(['dpkg-query', '-W', '-f', '${Package} ${Version}\n'] + list(PACKAGES),
                                  capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return {}
        return dict(line.split(' ', 1) for line in done.stdout.splitlines() if ' ' in line and line.split(' ', 1)[1])

    def archive(self, source, left_out, target):
        """Write a gzip tar of the folder to a new file only root can read: (bytes, sha256, a file changed while read)."""
        argv = ['tar', '--create', '--gzip', '--file', '-', '--directory', str(source), '--numeric-owner', '--one-file-system',
                '--anchored'] + ['--exclude=./' + rel for rel in left_out] + ['.']
        digest, size = hashlib.sha256(), 0
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as out:
            with tempfile.TemporaryFile() as errors, subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=errors) as process:
                try:
                    for block in iter(lambda: process.stdout.read(MiB), b''):
                        digest.update(block); out.write(block); size += len(block)
                except BaseException:
                    process.kill()
                    raise
                code = process.wait()
                errors.seek(0)
                words = errors.read().decode('utf-8', 'replace').strip()
            out.flush(); os.fsync(out.fileno())
        if code not in (0, 1):      # 1: a file changed while it was read; the archive is whole
            raise ItemFailed('tar ended with exit %s: %s' % (code, words[-300:] or 'no words'))
        return size, digest.hexdigest(), code == 1

    def unpack(self, archive, target):
        """Unpack one archive into an empty folder as a restore does: owners and permissions as they were recorded."""
        argv = ['tar', '--extract', '--gzip', '--file', str(archive), '--directory', str(target), '--numeric-owner',
                '--same-owner', '--preserve-permissions']
        try:
            done = subprocess.run(argv, capture_output=True, text=True, errors='replace', timeout=3600)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ItemFailed('tar could not unpack it (%s)' % exc)
        if done.returncode != 0:
            raise ItemFailed('tar ended with exit %s: %s' % (done.returncode, done.stderr.strip()[-300:] or 'no words'))


# ---------------------------------------------------------------- where they live

def home(deps):
    """Where this machine's device-backups live: {kind: 'partition'|'local', path, filesystemUuid}."""
    import storage_roles
    try:
        _, _, partitions = storage_roles.survey(deps.roles_io())
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        raise Refused('the storage records or the disks of this machine could not be read (%s). See: isle storage partition check' % exc)
    mine = [row for row in partitions if 'backup' in (row.get('recordedRoles') or ())]
    if not mine:
        return dict(kind='local', path=str(Path(deps.state) / FOLDER), filesystemUuid=None)
    if len(mine) > 1:
        raise Refused('this machine records %d partitions with the role backup, and one is expected. See: isle storage role show backup' % len(mine))
    row = mine[0]
    state = row.get('roleState') if row.get('roleState') != 'ok' else 'mounted read-only' if row.get('mountedReadOnly') else None
    if state or not row.get('wholeMount'):
        raise Refused('the partition with the role backup is %s. See: isle storage partition check' % (state or 'not mounted whole'))
    return dict(kind='partition', path=str(Path(row['wholeMount']) / FOLDER), filesystemUuid=row.get('filesystemUuid'))


def _when(text):
    try: found = datetime.fromisoformat(text)
    except (ValueError, TypeError): return None
    return found if found.tzinfo else None


def read_manifest(folder, device=None):
    """(manifest or None, state): complete, incomplete (no manifest, or one not marked complete), damaged."""
    try: text = (Path(folder) / MANIFEST).read_text()
    except FileNotFoundError: return None, 'incomplete'
    except OSError: return None, 'damaged'
    try:
        doc = json.loads(text)
        good = (doc['kind'] == KIND and doc['version'] == 1 and doc['id'] == Path(folder).name and doc['slot'] in SLOTS
                and _when(doc['createdAt']) is not None and isinstance(doc['items'], list) and isinstance(doc['device'], str)
                and all(isinstance(item, dict) for item in doc['items']) and (device is None or doc['device'] == device))
    except (ValueError, KeyError, TypeError): good = False
    if not good: return None, 'damaged'
    return doc, 'complete' if doc.get('complete') is True else 'incomplete'


def held(base, device=None):
    """Every device-backup folder below base, oldest first: [(folder, manifest or None, state)]."""
    try: folders = sorted(p for p in Path(base).iterdir() if p.is_dir() and not p.is_symlink() and ID.match(p.name))
    except FileNotFoundError: return []
    return [(folder,) + read_manifest(folder, device) for folder in folders]


def reread(folder, manifest):
    """Read every file of a device-backup again: the list of what does not match its manifest."""
    wrong = []
    for item in manifest['items']:
        name = item.get('file')
        if not isinstance(name, str) or '/' in name or item.get('error'):
            wrong.append('%s: not taken' % item.get('name')); continue
        digest, size = hashlib.sha256(), 0
        try:
            with open(Path(folder) / name, 'rb') as source:
                for block in iter(lambda: source.read(MiB), b''):
                    digest.update(block); size += len(block)
        except OSError as exc:
            wrong.append('%s: %s cannot be read (%s)' % (item.get('name'), name, exc.strerror)); continue
        if size != item.get('sizeBytes') or digest.hexdigest() != item.get('sha256'):
            wrong.append('%s: %s is not what was written (%d bytes read, %s recorded)' % (item.get('name'), name, size, item.get('sizeBytes')))
    return wrong


def rotation(rows, now):
    """The two-slot rule.  rows: the complete manifests older than the new one.  Returns (the id that becomes the
    long-term one or None, the ids to delete)."""
    by_time = lambda doc: _when(doc['createdAt'])
    releases = sorted((doc for doc in rows if doc['slot'] == 'release'), key=by_time)
    long_terms = sorted((doc for doc in rows if doc['slot'] == 'long-term'), key=by_time)
    delete = [doc['id'] for doc in releases[:-1] + long_terms[:-1]]
    previous, current = (releases[-1] if releases else None), (long_terms[-1] if long_terms else None)
    if previous is None: return None, delete
    if current is None or now - by_time(current) >= LONG_TERM_AGE:
        return previous['id'], delete + ([current['id']] if current else [])
    return None, delete + [previous['id']]


# ---------------------------------------------------------------- create

def take(deps, item, folder, number, move):
    """Copy one item into the folder.  Returns its manifest row; `error` in it means it was not taken."""
    name = '%02d-%s.tar.gz' % (number, item['name'])
    row = dict(name=item['name'], kind=item['kind'], file=name, leftOut=item['leftOut'],
               source=item['volume'] if item['kind'] == 'polari-volume' else item['source'])
    gate = Gate(deps, item) if item['kind'] == 'polari-volume' and item['running'] else None
    try:
        if gate:
            row['hold'] = gate.raise_and_wait(move)
        row['method'] = ('archive of the volume behind the write gate' if gate else 'archive of the volume; the instance was not running'
                         if item['kind'] == 'polari-volume' else 'archive of the folder')
        row['sizeBytes'], row['sha256'], row['changedWhileRead'] = deps.archive(item['source'], item['exclude'], folder / name)
        if gate and row['changedWhileRead']:
            raise ItemFailed('a file of instance %s changed while it was copied behind the write gate' % item['instance'])
    except ItemFailed as exc:
        row['error'] = str(exc)
    except OSError as exc:
        row['error'] = 'could not be written (%s)' % (exc.strerror or exc)
    finally:
        if gate:        # whatever happened above, the instance must not be left unable to write
            try:
                gate.lower_if(move)
            except ItemFailed as exc:
                row['error'] = '%s Its write gate may still be up (%s). Lower it with: sudo isle backup release-hold' % (
                    row.get('error') or 'Copied, and the instance could not be told to write again.', exc)
    return row


def _write(folder, manifest):
    operations.atomic(Path(folder) / MANIFEST, manifest, 0o600)


def create(deps, argv):
    if argv != ['--yes']:
        deps.say(USAGE); return 2
    if not deps.is_root():
        raise Refused('this writes a device-backup. Type: sudo isle backup create --yes')
    now, operation = deps.now(), deps.operation()
    if not operation:       # the journal holds the lock that makes this the only device-backup running on this machine
        raise Refused('a device-backup is taken through the operation journal. Type: sudo isle backup create --yes')
    where, device = home(deps), deps.device()
    listing, not_covered = items_module.listing(deps)
    need = sum(item['sizeBytes'] for item in listing)
    base = Path(where['path']) / device
    for folder, _, state in held(base):
        if state == 'incomplete':       # left by an interrupted run; never offered for anything
            shutil.rmtree(folder)
            deps.say('Removed the incomplete device-backup %s.' % folder.name)
    free = deps.free(next(p for p in (base, *base.parents) if p.exists()))
    if free < 2 * need + deps.reserve:
        raise Refused('the home of the device-backups (%s) has %d MiB free, and %d MiB are needed (twice what is copied, and %d MiB kept free)'
                      % (where['path'], free // MiB, (2 * need + deps.reserve) // MiB + 1, deps.reserve // MiB))
    ident = '%s-%s' % (now.strftime('%Y%m%dT%H%M%SZ'), operation[:8])
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder = base / ident
    folder.mkdir(mode=0o700)
    manifest = dict(kind=KIND, version=1, id=ident, slot='release', device=device, createdAt=now.isoformat(), operationId=operation,
                    cliVersions=deps.versions(), home=dict(kind=where['kind'], filesystemUuid=where['filesystemUuid']),
                    instances=[item['instance'] for item in listing if item['kind'] == 'polari-volume'],
                    items=[], notCovered=not_covered, complete=False)
    _write(folder, manifest)
    deps.say('Device-backup %s of %s in %s' % (ident, device, folder))
    move = HOLD_PREFIX + operation
    for number, item in enumerate(listing, 1):
        row = take(deps, item, folder, number, move)
        manifest['items'].append(row)
        _write(folder, manifest)
        if row.get('error'):
            deps.say('  %-28s NOT TAKEN: %s' % (row['name'], row['error']))
            deps.say('The device-backup is incomplete and nothing else was changed. The two kept before are untouched.')
            return 1
        deps.say('  %-28s %8.1f MiB  %s' % (row['name'], row['sizeBytes'] / MiB, row['method']))
    wrong = reread(folder, manifest)
    if wrong:
        for line in wrong: deps.say('  re-read: ' + line)
        deps.say('The device-backup is incomplete: what was read back is not what was written. The two kept before are untouched.')
        return 1
    manifest.update(complete=True, verifiedAt=deps.now().isoformat())
    _write(folder, manifest)
    deps.say('Every file was read back and matches. Slot: release.')
    keep, delete = rotation([doc for _, doc, state in held(base, device) if state == 'complete' and doc['id'] != ident], now)
    for other, doc, state in held(base, device):
        if state == 'complete' and doc['id'] == keep:
            doc.update(slot='long-term', slotChangedAt=deps.now().isoformat())
            _write(other, doc)
            deps.say('The previous one, %s, is now the long-term one.' % keep)
    for other, doc, state in held(base, device):
        if state == 'complete' and doc['id'] in delete:
            shutil.rmtree(other)
            deps.say('Deleted %s (%s).' % (doc['id'], doc['slot']))
    for row in not_covered:
        deps.say('  not covered: %s - %s' % (row['name'], row['why']))
    if deps.operation_dir():
        operations.atomic(Path(deps.operation_dir()) / 'device-backup.json', dict(id=ident, folder=str(folder), slot='release',
                          sizeBytes=sum(row['sizeBytes'] for row in manifest['items']), becameLongTerm=keep, deleted=delete))
    return 0


# ---------------------------------------------------------------- read-only verbs and the hold

def plan(deps, argv):
    if argv not in ([], ['--json']):
        deps.say(USAGE); return 2
    where, device = home(deps), deps.device()
    listing, not_covered = items_module.listing(deps)
    base = Path(where['path']) / device
    rows = _held_or_none(base, device)
    existing = [dict(id=folder.name, slot=doc['slot'] if doc else None, createdAt=doc['createdAt'] if doc else None, state=state)
                for folder, doc, state in rows or []]
    need = sum(item['sizeBytes'] for item in listing)
    present = next((p for p in (base, *base.parents) if p.exists()), None)
    try: free = deps.free(present) if present else None
    except OSError: free = None
    report = dict(device=device, home=where, freeBytes=free, neededBytes=2 * need + deps.reserve, sizeBytes=need,
                  sizesComplete=not any(item['unread'] for item in listing),
                  ready=free is not None and free >= 2 * need + deps.reserve,
                  items=[dict({k: item[k] for k in ('name', 'kind', 'sizeBytes', 'unread', 'leftOut')}, **(
                      dict(instance=item['instance'], volume=item['volume'], running=item['running']) if item['kind'] == 'polari-volume' else
                      dict(source=item['source']))) for item in listing],
                  notCovered=not_covered, held=None if rows is None else existing)
    if argv:
        deps.say(json.dumps(report, indent=2)); return 0
    deps.say('Device-backup of %s' % device)
    deps.say('Home: %s (%s)' % (where['path'], 'the partition with the role backup' if where['kind'] == 'partition'
                                else 'this machine records no partition with the role backup, so its state folder'))
    for item in listing:
        how = ('behind the write gate of instance %s' % item['instance'] if item.get('running') else
               'instance %s is not running' % item['instance']) if item['kind'] == 'polari-volume' else item['source']
        deps.say('  %-28s %8.1f MiB  %s%s' % (item['name'], item['sizeBytes'] / MiB, how, '  (size incomplete: not all of it can be read without sudo)' if item['unread'] else ''))
        for row in item['leftOut']:
            deps.say('      left out: %s - %s' % (row['path'], row['why']))
    for row in not_covered:
        deps.say('  not covered: %s - %s' % (row['name'], row['why']))
    deps.say('Free on the home: %s MiB; needed: %d MiB. %s' % ('?' if free is None else free // MiB, report['neededBytes'] // MiB + 1,
                                                                'Ready.' if report['ready'] else 'NOT ready.'))
    deps.say('Kept now: %s' % ('they can be read by root only (sudo isle backup list)' if rows is None else
                               ', '.join('%s (%s)' % (row['id'], row['slot'] or row['state']) for row in existing) or 'none'))
    return 0


def _held_or_none(base, device):
    try: return held(base, device)
    except PermissionError: return None


def listing_verb(deps, argv):
    if argv not in ([], ['--json']):
        deps.say(USAGE); return 2
    base = Path(home(deps)['path']) / deps.device()
    rows = _held_or_none(base, deps.device())
    if rows is None:
        raise Refused('the device-backups can be read by root only. Type: sudo isle backup list')
    out = [dict(id=folder.name, state=state, slot=doc['slot'] if doc else None, createdAt=doc['createdAt'] if doc else None,
                sizeBytes=sum(i.get('sizeBytes') or 0 for i in doc['items']) if doc else None,
                drill=drilled(folder, doc)['state'] if state == 'complete' else None) for folder, doc, state in rows]
    if argv:
        deps.say(json.dumps(dict(home=str(base), deviceBackups=out), indent=2)); return 0
    deps.say('Device-backups in %s' % base)
    for row in out:
        deps.say('  %s  %-9s  %-10s  %s%s' % (row['id'], row['slot'] or '-', row['state'],
                                              '%.1f MiB' % (row['sizeBytes'] / MiB) if row['sizeBytes'] is not None else '',
                                              '  drill: ' + row['drill'] if row['drill'] else ''))
    if not out: deps.say('  none')
    return 0


def verify(deps, argv):
    if len(argv) != 1 or not ID.match(argv[0]):
        deps.say(USAGE); return 2
    folder = Path(home(deps)['path']) / deps.device() / argv[0]
    try: doc, state = read_manifest(folder, deps.device())
    except PermissionError: raise Refused('the device-backups can be read by root only. Type: sudo isle backup verify ' + argv[0])
    if not folder.is_dir(): raise Refused('no device-backup %s in %s' % (argv[0], folder.parent))
    if doc is None or state != 'complete':
        deps.say('%s is %s: it is not offered for a restore.' % (argv[0], state)); return 1
    wrong = reread(folder, doc)
    for line in wrong: deps.say('  ' + line)
    deps.say('%s: %s' % (argv[0], 'every file matches its record (%d files).' % len(doc['items']) if not wrong else 'DOES NOT MATCH its record.'))
    return 1 if wrong else 0


def release_hold(deps, argv):
    if argv:
        deps.say(USAGE); return 2
    if not deps.is_root():
        raise Refused('this changes a running instance. Type: sudo isle backup release-hold')
    Path(deps.lock_file).parent.mkdir(parents=True, exist_ok=True)
    with open(deps.lock_file, 'a+') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise Refused('a device-backup is running now and uses its hold; nothing was changed')
        found, failed = items_module.instances(deps)[0], 0
        for item in found:
            if not item['running']:
                deps.say('%s: not running, nothing held.' % item['instance']); continue
            try: was = Gate(deps, item).lower_stale()
            except ItemFailed as exc:
                deps.say('%s: %s' % (item['instance'], exc)); failed += 1; continue
            deps.say('%s: %s' % (item['instance'], 'lowered the write gate left by %s.' % was if was else 'no write gate of a device-backup is up.'))
        if not found: deps.say('No Polari instance with data is deployed by the isle CLI on this machine.')
    return 1 if failed else 0


# ---------------------------------------------------------------- the restore drill

def drilled(folder, manifest):
    """The last drill of a device-backup: {state: 'none'|'passed'|'failed'|'unreadable', at, cliVersions}.
    A record of other archives than the ones the device-backup names now is not a drill of it."""
    try: text = (Path(folder) / DRILL).read_text()
    except FileNotFoundError: return dict(state='none')
    except OSError: return dict(state='unreadable')
    try:
        doc = json.loads(text)
        if doc['kind'] != DRILL_KIND or doc['version'] != 1 or doc['id'] != manifest['id'] or _when(doc['at']) is None:
            return dict(state='unreadable')
        if doc['archives'] != {item.get('file'): item.get('sha256') for item in manifest['items']}:
            return dict(state='none')
        return dict(state='passed' if doc['passed'] is True else 'failed', at=doc['at'], cliVersions=doc.get('cliVersions'))
    except (ValueError, KeyError, TypeError):
        return dict(state='unreadable')


def _drill_item(deps, item, archive, rows, target):
    """Unpack one archive into its scratch folder and compare.  Returns the item's row of the drill record."""
    entries, files, size = drill.totals(rows)
    row = dict(name=item.get('name'), kind=item.get('kind'), file=item['file'], entries=entries, files=files, bytes=size, wrong=[])
    try:
        deps.unpack(archive, target)
    except ItemFailed as exc:
        row['wrong'] = [str(exc)]
        return row
    row['wrong'] = drill.compare(target, rows)
    if item.get('kind') == 'polari-volume':     # after the comparison: SQLite may write its own side files beside a database
        row['databases'] = drill.databases(target)
        row['wrong'] += ['%s: the database is not whole (%s)' % (found['path'], found['integrity'])
                         for found in row['databases'] if found['integrity'] != 'ok']
    return row


def restore(deps, argv):
    if argv[1:2] == ['--apply']:
        import device_backup_apply
        return device_backup_apply.apply(deps, argv, sys.modules[__name__])
    if len(argv) != 2 or not ID.match(argv[0]) or argv[1] != '--drill':
        deps.say(USAGE); return 2
    if not deps.is_root():
        raise Refused('a device-backup can be read by root only. Type: sudo isle backup restore %s --drill' % argv[0])
    device = deps.device()
    base = Path(home(deps)['path']) / device
    folder = base / argv[0]
    if not folder.is_dir(): raise Refused('no device-backup %s in %s' % (argv[0], base))
    Path(deps.lock_file).parent.mkdir(parents=True, exist_ok=True)
    with open(deps.lock_file, 'a+') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise Refused('a device-backup or another drill is running now; nothing was unpacked')
        doc, state = read_manifest(folder, device)
        if doc is None or state != 'complete':
            deps.say('%s is %s: it is not offered for a restore, and not for a drill.' % (argv[0], state)); return 1
        wrong = reread(folder, doc)
        if wrong:
            for line in wrong: deps.say('  ' + line)
            deps.say('%s DOES NOT MATCH its record. Nothing was unpacked.' % argv[0]); return 1
        try:
            listings = {item['file']: drill.contents(folder / item['file']) for item in doc['items']}
        except drill.DrillFailed as exc:
            deps.say('%s: %s. Nothing was unpacked.' % (argv[0], exc)); return 1
        need = sum(drill.totals(rows)[2] for rows in listings.values())
        free = deps.free(base)
        if free < need + deps.reserve:
            raise Refused('the scratch copy needs %d MiB beside the device-backup (%d MiB kept free), and %d MiB are free in %s'
                          % ((need + deps.reserve) // MiB + 1, deps.reserve // MiB, free // MiB, base))
        scratch = base / (SCRATCH + argv[0])
        if scratch.is_symlink() or (scratch.exists() and not scratch.is_dir()):
            raise Refused('%s is in the way and is not a scratch folder of a drill; nothing was unpacked' % scratch)
        if scratch.exists(): shutil.rmtree(scratch)       # left by a drill that was interrupted
        deps.say('Drill of device-backup %s of %s' % (argv[0], device))
        rows = []
        try:
            scratch.mkdir(mode=0o700)
            for item in doc['items']:
                target = scratch / item['file'][:-len('.tar.gz')]
                target.mkdir(mode=0o700)
                rows.append(_drill_item(deps, item, folder / item['file'], listings[item['file']], target))
        except OSError as exc:
            raise Refused('the scratch folder %s could not be written (%s); the drill did not run' % (scratch, exc.strerror or exc))
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        left = scratch.exists()
        for row in rows:
            held_rows = sum(sum(found['tables'].values()) for found in row.get('databases', []))
            words = '' if 'databases' not in row else '; no database in it' if not row['databases'] else \
                '; %d database%s opened whole, %d rows' % (len(row['databases']), '' if len(row['databases']) == 1 else 's', held_rows)
            deps.say('  %-28s %6d files %8.1f MiB  %s' % (row['name'], row['files'], row['bytes'] / MiB,
                     'NOT what the archive holds' if row['wrong'] else 'unpacked and equal to its archive' + words))
            for line in row['wrong'][:drill.SHOWN]: deps.say('      ' + line)
            if len(row['wrong']) > drill.SHOWN: deps.say('      and %d more' % (len(row['wrong']) - drill.SHOWN))
        passed = not any(row['wrong'] for row in rows)
        for row in rows: row.update(wrongCount=len(row['wrong']), wrong=row['wrong'][:100])
        operations.atomic(folder / DRILL, dict(kind=DRILL_KIND, version=1, id=doc['id'], device=device, at=deps.now().isoformat(),
                          cliVersions=deps.versions(), passed=passed, items=rows,
                          archives={item['file']: item['sha256'] for item in doc['items']}), 0o600)
        deps.say('The drill %s. Nothing on this machine was changed and the scratch copy %s; the result is kept in %s.'
                 % ('passed' if passed else 'FAILED', 'COULD NOT BE REMOVED (%s)' % scratch if left else 'is removed', folder / DRILL))
        return 0 if passed else 1


def before(deps, argv):
    import device_backup_apply
    return device_backup_apply.before(deps, argv, sys.modules[__name__])


VERBS = {'plan': plan, 'create': create, 'list': listing_verb, 'verify': verify, 'release-hold': release_hold, 'restore': restore,
         'before': before}


def main(argv=None, deps=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    deps = deps or Deps()
    if not argv or argv[0] in ('help', '-h', '--help'):
        deps.say(__doc__.strip()); return 0
    if argv[0] not in VERBS:
        deps.say(USAGE); return 2
    try:
        return VERBS[argv[0]](deps, argv[1:])
    except Refused as exc:
        deps.say('Refused: %s' % exc)
        return 1


if __name__ == '__main__':
    sys.exit(main())
