#!/bin/sh
#
# Isle VLAN Agent Entrypoint
# Starts nginx and the registry watcher (self-reloading config generator)
#

set -e

echo "=================================================="
echo "  Isle VLAN Agent - Self-Reloading Nginx Proxy"
echo "=================================================="
echo ""

# Copy initial nginx.conf if it doesn't exist in active location
if [ ! -f /etc/nginx/nginx.conf ]; then
    echo "Copying initial nginx.conf to active location..."
    if [ -f /etc/nginx/nginx-initial.conf ]; then
        cp /etc/nginx/nginx-initial.conf /etc/nginx/nginx.conf
        echo "  ✓ Initial config copied"
    else
        echo "  ⚠ No initial config found, using nginx default"
    fi
else
    echo "Active nginx.conf already exists, keeping current version"
fi
echo ""

# Ensure configs directory exists
mkdir -p /etc/nginx/configs
echo "Configs directory ready at /etc/nginx/configs"
echo ""

# Start registry watcher in background
echo "Starting registry watcher..."
/usr/local/bin/registry-watcher.sh &
WATCHER_PID=$!
echo "  Registry watcher PID: $WATCHER_PID"
echo ""

# Cleanup handler
cleanup() {
    echo "Shutting down..."
    kill $WATCHER_PID 2>/dev/null || true
    exit 0
}
trap cleanup SIGTERM SIGINT

# Start nginx in foreground
echo "Starting nginx..."
echo "  Active config: /etc/nginx/nginx.conf (observable at /etc/isle-mesh/agent/nginx/nginx.conf)"
echo "  Generated configs: /etc/nginx/configs/ (observable at /etc/isle-mesh/agent/nginx/configs/)"
echo ""
exec nginx -g "daemon off;"
