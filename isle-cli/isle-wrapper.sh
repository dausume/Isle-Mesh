#!/bin/bash
#############################################################################
# Isle CLI Sudo Wrapper
#
# This wrapper ensures isle commands work correctly with sudo by using
# the proper Node.js environment. Without this, sudo may not find node
# or might use the wrong version.
#
# Setup:
#   sudo ln -sf /path/to/isle-wrapper.sh /usr/local/bin/isle
#
# Then you can run:
#   sudo isle router init
#
#############################################################################

set -e

# Node.js and isle paths.  Nothing is written in: the CLI is the index.js beside this wrapper, and node is found
# at run time (ISLE_NODE, then PATH, then the newest node in the nvm folder of the user who called sudo).
HERE="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
ISLE_SCRIPT="$HERE/index.js"
NODE_PATH="${ISLE_NODE:-$(command -v node || true)}"
if [[ -z "$NODE_PATH" && -n "${SUDO_USER:-}" ]]; then
    CALLER_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
    NODE_PATH=$(ls -1 "$CALLER_HOME"/.nvm/versions/node/*/bin/node 2>/dev/null | sort -V | tail -n 1)
fi

# Verify node exists
if [[ ! -f "$NODE_PATH" ]]; then
    echo "Error: Node.js not found (looked at ISLE_NODE, PATH and the calling user's nvm folder)" >&2
    echo "Set ISLE_NODE to the node program, for example: sudo ISLE_NODE=\$(command -v node) isle ..." >&2
    exit 1
fi

# Verify isle script exists
if [[ ! -f "$ISLE_SCRIPT" ]]; then
    echo "Error: Isle CLI script not found at $ISLE_SCRIPT" >&2
    echo "This wrapper must stay beside index.js in the isle-cli folder" >&2
    exit 1
fi

# Execute isle with the correct node environment
exec "$NODE_PATH" "$ISLE_SCRIPT" "$@"
