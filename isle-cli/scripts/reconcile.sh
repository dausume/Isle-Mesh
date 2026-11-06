#!/bin/bash
# Reconcile existing membership and local name routes; never rejoin/recreate apps.
set -eu
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [[ -z "${ISLE_OPERATION_ID:-}" ]]; then
    [[ $# == 0 ]] || { echo "Usage: sudo isle reconcile"; exit 1; }
    exec python3 "$DIR/operations.py" run reconcile apply device
fi
[[ $# == 2 && "$1" == apply && "$2" == device && "${ISLE_OPERATION_ID:-}" =~ ^[a-f0-9]{32}$ ]] || { echo "Invalid reconcile context"; exit 1; }
[[ $EUID == 0 ]] || { echo 'Use sudo isle reconcile'; exit 1; }
python3 "$DIR/member_preflight.py" --repair
python3 "$DIR/legacy_units.py"
python3 "$DIR/runtime_reconcile.py"
python3 "$DIR/provider_reconcile.py"
bash "$DIR/hosts-reconcile.sh"
python3 "$DIR/engine_sync.py"
python3 "$DIR/capabilities.py" status --publish https://api.polari.isle
