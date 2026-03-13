#!/bin/bash
#
# install-agent-registry-sync.sh
# Install the agent registry sync service
#

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Service configuration
SERVICE_NAME="agent-registry-sync"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# Script locations
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCHER_SCRIPT="${SCRIPT_DIR}/agent-registry-watcher.sh"
SYNC_SCRIPT="${SCRIPT_DIR}/sync-lh-mdns-and-agent-registry.sh"

# Install locations
INSTALL_DIR="/usr/local/bin"
WATCHER_INSTALL="${INSTALL_DIR}/agent-registry-watcher"
SYNC_INSTALL="${INSTALL_DIR}/sync-lh-mdns-and-agent-registry"

# Logging functions
log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }
log_step() {
    echo ""
    echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}$*${NC}"
    echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
    echo ""
}

# Check if running as root
if [[ $EUID -ne 0 ]]; then
    log_error "This script must be run as root (use sudo)"
    exit 1
fi

# Check if required scripts exist
if [[ ! -f "$WATCHER_SCRIPT" ]]; then
    log_error "Watcher script not found: $WATCHER_SCRIPT"
    exit 1
fi

if [[ ! -f "$SYNC_SCRIPT" ]]; then
    log_error "Sync script not found: $SYNC_SCRIPT"
    exit 1
fi

log_step "Installing Agent Registry Sync Service"

# Install scripts
log_info "Installing watcher script to $WATCHER_INSTALL"
cp "$WATCHER_SCRIPT" "$WATCHER_INSTALL"
chmod +x "$WATCHER_INSTALL"

log_info "Installing sync script to $SYNC_INSTALL"
cp "$SYNC_SCRIPT" "$SYNC_INSTALL"
chmod +x "$SYNC_INSTALL"

# Create systemd service
log_info "Creating service file: $SERVICE_FILE"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Isle Agent Registry mDNS Sync Service
After=network.target docker.service mesh-mdns.service
Wants=mesh-mdns.service

[Service]
Type=simple
ExecStart=$WATCHER_INSTALL
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=agent-registry-sync

# Environment
Environment="AGENT_REGISTRY=/etc/isle-mesh/agent/registry.json"
Environment="SYNC_SCRIPT=$SYNC_INSTALL"
Environment="USE_INOTIFY=auto"
Environment="WATCH_INTERVAL=5"

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/etc/isle-mesh /usr/local/etc

[Install]
WantedBy=multi-user.target
EOF

# Enable and start service
log_info "Enabling and starting service"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl start "$SERVICE_NAME"

# Wait and check status
sleep 2

if systemctl is-active --quiet "$SERVICE_NAME"; then
    log_step "Installation Complete!"
    log_success "Service is running"
    echo ""
    log_info "The service will automatically:"
    log_info "  • Watch /etc/isle-mesh/agent/registry.json for changes"
    log_info "  • Sync .local domains to mDNS broadcast configuration"
    log_info "  • Reload mesh-mdns service when domains change"
    echo ""
    log_info "Check logs with: sudo journalctl -u $SERVICE_NAME -f"
else
    log_error "Service failed to start"
    log_info "Check status with: systemctl status $SERVICE_NAME"
    exit 1
fi
