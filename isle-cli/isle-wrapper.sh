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

# Node.js and isle paths
NODE_PATH="/home/dustin/.nvm/versions/node/v20.19.5/bin/node"
ISLE_SCRIPT="/home/dustin/.nvm/versions/node/v20.19.5/bin/isle"

# Verify node exists
if [[ ! -f "$NODE_PATH" ]]; then
    echo "Error: Node.js not found at $NODE_PATH" >&2
    echo "Please update this wrapper script with the correct node path" >&2
    exit 1
fi

# Verify isle script exists
if [[ ! -f "$ISLE_SCRIPT" ]]; then
    echo "Error: Isle CLI script not found at $ISLE_SCRIPT" >&2
    echo "Please update this wrapper script with the correct isle path" >&2
    exit 1
fi

# Execute isle with the correct node environment
exec "$NODE_PATH" "$ISLE_SCRIPT" "$@"
