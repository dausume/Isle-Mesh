#!/bin/bash
#
# cli-paths.sh — SINGLE SOURCE OF TRUTH for where the isle CLI lives.
#
# Every install route (the .deb postinst, cliOnlyInstall, uninstall) converges on
# ONE canonical PATH entry so we never end up with two `isle` commands from
# different install patterns. Source this and call link_isle / unlink_isle.
#
# NOTE: the .deb postinst/prerm are /bin/sh and can't source this (the repo isn't
# present for a packaged user), so they hardcode the SAME values — keep them in
# sync with the constants below.

# The one and only PATH entry for the CLI:
ISLE_CLI_LINK="${ISLE_CLI_LINK:-/usr/local/bin/isle}"

# Where the packaged (.deb) CLI lives on disk:
ISLE_CLI_BUNDLE="/usr/share/isle-mesh/isle-cli/index.js"

# Older / alternate locations any route should clean up to avoid duplicates:
ISLE_CLI_LEGACY="/usr/bin/isle"

# link_isle <source-index.js>
#   Point the canonical symlink at <source>, after removing legacy duplicates.
#   Reports if it is repointing an existing install (so switching patterns is
#   visible, not silent duplication).
link_isle() {
    local src="$1"
    [ -z "$src" ] && { echo "link_isle: missing source path" >&2; return 1; }
    src="$(readlink -f "$src" 2>/dev/null || echo "$src")"

    # Remove legacy duplicate locations (different from the canonical link).
    local legacy
    for legacy in $ISLE_CLI_LEGACY; do
        [ "$legacy" = "$ISLE_CLI_LINK" ] && continue
        if [ -e "$legacy" ] || [ -L "$legacy" ]; then
            sudo rm -f "$legacy" 2>/dev/null && echo "  removed duplicate: $legacy" || true
        fi
    done

    local prev; prev="$(readlink -f "$ISLE_CLI_LINK" 2>/dev/null || true)"
    if [ -n "$prev" ] && [ "$prev" != "$src" ]; then
        echo "  repointing isle: $prev -> $src"
    fi
    sudo ln -sf "$src" "$ISLE_CLI_LINK"
    echo "  isle -> $(readlink -f "$ISLE_CLI_LINK" 2>/dev/null || echo "$src")"
}

# unlink_isle — remove the canonical link + any legacy duplicates (not the source).
unlink_isle() {
    local p
    for p in "$ISLE_CLI_LINK" $ISLE_CLI_LEGACY; do
        if [ -L "$p" ] || [ -f "$p" ]; then
            sudo rm -f "$p" 2>/dev/null && echo "  removed $p" || true
        fi
    done
}
