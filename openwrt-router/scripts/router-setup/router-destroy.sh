#!/bin/bash

#############################################################################
# OpenWRT Router VM Destruction Script
#
# Completely removes the OpenWRT router VM and cleans up bridges by default.
#
# WARNING: This will permanently delete:
#   - The virtual machine and its configuration
#   - All router settings and customizations
#   - Any DHCP leases and firewall rules
#   - Network configurations on the router
#   - Network bridges (br-mgmt, isle-br-*)
#
# Usage: sudo ./router-destroy.sh [options]
#
# Options:
#   --vm-only           Only destroy VM, keep bridges (br-mgmt, isle-br-*)
#   --full              Destroy VM and remove all bridges (DEFAULT)
#   --force             Skip confirmation prompts (DANGEROUS!)
#   -h, --help          Show this help message
#############################################################################

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
NC='\033[0m'

# Defaults
VM_NAME="${VM_NAME:-openwrt-isle-router}"
CLEANUP_MODE="full"
FORCE=false

# Log functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_critical() {
    echo -e "${RED}${MAGENTA}[!!!]${NC} ${RED}$1${NC}"
}

show_usage() {
    cat << EOF
OpenWRT Router VM Destruction Script

Usage: sudo $0 [options]

Options:
  --vm-only           Only destroy VM, keep bridges (br-mgmt, isle-br-*)
  --full              Destroy VM and remove all bridges (DEFAULT)
  --force             Skip confirmation prompts (DANGEROUS!)
  -h, --help          Show this help message

Examples:
  # Complete cleanup (removes everything) - DEFAULT
  sudo $0

  # Remove VM only (keeps bridges for re-initialization)
  sudo $0 --vm-only

  # Force removal without confirmation
  sudo $0 --force

EOF
}

# Parse arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --vm-only)
                CLEANUP_MODE="vm-only"
                shift
                ;;
            --full)
                CLEANUP_MODE="full"
                shift
                ;;
            --force)
                FORCE=true
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

# Check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root (use sudo)"
        exit 1
    fi
}

# Show warning and get confirmation
confirm_destruction() {
    echo -e ""
    echo -e "${RED}╔═══════════════════════════════════════════════════════════════════════════╗"
    echo -e "║                           ⚠  DESTRUCTIVE ACTION  ⚠                        ║"
    echo -e "╚═══════════════════════════════════════════════════════════════════════════╝${NC}"
    echo -e ""
    echo -e "${YELLOW}You are about to permanently delete:${NC}"
    echo -e ""

    # Check if VM exists and show details
    if virsh dominfo "$VM_NAME" &> /dev/null; then
        local VM_STATE=$(virsh domstate "$VM_NAME" 2>/dev/null || echo "unknown")
        local VM_IFACES=$(virsh dumpxml "$VM_NAME" 2>/dev/null | grep -c "interface type" || echo "0")

        echo -e "  ${RED}✗${NC} Virtual Machine: ${YELLOW}$VM_NAME${NC}"
        echo -e "    - State: $VM_STATE"
        echo -e "    - Network Interfaces: $VM_IFACES"
        echo -e "    - All VM configuration and settings"
        echo -e ""
    else
        log_info "VM '$VM_NAME' not found - nothing to destroy"
        exit 0
    fi

    if [[ "$CLEANUP_MODE" == "full" ]]; then
        echo -e "  ${RED}✗${NC} Network Bridges:"
        echo -e "    - br-mgmt (192.168.1.x management network)"
        echo -e "    - isle-br-0 (local isle-agent connectivity)"
        echo -e ""
    fi

    echo -e "${RED}THIS ACTION CANNOT BE UNDONE!${NC}"
    echo -e ""
    echo -e "${YELLOW}What will be lost:${NC}"
    echo -e "  • All router configurations (network, firewall, DHCP)"
    echo -e "  • Any customizations or manual changes"
    echo -e "  • Active connections and DHCP leases"
    echo -e "  • Router logs and statistics"
    echo -e ""

    if [[ "$CLEANUP_MODE" == "full" ]]; then
        echo -e "${YELLOW}Additional cleanup (--full mode):${NC}"
        echo -e "  • Host network bridges will be removed"
        echo -e "  • Any containers using these bridges will lose connectivity"
        echo -e "  • You'll need to run 'isle router init' to set up again"
        echo -e ""
    else
        echo -e "${BLUE}Cleanup mode: VM only${NC}"
        echo -e "  • Bridges (br-mgmt, isle-br-0) will be preserved"
        echo -e "  • You can run 'isle router init' to recreate the VM"
        echo -e ""
    fi

    if [[ "$FORCE" == "true" ]]; then
        log_warning "Force mode enabled - skipping confirmation"
        return 0
    fi

    echo -e "${RED}Are you absolutely sure you want to proceed?${NC}"
    echo -e "Type '${YELLOW}DELETE${NC}' (in uppercase) to confirm: "
    read -r CONFIRMATION

    if [[ "$CONFIRMATION" != "DELETE" ]]; then
        log_info "Destruction cancelled - no changes made"
        exit 0
    fi

    echo
    log_warning "Proceeding with destruction in 3 seconds... (Press Ctrl+C to cancel)"
    sleep 3
}

# Destroy the VM
destroy_vm() {
    log_info "Destroying VM: $VM_NAME"

    # Check if VM exists
    if ! virsh dominfo "$VM_NAME" &> /dev/null; then
        log_warning "VM '$VM_NAME' does not exist"
        return 0
    fi

    # Stop VM if running
    if virsh domstate "$VM_NAME" 2>/dev/null | grep -q "running"; then
        log_info "Stopping running VM..."
        virsh destroy "$VM_NAME" &> /dev/null || {
            log_error "Failed to stop VM"
            return 1
        }
        log_success "VM stopped"
    fi

    # Undefine VM (remove configuration)
    log_info "Removing VM configuration..."
    if virsh undefine "$VM_NAME" --nvram &> /dev/null || virsh undefine "$VM_NAME" &> /dev/null; then
        log_success "VM configuration removed"
    else
        log_error "Failed to remove VM configuration"
        return 1
    fi

    log_success "VM '$VM_NAME' destroyed"
}

# Remove network bridge
remove_bridge() {
    local BRIDGE_NAME="$1"

    if ! ip link show "$BRIDGE_NAME" &> /dev/null; then
        log_info "Bridge '$BRIDGE_NAME' does not exist"
        return 0
    fi

    log_info "Removing bridge: $BRIDGE_NAME"

    # Bring down the bridge
    if ! ip link set "$BRIDGE_NAME" down 2>/dev/null; then
        log_warning "Failed to bring down bridge $BRIDGE_NAME"
    fi

    # Delete the bridge
    if ip link delete "$BRIDGE_NAME" 2>/dev/null; then
        log_success "Bridge '$BRIDGE_NAME' removed"
    else
        log_error "Failed to remove bridge $BRIDGE_NAME"
        return 1
    fi
}

# Clean up bridges
cleanup_bridges() {
    log_info "Cleaning up network bridges..."

    # Remove management bridge
    remove_bridge "br-mgmt"

    # Remove all isle-br-* bridges (handles isle-br-0, isle-br-1, etc.)
    for bridge in $(ip link show type bridge 2>/dev/null | grep -oP 'isle-br-\d+' || true); do
        if [[ -n "$bridge" ]]; then
            remove_bridge "$bridge"
        fi
    done

    log_success "Bridges cleaned up"
}

# Check for running isle-agent or other containers using bridges
check_running_containers() {
    log_info "Checking for containers using isle bridges..."

    local CONTAINERS_USING_BRIDGES=()

    # Check for containers on isle-br-0
    if docker network ls --format '{{.Name}}' 2>/dev/null | grep -q "isle-br-0"; then
        local CONTAINERS=$(docker network inspect isle-br-0 -f '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null || echo "")
        if [[ -n "$CONTAINERS" ]]; then
            CONTAINERS_USING_BRIDGES+=("isle-br-0: $CONTAINERS")
        fi
    fi

    if [[ ${#CONTAINERS_USING_BRIDGES[@]} -gt 0 ]]; then
        log_warning "The following containers are using isle bridges:"
        for container_info in "${CONTAINERS_USING_BRIDGES[@]}"; do
            echo -e "  ${YELLOW}•${NC} $container_info"
        done
        echo
        log_warning "These containers will lose network connectivity when bridges are removed"

        if [[ "$FORCE" != "true" ]]; then
            echo -e "${YELLOW}Continue anyway? (y/N)${NC}: "
            read -r CONTINUE
            if [[ "$CONTINUE" != "y" && "$CONTINUE" != "Y" ]]; then
                log_info "Destruction cancelled"
                exit 0
            fi
        fi
    fi
}

# Display summary
show_summary() {
    echo -e ""
    echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════════════════╗"
    echo -e "║                     OpenWRT Router Cleanup Complete                       ║"
    echo -e "╚═══════════════════════════════════════════════════════════════════════════╝${NC}"
    echo -e ""
    echo -e "${BLUE}What was removed:${NC}"
    echo -e "  ${GREEN}✓${NC} VM '$VM_NAME' destroyed"
    echo -e ""

    if [[ "$CLEANUP_MODE" == "full" ]]; then
        echo -e "  ${GREEN}✓${NC} Bridge 'br-mgmt' removed"
        echo -e "  ${GREEN}✓${NC} Bridge 'isle-br-0' removed"
        echo -e ""
        echo -e "${BLUE}System is now clean${NC}"
        echo -e "  To set up a new router: ${GREEN}sudo isle router init${NC}"
        echo -e ""
    else
        echo -e "${BLUE}Bridges preserved:${NC}"
        echo -e "  • br-mgmt (for management access)"
        echo -e "  • isle-br-0 (for local agent)"
        echo -e ""
        echo -e "${BLUE}Next steps:${NC}"
        echo -e "  To recreate the router VM: ${GREEN}sudo isle router init${NC}"
        echo -e ""
    fi

    echo -e "${BLUE}Verification:${NC}"
    echo -e "  Check VMs:      virsh list --all"
    echo -e "  Check bridges:  ip link show type bridge"
    echo -e ""
}

# Main execution
main() {
    parse_args "$@"
    check_root

    if [[ "$CLEANUP_MODE" == "full" ]]; then
        check_running_containers
    fi

    confirm_destruction
    destroy_vm

    if [[ "$CLEANUP_MODE" == "full" ]]; then
        cleanup_bridges
    fi

    show_summary
    log_success "Cleanup complete!"
}

# Run main function
main "$@"
