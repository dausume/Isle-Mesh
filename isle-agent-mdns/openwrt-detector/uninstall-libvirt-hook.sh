#!/bin/bash
#
# Uninstall libvirt hook for router detection
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

log_error() {
    echo -e "${RED}[✗]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[⚠]${NC} $1"
}

# Check if running as root
if [[ $EUID -ne 0 ]]; then
    log_error "This script must be run as root"
    echo "Run with: sudo $0"
    exit 1
fi

echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║     Uninstall Libvirt Hook for Router Detection              ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

HOOK_FILE="/etc/libvirt/hooks/qemu"

if [ ! -f "$HOOK_FILE" ]; then
    log_warning "Hook file not found: $HOOK_FILE"
    log_info "Nothing to uninstall"
    exit 0
fi

# Check if this is our hook
if grep -q "isle-nginx-detector" "$HOOK_FILE" 2>/dev/null; then
    log_info "Removing hook file: $HOOK_FILE"
    rm -f "$HOOK_FILE"
    log_success "Hook file removed"

    # Restart libvirtd
    log_info "Restarting libvirtd..."
    if systemctl restart libvirtd; then
        log_success "Libvirtd restarted successfully"
    else
        log_error "Failed to restart libvirtd"
        exit 1
    fi

    echo ""
    log_success "Libvirt hook uninstalled successfully"
    echo ""
    echo "Nginx will no longer automatically reconfigure when router state changes."
    echo ""
    echo "If you want to re-enable automatic reconfiguration:"
    echo "  • Reinstall hook: sudo ./install-libvirt-hook.sh"
    echo "  • Or use systemd timer: see README.md"
    echo ""
else
    log_warning "Hook file exists but doesn't appear to be ours"
    log_info "Manual removal required: $HOOK_FILE"
    exit 1
fi
