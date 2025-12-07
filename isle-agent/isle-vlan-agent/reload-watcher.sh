#!/bin/sh
#
# Nginx Reload Watcher
# Watches for .reload sentinel file and triggers nginx reload
#
# This script runs as a sidecar in the isle-vlan-agent container
# and provides a safe mechanism for isle-agent-sync to trigger
# nginx reloads without requiring docker.sock access.

set -e

RELOAD_FILE="/etc/nginx/configs/.reload"
LAST_RELOAD=""

echo "🔄 Nginx Reload Watcher started"
echo "   Watching: $RELOAD_FILE"
echo "   Poll interval: 2 seconds"
echo ""

while true; do
    if [ -f "$RELOAD_FILE" ]; then
        CURRENT=$(cat "$RELOAD_FILE" 2>/dev/null || echo "")

        if [ -n "$CURRENT" ] && [ "$CURRENT" != "$LAST_RELOAD" ]; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] Reload signal received: $CURRENT"

            # Test nginx config before reloading
            if nginx -t 2>&1 | grep -q "successful"; then
                echo "  ✓ Nginx config is valid"

                # Reload nginx
                if nginx -s reload 2>&1; then
                    echo "  ✓ Nginx reloaded successfully"
                    LAST_RELOAD="$CURRENT"
                else
                    echo "  ✗ Nginx reload failed"
                fi
            else
                echo "  ✗ Nginx config test failed - skipping reload"
                nginx -t
            fi
            echo ""
        fi
    fi

    sleep 2
done
