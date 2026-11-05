#!/bin/bash
set -eu
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [[ "${1:-}" == describe-image ]]; then
    [[ $# == 3 ]] || { echo 'Usage: isle update describe-image <archive> <image-ref>'; exit 1; }
    exec python3 "$DIR/image_identity.py" "$2" "$3"
fi
if [[ "${1:-}" == publish-runtime ]]; then
    [[ $# == 2 ]] || { echo 'Usage: isle update publish-runtime <manifest>'; exit 1; }
    exec python3 "$DIR/runtime_publish.py" "$2"
fi
if [[ "${1:-plan}" == apply && -z "${ISLE_OPERATION_ID:-}" ]]; then
    exec python3 "$DIR/operations.py" run update "$@"
fi
exec python3 "$DIR/update.py" "$@"
