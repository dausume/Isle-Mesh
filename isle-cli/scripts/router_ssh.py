#!/usr/bin/env python3
"""How an isle script opens ssh to the isle's router: only to the router whose key is pinned on this machine.

  python3 router_ssh.py address    the router's management address, from this machine's own record (host_network.py)
  python3 router_ssh.py options    the ssh options every call to the router uses, on one line, for a shell script
  isle router key-status           whether a key is pinned, its fingerprint, and whether the router shows that key now

The key is pinned once, by a person who read it somewhere other than the network (`sudo isle router trust-key`,
router_trust.py).  After that every call checks it: a router that shows another key is refused, and so is every call
on a machine that has pinned none.  The options name the pinned line by the management address (HostKeyAlias), so a
script that reaches the same router by another of its addresses checks the same key.
"""
import os
from pathlib import Path
import subprocess
import sys

KEY = Path('/etc/isle-mesh/router/ssh/isle_router_key')
KNOWN = Path('/etc/isle-mesh/router/ssh/known_hosts')
PIN = 'sudo isle router trust-key %s <public-key-file> <SHA256:fingerprint>'
USAGE = 'usage: router_ssh.py address | options | status'


def address():
    """The router's management address.  ValueError or OSError when this machine's record cannot be read."""
    import host_network
    return host_network.management_gateway()


def options(gateway=None, known=None):
    """The options that make ssh accept the pinned key and nothing else (not a key from any other known-hosts file)."""
    return ['-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=%s' % (known or KNOWN),
            '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'HostKeyAlias=%s' % (gateway or address())]


class Io:
    """Everything that touches this machine; a test replaces it."""
    key, known = KEY, KNOWN
    run = staticmethod(subprocess.run)

    def say(self, line=''): print(line)
    def is_root(self): return os.geteuid() == 0
    def address(self): return address()


def status(io):
    """Exit 0 only when the router answers and shows the pinned key."""
    try: gateway = io.address()
    except (OSError, ValueError, KeyError, AttributeError) as exc:
        io.say("The router's address cannot be read from this machine's record: %s" % exc)
        return 1
    if not io.is_root():
        io.say('The pinned router key can only be read as root. Type: sudo isle router key-status')
        return 1
    try:
        found = io.run(['ssh-keygen', '-l', '-E', 'sha256', '-F', gateway, '-f', str(io.known)], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        io.say('The pinned router key cannot be read: %s' % exc)
        return 1
    prints = [line.split() for line in found.stdout.splitlines() if line and not line.startswith('#')]
    prints = ['%s (%s)' % (words[2], words[1]) for words in prints if len(words) >= 3]
    if found.returncode != 0 or not prints:
        io.say('No router key is pinned on this machine for %s. Until one is, no isle command connects to the router.' % gateway)
        io.say('Read the public key of the router on its own console, then: ' + PIN % gateway)
        return 1
    io.say('Pinned for the router at %s: %s.' % (gateway, ', '.join(prints)))
    try:
        done = io.run(['ssh', '-i', str(io.key), '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5'] + options(gateway, io.known)
                      + ['root@' + gateway, 'true'], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        io.say('The router did not answer, so what key it shows now is not known.')
        return 1
    if done.returncode == 0:
        io.say('The router answers and shows the pinned key.')
        return 0
    if 'verification failed' in done.stderr or 'IDENTIFICATION HAS CHANGED' in done.stderr:
        io.say('The router at that address shows ANOTHER key. No isle command connects to it while that is so.')
        io.say('If the router was installed again, read its new public key on its own console, then: ' + PIN % gateway)
    else:
        io.say('The router did not answer, or did not accept the key of this machine, so what key it shows now is not known.')
    return 1


def main(argv, io=None):
    if argv == ['status']: return status(io or Io())
    if argv not in (['address'], ['options']):
        print(USAGE, file=sys.stderr)
        return 2
    try: print(address() if argv == ['address'] else ' '.join(options()))
    except (OSError, ValueError, KeyError, AttributeError) as exc:
        print("router ssh: the router's address cannot be read from this machine's record: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
