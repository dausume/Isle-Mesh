#!/bin/bash
# os.sh - isle os: the operating systems the isle is run on (the record apt/supported-os.json).
# list, check and devices only read.
#   isle os list [--table]      every supported system, how far it is proven as a core and as a member, what differs
#   isle os check [--table]     this machine: its system, whether the record has it, the dependency set for its release
#   isle os devices [--table]   what every machine of the isle last reported as its system
#   isle os dependencies build <out-dir> --system <system id> --packages <dir>   no sudo: fetch the operating system
#                               packages that system needs offline (a dependency set); nothing is published
#   isle os dependencies check <set dir>   read a set against its own lists; changes nothing
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "${1:-}" = dependencies ]; then shift; exec python3 "$DIR/dependency_set.py" "$@"; fi
exec python3 "$DIR/supported_os.py" "$@"
