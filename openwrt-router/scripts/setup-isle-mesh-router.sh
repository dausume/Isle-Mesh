#!/usr/bin/env bash
# setup-isle-mesh-router.sh - Master setup script for Isle Mesh OpenWRT router
# Orchestrates complete router initialization and configuration

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common-log.sh"
source "$SCRIPT_DIR/lib/common-utils.sh"

# Configuration defaults
ISLE_NAME="${ISLE_NAME:-my-isle}"
VLAN_ID="${VLAN_ID:-10}"
ROUTER_VM_NAME="${ROUTER_VM_NAME:-openwrt-isle-router}"
SKIP_VM_INIT=false
SKIP_VLAN_CONFIG=false
SKIP_DHCP_CONFIG=false
SKIP_DISCOVERY=false

show_usage() {
    cat << EOF
Isle Mesh OpenWRT Router Setup

Usage: $0 [options]

Options:
  --isle-name NAME         Isle name (default: my-isle)
  --vlan-id ID            VLAN ID (default: 10)
  --vm-name NAME          VM name (default: openwrt-isle-router)
  --skip-vm-init          Skip VM initialization
  --skip-vlan-config      Skip vLAN network configuration
  --skip-dhcp-config      Skip DHCP configuration
  --skip-discovery        Skip discovery beacon setup
  -h, --help              Show this help

Description:
  Complete setup script for Isle Mesh OpenWRT router. This script:

  1. Initializes OpenWRT VM (if needed)
  2. Configures vLAN networking
  3. Sets up DHCP server for virtual MACs
  4. Deploys discovery beacon for remote nginx auto-join
  5. Configures mDNS reflection

Example:
  # Full setup with defaults
  sudo $0

  # Custom isle and vlan
  sudo $0 --isle-name production --vlan-id 20

  # Skip VM init if already created
  sudo $0 --skip-vm-init

EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --isle-name)
                ISLE_NAME="$2"
                shift 2
                ;;
            --vlan-id)
                VLAN_ID="$2"
                shift 2
                ;;
            --vm-name)
                ROUTER_VM_NAME="$2"
                shift 2
                ;;
            --skip-vm-init)
                SKIP_VM_INIT=true
                shift
                ;;
            --skip-vlan-config)
                SKIP_VLAN_CONFIG=true
                shift
                ;;
            --skip-dhcp-config)
                SKIP_DHCP_CONFIG=true
                shift
                ;;
            --skip-discovery)
                SKIP_DISCOVERY=true
                shift
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

init_router_vm() {
    if $SKIP_VM_INIT; then
        log_info "Skipping VM initialization"
        if ! vm_exists "$ROUTER_VM_NAME"; then
            log_error "VM '$ROUTER_VM_NAME' does not exist and --skip-vm-init was specified"
            exit 1
        fi
        return 0
    fi

    log_step "Step 1: Initializing OpenWRT Router VM"

    if vm_exists "$ROUTER_VM_NAME"; then
        log_warning "VM '$ROUTER_VM_NAME' already exists"
        echo -n "Use existing VM? (Y/n): "
        read -r response
        if [[ "$response" =~ ^[Nn]$ ]]; then
            log_error "Please destroy existing VM or use --vm-name with different name"
            exit 1
        fi
        log_info "Using existing VM"
        return 0
    fi

    "$SCRIPT_DIR/router-setup/router-init.main.sh" \
        --vm-name "$ROUTER_VM_NAME" || {
        log_error "VM initialization failed"
        exit 1
    }

    log_success "Router VM initialized"
}

configure_vlan() {
    if $SKIP_VLAN_CONFIG; then
        log_info "Skipping vLAN configuration"
        return 0
    fi

    log_step "Step 2: Configuring vLAN Network"

    "$SCRIPT_DIR/router-setup/isle-vlan-router-config.main.sh" \
        --isle-name "$ISLE_NAME" \
        --vlan-id "$VLAN_ID" || {
        log_error "vLAN configuration failed"
        exit 1
    }

    log_success "vLAN network configured"
}

configure_dhcp() {
    if $SKIP_DHCP_CONFIG; then
        log_info "Skipping DHCP configuration"
        return 0
    fi

    log_step "Step 3: Configuring DHCP Server"

    "$SCRIPT_DIR/router-setup/configure-dhcp-vlan.sh" \
        --isle-name "$ISLE_NAME" \
        --vlan-id "$VLAN_ID" || {
        log_error "DHCP configuration failed"
        exit 1
    }

    log_success "DHCP server configured"
}

setup_discovery() {
    if $SKIP_DISCOVERY; then
        log_info "Skipping discovery beacon setup"
        return 0
    fi

    log_step "Step 4: Setting Up Discovery Beacon"

    "$SCRIPT_DIR/router-setup/configure-discovery.sh" \
        --isle-name "$ISLE_NAME" \
        --vlan-id "$VLAN_ID" || {
        log_error "Discovery beacon setup failed"
        exit 1
    }

    log_success "Discovery beacon configured"
}

show_completion() {
    log_step "Isle Mesh Router Setup Complete"

    cat << EOF

${GREEN}╔═══════════════════════════════════════════════════════════════╗
║        Isle Mesh OpenWRT Router Successfully Configured       ║
╚═══════════════════════════════════════════════════════════════╝${NC}

${BLUE}Configuration Summary:${NC}
  Isle Name:      ${ISLE_NAME}
  vLAN ID:        ${VLAN_ID}
  Router VM:      ${ROUTER_VM_NAME}
  Router IP:      10.${VLAN_ID}.0.1
  DHCP Range:     10.${VLAN_ID}.0.50-250

${BLUE}What's Running:${NC}
  ✓ OpenWRT KVM router on host
  ✓ vLAN network (${VLAN_ID}) configured
  ✓ DHCP server assigning IPs to virtual MACs
  ✓ Discovery beacon broadcasting every 30s
  ✓ mDNS reflector enabled

${BLUE}Router Management:${NC}
  # Access OpenWRT shell
  virsh console ${ROUTER_VM_NAME}

  # SSH to OpenWRT (if configured)
  ssh root@192.168.1.1

  # Check router status
  virsh list --all

${BLUE}Discovery System:${NC}
  # Test discovery broadcasts (on any machine)
  sudo nc -l -u 7878

  # Should receive packets like:
  # ISLE_MESH_DISCOVERY|isle=${ISLE_NAME}|vlan=${VLAN_ID}|...

${BLUE}Next Steps:${NC}
  1. ${CYAN}Install isle-agent on remote machines${NC}
     Remote nginx proxies need the agent to auto-join mesh
     (Agent installation will be provided separately)

  2. ${CYAN}Add physical interfaces to router${NC}
     # Ethernet
     sudo ./utilities/add-ethernet-connection.main.sh

     # USB WiFi
     sudo ./utilities/add-usb-wifi.main.sh

  3. ${CYAN}Deploy nginx proxies${NC}
     Ensure proxies have labels:
     - isle-mesh.isle=${ISLE_NAME}
     - isle-mesh.proxy=true
     - isle-mesh.vlan=${VLAN_ID}

${BLUE}Testing the Mesh:${NC}
  1. Start a remote machine with isle-agent installed
  2. Agent receives discovery broadcast
  3. Agent creates bridge and connects nginx container
  4. nginx gets DHCP IP from OpenWRT (10.${VLAN_ID}.0.XX)
  5. mDNS services are reflected across the mesh

${YELLOW}Logs:${NC}
  OpenWRT:  virsh console ${ROUTER_VM_NAME}, then: logread -f
  Host:     /var/log/isle-mesh/*.log

${GREEN}The Isle Mesh router is ready to accept connections!${NC}

EOF
}

main() {
    log_banner "Isle Mesh OpenWRT Router Setup"

    parse_args "$@"
    require_root
    init_common_env

    log_info "Setting up Isle Mesh router: ${ISLE_NAME} (vLAN ${VLAN_ID})"
    echo

    init_router_vm
    configure_vlan
    configure_dhcp
    setup_discovery

    show_completion
    log_success "Setup complete!"
}

main "$@"
