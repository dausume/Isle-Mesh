#!/usr/bin/env bash
set -euo pipefail
exec python3 "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/capabilities.py" "$@"
