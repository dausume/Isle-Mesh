#!/bin/bash
#
# Install libvirt hook for event-driven router detection
#
# This creates a libvirt QEMU hook that triggers nginx reconfiguration
# automatically when the OpenWRT router VM state changes.
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

# Check if running as root
if [[ $EUID -ne 0 ]]; then
    log_error "This script must be run as root"
    echo "Run with: sudo $0"
    exit 1
fi

# Get the directory where this script lives
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR_SCRIPT="$SCRIPT_DIR/detect-and-configure-nginx.sh"

# Check if detector script exists
if [ ! -f "$DETECTOR_SCRIPT" ]; then
    log_error "Detector script not found: $DETECTOR_SCRIPT"
    exit 1
fi

echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║     Install Libvirt Hook for Router Detection                ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

log_info "This will install a libvirt QEMU hook that automatically"
log_info "triggers nginx reconfiguration when router VM state changes."
echo ""

# Create hooks directory if it doesn't exist
HOOKS_DIR="/etc/libvirt/hooks"
if [ ! -d "$HOOKS_DIR" ]; then
    log_info "Creating hooks directory: $HOOKS_DIR"
    mkdir -p "$HOOKS_DIR"
fi

# Create the hook script
HOOK_FILE="$HOOKS_DIR/qemu"

log_info "Creating hook script: $HOOK_FILE"

cat > "$HOOK_FILE" << 'EOF'
#!/bin/bash
#
# Libvirt QEMU hook for OpenWRT router detection
#
# Called by libvirt when VM state changes:
#   $1 = VM name
#   $2 = operation (start, stopped, etc.)
#

VM_NAME="$1"
OPERATION="$2"

# Path to detector script (updated during installation)
DETECTOR_SCRIPT="__DETECTOR_SCRIPT_PATH__"

# Log hook invocation
logger -t "libvirt-hook" "VM: $VM_NAME, Operation: $OPERATION"

# Only care about openwrt-isle-router
if [[ "$VM_NAME" != "openwrt-isle-router" ]]; then
    exit 0
fi

# Trigger reconfiguration on relevant events
case "$OPERATION" in
    start|started|stopped|shutdown)
        logger -t "libvirt-hook" "Triggering nginx reconfiguration for $VM_NAME ($OPERATION)"
        # Run detector script in background to not block libvirt
        nohup "$DETECTOR_SCRIPT" >> /var/log/isle-nginx-detector.log 2>&1 &
        ;;
esac

exit 0
EOF

# Replace placeholder with actual detector script path
sed -i "s|__DETECTOR_SCRIPT_PATH__|$DETECTOR_SCRIPT|g" "$HOOK_FILE"

# Make hook executable
chmod +x "$HOOK_FILE"

log_success "Hook script created: $HOOK_FILE"

# Restart libvirtd to load the hook
log_info "Restarting libvirtd to load hook..."
if systemctl restart libvirtd; then
    log_success "Libvirtd restarted successfully"
else
    log_error "Failed to restart libvirtd"
    exit 1
fi

echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║                    Installation Complete                      ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""
echo -e "${GREEN}✅ Libvirt hook installed successfully${NC}"
echo ""
echo "The nginx configuration will now automatically update when:"
echo "  • Router VM starts"
echo "  • Router VM stops"
echo "  • Router VM shuts down"
echo ""
echo "View hook logs with:"
echo "  sudo journalctl -t libvirt-hook -f"
echo ""
echo "View detector logs with:"
echo "  sudo tail -f /var/log/isle-nginx-detector.log"
echo ""
echo "Test the hook:"
echo "  sudo isle router down openwrt-isle-router"
echo "  sudo isle router up openwrt-isle-router"
echo ""
