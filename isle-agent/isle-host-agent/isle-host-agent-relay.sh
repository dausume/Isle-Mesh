#!/bin/bash
# isle-host-agent-relay.sh
# Consolidated Isle Host Agent with integrated registry watcher and mDNS sync
# Combines mDNS broadcasting, registry updates, and automatic sync functionality

set -e

# === Configuration ===
MODE="${ISLE_AGENT_MODE:-both}"  # broadcast | registry | both
REGISTRY_FILE="${REGISTRY_FILE:-/etc/isle-mesh/agent/registry.json}"
RELAY_INTERVAL="${RELAY_INTERVAL:-30}"  # seconds between registry updates
DOMAIN_LIST_FILE="${DOMAIN_LIST_FILE:-/usr/local/etc/mesh-mdns-domains.list}"
WATCH_INTERVAL="${WATCH_INTERVAL:-5}"  # seconds between registry watch checks
USE_INOTIFY="${USE_INOTIFY:-auto}"  # auto | true | false

# Use existing mesh-mdns broadcast script
BROADCAST_SCRIPT="/usr/local/bin/isle-mesh/mesh-mdns-broadcast.sh"

# Process tracking
BROADCAST_PID=""
REGISTRY_PID=""
WATCHER_PID=""
CLEANUP_DONE=false

log() {
  local timestamp="[$(date '+%Y-%m-%d %H:%M:%S')]"
  if [ -t 1 ]; then
    echo "$timestamp [isle-host-agent] $*"
  else
    logger -t isle-host-agent "$*"
  fi
}

# === Function: Initialize registry with default structure ===
init_registry() {
  # Ensure registry directory exists
  mkdir -p "$(dirname "$REGISTRY_FILE")"

  # Create default registry structure if it doesn't exist
  if [ ! -f "$REGISTRY_FILE" ]; then
    log "Creating default registry with health check app..."
    cat > "$REGISTRY_FILE" <<'EOF'
{
  "domains": {},
  "subdomains": {},
  "apps": {
    "health": {
      "name": "health",
      "domain": "health.local",
      "target": "localhost",
      "port": 80,
      "protocol": "http",
      "description": "System health check endpoint",
      "created_at": "",
      "updated_at": ""
    }
  }
}
EOF
    # Update timestamps
    if command -v jq >/dev/null 2>&1; then
      local now
      now=$(date -Iseconds)
      local temp_file=$(mktemp)
      jq --arg now "$now" \
         '.apps.health.created_at = $now | .apps.health.updated_at = $now' \
         "$REGISTRY_FILE" > "$temp_file"
      cp "$temp_file" "$REGISTRY_FILE" && rm -f "$temp_file"  # inode-preserving (bind mount)
    fi
    log "✅ Default registry created with health.local app"
  else
    # Ensure health app exists in existing registry
    ensure_health_app
  fi
}

# === Function: Ensure health app exists in registry ===
ensure_health_app() {
  if ! command -v jq >/dev/null 2>&1; then
    return 0
  fi

  # Check if health app exists
  local has_health
  has_health=$(jq -r '.apps.health // empty' "$REGISTRY_FILE" 2>/dev/null)

  if [ -z "$has_health" ]; then
    log "Adding default health check app to registry..."
    local now
    now=$(date -Iseconds)
    local temp_file=$(mktemp)
    trap "rm -f $temp_file" EXIT

    jq --arg now "$now" \
       '.apps.health = {
         "name": "health",
         "domain": "health.local",
         "target": "localhost",
         "port": 80,
         "protocol": "http",
         "description": "System health check endpoint",
         "created_at": $now,
         "updated_at": $now
       }' "$REGISTRY_FILE" > "$temp_file"

    cp "$temp_file" "$REGISTRY_FILE" && rm -f "$temp_file"  # inode-preserving (bind mount)
    log "✅ Added health.local app to registry"
  fi
}

# === Function: Update registry.json with service data ===
update_registry() {
  local domain="$1"
  local target_ip="${2:-127.0.0.1}"

  log "Updating registry for $domain..."

  # Resolve domain to IP (if broadcasting)
  local resolved_ip=$(avahi-resolve -n "$domain" 2>/dev/null | awk '{print $2}' || echo "$target_ip")

  # Ensure registry is initialized
  init_registry

  # Create temporary file for atomic update
  local temp_file=$(mktemp)
  trap "rm -f $temp_file" EXIT

  # Update or add service using jq
  if command -v jq >/dev/null 2>&1; then
    # Use jq for proper JSON manipulation
    jq --arg name "$domain" \
       --arg addr "$resolved_ip" \
       --arg port "443" \
       --arg proto "https" \
       --arg updated "$(date -Iseconds)" \
       '.services[$name] = {
         "name": $name,
         "addresses": [$addr],
         "port": ($port | tonumber),
         "protocol": $proto,
         "updated_at": $updated,
         "created_at": (.services[$name].created_at // $updated)
       }' "$REGISTRY_FILE" > "$temp_file"

    # Atomic replace
    cp "$temp_file" "$REGISTRY_FILE" && rm -f "$temp_file"  # inode-preserving (bind mount)
    log "✅ Updated registry for $domain -> $resolved_ip"
  else
    log "⚠️  jq not found - cannot update registry"
    return 1
  fi
}

# === Function: Update registry for all domains periodically ===
registry_loop() {
  log "Starting registry update loop (interval: ${RELAY_INTERVAL}s)"

  while true; do
    if [ -f "$DOMAIN_LIST_FILE" ]; then
      while IFS= read -r domain; do
        [[ -n "$domain" && ! "$domain" =~ ^# ]] && update_registry "$domain"
      done < "$DOMAIN_LIST_FILE"
    fi

    sleep "$RELAY_INTERVAL"
  done
}

# === Function: Sync .local domains from registry to mDNS list ===
sync_registry_to_mdns() {
  log "Syncing .local domains from registry to mDNS list..."

  # Check if jq is available
  if ! command -v jq >/dev/null 2>&1; then
    log "⚠️  jq not found - cannot sync domains"
    return 1
  fi

  # Check if registry exists
  if [ ! -f "$REGISTRY_FILE" ]; then
    log "⚠️  Registry file not found: $REGISTRY_FILE"
    return 0
  fi

  # Extract .local domains from registry
  local agent_domains
  agent_domains=$(jq -r '.apps | to_entries[]? | .value.domain | select(endswith(".local"))' "$REGISTRY_FILE" 2>/dev/null | sort -u || echo "")

  if [ -z "$agent_domains" ]; then
    log "No .local domains found in registry"
    return 0
  fi

  # Ensure domain list directory exists
  mkdir -p "$(dirname "$DOMAIN_LIST_FILE")" 2>/dev/null || true

  # Create domain list file if it doesn't exist
  if [ ! -f "$DOMAIN_LIST_FILE" ]; then
    touch "$DOMAIN_LIST_FILE" 2>/dev/null || sudo touch "$DOMAIN_LIST_FILE" 2>/dev/null || true
  fi

  local changes_made=false

  # Add each domain to mDNS list if not already present
  while IFS= read -r domain; do
    if [ -n "$domain" ]; then
      if ! grep -Fxq "$domain" "$DOMAIN_LIST_FILE" 2>/dev/null; then
        echo "$domain" >> "$DOMAIN_LIST_FILE" 2>/dev/null || echo "$domain" | sudo tee -a "$DOMAIN_LIST_FILE" >/dev/null 2>&1 || true
        log "✅ Added to mDNS list: $domain"
        changes_made=true
      fi
    fi
  done <<< "$agent_domains"

  # Reload mesh-mdns service if changes were made
  if [ "$changes_made" = true ]; then
    if systemctl is-active --quiet mesh-mdns.service 2>/dev/null; then
      log "Reloading mesh-mdns.service..."
      sudo systemctl reload mesh-mdns.service 2>/dev/null || systemctl reload mesh-mdns.service 2>/dev/null || true
      log "✅ mDNS service reloaded"
    fi
  fi
}

# === Function: Check if inotify is available ===
has_inotify() {
  command -v inotifywait >/dev/null 2>&1
}

# === Function: Watch registry with inotify ===
watch_registry_inotify() {
  log "Starting registry watcher (inotify mode)"

  # Ensure registry is initialized with health app
  init_registry

  # Run initial sync
  sync_registry_to_mdns

  # Watch for changes
  log "Watching for registry changes..."

  while true; do
    # Wait for file modification events
    if inotifywait -e modify,create,move "$REGISTRY_FILE" 2>/dev/null; then
      log "Registry file modified, syncing..."
      sleep 1  # Small delay to ensure file write is complete
      sync_registry_to_mdns
    else
      log "⚠️  inotifywait failed, retrying in ${WATCH_INTERVAL}s..."
      sleep "$WATCH_INTERVAL"
    fi
  done
}

# === Function: Watch registry with polling ===
watch_registry_polling() {
  log "Starting registry watcher (polling mode, interval: ${WATCH_INTERVAL}s)"

  local last_mtime=""

  # Ensure registry is initialized with health app
  init_registry

  # Run initial sync
  sync_registry_to_mdns

  # Get initial modification time
  if [ -f "$REGISTRY_FILE" ]; then
    last_mtime=$(stat -c %Y "$REGISTRY_FILE" 2>/dev/null || echo "0")
  fi

  log "Polling for registry changes..."

  # Watch loop
  while true; do
    sleep "$WATCH_INTERVAL"

    if [ -f "$REGISTRY_FILE" ]; then
      local current_mtime
      current_mtime=$(stat -c %Y "$REGISTRY_FILE" 2>/dev/null || echo "0")

      if [ -n "$current_mtime" ] && [ "$current_mtime" != "$last_mtime" ]; then
        log "Registry file modified, syncing..."
        last_mtime="$current_mtime"
        sync_registry_to_mdns
      fi
    fi
  done
}

# === Function: Start registry watcher ===
start_registry_watcher() {
  # Determine watch method
  local watch_method="polling"

  if [ "$USE_INOTIFY" = "true" ] || [ "$USE_INOTIFY" = "auto" ]; then
    if has_inotify; then
      watch_method="inotify"
    else
      if [ "$USE_INOTIFY" = "true" ]; then
        log "⚠️  inotify-tools not available, falling back to polling"
      fi
    fi
  fi

  # Start watcher based on method
  if [ "$watch_method" = "inotify" ]; then
    watch_registry_inotify &
  else
    watch_registry_polling &
  fi

  WATCHER_PID=$!
  log "Registry watcher started (PID: $WATCHER_PID, method: $watch_method)"
}

# === Cleanup Handler ===
cleanup() {
  if [ "$CLEANUP_DONE" = true ]; then
    return
  fi
  CLEANUP_DONE=true

  log "Shutting down..."

  # Kill all child processes
  if [ -n "$BROADCAST_PID" ]; then
    log "Stopping broadcast process (PID: $BROADCAST_PID)..."
    kill $BROADCAST_PID 2>/dev/null || true
  fi

  if [ -n "$REGISTRY_PID" ]; then
    log "Stopping registry loop (PID: $REGISTRY_PID)..."
    kill $REGISTRY_PID 2>/dev/null || true
  fi

  if [ -n "$WATCHER_PID" ]; then
    log "Stopping registry watcher (PID: $WATCHER_PID)..."
    kill $WATCHER_PID 2>/dev/null || true
  fi

  # Wait for processes to terminate
  sleep 1

  log "Isle Host Agent stopped"
  exit 0
}

trap cleanup SIGTERM SIGINT EXIT

# === Main Logic ===
log "═══════════════════════════════════════════════════"
log "Isle Host Agent - Consolidated Service"
log "Mode: $MODE"
log "Registry: $REGISTRY_FILE"
log "mDNS List: $DOMAIN_LIST_FILE"
log "═══════════════════════════════════════════════════"

# Always start the registry watcher (monitors registry changes and syncs to mDNS)
log "Starting registry watcher..."
start_registry_watcher

case "$MODE" in
  broadcast)
    # Pure broadcast mode - use existing mesh-mdns system
    log "Broadcast mode: Using mesh-mdns broadcast system"
    exec "$BROADCAST_SCRIPT"
    ;;

  registry)
    # Registry mode only - update registry and watch for changes
    log "Registry mode: Updating registry.json and watching for changes"

    # Start registry update loop in background
    registry_loop &
    REGISTRY_PID=$!
    log "Registry update loop started (PID: $REGISTRY_PID)"

    # Wait for all background processes
    log "All components started - entering monitor mode"
    wait
    ;;

  both)
    # Broadcast AND update registry - full functionality
    log "Combined mode: Full mDNS broadcast + registry updates + auto-sync"

    # Start broadcast in background
    log "Starting mDNS broadcast..."
    "$BROADCAST_SCRIPT" &
    BROADCAST_PID=$!
    log "Broadcast started (PID: $BROADCAST_PID)"

    # Start registry update loop in background
    log "Starting registry update loop..."
    registry_loop &
    REGISTRY_PID=$!
    log "Registry loop started (PID: $REGISTRY_PID)"

    # Wait for all background processes
    log "All components started - entering monitor mode"
    wait
    ;;

  *)
    log "ERROR: Unknown mode '$MODE'. Use: broadcast|registry|both"
    exit 1
    ;;
esac
