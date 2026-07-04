#!/bin/bash
#
# cliOnlyInstall.sh — install ONLY the isle CLI (no desktop app).
#
# Thin wrapper that delegates to the isle-cli project's own isolated install
# shell (npm link against this checkout). Use this for headless / dev / CLI-only
# machines. For the normal user install of both app + CLI, use ./appInstall.sh.
#
# Usage:  ./cliOnlyInstall.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
bash "$ROOT/isle-cli/shells/install-cli.sh"

echo ""
echo "==> Next — set up plug-and-play node services (system 'isle' for sudo/udev,"
echo "    boot self-recovery, and cable hotplug):"
echo "      sudo bash \"$ROOT/install-node-services.sh\""
