#!/bin/sh
#
# Isle VLAN Agent - Registry Watcher
# Watches registry.json for changes and triggers nginx config regeneration
#
# This replaces the separate isle-agent-sync container by embedding
# the config generation logic directly in the vlan-agent.

set -e

REGISTRY_FILE="${REGISTRY_FILE:-/etc/nginx/registry.json}"
CONFIG_DIR="${CONFIG_DIR:-/etc/nginx/configs}"
CONFIG_GENERATOR="${CONFIG_GENERATOR:-/usr/local/bin/generate-nginx-configs.sh}"
WATCH_INTERVAL="${WATCH_INTERVAL:-2}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [registry-watcher] $*"
}

log "Registry Watcher started"
log "  Registry file: $REGISTRY_FILE"
log "  Config dir: $CONFIG_DIR"
log "  Watch interval: ${WATCH_INTERVAL}s"
echo ""

# Track last modification time
LAST_MTIME=""

# Initial config generation if registry exists
if [ -f "$REGISTRY_FILE" ]; then
    log "Initial registry found, generating configs..."
    if "$CONFIG_GENERATOR" "$REGISTRY_FILE" "$CONFIG_DIR"; then
        log "✓ Initial config generation successful"

        # Test and reload nginx
        if nginx -t 2>&1 | grep -q "successful"; then
            nginx -s reload 2>&1 && log "✓ Nginx reloaded"
        fi
    else
        log "✗ Initial config generation failed"
    fi
fi

# Watch loop
while true; do
    if [ -f "$REGISTRY_FILE" ]; then
        # Get current modification time
        CURRENT_MTIME=$(stat -c %Y "$REGISTRY_FILE" 2>/dev/null || echo "0")

        # Check if file was modified
        if [ -n "$CURRENT_MTIME" ] && [ "$CURRENT_MTIME" != "$LAST_MTIME" ]; then
            log "Registry changed detected"

            # Generate new configs
            if "$CONFIG_GENERATOR" "$REGISTRY_FILE" "$CONFIG_DIR"; then
                log "✓ Config generation successful"
                LAST_MTIME="$CURRENT_MTIME"

                # Test nginx config before reloading
                if nginx -t 2>&1 | grep -q "successful"; then
                    log "✓ Nginx config valid"

                    # Reload nginx
                    if nginx -s reload 2>&1; then
                        log "✓ Nginx reloaded successfully"
                    else
                        log "✗ Nginx reload failed"
                    fi
                else
                    log "✗ Nginx config test failed - skipping reload"
                    nginx -t 2>&1 | while IFS= read -r line; do
                        log "  $line"
                    done
                fi
            else
                log "✗ Config generation failed"
            fi

            echo ""
        fi
    else
        # Registry doesn't exist yet
        if [ -n "$LAST_MTIME" ]; then
            log "⚠ Registry file disappeared"
            LAST_MTIME=""
        fi
    fi

    sleep "$WATCH_INTERVAL"
done
