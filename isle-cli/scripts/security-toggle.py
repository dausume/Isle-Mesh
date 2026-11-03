#!/usr/bin/python3 -I
"""Fixed native sign-in toggle. No command, file path, or remote target from the page."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import pwd
import stat
from urllib.parse import urlsplit
import urllib.request

MAX_CONFIG = 1024 * 1024

def origin(value):
    u = urlsplit(value)
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError('A configured HTTPS frontend is required')
    return 'https://' + u.hostname.lower() + ((':' + str(u.port)) if u.port not in (None, 443) else '')


def open_config(path, owner, writable=False, shared_registry=False):
    # Walk using dirfds: symlink swaps in a writable home cannot redirect root IO.
    parts = Path(path).parts
    if not Path(path).is_absolute():
        raise ValueError('Configuration path must be absolute')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[1:-1]:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = new
            info = os.fstat(fd)
            if owner == 0 and (info.st_uid != 0 or info.st_mode & 0o022):
                raise ValueError('System configuration directories must be administrator-owned')
        out = os.open(parts[-1], (os.O_RDWR if writable else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    finally:
        os.close(fd)
    info = os.fstat(out)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_nlink != 1 or info.st_size > MAX_CONFIG:
        os.close(out)
        raise ValueError('Unsafe configuration ownership, links, type, or size')
    if owner == 0 and info.st_mode & 0o022 and not (shared_registry and not writable):
        os.close(out)
        raise ValueError('System configuration must not be writable by other users')
    return os.fdopen(out, 'r+' if writable else 'r')


def candidates(user):
    yield Path(user.pw_dir) / 'polari-isle/runtime-config.json', user.pw_uid
    base = Path('/etc/isle-mesh/polari')
    if base.is_dir():
        for directory in sorted(base.iterdir()):
            if directory.name and all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in directory.name):
                yield directory / 'runtime-config.json', 0


def target(user, requested):
    requested = origin(requested)
    with open_config('/etc/isle-mesh/agent/registry.json', 0, shared_registry=True) as f:
        registry = json.load(f)
    host = urlsplit(requested).hostname
    if not any(app.get('domain') == host and app.get('services') for app in registry.get('apps', {}).values()):
        raise ValueError('This frontend is not registered on this device. Use its hosting device.')
    matches = []
    for path, owner in candidates(user):
        try:
            with open_config(path, owner) as f:
                cfg = json.load(f)
            endpoint = cfg.get('frontend', {}).get('https', {})
            declared = 'https://' + endpoint.get('url', '') + ':' + str(endpoint.get('port', '443'))
            if origin(declared) == requested:
                matches.append((path, owner))
        except FileNotFoundError:
            continue
    if len(matches) != 1:
        raise ValueError('No unique local frontend configuration matches this page')
    return matches[0]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Keycloak discovery redirects are not accepted')


def check_provider(config):
    kc = config.get('keycloak', {})
    authority = kc.get('authority', '').rstrip('/')
    origin(authority)
    if not kc.get('clientId'):
        raise ValueError('Configure a Keycloak client ID first')
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(authority + '/.well-known/openid-configuration', timeout=5) as response:
        data = response.read(65537)
    if len(data) > 65536 or json.loads(data).get('issuer', '').rstrip('/') != authority:
        raise ValueError('Keycloak discovery does not identify the configured issuer')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'enable', 'disable'])
    parser.add_argument('frontend')
    a = parser.parse_args()
    privileged = a.action != 'status'
    if privileged:
        if os.geteuid() != 0 or not os.environ.get('PKEXEC_UID', '').isdigit():
            parser.error('Changes require the dedicated pkexec action')
        uid = int(os.environ['PKEXEC_UID'])
        if uid == 0:
            parser.error('A local desktop user must request this change')
    else:
        uid = os.getuid()
    try:
        user = pwd.getpwuid(uid)
        path, owner = target(user, a.frontend)
        # User-owned core config never needs root file privileges after approval.
        if privileged and owner != 0:
            os.setgroups([]); os.setgid(user.pw_gid); os.setuid(uid)
        with open_config(path, owner, writable=privileged) as f:
            fcntl.flock(f, fcntl.LOCK_EX if privileged else fcntl.LOCK_SH)
            cfg = json.load(f)
            endpoint = cfg.get('frontend', {}).get('https', {})
            if origin('https://' + endpoint.get('url', '') + ':' + str(endpoint.get('port', '443'))) != origin(a.frontend):
                raise ValueError('The frontend configuration changed; check availability again')
            if privileged:
                spec = importlib.util.spec_from_file_location('security', Path(__file__).with_name('polari-security.py'))
                module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
                module.update(cfg, a.action)
                if a.action == 'enable':
                    check_provider(cfg)
                f.seek(0); f.write(json.dumps(cfg, indent=2) + '\n'); f.truncate(); f.flush(); os.fsync(f.fileno())
            kc = cfg.get('keycloak', {})
            print(json.dumps({'ok': True, 'local': True, 'enabled': bool(kc) and kc.get('enabled', True),
                              'requiredBy': kc.get('requiredBy', []), 'frontend': origin(a.frontend),
                              'scope': 'frontend-sign-in', 'reloadRequired': privileged}))
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
