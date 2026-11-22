#!/usr/bin/env python3
"""isle storage slot ready <app>     read-only, for anyone
   isle storage slot gate <app>      root
   isle storage slot ungate <app>    root

gate writes one systemd unit, isle-app-<app>.service, for an app that is deployed with storage slots.  The unit is
bound to the mounts of the partitions the slots are bound to: when one of them goes away the app is stopped, and when
they are there again the app is started.  The unit is wanted by those mounts, not by the start of the machine, so a
disk that is away holds nothing up.  Before each start the unit asks `slot ready`: every slot usable, and the app's
overlay giving exactly the folders that are bound now.

gate starts the unit, so an app that is not running is started by it.  ungate takes the unit away and leaves the app
as it is, running or not.  Only a unit that `gate` wrote is ever changed or removed.

Nothing here erases or moves data, and nothing here writes an overlay or changes what an app is given.
"""
import os
from pathlib import Path
import subprocess

from operations import NAME as APP
from storage_mount import ISLE_CLI, mount_unit_name
import storage_roles as sr
import storage_slot_start
import storage_slots
from storage_units import _write

HEAD = '# Written by: isle storage slot gate %s'
COMPOSE = 'docker-compose.yml'
ERRORS = storage_slot_start.ERRORS


class Refused(Exception):
    pass


def unit_name(app):
    return 'isle-app-%s.service' % app


def unit_text(app, apps_dir, mounts):
    """The unit that starts and stops one app with the mounts of its storage slots."""
    units = ' '.join(mount_unit_name(mount) for mount in mounts)
    compose = '/usr/bin/docker compose -p isle-%s -f %s -f %s' % (app, COMPOSE, storage_slot_start.OVERLAY)
    return '\n'.join([
        HEAD % app,
        '[Unit]',
        'Description=Isle app %s, started and stopped with the partitions of its storage slots' % app,
        'BindsTo=' + units,
        'After=%s docker.service' % units,
        'Wants=docker.service',
        '',
        '[Service]',
        'Type=oneshot',
        'RemainAfterExit=yes',
        'WorkingDirectory=%s/%s' % (apps_dir, app),
        'ExecStartPre=%s storage slot ready %s' % (ISLE_CLI, app),
        # the compose call of app-deploy.sh, from the app's folder with both files; never a build, never a pull
        'ExecStart=%s up -d --no-build --pull never' % compose,
        'ExecStop=%s stop' % compose,
        '',
        '[Install]',
        'WantedBy=' + units,
        ''])


def released_text(app):
    """A unit with the same name that stops nothing.  `ungate` puts it in place before it stops the unit, so that the
    stop does not reach the app."""
    return '\n'.join([
        HEAD % app,
        '[Unit]',
        'Description=Isle app %s, no longer started or stopped with its partitions' % app,
        '',
        '[Service]',
        'Type=oneshot',
        'RemainAfterExit=yes',
        'ExecStart=/bin/true',
        ''])


def kind(io, app):
    """'none' when the app has no unit, 'ours' when `gate` wrote it, 'foreign' when something else has that name."""
    path = Path(io.unit_dir) / unit_name(app)
    if not os.path.lexists(path): return 'none'
    if path.is_symlink() or not path.is_file(): return 'foreign'
    return 'ours' if path.read_text(errors='replace').splitlines()[:1] == [HEAD % app] else 'foreign'


def expected(io, app, rows):
    """The unit text for an app as its slots are bound now, or None when a slot of it cannot be used as it stands."""
    mine = [r for r in rows if r['app'] == app]
    if not mine or any(r['state'] != storage_slots.OK for r in mine): return None
    # a slot's folder is <the whole mount>/isle-slots/<app>/<slot>
    return unit_text(app, str(io.apps_dir), sorted({str(Path(r['folder']).parents[2]) for r in mine}))


def out_of_date(io, rows):
    """The apps whose unit is not the one `gate` would write now: their slots were bound elsewhere since."""
    late = []
    for app in sorted({r['app'] for r in rows}):
        text = expected(io, app, rows)
        if text is not None and kind(io, app) == 'ours' and (Path(io.unit_dir) / unit_name(app)).read_text(errors='replace') != text:
            late.append(app)
    return late


def ready(io, app):
    if not APP.match(app or ''):
        io.say(sr.USAGE); return 2
    if not os.path.lexists(storage_slots._file(io, app)):
        io.say('The app %s declares no storage slot.' % app); return 0
    why = storage_slot_start.gate(Path(io.apps_dir) / app, io)
    if why:
        io.say('NOT READY: %s.' % why.rstrip('.')); return 1
    io.say('The app %s can be started with its storage slots as it is deployed.' % app)
    return 0


def _systemctl(io, *args):
    return io.run(['systemctl'] + list(args), capture_output=True, text=True, timeout=180)


def _is(io, question, unit):
    try: return _systemctl(io, question, unit).returncode == 0
    except (OSError, subprocess.SubprocessError): return False


def _steps(io, steps, reached):
    for step in steps:
        if callable(step): step()
        else:
            done = _systemctl(io, *step)
            if done.returncode != 0:
                lines = [line.strip() for line in (done.stderr or '').splitlines() if line.strip()]
                raise ValueError('systemctl %s ended with exit %d%s' % (step[0], done.returncode, ' (%s)' % lines[-1] if lines else ''))
        reached.append(step if callable(step) else step[0])


def _usable(io):
    try: found = _systemctl(io, '--version')
    except FileNotFoundError: raise Refused('systemctl is not on this machine, so there is nothing to keep a unit')
    if found.returncode != 0: raise Refused('systemctl does not answer (exit %d)' % found.returncode)


def gate(io, app):
    what = 'slot gate'
    if not APP.match(app or ''): return sr._refuse(io, what, 'that is not the name of an app')
    name, typed, reached = unit_name(app), 'sudo isle storage slot gate %s' % app, []
    path, directory = Path(io.unit_dir) / name, Path(io.apps_dir) / app
    try:
        slots, problem = storage_slots.read_declaration(io, app)
        if problem: raise Refused('the app %s: %s' % (app, problem))
        if not slots: raise Refused('the app %s declares no storage slot, so no partition has to be there for it' % app)
        if os.path.lexists(directory / 'storage-bind.json'):
            raise Refused('the app %s hosts a dataset, and the unit of that dataset starts and stops it already' % app)
        for needed in (COMPOSE, storage_slot_start.OVERLAY):
            if not (directory / needed).is_file(): raise Refused('the app %s is not deployed on this machine (it has no %s). First install or deploy it' % (app, needed))
        why = storage_slot_start.gate(directory, io)
        if why: raise Refused(why)
        text = expected(io, app, storage_slots.rows(io))
        if text is None: raise Refused('the storage slots of %s changed while this command ran' % app)
        found = kind(io, app)
        if found == 'foreign': raise Refused('a unit %s exists, and this verb did not write it' % name)
        if found == 'ours' and path.read_text(errors='replace') == text and _is(io, 'is-enabled', name) and _is(io, 'is-active', name):
            io.say('The app %s is started and stopped with its partitions already (unit %s). Nothing was changed.' % (app, name)); return 0
        if not io.is_root():
            io.say('This writes the unit %s and starts it: the app %s is started if it is not running. Type: %s' % (name, app, typed)); return 1
        _usable(io)
        with sr.locked(io):
            _steps(io, [lambda: _write(str(path), text), ('daemon-reload',), ('reenable', name), ('start', name)], reached)
    except (Refused,) + ERRORS as exc:
        why = (str(exc) or exc.__class__.__name__).rstrip('.')
        if not reached: return sr._refuse(io, what, why)
        io.say('FAILED: slot gate: %s. The unit %s is written%s, and it did not start. To try again, type the same command; to take the unit away: '
               'sudo isle storage slot ungate %s' % (why, name, ' and enabled' if 'reenable' in reached else '', app))
        return 1
    io.say('The app %s is now started and stopped with the partitions of its storage slots (unit %s).' % (app, name))
    io.say('It is stopped when one of them goes away, and started when they are there again. A start of this machine does not wait for them.')
    io.say('To take the unit away and leave the app as it is: sudo isle storage slot ungate %s' % app)
    return 0


def ungate(io, app):
    what = 'slot ungate'
    if not APP.match(app or ''): return sr._refuse(io, what, 'that is not the name of an app')
    name, reached = unit_name(app), []
    path = Path(io.unit_dir) / name
    try:
        found = kind(io, app)
        if found == 'none':
            io.say('The app %s has no unit of `isle storage slot gate`. Nothing was changed.' % app); return 0
        if found == 'foreign': raise Refused('a unit %s exists, and this verb did not write it' % name)
        if not io.is_root():
            io.say('This removes the unit %s. The app %s is left as it is, running or not. Type: sudo isle storage slot ungate %s' % (name, app, app)); return 1
        _usable(io)
        with sr.locked(io):
            reached.append('begun')
            # the stop of the unit must not stop the app: the unit is first replaced by one that stops nothing
            _steps(io, [('disable', name), lambda: _write(str(path), released_text(app)), ('daemon-reload',), ('stop', name),
                        lambda: os.unlink(path), ('daemon-reload',)], reached)
    except (Refused,) + ERRORS as exc:
        why = (str(exc) or exc.__class__.__name__).rstrip('.')
        if not reached: return sr._refuse(io, what, why)
        gone = not os.path.lexists(path)
        io.say('FAILED: slot ungate: %s. %s' % (why, (
            'The unit %s is removed; systemd has not read that yet (a restart of the machine does it).' % name) if gone else (
            'The unit %s is kept%s. To finish, type the same command.' % (name, ', and it is no longer enabled' if 'disable' in reached else ''))))
        return 1
    io.say('The unit %s is removed. The app %s was left as it is, and nothing stops it now when a partition of its storage slots goes away.' % (name, app))
    return 0


def main(io, argv):
    if len(argv) == 3 and argv[1] == 'ready': return ready(io, argv[2])
    if len(argv) == 3 and argv[1] == 'gate': return gate(io, argv[2])
    if len(argv) == 3 and argv[1] == 'ungate': return ungate(io, argv[2])
    io.say(sr.USAGE)
    return 2
