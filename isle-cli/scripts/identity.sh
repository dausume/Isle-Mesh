#!/bin/bash
# Saved member identity: read-only status/plan; guarded reconcile through the shared operation journal.
set -eu
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
case "${1:-}" in
    status|plan) exec python3 "$DIR/identity_reconcile.py" "$@" ;;
    reconcile)
        if [[ -z "${ISLE_OPERATION_ID:-}" ]]; then
            exec python3 "$DIR/operations.py" run identity "$@"
        fi
        exec python3 "$DIR/identity_reconcile.py" "$@" ;;
    *)
        echo 'Usage: isle identity status'
        echo '       isle identity plan --expected-legacy <saved-registration-name> --canonical <device-name>'
        echo '       sudo isle identity reconcile --expected-legacy <saved-registration-name> --canonical <device-name>'
        echo 'Then: sudo isle operation retry <id-of-the-refused-operation>'
        exit 1 ;;
esac
