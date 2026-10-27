#!/bin/bash
set -eu
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
case "${1:-help}" in
    install) shift; exec bash "$SCRIPT_DIR/apt-install.sh" "$@" ;;
    status) exec bash "$SCRIPT_DIR/apt-repo.sh" status ;;
    *) echo 'usage: isle apt install <package[=version]|absolute.deb> ... | status' ;;
esac
