#!/bin/bash
#
# appInstall.sh — normal full install of Isle-Mesh (desktop app + isle CLI).
#
# This is the traditional, package-managed route: it builds the .deb (which now
# BUNDLES the isle CLI) and installs it with dpkg, so a single command lands both
# the GUI and the `isle` command. Thin wrapper — the real logic lives in the
# project's own isolated shell: isle-manager-app/shells/build-deb.sh.
#
# Usage:  ./appInstall.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$ROOT/isle-manager-app"
DEB="$APP_DIR/build/isle-manager-app_0.1.0_all.deb"

echo "==> Building the app .deb (bundles the isle CLI)..."
bash "$APP_DIR/shells/build-deb.sh"

echo "==> Installing $DEB (requires sudo)..."
if ! sudo dpkg -i "$DEB"; then
    echo "==> Resolving dependencies..."
    sudo apt-get -f install -y
fi

echo ""
echo "==> Installed."
echo "    Desktop app:  isle-manager-app"
echo "    CLI tool:     isle"
