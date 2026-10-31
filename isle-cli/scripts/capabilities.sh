#!/bin/bash
set -euo pipefail
DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
if [[ "${1:-status}" == enable ]]; then
    [[ $# == 2 ]] || { echo "usage: isle capabilities enable <profile> (local device only)" >&2; exit 2; }
    exec bash "$DIR/capabilities-enable.sh" "${2:-}"
fi
exec python3 "$DIR/capabilities.py" "$@"
