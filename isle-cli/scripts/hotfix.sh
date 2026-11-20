#!/bin/bash
# hotfix.sh - isle hotfix: named, idempotent, reviewable repairs of state already written on this device.
#   isle hotfix list                         every registered fix: summary, why it exists, what replaces it
#   isle hotfix status                       per fix, the state on THIS device; plus installed files that differ from their package
#   isle hotfix check <name>                 read-only: every action the fix would take (exit 0 nothing to do, 3 changes needed, 1 error)
#   sudo isle hotfix apply <name> [--yes] [--no-sync-proof]
#                                            show the same list, ask, back up what is replaced, apply, verify, write a receipt
#   isle hotfix help
# Also a step of `isle release run` (step kind "hotfix").
set -u
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
case "${1:-help}" in
    apply)
        [[ $EUID == 0 ]] || { echo "This changes the device. Type: sudo isle hotfix $*"; exit 1; } ;;
    list|status|check|help|-h|--help) ;;
    *) echo "unknown hotfix command: $1 (try: isle hotfix help)"; exit 2 ;;
esac
exec python3 "$DIR/hotfix.py" "$@"
