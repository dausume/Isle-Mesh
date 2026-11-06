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

# Observe only proxy-relevant facts and public certificates, not relay timestamps.
fingerprint() {
    jq -cS '.apps | with_entries(.value |= {domain,services})' "$REGISTRY_FILE" || return 1
    for certificate in /etc/nginx/ssl/certs/*.crt; do
        [ ! -f "$certificate" ] || sha256sum "$certificate"
    done
}
LAST_FINGERPRINT=""
while true; do
    if CURRENT_FACTS=$(fingerprint 2>/dev/null); then
        CURRENT_FINGERPRINT=$(printf '%s' "$CURRENT_FACTS" | sha256sum)
        if [ "$CURRENT_FINGERPRINT" != "$LAST_FINGERPRINT" ]; then
            if "$CONFIG_GENERATOR" "$REGISTRY_FILE" "$CONFIG_DIR" && nginx -t && nginx -s reload; then
                LAST_FINGERPRINT="$CURRENT_FINGERPRINT"
                log 'Complete proxy configuration validated and reloaded'
            else
                log 'Candidate rejected or reload failed; previous running configuration retained, retry pending'
            fi
        fi
    else
        log 'Registry is missing/invalid; retaining previous proxy configuration'
    fi
    sleep "$WATCH_INTERVAL"
done
