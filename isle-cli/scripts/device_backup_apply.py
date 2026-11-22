#!/usr/bin/env python3
"""Putting the data of a Polari instance back from a device-backup.

  sudo isle backup restore <id> --apply [--item <name>]...          says what would be put back; changes nothing
  sudo isle backup restore <id> --apply --yes [--item <name>]...    journaled: puts it back
  sudo isle backup before list [--json]                             the data kept from before a restore
  sudo isle backup before drop <item> --yes                         removes it
  sudo isle backup before undo --yes                                puts a restore that did not finish back as it was

A device-backup holds data, not programs: this puts data back.  Going back to earlier programs is a release.
Without --item it is the data of every Polari instance the device-backup holds.  The isle's own two folders are
never put back by this: nothing here stops the isle's own services, and that is not built.

For one instance, in this order:
  1. unpack its archive into a new folder beside the live data, on the same filesystem, and compare every entry
     with the archive (what the drill does);
  2. stop every running container that uses the volume;
  3. two renames: the live data aside as before-<id>-<operation>, the new folder into its place;
  4. start those containers again and wait for the instance's health answer;
  5. open the databases of the live data read-only and compare their tables with those the drill read.
If 4 or 5 fails, the two renames are undone and the containers are started on the data they had.  The data kept
from before stays until the next restore of that instance has succeeded, or until `before drop` removes it.

While an instance is being put back, a record of it is in the state folder (restore-apply/<item>.json), written
before each step.  A restore that was interrupted is put back as it was by `before undo --yes`, and by the next
`--apply --yes` before anything else; `sudo isle operation retry <id>` is such a run.
"""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import shutil

import device_backup_drill as drill
import device_backup_items as items_module
from device_backup_items import Gate, ItemFailed
import operations

RECORDS, RECORD_KIND = 'restore-apply', 'isle.device-backup-restore'
DATA, NEW, BEFORE = '_data', 'restore-', 'before-'
IDLE = ('exited', 'created', 'dead')          # states of a container that holds nothing of the volume open
HEALTH = '/api/health'
MiB = 1024 * 1024


class Undone(Exception):
    """One instance could not be put back; it has the data it had."""


class Stuck(Exception):
    """An instance could not be brought back to the data it had.  Its record stays; a person has to look."""


@contextlib.contextmanager
def _locked(deps, core, busy):
    Path(deps.lock_file).parent.mkdir(parents=True, exist_ok=True)
    with open(deps.lock_file, 'a+') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise core.Refused(busy)
        yield


# ---------------------------------------------------------------- the record of what is under way

def _record_path(deps, name): return Path(deps.state) / RECORDS / (name + '.json')


def _write(deps, record):
    path = _record_path(deps, record['item'])
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    operations.atomic(path, record, 0o600)


def records(deps):
    """The restores that are under way or were interrupted: [(path, record or None)]; None cannot be read."""
    try: paths = sorted((Path(deps.state) / RECORDS).glob('*.json'))
    except OSError: return []
    out = []
    for path in paths:
        try:
            doc = json.loads(path.read_text())
            good = (doc['kind'] == RECORD_KIND and doc['version'] == 1 and doc['item'] == path.stem
                    and all(isinstance(doc[key], str) and doc[key] for key in ('volumeFolder', 'new', 'before', 'id'))
                    and doc['new'].startswith(NEW) and doc['before'].startswith(BEFORE) and '/' not in doc['new'] + doc['before']
                    and os.path.isabs(doc['volumeFolder']) and isinstance(doc['stopped'], list)
                    and all(isinstance(name, str) and name for name in doc['stopped']))
        except (OSError, ValueError, KeyError, TypeError): good = False
        out.append((path, doc if good else None))
    return out


def _kept_in(volume):
    try: return sorted(path for path in Path(volume).iterdir() if path.name.startswith(BEFORE) and path.is_dir() and not path.is_symlink())
    except OSError: return []


def kept(deps):
    """The data kept from before a restore, for the instances on this machine: [{item, path, underWay}]."""
    busy = {doc['item']: doc['before'] for _, doc in records(deps) if doc}
    return [dict(item=item['name'], path=str(path), underWay=busy.get(item['name']) == path.name)
            for item in items_module.instances(deps)[0] if Path(item['source']).name == DATA
            for path in _kept_in(Path(item['source']).parent)]


# ---------------------------------------------------------------- containers and the health answer

def containers_of(deps, volume):
    """[(name, state)] of every container that uses the volume, running or not; None when Docker did not say."""
    code, out = deps.docker(['ps', '--all', '--no-trunc', '--filter', 'volume=' + volume, '--format', '{{.Names}}\t{{.State}}'])
    rows = [line.split('\t') for line in out.splitlines() if line.strip()]
    if code != 0 or any(len(row) != 2 or not all(row) for row in rows): return None
    return sorted((name, state) for name, state in rows)


def _stop(deps, names):
    """Stop them.  Returns the first that did not stop, or None."""
    for name in names:
        if deps.docker(['stop', '--time', str(deps.stop_grace), name], timeout=deps.stop_grace + 60)[0] != 0:
            return name
    return None


def _start(deps, names):
    """Start them, last stopped first.  Returns those that did not start."""
    return [name for name in reversed(names) if deps.docker(['start', name], timeout=120)[0] != 0]


def healthy(deps, item):
    """Wait for the instance's health answer over verified HTTPS: True, or False when the time is up."""
    gate = Gate(deps, item)
    deadline = deps.time() + deps.start_limit
    while True:
        try:
            gate._each(lambda route: gate.http.get_bytes(item['api'] + HEALTH, resolve=route, max_bytes=MiB))
            return True
        except ItemFailed:
            if deps.time() >= deadline: return False
            deps.sleep(deps.poll_interval)


def differences(was, now):
    """The databases the drill read against those of the live data: (what fails the restore, what only differs).
    A started instance may add and remove rows, so a count that moved is told and does not fail.  A database or a
    table that is gone, a database that is not whole, and a table that held rows and holds none fail it."""
    fails, notes, found = [], [], {row['path']: row for row in now}
    for row in was:
        here = found.get(row['path'])
        if here is None:
            fails.append('%s: the database is no longer there' % row['path']); continue
        if here['integrity'] != 'ok':
            fails.append('%s: the database is not whole (%s)' % (row['path'], here['integrity'])); continue
        for table, count in sorted(row['tables'].items()):
            if table not in here['tables']: fails.append('%s: the table %s is gone' % (row['path'], table))
            elif count and not here['tables'][table]: fails.append('%s: the table %s held %d rows and holds none' % (row['path'], table, count))
            elif here['tables'][table] != count:
                notes.append('%s: the table %s holds %d rows; the device-backup holds %d' % (row['path'], table, here['tables'][table], count))
    return fails, notes


def _read_live(deps, data):
    """The databases of the live data.  The instance is running and may hold one for a moment: asked three times."""
    for attempt in range(3):
        rows = drill.databases(data)
        if not any(row['integrity'].startswith('cannot be read') for row in rows): break
        deps.sleep(deps.poll_interval)
    return rows


# ---------------------------------------------------------------- putting an instance back as it was

def undo(deps, record):
    """Bring one instance back to the data it had before a restore that did not finish, at whatever point it
    stopped.  Returns the containers that did not start again; the record is removed when all of them did."""
    volume = Path(record['volumeFolder'])
    data, before, new = volume / DATA, volume / record['before'], volume / record['new']
    try:
        if before.exists():                     # the first rename happened
            failed = _stop(deps, record['stopped'])     # they may be running on the data of the device-backup
            if failed: raise Stuck('the container %s could not be stopped' % failed)
            if data.exists():
                if new.exists(): raise Stuck('%s, %s and %s all exist' % (data, before, new))
                os.rename(data, new)
            os.rename(before, data)
        if new.exists(): shutil.rmtree(new)
    except OSError as exc:
        raise Stuck('%s (%s)' % (exc.strerror or exc, exc.filename or volume))
    not_started = _start(deps, record['stopped'])
    if not not_started: _record_path(deps, record['item']).unlink(missing_ok=True)
    return not_started


def put_back_unfinished(deps, say):
    """Every restore that did not finish is put back as it was.  Raises Stuck when one cannot be."""
    for path, doc in records(deps):
        if doc is None:
            raise Stuck('the record %s cannot be read. Nothing was changed; look at the volume it names before anything else' % path)
        say('An earlier restore of %s did not finish (device-backup %s). Putting its data back as it was.' % (doc['item'], doc['id']))
        try:
            not_started = undo(deps, doc)
        except Stuck as exc:
            raise Stuck('%s could not be put back as it was: %s. Its record is %s' % (doc['item'], exc, path))
        if not_started:
            raise Stuck('%s has the data it had, and these containers did not start: %s. Start them, then run this again; the record is %s'
                        % (doc['item'], ', '.join(not_started), path))
        say('  %s has the data it had.' % doc['item'])


# ---------------------------------------------------------------- what would be put back

def targets(deps, core, folder, doc, names):
    """The instances to put back, each checked against this machine as it is now.  Raises core.Refused."""
    rows = {item.get('name'): item for item in doc['items']}
    chosen = list(names) or [item['name'] for item in doc['items'] if item.get('kind') == 'polari-volume']
    if not chosen:
        raise core.Refused('device-backup %s holds the data of no Polari instance; there is nothing this can put back' % doc['id'])
    if len(set(chosen)) != len(chosen): raise core.Refused('an item is named twice')
    for name in chosen:
        if name not in rows:
            raise core.Refused('device-backup %s holds no item %s. It holds: %s' % (doc['id'], name, ', '.join(sorted(rows))))
        if rows[name].get('kind') != 'polari-volume':
            raise core.Refused('%s is one of the isle\'s own folders. Putting those back is not built: it needs every isle '
                               'service of the machine stopped. The data of an instance can be put back' % name)
    drilled = {row.get('name'): row for row in json.loads((Path(folder) / core.DRILL).read_text())['items']}
    present = {item['name']: item for item in items_module.instances(deps)[0]}
    out = []
    for name in chosen:
        row, now = rows[name], present.get(name)
        if now is None:
            raise core.Refused('the instance of %s is not deployed on this machine now, so its data cannot be put back' % name)
        if now['volume'] != row.get('source'):
            raise core.Refused('%s uses the volume %s now; the device-backup holds the volume %s' % (name, now['volume'], row.get('source')))
        data = Path(now['source'])
        try:
            plain = data.name == DATA and data.is_dir() and not data.is_symlink() and os.stat(data).st_dev == os.stat(data.parent).st_dev
        except OSError: plain = False
        if not plain:
            raise core.Refused('the data of %s is not a plain folder of a local Docker volume (a mount of its own, or another layout); '
                               'putting it back is not built for that' % name)
        users = containers_of(deps, now['volume'])
        if users is None: raise core.Refused('Docker did not say which containers use the volume of %s' % name)
        odd = ['%s (%s)' % pair for pair in users if pair[1] != 'running' and pair[1] not in IDLE]
        if odd: raise core.Refused('a container that uses the volume of %s is neither running nor stopped: %s' % (name, ', '.join(odd)))
        running = [pair[0] for pair in users if pair[1] == 'running']
        if now['running'] and (now['container'] not in running or not now['api']):
            raise core.Refused('the instance of %s is running and %s' % (name, 'its record names no address to ask for its health'
                               if not now['api'] else 'Docker does not list its container %s as using the volume' % now['container']))
        try: listing = drill.contents(Path(folder) / row['file'])
        except drill.DrillFailed as exc: raise core.Refused('%s: %s' % (row['file'], exc))
        out.append(dict(name=name, item=now, row=row, volumeFolder=data.parent, listing=listing, bytes=drill.totals(listing)[2],
                        running=running, databases=(drilled.get(name) or {}).get('databases') or []))
    need = sum(target['bytes'] for target in out)
    for target in out:
        free = deps.free(target['volumeFolder'])
        if free < need + deps.reserve:
            raise core.Refused('a second copy of the data needs %d MiB beside the data of %s (%d MiB kept free), and %d MiB are free there'
                               % ((need + deps.reserve) // MiB + 1, target['name'], deps.reserve // MiB, free // MiB))
    return out


# ---------------------------------------------------------------- one instance

def one(deps, target, archive, ident, operation, say):
    """Put the data of one instance back.  Returns the row of the result; raises Undone or Stuck."""
    volume, item, tag = target['volumeFolder'], target['item'], '%s-%s' % (ident, operation[:8])
    record = dict(kind=RECORD_KIND, version=1, item=target['name'], instance=item['instance'], volume=item['volume'], volumeFolder=str(volume),
                  id=ident, operation=operation, new=NEW + tag, before=BEFORE + tag, stopped=[], stage='unpacking',
                  startedAt=deps.now().isoformat())
    data, new, before = volume / DATA, volume / record['new'], volume / record['before']
    result = dict(name=target['name'], instance=item['instance'], before=str(before), started=[], notes=[], already=False)
    if before.is_dir() and not new.exists():        # this operation, run again: it put this one back before it stopped
        return dict(result, already=True)
    if new.exists() or before.exists():
        raise Undone('%s or %s is in the way; nothing of %s was touched' % (new, before, target['name']))
    _write(deps, record)

    def give_up(why):
        try: not_started = undo(deps, record)
        except Stuck as exc:
            raise Stuck('%s. Then it could not be put back as it was: %s. Its record is %s' % (why, exc, _record_path(deps, target['name'])))
        if not_started:
            raise Stuck('%s. It has the data it had, and these containers did not start: %s. The record is %s'
                        % (why, ', '.join(not_started), _record_path(deps, target['name'])))
        if record['stopped'] and item['running'] and not healthy(deps, item):
            raise Undone('%s. It has the data it had and its containers are started, but it gave no health answer in %d seconds'
                         % (why, deps.start_limit))
        raise Undone('%s. %s has the data it had%s' % (why, target['name'], ' and is running on it' if record['stopped'] else ''))

    try:
        new.mkdir(mode=0o700)
        deps.unpack(archive, new)
    except (ItemFailed, OSError) as exc:
        give_up('its archive could not be unpacked beside the data (%s)' % exc)
    wrong = drill.compare(new, target['listing'])
    if wrong:
        give_up('what was unpacked is not what the archive holds (%s%s)' % (wrong[0], ', and %d more' % (len(wrong) - 1) if len(wrong) > 1 else ''))
    record.update(stage='stopping', stopped=target['running']); _write(deps, record)
    failed = _stop(deps, record['stopped'])
    if failed: give_up('the container %s could not be stopped' % failed)
    record['stage'] = 'swapping'; _write(deps, record)
    try:
        os.rename(data, before)
        os.rename(new, data)
    except OSError as exc:
        give_up('the data could not be moved into place (%s)' % (exc.strerror or exc))
    record['stage'] = 'checking'; _write(deps, record)
    if record['stopped']:
        not_started = _start(deps, record['stopped'])
        if not_started: give_up('on the data of the device-backup these containers did not start: %s' % ', '.join(not_started))
        result['started'] = list(reversed(record['stopped']))
        if item['running']:
            if not healthy(deps, item):
                give_up('on the data of the device-backup the instance gave no health answer in %d seconds' % deps.start_limit)
            fails, result['notes'] = differences(target['databases'], _read_live(deps, data))
            if fails:
                give_up('after its start the data is not that of the device-backup (%s%s)'
                        % (fails[0], ', and %d more' % (len(fails) - 1) if len(fails) > 1 else ''))
    _record_path(deps, target['name']).unlink(missing_ok=True)      # from here it is put back
    for other in _kept_in(volume):                                  # kept from before an earlier restore of this instance
        if other != before:
            shutil.rmtree(other); say('      removed what was kept from before an earlier restore: %s' % other.name)
    return result


# ---------------------------------------------------------------- the verbs

def _words(argv):
    """(id, --yes given, item names) or None."""
    if len(argv) < 2 or argv[1] != '--apply': return None
    rest, yes = argv[2:], False
    if rest[:1] == ['--yes']: yes, rest = True, rest[1:]
    if len(rest) % 2 or any(rest[i] != '--item' or not operations.NAME.match(rest[i + 1]) for i in range(0, len(rest), 2)): return None
    return argv[0], yes, rest[1::2]


def apply(deps, argv, core):
    words = _words(argv)
    if words is None or not core.ID.match(argv[0]):
        deps.say(core.USAGE); return 2
    ident, yes, names = words
    again = 'sudo isle backup restore %s --apply --yes%s' % (ident, ''.join(' --item ' + name for name in names))
    if not deps.is_root():
        raise core.Refused('a device-backup can be read by root only. Type: sudo isle backup restore %s --apply' % ident)
    operation = deps.operation()
    if yes and not operation:       # the journal holds the lock that keeps a device-backup and a drill away while this runs
        raise core.Refused('data is put back through the operation journal. Type: ' + again)
    if yes:
        try: put_back_unfinished(deps, deps.say)
        except Stuck as exc:
            deps.say('STOPPED: %s.' % exc); return 1
    elif records(deps):
        deps.say('An earlier restore did not finish (%s). What this would do can be said only once its data is back as it was: '
                 'sudo isle backup before undo --yes. Nothing was changed.' % ', '.join(path.stem for path, _ in records(deps)))
        return 1
    device = deps.device()
    base = Path(core.home(deps)['path']) / device
    folder = base / ident
    if not folder.is_dir(): raise core.Refused('no device-backup %s in %s' % (ident, base))
    doc, state = core.read_manifest(folder, device)
    if doc is None or state != 'complete':
        deps.say('%s is %s: it is not offered for a restore. Nothing was changed.' % (ident, state)); return 1
    wrong = core.reread(folder, doc)
    if wrong:
        for line in wrong: deps.say('  ' + line)
        deps.say('%s DOES NOT MATCH its record. Nothing was changed.' % ident); return 1
    last, mine = core.drilled(folder, doc), deps.versions().get('isle-mesh-cli')
    if last['state'] != 'passed':
        raise core.Refused('device-backup %s has no passed drill of these archives (%s). First: sudo isle backup restore %s --drill'
                           % (ident, 'the last one failed' if last['state'] == 'failed' else 'none is recorded', ident))
    if not mine or (last.get('cliVersions') or {}).get('isle-mesh-cli') != mine:
        raise core.Refused('the drill of %s passed with another version of the isle CLI than this machine runs now (%s). '
                           'Run it again: sudo isle backup restore %s --drill' % (ident, mine or 'not known', ident))
    found = targets(deps, core, folder, doc, names)
    deps.say('Device-backup %s of %s, taken %s' % (ident, device, doc['createdAt']))
    for target in found:
        deps.say('  %-28s %8.1f MiB  %s' % (target['name'], target['bytes'] / MiB, 'stops and starts: ' + ', '.join(target['running'])
                                           if target['running'] else 'no container that uses it is running; nothing is started'))
    if not yes:
        deps.say('This would replace the data of %s with that of the device-backup. The data they have now would be kept beside it.'
                 % ', '.join(target['name'] for target in found))
        deps.say('Nothing was changed. To do it: ' + again)
        return 0
    done, code = [], 0
    for target in found:
        try:
            row = one(deps, target, folder / target['row']['file'], ident, operation, deps.say)
        except Undone as exc:
            deps.say('  %-28s NOT PUT BACK: %s.' % (target['name'], exc)); code = 1; break
        except Stuck as exc:
            deps.say('  %-28s STOPPED: %s.' % (target['name'], exc)); code = 1; break
        done.append(row)
        deps.say('  %-28s %s' % (target['name'], 'was put back by this operation before it stopped; left as it is' if row['already'] else
                                 'put back; the instance answers and its tables are those of the device-backup' if
                                 target['item']['running'] and row['started'] else 'put back; equal to its archive, nothing was started'))
        for line in row['notes'][:drill.SHOWN]: deps.say('      ' + line)
        if len(row['notes']) > drill.SHOWN: deps.say('      and %d more tables with another count' % (len(row['notes']) - drill.SHOWN))
        deps.say('      the data it had is kept in %s' % row['before'])
    left = [target['name'] for target in found[len(done):]]
    if deps.operation_dir():
        operations.atomic(Path(deps.operation_dir()) / 'device-backup-restore.json', dict(id=ident, putBack=done, notPutBack=left))
    if code:
        deps.say('Not put back: %s.%s' % (', '.join(left), ' Put back before it and left so: %s.' % ', '.join(r['name'] for r in done) if done else ''))
    else:
        deps.say('Done. What was kept from before: sudo isle backup before list')
    return code


def before(deps, argv, core):
    if argv in ([], ['list'], ['list', '--json']):
        if not deps.is_root(): raise core.Refused('the volumes can be read by root only. Type: sudo isle backup before list')
        rows = kept(deps)
        if argv[1:]:
            deps.say(json.dumps(dict(keptBefore=rows), indent=2)); return 0
        deps.say('Data kept from before a restore:')
        for row in rows:
            deps.say('  %-28s %s%s' % (row['item'], row['path'], '  (a restore of it is under way or was interrupted)' if row['underWay'] else ''))
        if not rows: deps.say('  none')
        else: deps.say('To remove one: sudo isle backup before drop <item> --yes')
        return 0
    if argv == ['undo', '--yes']:
        if not deps.is_root(): raise core.Refused('this stops and starts containers. Type: sudo isle backup before undo --yes')
        with _locked(deps, core, 'a device-backup, a drill or a restore is running now; nothing was changed'):
            if not records(deps):
                deps.say('No restore is unfinished. Nothing was changed.'); return 0
            try: put_back_unfinished(deps, deps.say)
            except Stuck as exc:
                deps.say('STOPPED: %s.' % exc); return 1
        return 0
    if len(argv) != 3 or argv[0] != 'drop' or argv[2] != '--yes' or not operations.NAME.match(argv[1]):
        deps.say(core.USAGE); return 2
    if not deps.is_root():
        raise core.Refused('this removes data. Type: sudo isle backup before drop %s --yes' % argv[1])
    with _locked(deps, core, 'a device-backup, a drill or a restore is running now; nothing was removed'):
        if any(path.stem == argv[1] for path, _ in records(deps)):
            raise core.Refused('a restore of %s did not finish; what is kept is the data it goes back to. '
                               'First: sudo isle backup before undo --yes' % argv[1])
        rows = [row for row in kept(deps) if row['item'] == argv[1]]
        if not rows: raise core.Refused('nothing is kept from before a restore of %s' % argv[1])
        for row in rows:
            shutil.rmtree(row['path'])
            deps.say('Removed %s.' % row['path'])
    return 0
