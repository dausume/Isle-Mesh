#!/bin/sh
#
# Isle VLAN Agent Entrypoint
# Starts nginx and the reload watcher sidecar
#

set -e

echo "=================================================="
echo "  Isle VLAN Agent - Nginx Reverse Proxy"
echo "=================================================="
echo ""

# Start reload watcher in background
echo "Starting nginx reload watcher..."
/usr/local/bin/reload-watcher.sh &
WATCHER_PID=$!
echo "  Reload watcher PID: $WATCHER_PID"
echo ""

# Start nginx in foreground
echo "Starting nginx..."
exec nginx -g "daemon off;"
