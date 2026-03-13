#!/bin/bash
#
# agent-registry-watcher.sh
# Watches the agent registry for changes and triggers mDNS sync
#
# This service runs continuously, monitoring the agent registry.json file
# for modifications. When changes are detected, it automatically syncs
# the .local domains to the mDNS broadcast configuration.

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Configuration
AGENT_REGISTRY="${AGENT_REGISTRY:-/etc/isle-mesh/agent/registry.json}"
SYNC_SCRIPT="${SYNC_SCRIPT:-/home/detts/Isle-Mesh/isle-agent/scripts/sync-lh-mdns-and-agent-registry.sh}"
WATCH_INTERVAL="${WATCH_INTERVAL:-5}"
USE_INOTIFY="${USE_INOTIFY:-auto}"

# Logging functions
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [registry-watcher] $*"
}

log_info() { log "INFO: $*"; }
log_success() { log "SUCCESS: $*"; }
log_warn() { log "WARN: $*"; }
log_error() { log "ERROR: $*" >&2; }

# Check if inotify-tools is available
has_inotify() {
    command -v inotifywait &> /dev/null
}

# Determine watch method
determine_watch_method() {
    if [[ "$USE_INOTIFY" == "true" ]] || [[ "$USE_inotify" == "auto" && $(has_inotify && echo "yes") == "yes" ]]; then
        if has_inotify; then
            echo "inotify"
        else
            log_warn "inotify-tools not available, falling back to polling"
            echo "polling"
        fi
    else
        echo "polling"
    fi
}

# Run sync script
run_sync() {
    log_info "Registry change detected, running sync..."

    if [[ ! -x "$SYNC_SCRIPT" ]]; then
        log_error "Sync script not found or not executable: $SYNC_SCRIPT"
        return 1
    fi

    if "$SYNC_SCRIPT"; then
        log_success "Sync completed successfully"
        return 0
    else
        log_error "Sync failed"
        return 1
    fi
}

# Watch using inotify (efficient)
watch_with_inotify() {
    log_info "Starting registry watcher (inotify mode)"
    log_info "  Registry: $AGENT_REGISTRY"
    log_info "  Sync script: $SYNC_SCRIPT"
    echo ""

    # Create registry file if it doesn't exist
    if [[ ! -f "$AGENT_REGISTRY" ]]; then
        log_warn "Registry file not found, creating: $AGENT_REGISTRY"
        local registry_dir
        registry_dir=$(dirname "$AGENT_REGISTRY")
        sudo mkdir -p "$registry_dir" 2>/dev/null || mkdir -p "$registry_dir"
        echo '{"domains": {}, "subdomains": {}, "apps": {}}' | sudo tee "$AGENT_REGISTRY" > /dev/null 2>&1 || echo '{"domains": {}, "subdomains": {}, "apps": {}}' > "$AGENT_REGISTRY"
    fi

    # Run initial sync
    log_info "Running initial sync..."
    run_sync
    echo ""

    # Watch for changes
    log_info "Watching for registry changes..."
    echo ""

    while true; do
        # Wait for file modification events
        inotifywait -e modify,create,move "$AGENT_REGISTRY" 2>/dev/null || {
            log_error "inotifywait failed, retrying in ${WATCH_INTERVAL}s..."
            sleep "$WATCH_INTERVAL"
            continue
        }

        # File was modified
        log_info "Registry file modified"

        # Small delay to ensure file write is complete
        sleep 1

        # Run sync
        run_sync
        echo ""
    done
}

# Watch using polling (fallback)
watch_with_polling() {
    log_info "Starting registry watcher (polling mode)"
    log_info "  Registry: $AGENT_REGISTRY"
    log_info "  Sync script: $SYNC_SCRIPT"
    log_info "  Poll interval: ${WATCH_INTERVAL}s"
    echo ""

    # Track last modification time
    local last_mtime=""

    # Create registry file if it doesn't exist
    if [[ ! -f "$AGENT_REGISTRY" ]]; then
        log_warn "Registry file not found, creating: $AGENT_REGISTRY"
        local registry_dir
        registry_dir=$(dirname "$AGENT_REGISTRY")
        sudo mkdir -p "$registry_dir" 2>/dev/null || mkdir -p "$registry_dir"
        echo '{"domains": {}, "subdomains": {}, "apps": {}}' | sudo tee "$AGENT_REGISTRY" > /dev/null 2>&1 || echo '{"domains": {}, "subdomains": {}, "apps": {}}' > "$AGENT_REGISTRY"
    fi

    # Run initial sync
    log_info "Running initial sync..."
    run_sync
    echo ""

    # Get initial modification time
    if [[ -f "$AGENT_REGISTRY" ]]; then
        last_mtime=$(stat -c %Y "$AGENT_REGISTRY" 2>/dev/null || echo "0")
    fi

    log_info "Polling for registry changes..."
    echo ""

    # Watch loop
    while true; do
        sleep "$WATCH_INTERVAL"

        if [[ -f "$AGENT_REGISTRY" ]]; then
            # Get current modification time
            local current_mtime
            current_mtime=$(stat -c %Y "$AGENT_REGISTRY" 2>/dev/null || echo "0")

            # Check if file was modified
            if [[ -n "$current_mtime" ]] && [[ "$current_mtime" != "$last_mtime" ]]; then
                log_info "Registry file modified"
                last_mtime="$current_mtime"

                # Run sync
                run_sync
                echo ""
            fi
        else
            # Registry file disappeared
            if [[ -n "$last_mtime" ]]; then
                log_warn "Registry file disappeared"
                last_mtime=""
            fi
        fi
    done
}

# Show help
show_help() {
    cat <<EOF
${BOLD}agent-registry-watcher.sh${NC}

Watches the agent registry for changes and triggers mDNS sync.

${BOLD}Usage:${NC}
  $0 [options]

${BOLD}Options:${NC}
  --inotify              Force use of inotify (requires inotify-tools)
  --polling              Force use of polling method
  --interval SECONDS     Polling interval (default: 5, only for polling mode)
  --help, -h             Show this help message

${BOLD}Environment Variables:${NC}
  AGENT_REGISTRY         Path to agent registry.json
                         (default: /etc/isle-mesh/agent/registry.json)

  SYNC_SCRIPT            Path to sync script
                         (default: /home/detts/Isle-Mesh/isle-agent/scripts/sync-lh-mdns-and-agent-registry.sh)

  WATCH_INTERVAL         Polling interval in seconds (default: 5)

  USE_INOTIFY            Watch method: 'true', 'false', or 'auto' (default: auto)

${BOLD}Description:${NC}
  This service continuously monitors the agent registry.json file for
  changes. When modifications are detected, it automatically triggers
  the sync script to update the mDNS broadcast configuration.

  The service can use two methods:
  - inotify: Efficient event-driven monitoring (requires inotify-tools)
  - polling: Fallback method that checks file modification time periodically

${BOLD}Installation as systemd service:${NC}
  Use the install-agent-registry-sync.sh script to install this as
  a systemd service that starts automatically on boot.

${BOLD}Examples:${NC}
  # Auto-detect best watch method
  $0

  # Force inotify method
  $0 --inotify

  # Use polling with custom interval
  $0 --polling --interval 10

EOF
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --inotify)
            USE_INOTIFY=true
            shift
            ;;
        --polling)
            USE_INOTIFY=false
            shift
            ;;
        --interval)
            WATCH_INTERVAL="$2"
            shift 2
            ;;
        --help|-h)
            show_help
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# Main execution
main() {
    # Determine watch method
    local watch_method
    watch_method=$(determine_watch_method)

    # Start watching
    case "$watch_method" in
        inotify)
            watch_with_inotify
            ;;
        polling)
            watch_with_polling
            ;;
        *)
            log_error "Unknown watch method: $watch_method"
            exit 1
            ;;
    esac
}

# Handle signals for graceful shutdown
trap 'log_info "Shutting down..."; exit 0' SIGTERM SIGINT

main
