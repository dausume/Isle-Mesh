#!/usr/bin/env python3
"""isle storage slot list | check | declare | bind | unbind      (ready | gate | ungate: storage_slot_gate.py)

A slot is one storage need of one app: a folder the app sees, the role of the partition that has to hold it, and
whether the app writes to it.  A release declares its slots in the list `storageSlots` of its release manifest.
`slot bind` places one slot on a partition of this machine that carries the role.

Two places hold this.  The declaration is kept with the app (<apps>/<app>/storage-slots.json): it is what the release
says.  The binding is kept in this machine's storage record, beside the roles of the partition it names: it is what
was decided for this machine.  The data is a folder of the partition, isle-slots/<app>/<slot>, so a disk that is
carried to another machine still says whose data it holds.

Nothing here starts, stops or changes an app, and nothing here erases or moves data.  `bind` makes one folder;
`unbind` leaves it where it is.
"""
import json
import os
from pathlib import Path
import re
import subprocess

from operations import NAME as APP, atomic
import storage_devices
import storage_kinds
from storage_mount import _absolute
import storage_roles as sr

KEY = 'storageSlots'              # the list of a release manifest; its `storage` is the dataset bind of a hosting app
FILE = 'storage-slots.json'
DOC_KIND = 'isle.storage.slots'
FOLDER = 'isle-slots'
SLOT = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')
SERVICE = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z')
OWNER = re.compile(r'(0|[1-9][0-9]{0,8}):(0|[1-9][0-9]{0,8})\Z')
COPIES = (1, 2)
LIFETIMES = {'until-removed': 'kept until someone removes it', 'release': 'kept for one release',
             'scratch': 'may be emptied at any time'}
KEYS = ('name', 'role', 'target', 'service', 'writable', 'owner', 'distribution', 'association', 'copies', 'lifetime', 'sizeBytes')
OK, NOT_BOUND, NOT_DECLARED, UNREADABLE = 'ok', 'not bound', 'not declared', 'declaration cannot be read'
ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError)


def declared(listed):
    """The slots a release declares, each with every field filled in.  ValueError says what is wrong with them."""
    if not isinstance(listed, list): raise ValueError('the storage slots of a release are a list')
    out = []
    for entry in listed:
        if not isinstance(entry, dict) or set(entry) - set(KEYS): raise ValueError('a storage slot has only: ' + ', '.join(KEYS))
        given = {k: v for k, v in entry.items() if v is not None}
        name, role = given.get('name'), given.get('role')
        if not isinstance(name, str) or not SLOT.match(name):
            raise ValueError('the name of a storage slot is made of lower-case letters, digits and -')
        what = 'the storage slot %s' % name
        if not isinstance(role, str) or role not in sr.ROLES: raise ValueError('%s needs a role, one of: %s' % (what, ', '.join(sr.ROLES)))
        allows = storage_kinds.of(role)
        slot = dict(name=name, role=role, kind=allows['kind'], target=given.get('target'), service=given.get('service'),
                    writable=given.get('writable', False), owner=given.get('owner'),
                    distribution=given.get('distribution', allows['distribution'][0]),
                    association=given.get('association', 'app' if 'app' in allows['association'] else allows['association'][0]),
                    copies=given.get('copies', 1), lifetime=given.get('lifetime', 'until-removed'), sizeBytes=given.get('sizeBytes'))
        try: target = _absolute(slot['target'], 'It').rstrip('/')
        except ValueError: target = ''
        if not target or os.path.normpath(target) != target:
            raise ValueError('%s needs a target: the plain absolute path of the folder the app sees, and not /' % what)
        slot['target'] = target
        if slot['service'] is not None and (not isinstance(slot['service'], str) or not SERVICE.match(slot['service'])):
            raise ValueError('%s names a service that is not the name of a service' % what)
        if type(slot['writable']) is not bool: raise ValueError('%s: writable is true or false' % what)
        if slot['owner'] is not None and (not isinstance(slot['owner'], str) or not OWNER.match(slot['owner'])):
            raise ValueError('%s: owner is two numbers, <user>:<group>' % what)
        for field in ('distribution', 'association'):
            if slot[field] not in allows[field]:
                raise ValueError('%s: the role %s allows the %s: %s' % (what, role, field, ', '.join(allows[field])))
        if type(slot['copies']) is not int or slot['copies'] not in COPIES: raise ValueError('%s: copies is 1 or 2' % what)
        if not isinstance(slot['lifetime'], str) or slot['lifetime'] not in LIFETIMES: raise ValueError('%s: lifetime is one of: %s' % (what, ', '.join(LIFETIMES)))
        if slot['sizeBytes'] is not None and (type(slot['sizeBytes']) is not int or slot['sizeBytes'] <= 0):
            raise ValueError('%s: sizeBytes is a number of bytes above 0' % what)
        if any(o['name'] == name for o in out): raise ValueError('two storage slots have the name %s' % name)
        if any((o['service'], o['target']) == (slot['service'], target) for o in out):
            raise ValueError('two storage slots have the target %s in the same service' % target)
        out.append(slot)
    return out


def key(app, name):
    return '%s/%s' % (app, name)


def bindings(records):
    """What this machine has bound: {'<app>/<slot>': {role, filesystemUuid, boundAt}}.  A list that is damaged is an
    error, never an empty one: an empty one would hide the slots that are bound."""
    held = records.get('slots', {})
    try:
        good = all(
            APP.match(k.split('/')[0]) and SLOT.match(k.split('/')[1]) and len(k.split('/')) == 2 and v['role'] in sr.ROLES
            and sr.IDENTIFIER.match(v['filesystemUuid']) for k, v in held.items())
    except (KeyError, TypeError, AttributeError, IndexError): good = False
    if not good: raise ValueError('the storage slots in the records of this machine are damaged')
    return held


def bound_to(records, identifier, roles=None):
    """The slots bound to one partition (under these roles, or under any): ['<app>/<slot>']."""
    return sorted(k for k, v in bindings(records).items()
                  if v['filesystemUuid'] == identifier and (roles is None or v['role'] in roles))


def _file(io, app):
    return Path(io.apps_dir) / app / FILE


def read_declaration(io, app):
    """(the slots an app declares, what is wrong with its declaration).  ([], None) when it declares none."""
    path = _file(io, app)
    try: text = path.read_text()
    except (FileNotFoundError, NotADirectoryError): return [], None
    except OSError as exc: return None, 'its declaration %s cannot be read (%s)' % (path, exc.strerror)
    try:
        doc = json.loads(text)
        if not isinstance(doc, dict) or (doc.get('kind'), doc.get('version'), doc.get('app')) != (DOC_KIND, 1, app): raise ValueError
        return declared(doc.get('slots')), None
    except ValueError: return None, 'its declaration %s is not one this CLI understands' % path


def apps(io):
    try: names = sorted(os.listdir(io.apps_dir))
    except (FileNotFoundError, NotADirectoryError): return []
    return [name for name in names if APP.match(name) and os.path.lexists(_file(io, name))]


def _folder(mount, app, name):
    return os.path.join(mount, FOLDER, app, name)


def _is_folder(mount, app, name):
    """A real folder of the partition itself: no link on the way to it."""
    folder = _folder(mount, app, name)
    return os.path.isdir(folder) and os.path.realpath(folder) == _folder(os.path.realpath(mount), app, name)


def _state(app, slot, binding, present):
    found = present.get(binding['filesystemUuid'], [])
    if not found: return 'partition away', None
    if len(found) > 1: return 'partition needs attention', None
    part = found[0]
    if binding['role'] != slot['role']: return 'declared with another role', None
    if part['recordedRoles'] and part['roleState'] in sr.AWAY: return 'partition away', None
    if slot['role'] not in part['recordedRoles']: return 'role taken away', None
    if part['roleState'] != 'ok': return 'partition needs attention', None
    if not _is_folder(part['wholeMount'], app, slot['name']): return 'folder missing', None
    folder = _folder(part['wholeMount'], app, slot['name'])
    return ('mounted read-only' if slot['writable'] and part['mountedReadOnly'] else OK), folder


def _present(partitions):
    present = {}
    for part in partitions:
        if part['state'] != 'not present' and part['filesystemUuid']: present.setdefault(part['filesystemUuid'], []).append(part)
    return present


def rows(io):
    """Every slot an app of this machine declares and every slot this machine has bound, each with its state."""
    records, _, partitions = sr.survey(io)
    held, present = bindings(records), _present(partitions)
    out, seen, broken = [], set(), set()

    def row(app, name, slot, binding, state, folder=None, problem=None):
        fields = {k: (slot or {}).get(k) for k in ('role', 'kind') + KEYS[2:]}
        if binding and not slot: fields.update(role=binding['role'], kind=storage_kinds.ROLE[binding['role']][0])
        out.append(dict(fields, app=app, slot=name, filesystemUuid=binding['filesystemUuid'] if binding else None,
                        folder=folder, state=state, problem=problem))
    for app in apps(io):
        slots, problem = read_declaration(io, app)
        if problem:
            broken.add(app); row(app, None, None, None, UNREADABLE, problem=problem); continue
        for slot in slots:
            binding = held.get(key(app, slot['name']))
            seen.add(key(app, slot['name']))
            row(app, slot['name'], slot, binding, *(_state(app, slot, binding, present) if binding else (NOT_BOUND, None)))
    for bound, binding in sorted(held.items()):
        if bound in seen: continue
        app, name = bound.split('/')
        row(app, name, None, binding, UNREADABLE if app in broken else NOT_DECLARED)
    return out


def binds(io, app):
    """What an app's start gives it: [{service, source, target, readOnly}], one for each slot it declares.
    ValueError names the first slot that cannot be given as it stands; then the app is not to be started."""
    mine = [r for r in rows(io) if r['app'] == app]
    for r in mine:
        if r['state'] != OK:
            raise ValueError('the storage slot %s of %s cannot be used: %s. See: isle storage slot check --app %s --table' % (
                r['slot'] or '', app, r['problem'] or r['state'], app))
    return [dict(service=r['service'], source=r['folder'], target=r['target'], readOnly=not r['writable']) for r in mine]


def write_declaration(io, app, listed):
    """Keep what a release declares for an app.  True when the file changed.  ValueError when a slot that is bound
    here would be dropped by it or would change its role: that is said before anything is written."""
    slots = {s['name']: s for s in declared(listed)}
    doc = dict(kind=DOC_KIND, version=1, app=app, slots=listed)
    with sr.locked(io):
        for bound, binding in bindings(sr.read_records(io.records)).items():
            owner, name = bound.split('/')
            if owner != app: continue
            first = 'First: sudo isle storage slot unbind %s %s' % (app, name)
            if name not in slots:
                raise ValueError('its slot %s is bound to the partition %s, and this release does not declare it. %s' % (
                    name, binding['filesystemUuid'], first))
            if slots[name]['role'] != binding['role']:
                raise ValueError('its slot %s is bound with the role %s, and this release declares it with the role %s. %s' % (
                    name, binding['role'], slots[name]['role'], first))
        path = _file(io, app)
        try:
            if json.loads(path.read_text()) == doc: return False
        except (OSError, ValueError): pass
        if path.is_symlink(): raise ValueError('%s is a link' % path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        atomic(path, doc)
    return True


def declare(io, app, source):
    what = 'slot declare'
    if not APP.match(app or ''): return sr._refuse(io, what, 'that is not the name of an app')
    try:
        try: doc = json.loads(Path(source).read_text())
        except OSError as exc: return sr._refuse(io, what, '%s cannot be read (%s)' % (source, exc.strerror))
        except ValueError: return sr._refuse(io, what, '%s is not JSON' % source)
        if not isinstance(doc, dict) or KEY not in doc:
            return sr._refuse(io, what, '%s has no list `%s`, which is where a release declares its storage slots' % (source, KEY))
        slots = declared(doc[KEY])
        try: same = json.loads(_file(io, app).read_text()) == dict(kind=DOC_KIND, version=1, app=app, slots=doc[KEY])
        except (OSError, ValueError): same = False
        if same:
            io.say('The app %s already declares these storage slots. Nothing was changed.' % app); return 0
        if not io.is_root():
            io.say('This writes what the app %s declares (%s). Type: sudo isle storage slot declare %s --from %s' % (app, _file(io, app), app, source))
            return 1
        write_declaration(io, app, doc[KEY])
    except ERRORS as exc:
        return sr._refuse(io, what, str(exc) or exc.__class__.__name__)
    io.say('The app %s declares %s: %s.' % (app, '%d storage slots' % len(slots) if len(slots) != 1 else '1 storage slot',
                                          ', '.join('%s (role %s)' % (s['name'], s['role']) for s in slots) or 'none'))
    io.say('Nothing was bound, made or started. See: isle storage slot list --table')
    return 0


def make_folder(io, mount, app, name, owner):
    """Make isle-slots/<app>/<slot> at the root of a mounted partition, never through a link.  True when the slot's
    own folder was made now; one that is there already is kept as it is, with what is in it and with its owner.
    When it fails, the folders it made are taken away again."""
    parts, new = (FOLDER, app, name), []
    top = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fd = os.dup(top)
        try:
            for index, part in enumerate(parts):
                try: os.mkdir(part, 0o755, dir_fd=fd); new.append(os.path.join(*parts[:index + 1]))
                except FileExistsError: pass
                below = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd); fd = below
                if new: os.fchmod(fd, 0o755)       # once one was made now, every folder below it was made now too
            made = os.path.join(*parts) in new
            if made and owner: io.chown(fd, *(int(n) for n in owner.split(':')))
            os.fsync(fd)
        except BaseException:
            for path in reversed(new):
                try: os.rmdir(path, dir_fd=top)
                except OSError: pass
            raise
        finally: os.close(fd)
    finally: os.close(top)
    return made


def bind(io, app, name, identifier):
    what = 'slot bind'
    if not APP.match(app or '') or not SLOT.match(name or ''): return sr._refuse(io, what, 'that is not an app and a storage slot of it')
    if not sr.IDENTIFIER.match(identifier or ''): return sr._refuse(io, what, 'that is not a filesystem identifier')
    bound, typed = key(app, name), 'sudo isle storage slot bind %s %s --partition %s' % (app, name, identifier)
    try:
        slots, problem = read_declaration(io, app)
        if problem: return sr._refuse(io, what, 'the app %s: %s' % (app, problem))
        slot = next((s for s in slots if s['name'] == name), None)
        if not slot: return sr._refuse(io, what, 'the app %s declares no storage slot %s (see: isle storage slot list --table)' % (app, name))
        role = slot['role']
        records, _, partitions = sr.survey(io)
        was = bindings(records).get(bound)
        if was and was['filesystemUuid'] != identifier:
            return sr._refuse(io, what, 'it is bound to the partition %s already. To bind it to another one, first: sudo isle storage slot unbind %s %s '
                              '(its data stays on that partition; no verb moves it yet)' % (was['filesystemUuid'], app, name))
        if was and was['role'] != role:
            return sr._refuse(io, what, 'it is bound with the role %s and declared with the role %s. First: sudo isle storage slot unbind %s %s' % (
                was['role'], role, app, name))
        found = _present(partitions).get(identifier, [])
        if not found: return sr._refuse(io, what, 'no partition with the identifier %s is present on this machine (see: isle storage partition list --table)' % identifier)
        if len(found) > 1: return sr._refuse(io, what, 'two partitions report the identifier %s (one is a copy of the other), so it does not name one partition' % identifier)
        part = found[0]
        pname = 'partition %s' % part['name']
        if role not in part['recordedRoles']:
            carry = sorted(p['filesystemUuid'] for p in partitions if role in p['recordedRoles'])
            return sr._refuse(io, what, '%s does not carry the role %s on this machine, and the slot needs it. %s' % (pname, role, (
                'It is carried by: %s' % ', '.join(carry)) if carry else 'No partition of this machine carries it. To give it: sudo isle storage partition assign %s --role %s' % (identifier, role)))
        if part['roleState'] != 'ok':
            return sr._refuse(io, what, '%s needs attention first (%s). See: isle storage partition check --table' % (pname, part['roleState']))
        if slot['writable'] and part['mountedReadOnly']: return sr._refuse(io, what, '%s is mounted read-only, and the app writes to this slot' % pname)
        folder = _folder(part['wholeMount'], app, name)
        there = _is_folder(part['wholeMount'], app, name)
        if os.path.lexists(folder) and not there: return sr._refuse(io, what, '%s is there and is not a folder of the partition itself' % folder)
        if was and there:
            io.say('Slot %s of %s is bound to %s (%s) already. Nothing was changed.' % (name, app, pname, identifier)); return 0
        if slot['sizeBytes'] and not there and slot['sizeBytes'] > part.get('availableBytes', 0):
            return sr._refuse(io, what, 'the slot asks for %s and %s has %s free' % (
                storage_devices._size(slot['sizeBytes']), pname, storage_devices._size(part.get('availableBytes', 0))))
        if not io.is_root():
            io.say('This makes the folder %s and changes the storage records of this machine. Type: %s' % (folder, typed)); return 1
        with sr.locked(io):
            records = sr.read_records(io.records)
            if bindings(records).get(bound) != was: return sr._refuse(io, what, 'the binding of this slot changed while this command ran')
            recorded = records['partitions'].get(identifier)
            if not recorded or role not in sr.roles_of(recorded): return sr._refuse(io, what, 'this machine no longer records %s with the role %s' % (pname, role))
            still = sr.whole_mount(io.mounts(), identifier)[0]      # looked at again, now that the lock is held
            if not still or still['target'] != part['wholeMount'] or not io.is_mount(part['wholeMount']):
                return sr._refuse(io, what, '%s is no longer mounted at %s' % (pname, part['wholeMount']))
            made = make_folder(io, part['wholeMount'], app, name, slot['owner'])
            records.setdefault('slots', {})[bound] = dict(role=role, filesystemUuid=identifier, boundAt=was['boundAt'] if was else io.now())
            atomic(Path(io.records), records)
    except ERRORS as exc:
        return sr._refuse(io, what, str(exc) or exc.__class__.__name__)
    io.say('Slot %s of %s is bound to %s (%s), under its role %s.' % (name, app, pname, identifier, role))
    if made: io.say('Its data is the folder %s, made now%s.' % (folder, ' and owned by %s' % slot['owner'] if slot['owner'] else ''))
    else: io.say('Its data is the folder %s. It was there already: what is in it and its owner were kept.' % folder)
    io.say('The app sees it at %s%s, and %s.' % (slot['target'], ' in its service %s' % slot['service'] if slot['service'] else '',
                                                'writes to it' if slot['writable'] else 'only reads it'))
    if slot['copies'] > 1: io.say('The release asks for %d copies. One is bound here; nothing keeps a second copy yet.' % slot['copies'])
    io.say('Nothing was started or stopped.')
    return 0


def unbind(io, app, name):
    what = 'slot unbind'
    if not APP.match(app or '') or not SLOT.match(name or ''): return sr._refuse(io, what, 'that is not an app and a storage slot of it')
    bound = key(app, name)
    try:
        was = bindings(sr.read_records(io.records)).get(bound)
        if not was:
            io.say('Slot %s of %s is not bound on this machine. Nothing was changed.' % (name, app)); return 0
        if not io.is_root():
            io.say('This changes the storage records of this machine. Type: sudo isle storage slot unbind %s %s' % (app, name)); return 1
        with sr.locked(io):
            records = sr.read_records(io.records)
            if bindings(records).get(bound) != was: return sr._refuse(io, what, 'the binding of this slot changed while this command ran')
            del records['slots'][bound]
            if not records['slots']: del records['slots']
            atomic(Path(io.records), records)
    except ERRORS as exc:
        return sr._refuse(io, what, str(exc) or exc.__class__.__name__)
    io.say('Slot %s of %s is no longer bound to the partition %s. No data was moved or removed.' % (name, app, was['filesystemUuid']))
    io.say('Its folder %s/%s/%s on that partition is left where it is.' % (FOLDER, app, name))
    io.say('An app that is running keeps the folder until it is started again. `isle storage slot check --app %s` fails while the slot is declared and not bound.' % app)
    return 0


def _table(found):
    return sr._table(('APP', 'SLOT', 'ROLE', 'KIND', 'THE APP SEES IT AT', 'WRITES', 'COPIES', 'PARTITION', 'STATE'), [
        (r['app'], r['slot'] or '-', r['role'] or '-', r['kind'] or '-', r['target'] or '-',
         '-' if r['writable'] is None else 'yes' if r['writable'] else 'no', r['copies'] or '-', r['filesystemUuid'] or '-', r['state']) for r in found])


def report(io, argv):
    plain = '--table' in argv
    words = [a for a in argv if a != '--table']
    check, app = words[1] == 'check', None
    if check and len(words) == 4 and words[2] == '--app' and APP.match(words[3]): app = words[3]
    elif len(words) != 2:
        io.say(sr.USAGE); return 2

    def answer(ok, **body):
        io.say(json.dumps(dict(ok=ok, evaluatedAt=io.now(), operation='storage.slot.' + words[1], readOnly=True, **body)))
        return 0 if ok else 1
    try:
        import storage_slot_gate
        found = [r for r in rows(io) if app is None or r['app'] == app]
        late = storage_slot_gate.out_of_date(io, found)
    except ERRORS as exc: return answer(False, error=str(exc) or exc.__class__.__name__)
    bad = [r for r in found if r['state'] != OK]
    if not plain:
        return answer(not (check and bad), result=dict(slots=found, needAttention=[key(r['app'], r['slot'] or '') for r in bad], unitsOutOfDate=late))
    io.say(_table(found) if found else 'The app %s declares no storage slot.' % app if app else 'No app of this machine declares a storage slot.')
    for r in bad:
        if r['state'] == NOT_BOUND:
            io.say('Slot %s of %s is not bound. Type: sudo isle storage slot bind %s %s --partition <filesystem-identifier>   (those with its role: isle storage role show %s --table)' % (
                r['slot'], r['app'], r['app'], r['slot'], r['role']))
        elif r['problem']: io.say('The app %s: %s.' % (r['app'], r['problem']))
    for name in late:
        io.say('The unit that starts and stops %s with its partitions does not name the partitions its slots are bound to now. '
               'Type: sudo isle storage slot gate %s' % (name, name))
    if check: io.say('%d of %d need attention.' % (len(bad), len(found)) if bad else 'The slots themselves need no attention.' if late else 'Nothing needs attention.')
    return 1 if check and bad else 0


def main(io, argv):
    if argv[1:2] in (['list'], ['check']): return report(io, argv)
    if len(argv) == 5 and argv[1] == 'declare' and argv[3] == '--from': return declare(io, argv[2], argv[4])
    if len(argv) == 6 and argv[1] == 'bind' and argv[4] == '--partition': return bind(io, argv[2], argv[3], argv[5])
    if len(argv) == 4 and argv[1] == 'unbind': return unbind(io, argv[2], argv[3])
    if argv[1:2] in (['ready'], ['gate'], ['ungate']):
        import storage_slot_gate
        return storage_slot_gate.main(io, argv)
    io.say(sr.USAGE)
    return 2
