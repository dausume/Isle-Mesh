#!/usr/bin/env python3
"""isle polari security provider [<issuer-address> [--client <id>] | none | require <what> | unrequire <what>]

Which Keycloak this isle trusts for sign-in, and what on this isle needs a signed-in person. There is one Keycloak
for the whole isle and it may run on any device of it. The record is kept on the core and published to the members
in the core's release notice.

Naming a Keycloak switches nothing on: sign-in stays off until `isle polari security enable|require`, and no
backend rule changes. With none named, everything an isle does without sign-in works as before.

  require <what>     this isle requires sign-in for <what>; needs a named Keycloak
  unrequire <what>   it no longer does
What can be named: releases (a release packet a member offers to the core, start setting any-device). A release
started on the core itself is not asked for a sign-in: sudo on the core is what allows it.
"""
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from operations import atomic

SETTING_FILE = '/etc/isle-mesh/identity/provider.json'
NOTICE_KEY = 'identityProvider'
DEFAULT_CLIENT = 'polari-frontend'
ISSUER_PATH = re.compile(r'(?:/[A-Za-z0-9._~-]+)*/realms/([A-Za-z0-9._-]{1,64})')
CLIENT = re.compile(r'[A-Za-z0-9._-]{1,64}')
VERB = 'isle polari security provider'
# What an isle can say needs a signed-in person. The list is part of the core's Keycloak record: nothing can need
# sign-in on an isle that names no Keycloak, and there is one place on the isle to read it from.
REQUIRABLE = {'releases': 'a release packet a member offers to the core (start setting any-device)'}
REQUIRED_NAME = re.compile(r'[a-z][a-z0-9-]{0,39}')
# What requiring sign-in for releases does while an offer cannot carry a sign-in. Said by the verb, the start setting,
# the member that would offer and the core that refuses, in the same words.
RELEASES_NEED_SIGN_IN = ('this isle requires sign-in for releases, and an offered packet cannot carry a sign-in yet, so '
                         'the core takes no packet from a member; start the release on the core: sudo isle release run <packet-dir>')
CHANGE = 'It is changed on the core only: sudo %s <https://host/realms/name> [--client <id>]   or   sudo %s none' % (VERB, VERB)


def checked_issuer(value):
    """-> (issuer, realm). The issuer is kept exactly as typed (less a closing slash): a token names it letter for letter."""
    if not isinstance(value, str): raise ValueError('the address is missing')
    value = value.rstrip('/')
    url = urlsplit(value)
    try: url.port
    except ValueError: raise ValueError('the port in the address is not a number')
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('the address must be an HTTPS address without a user name, a query or a fragment')
    if not re.fullmatch(r'[A-Za-z0-9.-]+', url.hostname): raise ValueError('the host name in the address is not valid')
    found = ISSUER_PATH.fullmatch(url.path)
    if not found: raise ValueError('the address must end in /realms/<realm name>, the way Keycloak names a realm')
    return value, found.group(1)


def valid(record):
    """The record with only its known keys when it is well formed, else None."""
    if not isinstance(record, dict): return None
    try:
        issuer, realm = checked_issuer(record.get('issuer'))
        keys = urlsplit(record.get('jwksUri') or '')
        if keys.scheme != 'https' or keys.netloc != urlsplit(issuer).netloc: return None
    except (ValueError, TypeError, AttributeError): return None
    client = record.get('clientId')
    if not isinstance(client, str) or not CLIENT.fullmatch(client): return None
    required = record.get('requiredFor', [])
    # a list this code cannot read makes the whole record unreadable: a requirement is never dropped silently
    if not isinstance(required, list) or not all(isinstance(r, str) and REQUIRED_NAME.fullmatch(r) for r in required): return None
    return dict(issuer=issuer, realm=realm, jwksUri=record['jwksUri'], clientId=client,
                setAt=str(record.get('setAt') or ''), setOn=str(record.get('setOn') or ''), requiredFor=sorted(set(required)))


def discover(http, issuer):
    """Ask the Keycloak itself (verified TLS, no redirect followed) and return the address of its signing keys."""
    status, body = http(issuer + '/.well-known/openid-configuration')
    if status != 200:
        raise ValueError('no Keycloak realm answered at %s (%s)' % (issuer, 'HTTP %d' % status if status else 'no answer'))
    try: document = json.loads(body) if len(body) <= 65536 else None
    except ValueError: document = None
    if not isinstance(document, dict): raise ValueError('the answer at %s is not a Keycloak discovery document' % issuer)
    if document.get('issuer') != issuer:
        raise ValueError('the Keycloak at that address names itself %r, not %r; type the address the way it names itself'
                         % (document.get('issuer'), issuer))
    keys = document.get('jwks_uri')
    if not isinstance(keys, str) or urlsplit(keys).scheme != 'https' or urlsplit(keys).netloc != urlsplit(issuer).netloc:
        raise ValueError('the Keycloak at %s does not offer its signing keys at an HTTPS address on the same host' % issuer)
    return keys


def local_record(path=None):
    """The record kept on this device (only the core keeps one), or None."""
    try: return valid(json.loads(Path(path or SETTING_FILE).read_text()))
    except (OSError, ValueError): return None


def requirement(path=None):
    """What this core's record says needs sign-in: (list, None). No record: ([], None), nothing is required.
    A record that is there but cannot be read: (None, why) - the caller must then act as if it were required."""
    path = Path(path or SETTING_FILE)
    try: text = path.read_text()
    except FileNotFoundError: return [], None
    except OSError as exc: return None, '%s cannot be read: %s' % (path, exc.strerror or exc)
    try: record = valid(json.loads(text))
    except ValueError: record = None
    if record is None: return None, '%s is not a record this isle CLI can read' % path
    return record['requiredFor'], None


def published_record(http, api):
    """What the core publishes to members: (record or None, None), or (None, why it is not known)."""
    status, body = http(api + '/api/islemesh/updates')
    if status != 200: return None, 'the core did not answer'
    try: release = json.loads(body).get('release')
    except (ValueError, AttributeError): return None, 'the answer of the core could not be read'
    return valid((release or {}).get(NOTICE_KEY)), None


def backend_environment(record):
    """What a Polari backend needs to check a sign-in token of this Keycloak. Empty when the isle names none."""
    if not record: return {}
    return {'POLARI_KEYCLOAK_ISSUER_URI': record['issuer'], 'POLARI_KEYCLOAK_JWKS_URI': record['jwksUri'],
            'POLARI_KEYCLOAK_REALM': record['realm'], 'POLARI_KEYCLOAK_FRONTEND_CLIENT_ID': record['clientId']}


def fill_frontend(config, record):
    """Write the isle's Keycloak into a frontend runtime configuration. Returns the keys whose value changed."""
    block = config.setdefault('keycloak', {})
    wanted = dict(authority=record['issuer'], clientId=record['clientId'], realm=record['realm'])
    changed = sorted(key for key, value in wanted.items() if block.get(key) != value)
    block.update(wanted)
    block.setdefault('enabled', False)   # a block without this key reads as switched on
    return changed


class IO:
    """Everything that touches this device or the isle; a test replaces it."""
    setting_file = SETTING_FILE

    def __init__(self):
        import release_run   # the runner's own helpers: device name, role, clock, verified HTTPS, the notice writer
        self._runner, self._deps = release_run, release_run.Deps()
        self.api = release_run.API

    def say(self, line=''): self._deps.say(line)
    def now(self): return self._deps.now()
    def is_root(self): return self._deps.is_root()
    def mode(self): return self._deps.mode()
    def device(self): return self._deps.device()
    def http(self, url): return self._deps.http(url)

    def write(self, record):
        path = Path(self.setting_file)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        atomic(path, record)

    def remove(self):
        try: Path(self.setting_file).unlink()
        except FileNotFoundError: pass

    def publish(self, record):
        done = subprocess.run(['docker', 'exec', '-i', 'prf-isle-backend', 'python3', '-c', self._runner.NOTICE_WRITE, NOTICE_KEY],
                              input=json.dumps(record), text=True, capture_output=True, timeout=20)
        if done.returncode: raise RuntimeError(done.stderr.strip() or 'the backend of the core did not take it')


def known(io):
    """The isle's Keycloak as this device knows it: (record or None, why it is not known or None)."""
    mode = io.mode()
    if mode == 'core': return local_record(io.setting_file), None
    if mode == 'remote': return published_record(io.http, io.api)
    return None, 'this device is not set up as a core or a member'


def _words(record):
    return '%s (client %s)' % (record['issuer'], record['clientId'])


def _required_words(record):
    names = (record or {}).get('requiredFor') or []
    return 'Sign-in is required for: %s' % (', '.join(names) if names else 'nothing')


def _publish(io, record):
    try:
        io.publish(record)
        io.say('Published to the members in the release notice of the core.')
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        io.say('Recorded on the core, but the release notice could not be updated (%s). Members read it after the next publication.' % exc)


def show(io):
    mode, device = io.mode(), io.device()
    record, why = known(io)
    if mode == 'core':
        io.say('Keycloak of this isle: %s' % ('%s, set %s on %s' % (_words(record), record['setAt'], record['setOn']) if record
                                              else 'none named. Sign-in is not available; everything else works without it.'))
        required, why = requirement(io.setting_file)
        io.say(_required_words(record) if required is not None else 'What needs sign-in is not known: %s.' % why)
    elif mode == 'remote':
        if why: io.say('Keycloak of this isle: not known on %s (%s).' % (device, why))
        else:
            io.say('Keycloak of this isle, as the core publishes it: %s' % (_words(record) if record else 'none named'))
            io.say(_required_words(record))
    else:
        io.say('This device is not set up as a core or a member (agent mode is %r); it knows no Keycloak of an isle.' % mode)
        return 1
    io.say(CHANGE)
    io.say('What needs sign-in is changed on the core only: sudo %s require|unrequire <%s>' % (VERB, '|'.join(sorted(REQUIRABLE))))
    return 0


def _core_only(io, what):
    if io.mode() == 'core': return None
    return ('REFUSED: %s on %s: the Keycloak of an isle is named on the core only. Nothing was changed. '
            'Run it on the core device of the isle.' % (what, io.device()))


def set_provider(io, address, client=DEFAULT_CLIENT):
    why = _core_only(io, 'provider')
    if why: io.say(why); return 1
    try:
        issuer, realm = checked_issuer(address)
        if not CLIENT.fullmatch(client or ''): raise ValueError('the client id may hold letters, digits, dot, dash and underscore only')
        keys = discover(io.http, issuer)
    except ValueError as exc:
        io.say('REFUSED: provider on %s: %s. Nothing was changed.' % (io.device(), exc)); return 1
    if not io.is_root():
        io.say('This changes the isle. Type: sudo %s %s%s' % (VERB, issuer, '' if client == DEFAULT_CLIENT else ' --client ' + client)); return 1
    required, unread = requirement(io.setting_file)
    if required is None:
        io.say('REFUSED: provider on %s: %s, so what this isle requires sign-in for would be lost. Nothing was changed. '
               'To start again with nothing required: sudo %s none' % (io.device(), unread, VERB)); return 1
    # naming another Keycloak keeps what the isle requires sign-in for
    record = dict(issuer=issuer, realm=realm, jwksUri=keys, clientId=client, setAt=io.now(), setOn=io.device(), requiredFor=required)
    io.write(record)
    io.say('Keycloak of this isle: %s.' % _words(record))
    if required: io.say(_required_words(record) + ' (kept).')
    _publish(io, record)
    io.say('Nothing was switched on: sign-in stays as it was and no backend rule changed.')
    io.say('  The backend of the core takes this address with: sudo isle reconcile   (it restarts that backend once)')
    io.say('  A backend on another device takes it when its instance is deployed or updated; until then it keeps what it started with.')
    io.say('  A sign-in page takes it with: sudo isle polari security enable --config <runtime-config.json>')
    return 0


def clear_provider(io):
    why = _core_only(io, 'provider none')
    if why: io.say(why); return 1
    # The start setting of releases (core-only or any-device) does not decide this: a release needs sign-in only where
    # the isle's security settings switch Keycloak on and require it.
    required = requirement(io.setting_file)[0]
    if required:           # a record that cannot be read is removed: that is what `none` is for
        io.say('REFUSED: provider none on %s: this isle requires sign-in for %s, and sign-in needs a Keycloak. Nothing was '
               'changed. First: %s' % (io.device(), ', '.join(required), '   then   '.join('sudo %s unrequire %s' % (VERB, r) for r in required)))
        return 1
    if not io.is_root(): io.say('This changes the isle. Type: sudo %s none' % VERB); return 1
    io.remove()
    io.say('Keycloak of this isle: none named.')
    _publish(io, None)
    io.say('The backend of the core drops the old address with: sudo isle reconcile   (it restarts that backend once)')
    io.say('A backend on another device, or a sign-in page, that holds the old address keeps it until it is updated or switched off.')
    return 0


def set_required(io, what, wanted):
    verb = 'require' if wanted else 'unrequire'
    why = _core_only(io, 'provider ' + verb)
    if why: io.say(why); return 1
    device = io.device()
    if what not in REQUIRABLE:
        io.say('REFUSED: provider %s on %s: %r is not something this isle can require sign-in for. Nothing was changed. What can be named:'
               % (verb, device, what))
        for name in sorted(REQUIRABLE): io.say('  %-10s %s' % (name, REQUIRABLE[name]))
        return 1
    required, unread = requirement(io.setting_file)
    if required is None:
        io.say('REFUSED: provider %s on %s: %s. Nothing was changed.' % (verb, device, unread)); return 1
    record = local_record(io.setting_file)
    if record is None:
        if not wanted: io.say('Sign-in is required for nothing on this isle: no Keycloak is named. Nothing was changed.'); return 0
        io.say('REFUSED: provider require on %s: no Keycloak is named for this isle, and sign-in needs one. Nothing was changed. '
               'Name it first: sudo %s <https://host/realms/name>' % (device, VERB)); return 1
    if (what in required) == wanted:
        io.say('Sign-in is %s required for %s on this isle. Nothing was changed.' % ('already' if wanted else 'not', what)); return 0
    if not io.is_root(): io.say('This changes the isle. Type: sudo %s %s %s' % (VERB, verb, what)); return 1
    record['requiredFor'] = sorted(set(required) | {what}) if wanted else [r for r in required if r != what]
    io.write(record)
    io.say(_required_words(record) + '.')
    _publish(io, record)
    if what == 'releases':
        if wanted:
            io.say('What that does today: %s.' % RELEASES_NEED_SIGN_IN)
            io.say('A release started on the core is not asked for a sign-in: sudo on the core is what allows it.')
        else:
            io.say('Whether a member may offer a release packet follows the start setting again: isle release setting')
    return 0


def main(argv=None, io=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    io = io or IO()
    if not argv: return show(io)
    if argv[0] in ('help', '-h', '--help'): io.say(__doc__.strip()); return 0
    if argv == ['none']: return clear_provider(io)
    if argv[0] in ('require', 'unrequire'):
        if len(argv) == 2: return set_required(io, argv[1], argv[0] == 'require')
        io.say('usage: %s %s <%s>' % (VERB, argv[0], '|'.join(sorted(REQUIRABLE)))); return 2
    if len(argv) == 1: return set_provider(io, argv[0])
    if len(argv) == 3 and argv[1] == '--client': return set_provider(io, argv[0], argv[2])
    io.say('usage: %s [<issuer-address> [--client <id>] | none | require <what> | unrequire <what>]' % VERB)
    return 2


if __name__ == '__main__':
    sys.exit(main())
