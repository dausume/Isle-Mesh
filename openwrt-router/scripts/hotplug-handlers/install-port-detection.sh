#!/bin/bash

#############################################################################
# Isle-Mesh Port Detection Installation Script
#
# Installs the dynamic port detection system for Isle-Mesh:
# - Port monitoring service (systemd)
# - USB hotplug detection (udev rules)
# - Connection management CLI tools
# - YAD dependency for GUI dialogs
#
# Usage: sudo ./install-port-detection.sh
#
#############################################################################

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

log_error() {
    echo -e "${RED}[✗]${NC} $1"
}

log_step() {
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${CYAN}  $1${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root (use sudo)"
        exit 1
    fi
}

install_dependencies() {
    log_step "Step 1: Installing Dependencies"

    log_info "Updating package lists..."
    apt-get update -qq

    log_info "Installing YAD (Yet Another Dialog)..."
    apt-get install -y yad

    log_info "Installing network tools..."
    apt-get install -y ethtool usbutils

    log_success "Dependencies installed"
}

install_scripts() {
    log_step "Step 2: Installing Scripts"

    # Install port initialization script
    log_info "Installing port initialization script..."
    cp "$SCRIPT_DIR/port-init.sh" /usr/local/bin/isle-port-init
    chmod +x /usr/local/bin/isle-port-init
    log_success "Installed: /usr/local/bin/isle-port-init"

    # Install port event handler (main logic)
    log_info "Installing port event handler..."
    cp "$SCRIPT_DIR/port-event-handler.sh" /usr/local/bin/isle-port-event-handler
    chmod +x /usr/local/bin/isle-port-event-handler
    log_success "Installed: /usr/local/bin/isle-port-event-handler"

    # Install port event wrapper (called by udev)
    log_info "Installing port event wrapper..."
    cp "$SCRIPT_DIR/hotplug-handler.sh" /usr/local/bin/isle-port-event
    chmod +x /usr/local/bin/isle-port-event
    log_success "Installed: /usr/local/bin/isle-port-event"

    # Install add-connection command (manual mode)
    log_info "Installing add-connection command..."
    cp "$SCRIPT_DIR/add-connection.sh" /usr/local/bin/isle-add-connection
    chmod +x /usr/local/bin/isle-add-connection
    log_success "Installed: /usr/local/bin/isle-add-connection"

    # Create log directory
    mkdir -p /var/log/isle-mesh
    log_success "Created log directory: /var/log/isle-mesh"
}

install_systemd_service() {
    log_step "Step 3: Installing Systemd Service"

    log_info "Installing service file..."
    cp "$PROJECT_ROOT/systemd/isle-port-detection.service" /etc/systemd/system/
    log_success "Installed: /etc/systemd/system/isle-port-detection.service"

    log_info "Reloading systemd..."
    systemctl daemon-reload

    log_success "Systemd service installed"
}

install_udev_rules() {
    log_step "Step 4: Installing Udev Rules"

    log_info "Installing udev rules (event-driven detection)..."
    cp "$PROJECT_ROOT/udev/99-isle-mesh-ports.rules" /etc/udev/rules.d/
    log_success "Installed: /etc/udev/rules.d/99-isle-mesh-ports.rules"

    # Remove old rules if they exist
    if [[ -f "/etc/udev/rules.d/99-isle-mesh-usb.rules" ]]; then
        log_info "Removing old udev rules..."
        rm -f /etc/udev/rules.d/99-isle-mesh-usb.rules
    fi

    log_info "Reloading udev rules..."
    udevadm control --reload-rules
    udevadm trigger

    log_success "Udev rules installed and active (EVENT-DRIVEN)"
}

create_state_directory() {
    log_step "Step 5: Creating State Directory"

    mkdir -p /var/lib/isle-mesh
    touch /var/lib/isle-mesh/reserved-ports.conf
    touch /var/lib/isle-mesh/isp-interface.conf

    log_success "State directory created: /var/lib/isle-mesh"
}

show_completion() {
    log_step "Installation Complete!"

    cat << EOF

${GREEN}╔═══════════════════════════════════════════════════════════════╗
║     Isle-Mesh Port Detection System Successfully Installed    ║
║                   (EVENT-DRIVEN ARCHITECTURE)                  ║
╚═══════════════════════════════════════════════════════════════╝${NC}

${BLUE}Installed Components:${NC}
  ✓ Port initialization service (oneshot, boot only)
  ✓ Event-driven port detection (udev rules)
  ✓ USB WiFi hotplug detection
  ✓ Ethernet cable detection
  ✓ Connection management CLI
  ✓ YAD dialog dependency

${BLUE}Service Management:${NC}
  # Enable ISP interface detection on boot
  sudo systemctl enable isle-port-detection

  # Run initialization manually
  sudo systemctl start isle-port-detection

  # Check initialization status
  sudo systemctl status isle-port-detection

  # View event logs
  tail -f /var/log/isle-mesh/port-events.log

${BLUE}Manual Port Management:${NC}
  # Add connection manually
  sudo isle-add-connection

  # List available ports
  sudo isle-add-connection --list-only

  # Add only USB devices
  sudo isle-add-connection --type usb

${BLUE}How It Works (EVENT-DRIVEN - NO POLLING!):${NC}

  1. ${CYAN}Automatic Detection:${NC}
     - ${GREEN}Udev rules trigger on hardware events${NC}
     - Detects USB WiFi plugged in
     - Detects Ethernet cable plugged in
     - Shows YAD dialog INSTANTLY
     - ${YELLOW}NO continuous monitoring/polling${NC}
     - ${YELLOW}Much more efficient!${NC}

  2. ${CYAN}Manual Assignment:${NC}
     - Run: sudo isle-add-connection
     - Select port from list
     - Configure isle name and settings
     - Port is reserved for Isle-Mesh

${BLUE}Next Steps:${NC}
  1. Initialize OpenWRT router:
     ${CYAN}sudo isle router init${NC}
     (or legacy: sudo $SCRIPT_DIR/router-init.sh)

  2. Enable ISP detection on boot:
     ${CYAN}sudo systemctl enable --now isle-port-detection${NC}

  3. Plug in USB WiFi adapter
     ${GREEN}→ Dialog appears INSTANTLY (event-driven!)${NC}

     OR

     Run: ${CYAN}sudo isle router add-connection${NC} (manual)

${BLUE}Architecture Benefits:${NC}
  ${GREEN}✓${NC} Event-driven (no CPU waste from polling)
  ${GREEN}✓${NC} Instant response when hardware plugged in
  ${GREEN}✓${NC} Detects both USB and Ethernet events
  ${GREEN}✓${NC} ISP interface automatically excluded
  ${GREEN}✓${NC} Minimal resource usage

${YELLOW}Note:${NC} ISP network interface is detected at boot and automatically excluded.

EOF
}

main() {
    cat << EOF
${CYAN}╔═══════════════════════════════════════════════════════════════╗
║       Isle-Mesh Dynamic Port Detection Installation           ║
╚═══════════════════════════════════════════════════════════════╝${NC}

EOF

    check_root
    install_dependencies
    install_scripts
    install_systemd_service
    install_udev_rules
    create_state_directory
    show_completion
}

main "$@"
