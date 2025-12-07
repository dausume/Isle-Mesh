#!/bin/bash
# isle-host-agent-relay.sh
# Lightweight wrapper that extends the existing mesh-mdns system
# to relay mDNS indicators to isle-agent-sync when in agent mode

set -e

# === Configuration ===
MODE="${ISLE_AGENT_MODE:-broadcast}"  # broadcast | relay | both
SYNC_ENDPOINT="${SYNC_ENDPOINT:-http://localhost:8888/mdns}"
RELAY_INTERVAL="${RELAY_INTERVAL:-30}"  # seconds between relay updates
DOMAIN_LIST_FILE="${DOMAIN_LIST_FILE:-/usr/local/etc/mesh-mdns-domains.list}"

# Use existing mesh-mdns broadcast script
BROADCAST_SCRIPT="/usr/local/bin/isle-mesh/mesh-mdns-broadcast.sh"

log() {
  if [ -t 1 ]; then
    echo "[isle-host-agent] $*"
  else
    logger -t isle-host-agent "$*"
  fi
}

# === Function: Relay domain info to sync container ===
relay_to_sync() {
  local domain="$1"
  local target_ip="${2:-127.0.0.1}"

  log "Relaying $domain to sync container..."

  # Resolve domain to IP (if broadcasting)
  local resolved_ip=$(avahi-resolve -n "$domain" 2>/dev/null | awk '{print $2}' || echo "$target_ip")

  # Construct mDNS service data JSON
  local json_data=$(cat <<EOF
{
  "name": "${domain}",
  "type": "_http._tcp.local.",
  "addresses": ["${resolved_ip}"],
  "port": 443,
  "server": "${domain}",
  "properties": {
    "isle-agent": "true",
    "relayed_at": "$(date -Iseconds)"
  }
}
EOF
)

  # POST to sync container
  if curl -s -X POST "$SYNC_ENDPOINT" \
    -H "Content-Type: application/json" \
    -d "$json_data" > /dev/null 2>&1; then
    log "✅ Relayed $domain to sync"
  else
    log "⚠️  Failed to relay $domain to sync"
  fi
}

# === Function: Relay all domains periodically ===
relay_loop() {
  log "Starting relay loop (interval: ${RELAY_INTERVAL}s)"

  while true; do
    if [ -f "$DOMAIN_LIST_FILE" ]; then
      while IFS= read -r domain; do
        [[ -n "$domain" && ! "$domain" =~ ^# ]] && relay_to_sync "$domain"
      done < "$DOMAIN_LIST_FILE"
    fi

    sleep "$RELAY_INTERVAL"
  done
}

# === Main Logic ===
log "Isle Host Agent starting in mode: $MODE"

case "$MODE" in
  broadcast)
    # Pure broadcast mode - use existing mesh-mdns system
    log "Using existing mesh-mdns broadcast system"
    exec "$BROADCAST_SCRIPT"
    ;;

  relay)
    # Relay mode only - don't broadcast, just relay to sync
    log "Relay-only mode - sending domains to sync container"
    relay_loop
    ;;

  both)
    # Broadcast AND relay
    log "Combined mode - broadcasting via mDNS AND relaying to sync"

    # Start broadcast in background
    log "Starting mDNS broadcast..."
    "$BROADCAST_SCRIPT" &
    BROADCAST_PID=$!

    # Cleanup handler
    cleanup() {
      log "Shutting down..."
      kill $BROADCAST_PID 2>/dev/null || true
      exit 0
    }
    trap cleanup SIGTERM SIGINT

    # Start relay loop
    relay_loop
    ;;

  *)
    log "ERROR: Unknown mode '$MODE'. Use: broadcast|relay|both"
    exit 1
    ;;
esac
