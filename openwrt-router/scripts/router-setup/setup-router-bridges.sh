#!/bin/bash

#############################################################################
# Network Bridge Setup Script for OpenWRT Router
#
# This script creates and configures the network bridges needed for
# isle isolation and interconnection via the OpenWRT router.
#
# Usage: sudo ./setup-router-bridges.sh [options]
#
# Options:
#   -c, --config FILE   Path to vLAN mapping config (default: ../config/vlan-mapping.conf)
#   -h, --help          Show this help message
#############################################################################

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Default configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$PROJECT_ROOT/config/vlan-mapping.conf"

# Log functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Show usage
show_usage() {
    cat << EOF
Network Bridge Setup Script

Usage: sudo $0 [options]

Options:
  -c, --config FILE   Path to vLAN mapping config (default: ../config/vlan-mapping.conf)
  -h, --help          Show this help message

This script creates:
  - br-mgmt: Management bridge for OpenWRT access
  - br-isles: vLAN trunk bridge for isle interconnection
  - Individual isle bridges (optional, for direct host connection)

EOF
}

# Parse arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -c|--config)
                CONFIG_FILE="$2"
                shift 2
                ;;
            -h|--help)
                show_usage
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                show_usage
                exit 1
                ;;
        esac
    done
}

# Check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root (use sudo)"
        exit 1
    fi
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."

    for cmd in ip brctl; do
        if ! command -v $cmd &> /dev/null; then
            log_error "Missing required command: $cmd"
            log_info "Install with: apt-get install iproute2 bridge-utils"
            exit 1
        fi
    done

    log_success "Prerequisites met"
}

# Create bridge if it doesn't exist
create_bridge() {
    local BRIDGE_NAME="$1"
    local DESCRIPTION="$2"

    if ip link show "$BRIDGE_NAME" &> /dev/null; then
        log_info "Bridge $BRIDGE_NAME already exists"
        return 0
    fi

    log_info "Creating bridge: $BRIDGE_NAME ($DESCRIPTION)"

    ip link add "$BRIDGE_NAME" type bridge || {
        log_error "Failed to create bridge $BRIDGE_NAME"
        return 1
    }

    ip link set "$BRIDGE_NAME" up || {
        log_error "Failed to bring up bridge $BRIDGE_NAME"
        return 1
    }

    log_success "Created bridge: $BRIDGE_NAME"
}

# Configure management bridge
setup_mgmt_bridge() {
    log_info "Setting up management bridge..."

    create_bridge "br-mgmt" "Management network for OpenWRT access"

    # Assign IP address to management bridge if not already assigned
    if ! ip addr show br-mgmt | grep -q "192.168.1.254"; then
        log_info "Assigning IP address to br-mgmt"
        ip addr add 192.168.1.254/24 dev br-mgmt || log_warning "IP already assigned or failed"
    fi

    # Enable IP forwarding for management network
    sysctl -w net.ipv4.ip_forward=1 > /dev/null

    log_success "Management bridge configured (192.168.1.254/24)"
}

# Configure isles trunk bridge
setup_isles_bridge() {
    log_info "Setting up isles trunk bridge..."

    create_bridge "br-isles" "vLAN trunk for all isles"

    # Enable vLAN filtering on the bridge
    if ip link show br-isles &> /dev/null; then
        log_info "Enabling vLAN filtering on br-isles"
        ip link set br-isles type bridge vlan_filtering 1 || log_warning "vLAN filtering may not be supported"

        # Set bridge to be ageing-time appropriate for mesh networking
        ip link set br-isles type bridge ageing_time 30000  # 5 minutes

        log_success "Isles trunk bridge configured"
    fi
}

# Setup isle-specific bridges (optional)
setup_isle_bridges() {
    log_info "Setting up isle-specific bridges..."

    # Check if config file exists
    if [[ ! -f "$CONFIG_FILE" ]]; then
        log_warning "Config file not found: $CONFIG_FILE"
        log_info "Skipping isle-specific bridges"
        return 0
    fi

    # Read vLAN mappings from config
    local COUNT=0
    while IFS='=' read -r ISLE_NAME VLAN_ID; do
        # Skip comments and empty lines
        [[ "$ISLE_NAME" =~ ^#.*$ ]] && continue
        [[ -z "$ISLE_NAME" ]] && continue

        # Trim whitespace
        ISLE_NAME=$(echo "$ISLE_NAME" | xargs)
        VLAN_ID=$(echo "$VLAN_ID" | xargs)

        local BRIDGE_NAME="br-${ISLE_NAME,,}"  # Convert to lowercase
        local SUBNET="10.${VLAN_ID}.0"

        create_bridge "$BRIDGE_NAME" "Bridge for isle $ISLE_NAME (vLAN $VLAN_ID)"

        # Assign IP address (host gets .254 in each subnet)
        if ! ip addr show "$BRIDGE_NAME" | grep -q "${SUBNET}.254"; then
            log_info "Assigning IP ${SUBNET}.254/24 to $BRIDGE_NAME"
            ip addr add "${SUBNET}.254/24" dev "$BRIDGE_NAME" || log_warning "IP assignment failed"
        fi

        COUNT=$((COUNT + 1))
    done < "$CONFIG_FILE"

    if [[ $COUNT -eq 0 ]]; then
        log_warning "No isle mappings found in config file"
    else
        log_success "Configured $COUNT isle-specific bridge(s)"
    fi
}

# Configure kernel networking parameters
configure_kernel_params() {
    log_info "Configuring kernel networking parameters..."

    # Enable IP forwarding
    sysctl -w net.ipv4.ip_forward=1 > /dev/null

    # Enable bridge netfilter
    modprobe br_netfilter 2>/dev/null || log_warning "br_netfilter module not available"

    # Disable bridge filtering for better performance (OpenWRT will handle filtering)
    sysctl -w net.bridge.bridge-nf-call-iptables=0 > /dev/null 2>&1 || true
    sysctl -w net.bridge.bridge-nf-call-ip6tables=0 > /dev/null 2>&1 || true

    log_success "Kernel parameters configured"
}

# Make configuration persistent
make_persistent() {
    log_info "Making configuration persistent..."

    local NETPLAN_FILE="/etc/netplan/99-isle-mesh-bridges.yaml"
    local SYSTEMD_DIR="/etc/systemd/network"

    # Detect which network manager is in use
    if command -v netplan &> /dev/null; then
        log_info "Detected netplan, creating configuration..."

        cat > "$NETPLAN_FILE" << 'EOF'
# Isle-Mesh Bridge Configuration
# Generated by setup-network.sh

network:
  version: 2
  bridges:
    br-mgmt:
      dhcp4: no
      addresses:
        - 192.168.1.254/24
    br-isles:
      dhcp4: no
      parameters:
        stp: false
        forward-delay: 0
EOF

        log_success "Created netplan configuration: $NETPLAN_FILE"
        log_info "Apply with: sudo netplan apply"

    elif [[ -d "$SYSTEMD_DIR" ]]; then
        log_info "Detected systemd-networkd, creating configuration..."

        cat > "$SYSTEMD_DIR/br-mgmt.netdev" << 'EOF'
[NetDev]
Name=br-mgmt
Kind=bridge

[Bridge]
STP=no
EOF

        cat > "$SYSTEMD_DIR/br-mgmt.network" << 'EOF'
[Match]
Name=br-mgmt

[Network]
Address=192.168.1.254/24
EOF

        cat > "$SYSTEMD_DIR/br-isles.netdev" << 'EOF'
[NetDev]
Name=br-isles
Kind=bridge

[Bridge]
STP=no
VLANFiltering=yes
EOF

        cat > "$SYSTEMD_DIR/br-isles.network" << 'EOF'
[Match]
Name=br-isles

[Network]
LinkLocalAddressing=no
EOF

        log_success "Created systemd-networkd configuration"
        log_info "Restart with: sudo systemctl restart systemd-networkd"

    else
        log_warning "Could not detect network manager, configuration is not persistent"
        log_info "Bridges will be reset on reboot unless manually configured"
    fi

    # Make sysctl persistent
    local SYSCTL_FILE="/etc/sysctl.d/99-isle-mesh.conf"
    cat > "$SYSCTL_FILE" << 'EOF'
# Isle-Mesh Networking Configuration
net.ipv4.ip_forward=1
net.bridge.bridge-nf-call-iptables=0
net.bridge.bridge-nf-call-ip6tables=0
EOF

    log_success "Created sysctl configuration: $SYSCTL_FILE"
}

# Display network summary
show_network_summary() {
    cat << EOF

${GREEN}╔═══════════════════════════════════════════════════════════════════════════╗
║                    Network Bridges Configured                             ║
╚═══════════════════════════════════════════════════════════════════════════╝${NC}

${BLUE}Bridge Summary:${NC}

EOF

    # Show management bridge
    if ip link show br-mgmt &> /dev/null; then
        local MGMT_IP=$(ip addr show br-mgmt | grep "inet " | awk '{print $2}' || echo "N/A")
        echo "  br-mgmt      : $MGMT_IP (Management)"
    fi

    # Show isles bridge
    if ip link show br-isles &> /dev/null; then
        echo "  br-isles     : vLAN trunk (No IP)"
    fi

    # Show isle-specific bridges
    echo ""
    for bridge in $(ip link show type bridge | grep "^[0-9]" | awk -F': ' '{print $2}' | grep "^br-isle"); do
        local BRIDGE_IP=$(ip addr show "$bridge" | grep "inet " | awk '{print $2}' || echo "N/A")
        echo "  $bridge : $BRIDGE_IP"
    done

    cat << EOF

${BLUE}Verification Commands:${NC}
  List bridges:     ip link show type bridge
  Show bridge IPs:  ip addr show br-mgmt
  Test management:  ping 192.168.1.254

${BLUE}Configuration Files:${NC}
  vLAN mapping:     $CONFIG_FILE
  Netplan config:   /etc/netplan/99-isle-mesh-bridges.yaml
  Sysctl config:    /etc/sysctl.d/99-isle-mesh.conf

EOF
}

# Main execution
main() {
    log_info "Starting network bridge setup..."

    parse_args "$@"
    check_root
    check_prerequisites

    setup_mgmt_bridge
    setup_isles_bridge
    setup_isle_bridges
    configure_kernel_params
    make_persistent

    show_network_summary

    log_success "Network bridge setup complete!"
}

# Run main function
main "$@"
