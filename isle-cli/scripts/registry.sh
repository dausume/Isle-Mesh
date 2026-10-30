#!/usr/bin/env bash
# Registry setup on the CA host, read-only verification on any member.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "${1:-help}" in
    setup)
        shift
        exec bash "$SCRIPT_DIR/isle-registry-setup.sh" "$@" ;;
    verify|status)
        shift
        port=5000
        if [ $# -eq 2 ] && [ "$1" = --port ]; then
            port="$2"
        elif [ $# -ne 0 ]; then
            echo 'usage: isle registry verify [--port 5000]' >&2; exit 1
        fi
        source "$SCRIPT_DIR/isle-registry-setup.sh"
        registry_verify "$port" ;;
    help|--help|-h)
        echo 'usage: isle registry setup [--port 5000] [--address <core IPv4>]'
        echo '       isle registry verify [--port 5000]  (read-only, from this device)' ;;
    *) echo "unknown registry action: $1" >&2; exit 1 ;;
esac
