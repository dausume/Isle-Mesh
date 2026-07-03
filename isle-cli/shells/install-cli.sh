#!/bin/bash
#
# isle-cli/shells/install-cli.sh — isolated CLI-only install.
#
# Installs ONLY the `isle` command, pointing at THIS checkout (live edits take
# effect immediately). Ideal for development / CLI-only machines (no GUI). For a
# normal end-user install of BOTH the app and CLI, use the .deb (../../appInstall.sh).
#
# The CLI always lands at the SAME canonical location (see cli-paths.sh), so this
# never duplicates a CLI installed by the .deb — it just repoints the one link.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLI_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cli-paths.sh
source "$SCRIPT_DIR/cli-paths.sh"

echo "==> Installing isle CLI (canonical link) from: $CLI_DIR"
chmod 755 "$CLI_DIR/index.js" 2>/dev/null || true
link_isle "$CLI_DIR/index.js"
echo "==> Done — 'isle' is the live checkout at $CLI_DIR"
