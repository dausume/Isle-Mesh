#!/bin/bash
# backup.sh - isle backup: device-backups of this machine (device_backup.py says what one holds and where it lives).
#   isle backup plan [--json]          read-only: what one would hold, what is left out and why, where it would go
#   sudo isle backup create --yes      journaled (through operations.py, so the lock and the journal exist first)
#   sudo isle backup list [--json]     the device-backups of this machine
#   sudo isle backup verify <id>       re-read every file of one against its record
#   sudo isle backup release-hold      lower a write gate that an interrupted device-backup left up
#   sudo isle backup restore <id> --drill   unpack one into a scratch folder, compare, remove it (changes nothing)
#   sudo isle backup restore <id> --apply [--item <name>]...         says what it would put back (changes nothing)
#   sudo isle backup restore <id> --apply --yes [--item <name>]...   journaled: puts the data of the instances back
#   sudo isle backup before list | drop <item> --yes                 the data an instance had before a restore
#   sudo isle backup before undo --yes                               puts a restore that did not finish back as it was
# Also a step of `isle release run` (step kind "backup").
set -u
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [[ -z "${ISLE_OPERATION_ID:-}" && ( "${1:-}" == create || ( "${1:-}" == restore && "${3:-}" == --apply && "${4:-}" == --yes ) ) ]]; then
    exec python3 "$DIR/operations.py" run backup "$@"
fi
exec python3 "$DIR/device_backup.py" "$@"
