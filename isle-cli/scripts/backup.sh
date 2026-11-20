#!/bin/bash
# backup.sh - isle backup: device-backups of this machine (device_backup.py says what one holds and where it lives).
#   isle backup plan [--json]          read-only: what one would hold, what is left out and why, where it would go
#   sudo isle backup create --yes      journaled (through operations.py, so the lock and the journal exist first)
#   sudo isle backup list [--json]     the device-backups of this machine
#   sudo isle backup verify <id>       re-read every file of one against its record
#   sudo isle backup release-hold      lower a write gate that an interrupted device-backup left up
# Also a step of `isle release run` (step kind "backup").
set -u
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [[ "${1:-}" == create && -z "${ISLE_OPERATION_ID:-}" ]]; then
    exec python3 "$DIR/operations.py" run backup "$@"
fi
exec python3 "$DIR/device_backup.py" "$@"
