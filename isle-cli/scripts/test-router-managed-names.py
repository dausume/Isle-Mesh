#!/usr/bin/env python3
"""Router-side half of the storage ownership fence (contract section 5): the two router scripts skip names in
/etc/isle-managed-names, and `dns-register.sh managed-names push` derives and copies that list atomically."""
import os, re, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUTER = HERE.parent.parent / 'openwrt-router' / 'scripts'
LIB = ROUTER / 'lib' / 'managed-names.sh'
JOIN = ROUTER / 'router-setup' / 'configure-join-protocol.sh'
VLAN = ROUTER / 'utilities' / 'manage-vlan-domains.sh'
DNS = HERE / 'dns-register.sh'
COUNT = [0]; FAILED = []


def check(label, ok):
    COUNT[0] += 1
    print(('PASS ' if ok else 'FAIL ') + label)
    if not ok:
        FAILED.append(label)


def sh(script, env=None, shell='sh'):
    return subprocess.run([shell, '-c', script], capture_output=True, text=True, env=dict(os.environ, **(env or {})))


def func_text(path, name):
    m = re.search(r'^%s\(\) \{\n.*?^\}\n' % name, Path(path).read_text(), re.S | re.M)
    return m.group(0) if m else None


def t_lib(tmp):
    lst = Path(tmp) / 'names'
    lst.write_text('apt.isle\r\n# comment\nstore.isle\n')
    run = lambda name, f: sh('. %s; isle_managed_skip %s %s' % (LIB, name, f))
    r = run('apt.isle', lst)
    check('lib: listed name is skipped (rc 0) with the log line', r.returncode == 0 and 'managed name skipped: apt.isle' in r.stderr)
    check('lib: CRLF list entries still match', run('apt.isle', lst).returncode == 0)
    check('lib: unlisted name is not skipped', run('other.isle', lst).returncode == 1)
    check('lib: substring is not a match', run('apt', lst).returncode == 1 and run('pt.isle', lst).returncode == 1)
    check('lib: missing file = nothing managed', run('apt.isle', Path(tmp) / 'absent').returncode == 1)
    r = sh('. %s; isle_managed_skip apt.isle' % LIB, env={'ISLE_MANAGED_NAMES_FILE': str(lst)})
    check('lib: default file taken from ISLE_MANAGED_NAMES_FILE', r.returncode == 0)


def t_scripts(tmp):
    lib_fn = func_text(LIB, 'isle_managed_skip')
    join_fn = func_text(JOIN, 'isle_managed_skip')
    check('join daemon carries a verbatim copy of the lib function', lib_fn is not None and join_fn is not None
          and re.sub(r'ISLE_MANAGED_NAMES_FILE', '', lib_fn).replace('"$ISLE_MANAGED_NAMES_FILE"', '') is not None
          and join_fn.replace('${ISLE_MANAGED_NAMES_FILE:-/etc/isle-managed-names}', '$ISLE_MANAGED_NAMES_FILE') == lib_fn)
    # run the daemon's real update_dns_config with fake logger/dnsmasq on a temp list
    text = JOIN.read_text()
    daemon = re.search(r"<< 'DAEMON_EOF'\n(.*?)^DAEMON_EOF", text, re.S | re.M).group(1)
    fn_update = func_text_from(daemon, 'update_dns_config')
    lst = Path(tmp) / 'names'; lst.write_text('apt.isle\n')
    conf = Path(tmp) / 'out.conf'
    bindir = Path(tmp) / 'bin'; bindir.mkdir()
    (bindir / 'logger').write_text('#!/bin/sh\necho "$@" >> %s/log\n' % tmp); (bindir / 'logger').chmod(0o755)
    script = '\n'.join([
        'log_info(){ echo "$1" >> %s/log; }; log_error(){ :; }; log_warn(){ :; }' % tmp,
        'DNSMASQ_CONF=%s' % conf,
        'ISLE_MANAGED_NAMES_FILE=%s' % lst,
        func_text_from(daemon, 'isle_managed_skip'), fn_update,
        'mkdir -p /tmp/x$$; /etc/init.d/dnsmasq 2>/dev/null; true',
        'update_dns_config "apt|10.1.0.5\\nweb|10.1.0.6\\n"' ])
    # /etc/init.d/dnsmasq does not exist off-router: the reload failing is irrelevant to the written conf
    r = sh(script, env={'PATH': '%s:%s' % (bindir, os.environ['PATH'])}, shell='bash')  # busybox ash has echo -e; dash does not
    out = conf.read_text() if conf.exists() else ''
    log = (Path(tmp) / 'log').read_text() if (Path(tmp) / 'log').exists() else ''
    check('join daemon: managed host writes no address= line', 'apt.isle' not in out and 'apt.local' not in out and 'address=/apt' not in out)
    check('join daemon: unmanaged host still mapped (.local and .isle)', 'address=/web.isle/10.1.0.6' in out and 'address=/web.local/10.1.0.6' in out)
    check('join daemon: logs "managed name skipped: apt.isle"', 'managed name skipped: apt.isle' in log)
    check('join daemon: skip sits before the address= writes', text.index('isle_managed_skip "${hostname}.isle"') < text.index('echo "address=/${hostname}.local'))
    # manage-vlan-domains: the fence is in add + remove, before any router mutation
    v = VLAN.read_text()
    add = func_text(VLAN, 'add_domain_to_router'); rem = func_text(VLAN, 'remove_domain_from_router')
    check('vlan: add_domain_to_router skips managed names before any router_exec',
          add.index('router_managed_skip') < add.index('router_exec'))
    check('vlan: remove_domain_from_router skips managed names before any router_exec',
          rem.index('router_managed_skip') < rem.index('router_exec'))
    # run the vlan add function with a fake router_exec
    fake = '\n'.join([
        'log_info(){ echo "$1" >&2; }; log_success(){ :; }; DNSMASQ_CONF=/x/conf',
        '. %s' % LIB,
        'router_exec(){ case "$1" in "cat /etc/isle-managed-names"*) cat %s;; *) echo "EXEC $1" >> %s/exec;; esac; }' % (lst, tmp),
        func_text_from(v, 'router_managed_skip'), add, rem,
        'add_domain_to_router apt.local 10.1.0.5; add_domain_to_router web.local 10.1.0.6; remove_domain_from_router apt.isle'])
    r = sh(fake, shell='bash')
    ex = (Path(tmp) / 'exec').read_text() if (Path(tmp) / 'exec').exists() else ''
    check('vlan: managed name causes no router mutation, logs the skip', 'apt.isle' not in ex and 'managed name skipped: apt.isle' in r.stderr)
    check('vlan: unmanaged name is still written', 'address=/web.isle/10.1.0.6' in ex)


def func_text_from(text, name):
    m = re.search(r'^%s\(\) \{\n.*?^\}\n' % name, text, re.S | re.M)
    return m.group(0) if m else ''


def t_push(tmp):
    tmp = Path(tmp)
    router = tmp / 'router'; router.mkdir()
    bindir = tmp / 'bin'; bindir.mkdir()
    (bindir / 'ssh').write_text('''#!/bin/bash
args=("$@"); cmd="${args[-1]}"
echo "ssh $cmd" >> "$FAKE_LOG"
case "$cmd" in true) exit 0;; mv*) [ -n "${FAKE_MV_FAIL:-}" ] && exit 1
  eval "$(echo "$cmd" | sed "s#/etc/#$FAKE_ROOT/#g")"; exit $?;; rm*) eval "$(echo "$cmd" | sed "s#/etc/#$FAKE_ROOT/#g")"; exit 0;; esac
exit 0
''')
    (bindir / 'scp').write_text('''#!/bin/bash
args=("$@"); src="${args[-2]}"; dst="${args[-1]#*:}"
legacy=""; for a in "$@"; do [ "$a" = -O ] && legacy="-O "; done
echo "scp $legacy$dst" >> "$FAKE_LOG"
[ -n "${FAKE_SCP_FAIL:-}" ] && exit 1
cp "$src" "$FAKE_ROOT/${dst#/etc/}"
''')
    for f in ('ssh', 'scp'):
        (bindir / f).chmod(0o755)
    placement = tmp / 'placement.json'
    placement.write_text('{"datasets": {"apt-mirror": {"domain": "apt.isle", "state": "authorized"}, '
                         '"store": {"domain": "store.isle", "state": "hosting"}, "dup": {"domain": "apt.isle", "state": "migrated"}}}')
    env = {'PATH': '%s:%s' % (bindir, os.environ['PATH']), 'FAKE_ROOT': str(router), 'FAKE_LOG': str(tmp / 'calls'),
           'ISLE_PLACEMENT': str(placement), 'ISLE_DNS_ALLOW_NONROOT': '1', 'ISLE_SSH_KEY': str(tmp / 'nokey'), 'HOME': str(tmp)}
    run = lambda *a, **e: subprocess.run(['bash', str(DNS), 'managed-names'] + list(a), capture_output=True, text=True, env=dict(os.environ, **env, **e))
    r = run('push')
    pushed = router / 'isle-managed-names'
    check('push: exits 0', r.returncode == 0)
    check('push: router list = every entry domain, any state, sorted and deduplicated',
          pushed.exists() and pushed.read_text() == 'apt.isle\nstore.isle\n')
    calls = (tmp / 'calls').read_text()
    check('push: scp uses the legacy protocol (-O): OpenSSH 9 defaults to SFTP and the OpenWrt router has no sftp-server',
          re.search(r'scp -O /etc/isle-managed-names\.new\.\d+', calls))
    check('push: scp to a temp name then mv over the final name', re.search(r'scp (-O )?/etc/isle-managed-names\.new\.\d+', calls) and
          re.search(r"ssh mv -f '/etc/isle-managed-names\.new\.\d+' '/etc/isle-managed-names'", calls))
    check('push: no temp file left behind', not list(router.glob('isle-managed-names.new*')))
    pushed.write_text('keep\n')
    r = run('push', FAKE_SCP_FAIL='1')
    check('push: failed copy exits 1 and leaves the router list unchanged', r.returncode == 1 and pushed.read_text() == 'keep\n')
    r = run('push', FAKE_MV_FAIL='1')
    check('push: failed mv exits 1 and leaves the router list unchanged', r.returncode == 1 and pushed.read_text() == 'keep\n')
    check('push: usage error without "push"', run().returncode == 1)
    placement.unlink()
    check('push: no placement record is an error, not an empty push', run('push').returncode == 1 and pushed.read_text() == 'keep\n')
    placement.write_text('{"datasets": {}}')
    run('push')
    check('push: empty placement pushes an empty list', pushed.read_text() == '')
    r = subprocess.run(['bash', str(DNS), 'managed-names', 'push'], capture_output=True, text=True,
                       env=dict(os.environ, **{k: v for k, v in env.items() if k != 'ISLE_DNS_ALLOW_NONROOT'}))
    check('push: refuses a non-root caller', (os.geteuid() == 0) or (r.returncode == 1 and 'run as root' in r.stderr))



def t_cli_verb(tmp):
    """User ruling 2026-11-14: every standard step is a CLI verb. `isle dns managed-names push` routes through dns.sh."""
    import shutil
    d = Path(tmp)
    shutil.copy(HERE / 'dns.sh', d / 'dns.sh')
    (d / 'dns-register.sh').write_text('#!/bin/bash\necho "dns-register $*"\n')
    r = subprocess.run(['bash', str(d / 'dns.sh'), 'managed-names', 'push'], capture_output=True, text=True)
    check('cli: `isle dns managed-names push` reaches dns-register.sh managed-names push',
          r.returncode == 0 and r.stdout.strip() == 'dns-register managed-names push')
    r = subprocess.run(['bash', str(d / 'dns.sh'), 'help'], capture_output=True, text=True)
    check('cli: `isle dns help` lists managed-names push', 'managed-names push' in r.stdout)


def main():
    for p in (LIB, JOIN, VLAN, DNS):
        check('bash -n %s' % p.name, subprocess.run(['bash', '-n', str(p)]).returncode == 0)
    for fn in (t_lib, t_scripts, t_push, t_cli_verb):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                fn(tmp)
            except Exception as exc:
                import traceback; traceback.print_exc()
                check('%s ran without a harness error (%r)' % (fn.__name__, exc), False)
    print('%d checks, %d failure(s)' % (COUNT[0], len(FAILED)))
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
