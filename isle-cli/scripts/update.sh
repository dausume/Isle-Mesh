#!/bin/bash
set -eu
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [[ "${1:-plan}" == apply && -z "${ISLE_OPERATION_ID:-}" ]]; then
    exec python3 "$DIR/operations.py" run update "$@"
fi
exec python3 "$DIR/update.py" "$@"
