#!/usr/bin/env python3
"""isle storage device health <serial> [--table]

Asks one disk of this machine for its own health report (SMART, through `smartctl`) and says in plain words what
it found: the disk's verdict, how long it has been powered on, and every count that should be zero and is not.
When the device is registered, the result is kept in this machine's storage record, so a later step can see that
the disk was looked at and when.

It reads the disk's report and nothing else: no test is started on the disk and nothing is written to it.
"""
import json
from pathlib import Path
import re
import shlex
import subprocess

from operations import atomic
import storage_devices
import storage_roles as sr

TOOL = 'smartctl'
PACKAGE = 'smartmontools'
NODE = re.compile(r'[A-Za-z0-9]+\Z')
#: counts of a disk that should be zero: {attribute number: what a count above zero means}
ATA = {5: 'sectors replaced by spare ones', 187: 'read errors that could not be corrected',
       197: 'sectors waiting to be read again', 198: 'sectors that cannot be read'}
NO_ANSWER = 3       # smartctl exit bits 0 and 1: the command line was not understood, or the device did not open


class NoReport(Exception):
    pass


def ask(io, node, bridge=False):
    """(exit status, report) of the disk at this node.  The report is None when smartctl printed none."""
    command = [TOOL, '--json=c', '--info', '--health', '--attributes'] + (['--device', 'sat'] if bridge else []) + ['/dev/' + node]
    try: done = io.run(command, capture_output=True, text=True, timeout=60)
    except FileNotFoundError: raise NoReport('%s is not installed on this machine (package %s). Type: sudo isle apt install %s' % (TOOL, PACKAGE, PACKAGE))
    except subprocess.TimeoutExpired: raise NoReport('the disk did not answer within 60 seconds')
    try: doc = json.loads(done.stdout or '')
    except ValueError: doc = None
    return done.returncode, doc if isinstance(doc, dict) else None


def report_of(io, device):
    """The disk's report.  A disk behind a USB bridge is asked a second time, as a SATA disk, when the first try
    gets no answer."""
    code, doc = ask(io, device['name'])
    if (code & NO_ANSWER or doc is None) and device['transport'] == 'usb':
        code, doc = ask(io, device['name'], bridge=True)
    if code & NO_ANSWER or doc is None:
        said = [m.get('string') for m in ((doc or {}).get('smartctl') or {}).get('messages') or [] if isinstance(m, dict) and isinstance(m.get('string'), str)]
        raise NoReport('the disk gave no health report (%s)' % (said[-1].strip() if said else '%s ended with exit %d' % (TOOL, code)))
    return code, doc


def _part(doc, name):
    return doc.get(name) if isinstance(doc.get(name), dict) else {}


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def summary(code, doc):
    """What the report says: {verdict, attention: [words], powerOnHours, temperatureCelsius, reportedSerial}."""
    passed = _part(doc, 'smart_status').get('passed')
    verdict = 'passed' if passed is True else 'failing' if passed is False else 'not reported'
    attention = []
    if passed is False or code & 8: attention.append('the disk reports that it is failing')
    if code & 16: attention.append('a value that warns of failure is at or below its limit')
    table = _part(doc, 'ata_smart_attributes').get('table')
    for row in table if isinstance(table, list) else []:
        count = _count(_part(row, 'raw').get('value')) if isinstance(row, dict) else None
        if count and row.get('id') in ATA: attention.append('%s: %d' % (ATA[row['id']], count))
    nvme = _part(doc, 'nvme_smart_health_information_log')
    if _count(nvme.get('critical_warning')): attention.append('the disk raises a critical warning (code %d)' % nvme['critical_warning'])
    if _count(nvme.get('media_errors')): attention.append('errors on the medium: %d' % nvme['media_errors'])
    spare, limit = _count(nvme.get('available_spare')), _count(nvme.get('available_spare_threshold'))
    if spare is not None and limit is not None and spare < limit: attention.append('%d%% of the spare space is left, below the limit of %d%%' % (spare, limit))
    if (_count(nvme.get('percentage_used')) or 0) >= 100: attention.append('the disk has reached the wear it is rated for (%d%%)' % nvme['percentage_used'])
    hours = _count(_part(doc, 'power_on_time').get('hours'))
    return dict(verdict=verdict, attention=attention,
                powerOnHours=hours if hours is not None else _count(nvme.get('power_on_hours')),
                temperatureCelsius=_count(_part(doc, 'temperature').get('current')),
                reportedSerial=doc.get('serial_number') if isinstance(doc.get('serial_number'), str) else None)


def health(io, serial, plain):
    evaluated = io.now()

    def refuse(why):
        if plain: io.say('REFUSED: device health: %s. Nothing was changed.' % why)
        else: io.say(json.dumps(dict(ok=False, evaluatedAt=evaluated, operation='storage.device.health', error=why)))
        return 1
    if not sr.SERIAL.match(serial or ''): return refuse('that is not a hardware serial')
    try:
        records = sr.read_records(io.records)
        found = [d for d in storage_devices.devices(io.tree()) if d['serial'] == serial]
        if not found: return refuse('no storage device with the serial %s is present on this machine (see: isle storage device list --table)' % serial)
        if len(found) > 1: return refuse('two devices report the serial %s, so it does not name one device' % serial)
        device = found[0]
        if not NODE.match(device['name']): return refuse('the device has no name this CLI can ask by')
        if not io.is_root():
            io.say('Reading a disk\'s own health report needs root. Type: sudo isle storage device health %s%s' % (shlex.quote(serial), ' --table' if plain else ''))
            return 1
        found = summary(*report_of(io, device))
        registered = serial in records['devices']
        if registered:
            with sr.locked(io):
                records = sr.read_records(io.records)
                registered = serial in records['devices']
                if registered:
                    records['devices'][serial]['health'] = dict(checkedAt=evaluated, verdict=found['verdict'], attention=found['attention'],
                                                                powerOnHours=found['powerOnHours'])
                    atomic(Path(io.records), records)
    except NoReport as exc: return refuse(str(exc))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
        return refuse(str(exc) or exc.__class__.__name__)
    ok = found['verdict'] == 'passed' and not found['attention']
    other = found['reportedSerial'] if found['reportedSerial'] not in (None, serial) else None
    if not plain:
        io.say(json.dumps(dict(ok=ok, evaluatedAt=evaluated, operation='storage.device.health', result=dict(
            found, serial=serial, model=device['model'], sizeBytes=device['sizeBytes'], medium=device['medium'],
            **{'class': (records['devices'].get(serial) or {}).get('class')}, recorded=registered))))
        return 0 if ok else 1
    io.say('Device %s (%s, %s): %s.' % (serial, device['model'] or 'no model reported', storage_devices._size(device['sizeBytes']),
           {'passed': 'the disk\'s own verdict is that it is healthy', 'failing': 'the disk\'s own verdict is that it is FAILING',
            'not reported': 'the disk gives no verdict about its health'}[found['verdict']]))
    if found['powerOnHours'] is not None:
        io.say('Powered on for %d hours (%.1f years).' % (found['powerOnHours'], found['powerOnHours'] / 8766.0))
    if found['temperatureCelsius'] is not None: io.say('Temperature now: %d C.' % found['temperatureCelsius'])
    io.say('Needs attention: %s.' % '; '.join(found['attention']) if found['attention'] else 'No count that should be zero is above zero.')
    if other: io.say('The disk itself reports the serial %s; the serial above is the one this machine sees (a dock or a bridge can report its own).' % other)
    io.say('The result is kept in the storage record of this machine.' if registered else
           'The result is not kept: the device is not registered (sudo isle storage device register %s --class %s).' % (shlex.quote(serial), '|'.join(sr.CLASSES)))
    return 0 if ok else 1


def main(io, argv):
    words = [a for a in argv if a != '--table']
    if len(words) == 3 and words[:2] == ['device', 'health'] and argv.count('--table') <= 1:
        return health(io, words[2], '--table' in argv)
    io.say(sr.USAGE)
    return 2
