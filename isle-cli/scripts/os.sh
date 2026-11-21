#!/bin/bash
# os.sh - isle os: the operating systems the isle is run on (the record apt/supported-os.json), read-only.
#   isle os list [--table]      every supported system, how far it is proven as a core and as a member, what differs
#   isle os check [--table]     this machine: its system, whether the record has it, the dependency set for its release
#   isle os devices [--table]   what every machine of the isle last reported as its system
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/supported_os.py" "$@"
