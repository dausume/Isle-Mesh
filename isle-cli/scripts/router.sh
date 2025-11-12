#!/bin/bash

#############################################################################
# Isle Router Management
#
# This script manages OpenWRT router for Isle-Mesh network isolation.
#
# Usage: isle router <subcommand> [options]
#
# Subcommands:
#   init             - Initialize secure OpenWRT router (recommended)
#   add-connection   - Interactively add USB/Ethernet port to router
#   provision        - Provision production OpenWRT router (legacy)
#   configure        - Configure OpenWRT router
#   status           - Show router status with source attribution
#   security         - Verify network isolation security
#   help             - Show detailed help
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

# Get project root (parent of isle-cli)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ISLE_CLI_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$ISLE_CLI_ROOT/.." && pwd)"
ROUTER_DIR="$PROJECT_ROOT/openwrt-router"
TESTS_DIR="$ROUTER_DIR/tests"

# Source network source detection library if available
if [[ -f "$ROUTER_DIR/scripts/lib-network-sources.sh" ]]; then
    source "$ROUTER_DIR/scripts/lib-network-sources.sh"
fi

# Parse subcommand
SUBCOMMAND="$1"
shift || true

# Logging functions
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
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Wrapper for virsh commands that tries without sudo first, then with sudo
virsh_cmd() {
    # Try without sudo first (for users with libvirt group access)
    if virsh "$@" 2>/dev/null; then
        return 0
    fi

    # Fall back to sudo if needed
    sudo virsh "$@" 2>/dev/null
}

# Get the IP address of a router VM by looking up its MAC in ARP table
get_router_ip() {
    local vm_name="$1"

    # Get MAC address from VM configuration
    local mac_address=$(virsh_cmd dumpxml "$vm_name" | grep "mac address" | head -1 | sed -n "s/.*mac address='\([^']*\)'.*/\1/p")

    if [[ -z "$mac_address" ]]; then
        return 1
    fi

    # Look up IP in ARP table
    local ip_address=$(arp -n | grep -i "$mac_address" | awk '{print $1}' | head -1)

    if [[ -z "$ip_address" ]]; then
        return 1
    fi

    echo "$ip_address"
    return 0
}

# Select router and get its IP address
# If only one router running, auto-select it
# If multiple routers running, show interactive menu
select_router_with_ip() {
    local routers=()
    local router_ips=()

    # Find all running routers
    while IFS= read -r vm; do
        if [[ -n "$vm" ]]; then
            local ip=$(get_router_ip "$vm")
            if [[ -n "$ip" ]]; then
                routers+=("$vm")
                router_ips+=("$ip")
            fi
        fi
    done < <(virsh_cmd list --state-running | grep -E "openwrt|router" | awk '{print $2}')

    # No routers found
    if [[ ${#routers[@]} -eq 0 ]]; then
        log_error "No running routers found or routers not responding on network"
        return 1
    fi

    # Single router - auto-select
    if [[ ${#routers[@]} -eq 1 ]]; then
        echo "${routers[0]}|${router_ips[0]}"
        return 0
    fi

    # Multiple routers - show interactive menu
    echo ""
    log_info "Multiple routers detected. Please select one:"
    echo ""

    PS3="Select router (1-${#routers[@]}): "
    select choice in "${routers[@]}"; do
        if [[ -n "$choice" ]]; then
            local idx=$((REPLY - 1))
            echo "${routers[$idx]}|${router_ips[$idx]}"
            return 0
        else
            log_error "Invalid selection. Please try again."
        fi
    done

    return 1
}

# Check if router directory exists
check_router_dir() {
    if [[ ! -d "$ROUTER_DIR" ]]; then
        log_error "OpenWRT router directory not found: $ROUTER_DIR"
        log_info "Make sure you have the openwrt-router directory in your project"
        exit 1
    fi
}

# Clean up a single VM and its associated bridges
cleanup_vm_and_bridges() {
    local vm_name="$1"

    log_info "Cleaning up VM: $vm_name"

    # Get list of bridges attached to this VM before removing it
    local bridges=()
    if sudo virsh domiflist "$vm_name" &>/dev/null; then
        while IFS= read -r line; do
            # Extract bridge names from virsh domiflist output
            local bridge=$(echo "$line" | awk '{print $3}')
            if [[ -n "$bridge" ]] && [[ "$bridge" != "Source" ]] && [[ "$bridge" != "-" ]]; then
                bridges+=("$bridge")
            fi
        done < <(sudo virsh domiflist "$vm_name" 2>/dev/null | tail -n +3)
    fi

    # Stop VM if running
    if sudo virsh list --state-running 2>/dev/null | grep -q "$vm_name"; then
        log_info "Stopping VM $vm_name..."
        sudo virsh destroy "$vm_name" 2>/dev/null || log_warning "Failed to stop VM gracefully"
    fi

    # Undefine VM (this also removes vnet interfaces automatically)
    log_info "Removing VM definition..."
    sudo virsh undefine "$vm_name" 2>/dev/null || log_warning "Failed to undefine VM"

    # Clean up bridges that were created for this VM
    # Only remove bridges that look like they were created for routers (br-test-*, br-mgmt, br-isles)
    for bridge in "${bridges[@]}"; do
        if [[ "$bridge" =~ ^br-(test-|mgmt|isles) ]]; then
            if ip link show "$bridge" &>/dev/null; then
                log_info "Removing bridge $bridge..."
                sudo ip link set "$bridge" down 2>/dev/null || true
                sudo ip link delete "$bridge" 2>/dev/null || log_warning "Failed to remove bridge $bridge"
            fi
        fi
    done

    log_success "Cleaned up $vm_name and associated bridges"
}

# Check for existing routers
check_existing_routers() {
    local ROUTER_VMS=()

    # Check for dynamic router
    if sudo virsh list --all 2>/dev/null | grep -q "openwrt-isle-router"; then
        ROUTER_VMS+=("openwrt-isle-router")
    fi

    # Check for test router
    if sudo virsh list --all 2>/dev/null | grep -q "openwrt-test"; then
        ROUTER_VMS+=("openwrt-test")
    fi

    # Check for legacy production router (not router-core)
    if sudo virsh list --all 2>/dev/null | grep -q "^.*openwrt-router "; then
        ROUTER_VMS+=("openwrt-router")
    fi

    # Check for router-core
    if sudo virsh list --all 2>/dev/null | grep -q "router-core"; then
        ROUTER_VMS+=("router-core")
    fi

    if [[ ${#ROUTER_VMS[@]} -gt 0 ]]; then
        echo ""
        log_warning "Found existing router VM(s):"
        echo ""
        for vm in "${ROUTER_VMS[@]}"; do
            local state=$(sudo virsh list --all | grep "$vm" | awk '{print $3}')
            echo "  - $vm ($state)"

            # Show which bridges are attached
            if sudo virsh domiflist "$vm" &>/dev/null; then
                local vm_bridges=$(sudo virsh domiflist "$vm" 2>/dev/null | tail -n +3 | awk '{print $3}' | grep -v "^$" | tr '\n' ', ' | sed 's/,$//')
                if [[ -n "$vm_bridges" ]]; then
                    echo "    Bridges: $vm_bridges"
                fi
            fi
        done
        echo ""
        echo -e "${YELLOW}You can only have ONE router running at a time.${NC}"
        echo -e "${YELLOW}Removing old routers will also clean up their network bridges.${NC}"
        echo ""
        read -p "Do you want to stop and remove all existing routers? (y/N): " confirm
        if [[ "$confirm" =~ ^[Yy]$ ]]; then
            for vm in "${ROUTER_VMS[@]}"; do
                cleanup_vm_and_bridges "$vm"
            done
            echo ""
            log_success "All existing routers removed"
            echo ""
        else
            log_info "Exiting without changes"
            echo ""
            echo -e "When you're ready, remove the old router(s) and run this command again"
            exit 0
        fi
    fi
}

# Check if sudo wrapper symlink is configured
check_sudo_wrapper() {
    local WRAPPER_SYMLINK="/usr/local/bin/isle"
    local WRAPPER_SCRIPT="$ISLE_CLI_ROOT/isle-wrapper.sh"

    # Check if wrapper script exists
    if [[ ! -f "$WRAPPER_SCRIPT" ]]; then
        log_error "Wrapper script not found: $WRAPPER_SCRIPT"
        return 1
    fi

    # Check if symlink exists
    if [[ ! -L "$WRAPPER_SYMLINK" ]] && [[ ! -f "$WRAPPER_SYMLINK" ]]; then
        echo ""
        log_error "Sudo wrapper symlink not configured"
        echo ""
        echo -e "${YELLOW}╔═══════════════════════════════════════════════════════════════╗${NC}"
        echo -e "${YELLOW}║  Setup Required: Create Sudo Wrapper Symlink                 ║${NC}"
        echo -e "${YELLOW}╚═══════════════════════════════════════════════════════════════╝${NC}"
        echo ""
        echo -e "${BLUE}Why is this needed?${NC}"
        echo "  When you run 'sudo isle', the system needs to know where to find"
        echo "  the isle command and use the correct Node.js environment."
        echo ""
        echo -e "${BLUE}To fix this, run:${NC}"
        echo ""
        echo -e "  ${CYAN}sudo ln -sf \"$WRAPPER_SCRIPT\" \"$WRAPPER_SYMLINK\"${NC}"
        echo ""
        echo -e "${BLUE}Then you can run:${NC}"
        echo ""
        echo -e "  ${CYAN}sudo isle router init${NC}"
        echo ""
        return 1
    fi

    # Verify symlink points to the correct wrapper (only if it's a symlink)
    if [[ -L "$WRAPPER_SYMLINK" ]]; then
        local SYMLINK_TARGET=$(readlink -f "$WRAPPER_SYMLINK")
        local WRAPPER_TARGET=$(readlink -f "$WRAPPER_SCRIPT")

        if [[ "$SYMLINK_TARGET" != "$WRAPPER_TARGET" ]]; then
            echo ""
            log_warning "Symlink points to wrong target"
            echo ""
            echo -e "${YELLOW}Expected:${NC} $WRAPPER_TARGET"
            echo -e "${YELLOW}Current:${NC}  $SYMLINK_TARGET"
            echo ""
            echo -e "${YELLOW}Update the symlink with:${NC}"
            echo ""
            echo -e "  ${CYAN}sudo ln -sf \"$WRAPPER_SCRIPT\" \"$WRAPPER_SYMLINK\"${NC}"
            echo ""
            return 1
        fi
    fi

    return 0
}

# Check if running with sudo
check_sudo() {
    if [[ $EUID -ne 0 ]]; then
        # First check if wrapper is configured before suggesting sudo
        if ! check_sudo_wrapper; then
            exit 1
        fi

        log_error "This command requires sudo privileges"
        log_info "Run with: sudo isle router $SUBCOMMAND"
        exit 1
    fi
}

# Init - Initialize secure router
cmd_init() {
    check_router_dir
    check_sudo

    echo -e "${CYAN}╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║          Initialize Isle-Mesh Router (Complete Setup)         ║"
    echo -e "║                                                                ║"
    echo -e "║  Creates OpenWRT router VM and configures vLAN networking,    ║"
    echo -e "║  DHCP server, and discovery beacon for mesh connectivity.     ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    # Check for existing routers
    check_existing_routers

    log_info "Initializing secure OpenWRT router..."
    echo ""

    if [[ ! -f "$ROUTER_DIR/scripts/router-init.sh" ]]; then
        log_error "Init script not found: $ROUTER_DIR/scripts/router-init.sh"
        exit 1
    fi

    # Run the router initialization script
    # (This calls setup-isle-mesh-router.sh which provides comprehensive output)
    bash "$ROUTER_DIR/scripts/router-init.sh" "$@"
}

# Add Connection - Interactively add ports
cmd_add_connection() {
    check_router_dir
    check_sudo

    log_info "Adding connection to router..."
    echo ""

    if [[ ! -f "$ROUTER_DIR/scripts/add-connection.sh" ]]; then
        log_error "Add connection script not found"
        log_info "Expected location: $ROUTER_DIR/scripts/add-connection.sh"
        exit 1
    fi

    # Check if router exists
    if ! sudo virsh list --all 2>/dev/null | grep -q "openwrt-isle-router"; then
        log_error "Router VM not found: openwrt-isle-router"
        echo ""
        echo -e "Initialize router first: ${CYAN}sudo isle router init${NC}"
        exit 1
    fi

    # Run the add connection script
    bash "$ROUTER_DIR/scripts/add-connection.sh" "$@"
}

# reconfigure - Reconfigure network on existing VM
cmd_test_reconfigure() {
    check_router_dir
    check_sudo

    log_info "Reconfiguring OpenWRT test VM network interfaces..."
    echo ""

    if [[ ! -f "$ROUTER_DIR/scripts/auto-configure-network.sh" ]]; then
        log_error "Auto-configure script not found: $ROUTER_DIR/scripts/auto-configure-network.sh"
        exit 1
    fi

    # Run the auto-configure script with test VM defaults
    bash "$ROUTER_DIR/scripts/auto-configure-network.sh" -v "openwrt-test" -m "192.168.100.1" -i "10.100.0.1" "$@"
}

# Provision - Provision production router
cmd_provision() {
    check_router_dir
    check_sudo

    # Check for existing routers before provisioning
    check_existing_routers

    log_info "Provisioning OpenWRT router..."
    echo ""

    if [[ ! -f "$ROUTER_DIR/scripts/provision-vm.sh" ]]; then
        log_error "Provision script not found: $ROUTER_DIR/scripts/provision-vm.sh"
        exit 1
    fi

    # Parse optional VM name as first positional argument
    local PROVISION_ARGS=()
    if [[ -n "$1" ]] && [[ ! "$1" =~ ^- ]]; then
        # First argument is a VM name (not a flag)
        local VM_NAME="$1"
        shift
        PROVISION_ARGS+=("-n" "$VM_NAME")
    fi

    # Append any remaining arguments
    PROVISION_ARGS+=("$@")

    # Run the provision script with parsed arguments
    bash "$ROUTER_DIR/scripts/provision-vm.sh" "${PROVISION_ARGS[@]}"
}

# Configure - Configure OpenWRT router
cmd_configure() {
    check_router_dir

    log_info "Configuring OpenWRT router..."
    echo ""

    if [[ ! -f "$ROUTER_DIR/scripts/configure-openwrt.sh" ]]; then
        log_error "Configure script not found: $ROUTER_DIR/scripts/configure-openwrt.sh"
        exit 1
    fi

    # Run the configure script
    bash "$ROUTER_DIR/scripts/configure-openwrt.sh" "$@"
}

# Detect - Detect available USB/Ethernet ports
cmd_detect() {
    check_router_dir

    log_info "Detecting available hardware..."
    echo ""

    if [[ ! -f "$ROUTER_DIR/scripts/detect-ports.sh" ]]; then
        log_error "Detect script not found: $ROUTER_DIR/scripts/detect-ports.sh"
        exit 1
    fi

    # Run the detect script
    bash "$ROUTER_DIR/scripts/detect-ports.sh" "$@"
}

# Security - Verify network isolation
cmd_security() {
    check_router_dir

    log_info "Verifying network isolation security..."
    echo ""

    if [[ ! -f "$ROUTER_DIR/scripts/verify-network-isolation.sh" ]]; then
        log_error "Security verification script not found: $ROUTER_DIR/scripts/verify-network-isolation.sh"
        exit 1
    fi

    # Run the security verification script
    bash "$ROUTER_DIR/scripts/verify-network-isolation.sh" "$@"
}

# Discover mDNS - Discover .local domains from router
cmd_discover_mdns() {
    check_router_dir

    if [[ ! -f "$ROUTER_DIR/scripts/utilities/discover-mdns-domains.sh" ]]; then
        log_error "mDNS discovery script not found: $ROUTER_DIR/scripts/utilities/discover-mdns-domains.sh"
        exit 1
    fi

    # Detect router and its IP
    local router_info=$(select_router_with_ip)
    if [[ $? -ne 0 ]] || [[ -z "$router_info" ]]; then
        log_error "Could not detect router IP address"
        echo ""
        echo "Make sure a router is running and accessible on the network."
        echo "Run 'isle router status' to check router status."
        exit 1
    fi

    local ROUTER_VM=$(echo "$router_info" | cut -d'|' -f1)
    local ROUTER_IP=$(echo "$router_info" | cut -d'|' -f2)

    log_info "Using router: $ROUTER_VM at $ROUTER_IP"
    echo ""

    # Run the mDNS discovery script with the detected IP
    bash "$ROUTER_DIR/scripts/utilities/discover-mdns-domains.sh" "$ROUTER_IP" "$@"
}

# Domains - Manage .vlan domain mappings
cmd_domains() {
    check_router_dir

    if [[ ! -f "$ROUTER_DIR/scripts/utilities/manage-vlan-domains.sh" ]]; then
        log_error "Domain management script not found: $ROUTER_DIR/scripts/utilities/manage-vlan-domains.sh"
        exit 1
    fi

    # Detect router and its IP
    local router_info=$(select_router_with_ip)
    if [[ $? -ne 0 ]] || [[ -z "$router_info" ]]; then
        log_error "Could not detect router IP address"
        echo ""
        echo "Make sure a router is running and accessible on the network."
        echo "Run 'isle router status' to check router status."
        exit 1
    fi

    local ROUTER_VM=$(echo "$router_info" | cut -d'|' -f1)
    local ROUTER_IP=$(echo "$router_info" | cut -d'|' -f2)

    log_info "Using router: $ROUTER_VM at $ROUTER_IP"
    echo ""

    # Run the domain management script with the detected IP
    bash "$ROUTER_DIR/scripts/utilities/manage-vlan-domains.sh" "$ROUTER_IP" "$@"
}

# List - List all routers
cmd_list() {
    check_router_dir

    echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║              Available Routers                                ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    if ! command -v virsh &> /dev/null; then
        log_error "libvirt not installed"
        echo ""
        echo -e "${YELLOW}Install with:${NC} sudo apt-get install qemu-kvm libvirt-daemon-system libvirt-clients"
        return 1
    fi

    # Collect all router VMs
    local ALL_ROUTERS=()

    if sudo virsh list --all 2>/dev/null | grep -q "openwrt-test"; then
        ALL_ROUTERS+=("openwrt-test")
    fi

    if sudo virsh list --all 2>/dev/null | grep -q "^.*openwrt-router "; then
        ALL_ROUTERS+=("openwrt-router")
    fi

    if sudo virsh list --all 2>/dev/null | grep -q "openwrt-isle-router"; then
        ALL_ROUTERS+=("openwrt-isle-router")
    fi

    if sudo virsh list --all 2>/dev/null | grep -q "router-core"; then
        ALL_ROUTERS+=("router-core")
    fi

    if [[ ${#ALL_ROUTERS[@]} -eq 0 ]]; then
        echo -e "${YELLOW}No routers found${NC}"
        echo ""
        echo "Create a router with:"
        echo -e "  ${CYAN}sudo isle router init${NC}           - Initialize secure router"
        echo ""
        return 0
    fi

    echo -e "${CYAN}NAME${NC}                    ${CYAN}STATE${NC}           ${CYAN}BRIDGES${NC}"
    echo "─────────────────────────────────────────────────────────────"

    for vm in "${ALL_ROUTERS[@]}"; do
        local STATE=$(sudo virsh list --all 2>/dev/null | grep "$vm" | awk '{print $3}')
        local STATUS_SYMBOL="○"
        local STATUS_COLOR="${YELLOW}"

        if [[ "$STATE" == "running" ]]; then
            STATUS_SYMBOL="●"
            STATUS_COLOR="${GREEN}"
        fi

        # Get bridges
        local vm_bridges=""
        if sudo virsh domiflist "$vm" &>/dev/null; then
            vm_bridges=$(sudo virsh domiflist "$vm" 2>/dev/null | tail -n +3 | awk '{print $3}' | grep -v "^$" | tr '\n' ', ' | sed 's/,$//')
        fi

        printf "${STATUS_COLOR}${STATUS_SYMBOL}${NC} %-20s %-15s %s\n" "$vm" "$STATE" "$vm_bridges"
    done

    echo ""
    echo -e "${BLUE}Commands:${NC}"
    echo -e "  ${CYAN}isle router up <name>${NC}     - Start a router"
    echo -e "  ${CYAN}isle router down <name>${NC}   - Stop a router"
    echo -e "  ${CYAN}isle router delete <name>${NC} - Delete a router"
    echo ""
}

# Up - Start a router
cmd_up() {
    check_router_dir
    check_sudo

    local ROUTER_NAME="$1"

    if [[ -z "$ROUTER_NAME" ]]; then
        log_error "Router name required"
        echo ""
        echo "Usage: sudo isle router up <name>"
        echo ""
        echo "Available routers:"
        cmd_list
        exit 1
    fi

    # Check if router exists
    if ! sudo virsh list --all 2>/dev/null | grep -q "$ROUTER_NAME"; then
        log_error "Router not found: $ROUTER_NAME"
        echo ""
        echo "Use 'isle router list' to see available routers"
        exit 1
    fi

    # Check if already running
    if sudo virsh list --state-running 2>/dev/null | grep -q "$ROUTER_NAME"; then
        log_warning "Router $ROUTER_NAME is already running"
        return 0
    fi

    log_info "Starting router: $ROUTER_NAME"
    sudo virsh start "$ROUTER_NAME"

    if [[ $? -eq 0 ]]; then
        log_success "Router $ROUTER_NAME started successfully"
    else
        log_error "Failed to start router $ROUTER_NAME"
        exit 1
    fi
}

# Down - Stop a router
cmd_down() {
    check_router_dir
    check_sudo

    local ROUTER_NAME="$1"

    if [[ -z "$ROUTER_NAME" ]]; then
        log_error "Router name required"
        echo ""
        echo "Usage: sudo isle router down <name>"
        echo ""
        echo "Running routers:"
        sudo virsh list --state-running 2>/dev/null | grep "openwrt\|router-core" || echo "  No routers running"
        echo ""
        exit 1
    fi

    # Check if router exists
    if ! sudo virsh list --all 2>/dev/null | grep -q "$ROUTER_NAME"; then
        log_error "Router not found: $ROUTER_NAME"
        echo ""
        echo "Use 'isle router list' to see available routers"
        exit 1
    fi

    # Check if router is running
    if ! sudo virsh list --state-running 2>/dev/null | grep -q "$ROUTER_NAME"; then
        log_warning "Router $ROUTER_NAME is not running"
        return 0
    fi

    log_info "Stopping router: $ROUTER_NAME"
    sudo virsh shutdown "$ROUTER_NAME"

    if [[ $? -eq 0 ]]; then
        log_success "Router $ROUTER_NAME stopped successfully"
        echo ""
        echo "Use 'sudo isle router up $ROUTER_NAME' to start it again"
    else
        log_error "Failed to stop router $ROUTER_NAME"
        exit 1
    fi
}

# Delete - Delete a router completely
cmd_delete() {
    check_router_dir
    check_sudo

    local ROUTER_NAME="$1"
    local FORCE=false

    # Parse flags
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -f|--force)
                FORCE=true
                shift
                ;;
            *)
                ROUTER_NAME="$1"
                shift
                ;;
        esac
    done

    if [[ -z "$ROUTER_NAME" ]]; then
        log_error "Router name required"
        echo ""
        echo "Usage: sudo isle router delete <name> [-f|--force]"
        echo ""
        echo "Available routers:"
        cmd_list
        exit 1
    fi

    # Check if router exists
    if ! sudo virsh list --all 2>/dev/null | grep -q "$ROUTER_NAME"; then
        log_error "Router not found: $ROUTER_NAME"
        echo ""
        echo "Use 'isle router list' to see available routers"
        exit 1
    fi

    # Confirm deletion unless forced
    if [[ "$FORCE" != true ]]; then
        echo ""
        log_warning "This will permanently delete router: $ROUTER_NAME"
        echo ""
        echo "This will:"
        echo "  - Stop the VM if running"
        echo "  - Remove the VM definition"
        echo "  - Clean up associated network bridges"
        echo "  - Delete the router's XML config"
        echo ""
        read -p "Are you sure you want to delete $ROUTER_NAME? (y/N): " confirm

        if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
            log_info "Deletion cancelled"
            exit 0
        fi
        echo ""
    fi

    log_info "Deleting router: $ROUTER_NAME"
    echo ""

    # Use existing cleanup function
    cleanup_vm_and_bridges "$ROUTER_NAME"

    # Delete XML file from runtime if it exists
    local XML_FILE="$ROUTER_DIR/runtime/${ROUTER_NAME}.xml"
    if [[ -f "$XML_FILE" ]]; then
        log_info "Removing config file: $XML_FILE"
        rm -f "$XML_FILE"
    fi

    echo ""
    log_success "Router $ROUTER_NAME deleted successfully"
}

# Destroy - Complete cleanup with confirmation
cmd_destroy() {
    check_router_dir
    check_sudo

    local CLEANUP_MODE="vm-only"
    local FORCE_FLAG=""

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --full)
                CLEANUP_MODE="full"
                shift
                ;;
            --vm-only)
                CLEANUP_MODE="vm-only"
                shift
                ;;
            -f|--force)
                FORCE_FLAG="--force"
                shift
                ;;
            -h|--help)
                cat << EOF
${BLUE}╔═══════════════════════════════════════════════════════════════╗
║              Isle Router Destroy Command                      ║
╚═══════════════════════════════════════════════════════════════╝${NC}

Completely removes the OpenWRT router VM and cleans up bridges by default.

${GREEN}USAGE:${NC}
  sudo isle router destroy [options]

${GREEN}OPTIONS:${NC}
  --vm-only           Only destroy VM, keep bridges (br-mgmt, isle-br-*)
  --full              Destroy VM and remove all bridges (DEFAULT)
  -f, --force         Skip confirmation prompts (DANGEROUS!)
  -h, --help          Show this help message

${GREEN}EXAMPLES:${NC}

  ${YELLOW}# Complete cleanup (removes everything) - DEFAULT${NC}
  sudo isle router destroy

  ${YELLOW}# Remove VM only (keeps bridges for re-initialization)${NC}
  sudo isle router destroy --vm-only

  ${YELLOW}# Force removal without confirmation${NC}
  sudo isle router destroy --force

${GREEN}WHAT GETS DELETED:${NC}

${CYAN}Default (--full):${NC}
  - OpenWRT router VM and configuration
  - All router settings and customizations
  - br-mgmt (management bridge)
  - All isle-br-* bridges (isle-br-0, isle-br-1, etc.)
  - Any containers using these bridges will lose connectivity

${CYAN}With --vm-only:${NC}
  - OpenWRT router VM and configuration
  - All router settings and customizations
  - Bridges are preserved for quick re-initialization

${RED}WARNING:${NC} This action cannot be undone!

${GREEN}RECOVERY:${NC}
  To recreate the router: ${CYAN}sudo isle router init${NC}

EOF
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                echo ""
                echo "Run 'sudo isle router destroy --help' for usage"
                exit 1
                ;;
        esac
    done

    # Check if destroy script exists
    if [[ ! -f "$ROUTER_DIR/scripts/router-setup/router-destroy.sh" ]]; then
        log_error "Destroy script not found: $ROUTER_DIR/scripts/router-setup/router-destroy.sh"
        exit 1
    fi

    # Call the destroy script with appropriate flags
    bash "$ROUTER_DIR/scripts/router-setup/router-destroy.sh" --$CLEANUP_MODE $FORCE_FLAG
}

# Status - Show router status
cmd_status() {
    check_router_dir

    echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║              OpenWRT Router Status                            ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    # Check for VMs
    if ! command -v virsh &> /dev/null; then
        echo -e "${RED}libvirt:${NC}       Not installed"
        echo ""
        echo -e "${YELLOW}Install with:${NC} sudo apt-get install qemu-kvm libvirt-daemon-system libvirt-clients"
        return 1
    fi

    echo -e "${BLUE}═══ Virtual Machines ═══${NC}"
    echo ""

    # Collect all router VMs
    local ALL_ROUTERS=()
    local RUNNING_ROUTER=""
    local RUNNING_COUNT=0

    # Check for all known router types
    if virsh_cmd list --all | grep -q "openwrt-test"; then
        ALL_ROUTERS+=("openwrt-test")
    fi

    if virsh_cmd list --all | grep -q "^.*openwrt-router "; then
        ALL_ROUTERS+=("openwrt-router")
    fi

    if virsh_cmd list --all | grep -q "openwrt-isle-router"; then
        ALL_ROUTERS+=("openwrt-isle-router")
    fi

    if virsh_cmd list --all | grep -q "router-core"; then
        ALL_ROUTERS+=("router-core")
    fi

    # Show all routers
    if [[ ${#ALL_ROUTERS[@]} -eq 0 ]]; then
        echo -e "${YELLOW}No routers found${NC}"
        echo ""
        echo "To get started:"
        echo -e "  ${CYAN}sudo isle router init${NC}           - Initialize secure router"
        echo ""
        return 0
    fi

    for vm in "${ALL_ROUTERS[@]}"; do
        local STATE=$(virsh_cmd list --all | grep "$vm" | awk '{print $3}')
        local STATUS_COLOR="${GREEN}"

        if [[ "$STATE" == "running" ]]; then
            STATUS_COLOR="${GREEN}"
            RUNNING_ROUTER="$vm"
            RUNNING_COUNT=$((RUNNING_COUNT + 1))
            echo -e "${STATUS_COLOR}● $vm${NC} (${STATE})"
        elif [[ "$STATE" == "shut" ]]; then
            STATUS_COLOR="${YELLOW}"
            echo -e "${STATUS_COLOR}○ $vm${NC} (${STATE} off)"
        else
            STATUS_COLOR="${RED}"
            echo -e "${STATUS_COLOR}○ $vm${NC} (${STATE})"
        fi

        # Show which bridges are attached
        if virsh_cmd domiflist "$vm" &>/dev/null; then
            local vm_bridges=$(virsh_cmd domiflist "$vm" | tail -n +3 | awk '{print $3}' | grep -v "^$" | tr '\n' ', ' | sed 's/,$//')
            if [[ -n "$vm_bridges" ]]; then
                echo "  └─ Bridges: $vm_bridges"
            fi
        fi
    done

    echo ""

    # Warn if multiple routers are running
    if [[ $RUNNING_COUNT -gt 1 ]]; then
        log_warning "Multiple routers are running!"
        echo ""
        echo "Only ONE router should be active at a time to avoid conflicts."
        echo "Use 'isle router down <name>' to stop unused routers."
        echo ""
    fi

    # If no router is running, show quick start guide
    if [[ $RUNNING_COUNT -eq 0 ]]; then
        echo -e "${YELLOW}═══ No Running Router Detected ═══${NC}"
        echo ""
        echo "To start a router:"
        echo -e "  ${CYAN}sudo isle router up <name>${NC}      - Start a specific router"
        echo -e "  ${CYAN}isle router list${NC}                - List all available routers"
        echo ""
        return 0
    fi

    # From here on, show details about the running router
    local ROUTER_VM="$RUNNING_ROUTER"

    # Dynamically detect router IP
    local ROUTER_IP=$(get_router_ip "$ROUTER_VM")

    if [[ -z "$ROUTER_IP" ]]; then
        log_warning "Could not detect IP address for router: $ROUTER_VM"
        echo ""
        echo "The router VM is running but not responding on the network."
        echo "This may mean:"
        echo "  - The router is still booting (wait a moment and try again)"
        echo "  - The network configuration needs attention"
        echo ""
        echo "You can try:"
        echo "  - Wait 10-20 seconds and run 'isle router status' again"
        echo "  - Check if the router is on the ARP table: arp -n"
        echo ""
        return 1
    fi

    # From here on, we have a running router with an IP
    log_info "Querying router: ${ROUTER_VM} at ${ROUTER_IP}"
    echo ""

    # Check if router is reachable
    if ! ping -c 1 -W 2 "$ROUTER_IP" > /dev/null 2>&1; then
        log_warning "Router not responding to ping at $ROUTER_IP"
        echo ""
        echo "The router VM is running but not responding to network requests."
        echo ""

        # If multiple routers are running, suggest stopping extras first
        if [[ $RUNNING_COUNT -gt 1 ]]; then
            echo "Since multiple routers are running, stop the others first:"
            echo -e "  ${CYAN}sudo isle router down <router-name>${NC}"
            echo ""
        fi

        # Suggest appropriate reconfigure command based on router type
        if [[ "$ROUTER_VM" == "openwrt-test" ]]; then
            echo -e "Then try reconfiguring: ${CYAN}sudo isle router reconfigure${NC}"
        else
            echo -e "Try reconfiguring: ${CYAN}sudo isle router configure${NC}"
        fi

        echo ""
        return 1
    fi

    # Try to get detailed info from router via SSH
    local SSH_AVAILABLE=false
    if timeout 2 bash -c "echo > /dev/tcp/$ROUTER_IP/22" 2>/dev/null; then
        SSH_AVAILABLE=true
        log_success "SSH is available on router"

        # Run detailed query script if available
        if [[ -f "$ROUTER_DIR/scripts/query-router-status.sh" ]]; then
            echo ""
            log_info "Querying router internals..."
            echo ""
            bash "$ROUTER_DIR/scripts/query-router-status.sh" "$ROUTER_IP" || {
                log_warning "Detailed query failed, showing host-side info only"
            }
        fi
    else
        log_info "SSH not available, showing host-side information only"
    fi

    echo ""
    echo -e "${BLUE}═══ Network Interfaces (host side) ═══${NC}"
    echo ""

    # Show bridge information from host
    if ip addr show br-test-mgmt &> /dev/null; then
        local MGMT_IP=$(ip addr show br-test-mgmt | grep -oP 'inet \K[\d.]+' | head -1)
        local MGMT_STATE=$(ip link show br-test-mgmt | grep -oP 'state \K\w+')

        echo -e "${GREEN}Management Bridge:${NC}"
        echo "  Interface:     br-test-mgmt"

        if [[ -n "$MGMT_IP" ]]; then
            local MGMT_IP_SOURCE=$(detect_ip_source "$MGMT_IP")
            local MGMT_IP_SRC_FMT=$(format_source "$MGMT_IP_SOURCE")
            echo -e "  Host IP:       ${MGMT_IP}/24 $MGMT_IP_SRC_FMT"
        fi

        if [[ -n "$ROUTER_IP" ]]; then
            local RTR_IP_SOURCE=$(detect_ip_source "$ROUTER_IP")
            local RTR_IP_SRC_FMT=$(format_source "$RTR_IP_SOURCE")
            echo -e "  Router IP:     ${ROUTER_IP} $RTR_IP_SRC_FMT"
        fi

        echo "  State:         ${MGMT_STATE}"
        echo ""
    fi

    if ip addr show br-test-isle1 &> /dev/null; then
        local ISLE_IP=$(ip addr show br-test-isle1 | grep -oP 'inet \K[\d.]+' | head -1)
        local ISLE_STATE=$(ip link show br-test-isle1 | grep -oP 'state \K\w+')

        echo -e "${GREEN}Isle 1 Bridge:${NC}"
        echo "  Interface:     br-test-isle1"

        if [[ -n "$ISLE_IP" ]]; then
            local ISLE_IP_SOURCE=$(detect_ip_source "$ISLE_IP")
            local ISLE_IP_SRC_FMT=$(format_source "$ISLE_IP_SOURCE")
            echo -e "  Host IP:       ${ISLE_IP}/24 $ISLE_IP_SRC_FMT"
        fi

        local EXPECTED_ISLE_IP="10.100.0.1"
        local EXPECTED_IP_SOURCE=$(detect_ip_source "$EXPECTED_ISLE_IP")
        local EXPECTED_IP_SRC_FMT=$(format_source "$EXPECTED_IP_SOURCE")
        echo -e "  Router IP:     ${EXPECTED_ISLE_IP} (expected) $EXPECTED_IP_SRC_FMT"

        echo "  State:         ${ISLE_STATE}"
        echo ""
    fi

    # Show connected devices from host ARP table
    echo -e "${BLUE}═══ Connected Devices ═══${NC}"
    echo ""

    local FOUND_DEVICES=false
    local DEVICE_COUNT=0

    # Check management network
    echo -e "${CYAN}Management Network (192.168.100.0/24):${NC}"
    if ip neigh show dev br-test-mgmt 2>/dev/null | grep -q "REACHABLE\|STALE\|DELAY"; then
        ip neigh show dev br-test-mgmt 2>/dev/null | grep "REACHABLE\|STALE\|DELAY" | while read line; do
            local DEV_IP=$(echo "$line" | awk '{print $1}')
            local DEV_MAC=$(echo "$line" | awk '{print $5}')
            local DEV_STATE=$(echo "$line" | awk '{print $NF}')

            # Detect sources
            local IP_SOURCE=$(detect_ip_source "$DEV_IP")
            local MAC_SOURCE=$(detect_mac_source "$DEV_MAC")
            local IP_SRC_FMT=$(format_source "$IP_SOURCE")
            local MAC_SRC_FMT=$(format_source "$MAC_SOURCE")

            echo -e "  ${DEV_IP} $IP_SRC_FMT - ${DEV_MAC} $MAC_SRC_FMT [${DEV_STATE}]"
            DEVICE_COUNT=$((DEVICE_COUNT + 1))
            FOUND_DEVICES=true
        done
    fi

    if [[ "$FOUND_DEVICES" == false ]]; then
        echo -e "  ${YELLOW}No devices detected${NC}"
    fi
    echo ""

    # Check isle network
    FOUND_DEVICES=false
    echo -e "${CYAN}Isle Network (10.100.0.0/24):${NC}"
    if ip neigh show dev br-test-isle1 2>/dev/null | grep -q "REACHABLE\|STALE\|DELAY"; then
        ip neigh show dev br-test-isle1 2>/dev/null | grep "REACHABLE\|STALE\|DELAY" | while read line; do
            local DEV_IP=$(echo "$line" | awk '{print $1}')
            local DEV_MAC=$(echo "$line" | awk '{print $5}')
            local DEV_STATE=$(echo "$line" | awk '{print $NF}')

            # Detect sources
            local IP_SOURCE=$(detect_ip_source "$DEV_IP")
            local MAC_SOURCE=$(detect_mac_source "$DEV_MAC")
            local IP_SRC_FMT=$(format_source "$IP_SOURCE")
            local MAC_SRC_FMT=$(format_source "$MAC_SOURCE")

            echo -e "  ${DEV_IP} $IP_SRC_FMT - ${DEV_MAC} $MAC_SRC_FMT [${DEV_STATE}]"
            FOUND_DEVICES=true
        done
    fi

    if [[ "$FOUND_DEVICES" == false ]]; then
        echo -e "  ${YELLOW}No devices detected${NC}"
    fi
    echo ""

    # Show ethernet ports connected to VM
    echo -e "${BLUE}═══ Router Interfaces ═══${NC}"
    echo ""

    if sudo virsh domiflist "$ROUTER_VM" &> /dev/null; then
        echo -e "${CYAN}Virtual Network Interfaces:${NC}"
        sudo virsh domiflist "$ROUTER_VM" 2>/dev/null | tail -n +3 | while read line; do
            if [[ -n "$line" ]]; then
                local IFACE=$(echo "$line" | awk '{print $1}')
                local TYPE=$(echo "$line" | awk '{print $2}')
                local SOURCE=$(echo "$line" | awk '{print $3}')
                local MODEL=$(echo "$line" | awk '{print $4}')
                local MAC=$(echo "$line" | awk '{print $5}')

                # Detect MAC source
                local MAC_SOURCE=$(detect_mac_source "$MAC")
                local MAC_SRC_FMT=$(format_source "$MAC_SOURCE")

                echo "  Interface:     $IFACE"
                echo "  Type:          $TYPE"
                echo "  Source:        $SOURCE"
                echo -e "  MAC:           $MAC $MAC_SRC_FMT"
                echo ""
            fi
        done
    fi

    # Show segregation status
    echo -e "${BLUE}═══ Isle Segregation Status ═══${NC}"
    echo ""

    # Check if bridges exist and are properly configured
    local SEGREGATION_OK=true

    if ip addr show br-test-mgmt &> /dev/null && ip addr show br-test-isle1 &> /dev/null; then
        echo -e "${GREEN}✓${NC} Network bridges configured"

        # Check if IP forwarding is enabled (should be for routing)
        local IP_FORWARD=$(sysctl -n net.ipv4.ip_forward 2>/dev/null)
        if [[ "$IP_FORWARD" == "1" ]]; then
            echo -e "${GREEN}✓${NC} IP forwarding enabled"
        else
            echo -e "${YELLOW}⚠${NC} IP forwarding disabled"
            SEGREGATION_OK=false
        fi

        # Check if the bridges are on different subnets
        local MGMT_SUBNET=$(ip addr show br-test-mgmt | grep -oP 'inet \K[\d.]+' | cut -d. -f1-3)
        local ISLE_SUBNET=$(ip addr show br-test-isle1 | grep -oP 'inet \K[\d.]+' | cut -d. -f1-3)

        if [[ "$MGMT_SUBNET" != "$ISLE_SUBNET" ]]; then
            echo -e "${GREEN}✓${NC} Networks on separate subnets"
            echo "    Management: ${MGMT_SUBNET}.0/24"
            echo "    Isle:       ${ISLE_SUBNET}.0/24"
        else
            echo -e "${RED}✗${NC} Networks on same subnet (not segregated!)"
            SEGREGATION_OK=false
        fi

    else
        echo -e "${RED}✗${NC} Network bridges not properly configured"
        SEGREGATION_OK=false
    fi

    echo ""

    if [[ "$SEGREGATION_OK" == true ]]; then
        echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
        echo -e "${GREEN}  Isle Network is PROPERLY SEGREGATED ✓${NC}"
        echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    else
        echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
        echo -e "${YELLOW}  Isle Network segregation needs attention${NC}"
        echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
    fi

    echo ""
    echo -e "${BLUE}Available Commands:${NC}"
    echo -e "  ${CYAN}isle router security${NC}              - Verify network isolation security"
    echo -e "  ${CYAN}ping ${ROUTER_IP}${NC}                  - Test connectivity"
    echo ""
}

# Help - Show detailed help
cmd_help() {
    echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║              Isle Router Management                           ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "Manage OpenWRT virtual router for Isle-Mesh network isolation."
    echo ""
    echo -e "${GREEN}USAGE:${NC}"
    echo -e "  isle router <subcommand> [options]"
    echo ""
    echo -e "${GREEN}ROUTER MANAGEMENT:${NC}"
    echo -e "  ${CYAN}list${NC}                    List all available routers"
    echo -e "                           - Shows router name, state, and bridges"
    echo -e "                           - Color-coded status (green=running)"
    echo -e "                           Aliases: ls"
    echo ""
    echo -e "  ${CYAN}up <name>${NC}               Start a specific router"
    echo -e "                           - Brings up the named router VM"
    echo -e "                           - Only one router should run at a time"
    echo -e "                           Requires: sudo"
    echo -e "                           Aliases: start"
    echo ""
    echo -e "  ${CYAN}down <name>${NC}             Stop a specific router"
    echo -e "                           - Gracefully shuts down the router"
    echo -e "                           - Keeps VM definition for later use"
    echo -e "                           Requires: sudo"
    echo -e "                           Aliases: stop"
    echo ""
    echo -e "  ${CYAN}delete <name>${NC}           Delete a router completely"
    echo -e "                           - Stops VM if running"
    echo -e "                           - Removes VM definition"
    echo -e "                           - Cleans up network bridges"
    echo -e "                           - Deletes router config files"
    echo -e "                           Requires: sudo"
    echo -e "                           Options: -f (force, no prompt)"
    echo -e "                           Aliases: rm, remove"
    echo ""
    echo -e "  ${CYAN}destroy${NC}                 Completely remove router and bridges (DEFAULT: full cleanup)"
    echo -e "                           - Enhanced cleanup with safety warnings"
    echo -e "                           - Checks for running containers"
    echo -e "                           - Requires typing 'DELETE' to confirm"
    echo -e "                           - Removes all isle-br-* and br-mgmt bridges by default"
    echo -e "                           - Options: --vm-only (keep bridges), --force"
    echo -e "                           Requires: sudo"
    echo -e "                           Run: sudo isle router destroy --help"
    echo ""
    echo -e "${GREEN}INITIALIZATION COMMANDS:${NC}"
    echo -e "  ${CYAN}init${NC}                    Initialize secure OpenWRT router (recommended)"
    echo -e "                           - Creates router VM"
    echo -e "                           - Configures vLAN networking"
    echo -e "                           - Sets up DHCP server"
    echo -e "                           - Deploys discovery beacon"
    echo -e "                           Requires: sudo"
    echo -e "                           Run: sudo isle router init --help"
    echo ""
    echo -e "  ${CYAN}configure${NC}               Configure OpenWRT router"
    echo -e "                           - Sets up network interfaces"
    echo -e "                           - Configures firewall rules"
    echo -e "                           - Enables WiFi access points"
    echo ""
    echo -e "  ${CYAN}detect${NC}                  Detect available USB WiFi and Ethernet"
    echo -e "                           - Shows USB WiFi adapters"
    echo -e "                           - Shows Ethernet interfaces"
    echo -e "                           - Generates sample configuration"
    echo ""
    echo -e "${GREEN}UTILITY COMMANDS:${NC}"
    echo -e "  ${CYAN}status${NC}                  Show comprehensive router status"
    echo -e "                           - VM status and resource usage"
    echo -e "                           - Network interfaces (router and host)"
    echo -e "                           - Connected devices with IPs/MACs"
    echo -e "                           - Source attribution (Docker/Libvirt/OpenWRT/ISP)"
    echo -e "                           - Ethernet port/cable status"
    echo -e "                           - Isle segregation verification"
    echo -e "                           - Firewall and DHCP status"
    echo -e "                           - Queries router via SSH if available"
    echo ""
    echo -e "  ${CYAN}security${NC}                Verify network isolation security"
    echo -e "                           - Checks ISP network isolation"
    echo -e "                           - Verifies no real MAC addresses visible"
    echo -e "                           - Confirms no routes to real network"
    echo -e "                           - Validates proper subnet segregation"
    echo -e "                           - Independent security verification"
    echo -e "                           Options: -v (verbose), -q (quiet)"
    echo ""
    echo -e "  ${CYAN}discover${NC}                Discover mDNS .local domains from router"
    echo -e "                           - SSHes into router and runs avahi-browse"
    echo -e "                           - Lists all discoverable .local domains"
    echo -e "                           - Shows services and IP addresses"
    echo -e "                           - Can show copy/paste command with --show-command"
    echo -e "                           Options: --show-command, --raw"
    echo -e "                           Aliases: discover-mdns"
    echo ""
    echo -e "  ${CYAN}domains${NC}                 Manage .vlan domain mappings"
    echo -e "                           - Discovers .local domains and creates .vlan mappings"
    echo -e "                           - Interactive selection of domains to add"
    echo -e "                           - Auto-add all discovered domains with --auto"
    echo -e "                           - List configured domains with --list"
    echo -e "                           - Remove domain with --remove <domain>"
    echo -e "                           Options: --auto, --list, --remove <domain>"
    echo -e "                           Aliases: manage-domains"
    echo ""
    echo -e "  ${CYAN}help${NC}                    Show this help message"
    echo ""
    echo -e "${GREEN}EXAMPLES:${NC}"
    echo ""
    echo -e "  ${YELLOW}# List all routers${NC}"
    echo -e "  isle router list"
    echo ""
    echo -e "  ${YELLOW}# Start a specific router${NC}"
    echo -e "  sudo isle router up <router-name>"
    echo ""
    echo -e "  ${YELLOW}# Stop a running router${NC}"
    echo -e "  sudo isle router down <router-name>"
    echo ""
    echo -e "  ${YELLOW}# Delete a router completely${NC}"
    echo -e "  sudo isle router delete <router-name>"
    echo ""
    echo -e "  ${YELLOW}# Check router status (shows all routers and their IPs)${NC}"
    echo -e "  isle router status"
    echo ""
    echo -e "  ${YELLOW}# Initialize secure router${NC}"
    echo -e "  sudo isle router init"
    echo ""
    echo -e "  ${YELLOW}# Initialize with custom settings${NC}"
    echo -e "  sudo isle router init --isle-name my-isle --vlan-id 20"
    echo ""
    echo -e "  ${YELLOW}# Detect hardware for production setup${NC}"
    echo -e "  isle router detect"
    echo ""
    echo -e "  ${YELLOW}# Verify network isolation security${NC}"
    echo -e "  isle router security"
    echo ""
    echo -e "  ${YELLOW}# Verify network security (verbose)${NC}"
    echo -e "  isle router security -v"
    echo ""
    echo -e "  ${YELLOW}# Quick security check (quiet mode)${NC}"
    echo -e "  isle router security -q"
    echo ""
    echo -e "  ${YELLOW}# Discover mDNS .local domains from router${NC}"
    echo -e "  isle router discover"
    echo ""
    echo -e "  ${YELLOW}# Show SSH command to manually discover domains${NC}"
    echo -e "  isle router discover --show-command"
    echo ""
    echo -e "  ${YELLOW}# Manage .vlan domain mappings (interactive)${NC}"
    echo -e "  isle router domains"
    echo ""
    echo -e "  ${YELLOW}# Auto-add all discovered .local domains as .vlan${NC}"
    echo -e "  isle router domains --auto"
    echo ""
    echo -e "  ${YELLOW}# List currently configured .vlan domains${NC}"
    echo -e "  isle router domains --list"
    echo ""
    echo -e "  ${YELLOW}# Remove a .vlan domain${NC}"
    echo -e "  isle router domains --remove sample.vlan"
    echo ""
    echo -e "${GREEN}TROUBLESHOOTING:${NC}"
    echo ""
    echo -e "  ${YELLOW}If network interfaces don't respond:${NC}"
    echo -e "  ${CYAN}sudo isle router reconfigure${NC}"
    echo -e "     └─> Re-run auto-configuration on existing VM"
    echo ""
    echo -e "${GREEN}GETTING STARTED WORKFLOW:${NC}"
    echo ""
    echo -e "  1. ${CYAN}sudo isle router init${NC}"
    echo -e "     └─> Create and configure router VM"
    echo ""
    echo -e "  2. ${CYAN}isle router status${NC}"
    echo -e "     └─> Verify router is running and configured"
    echo ""
    echo -e "  3. ${CYAN}isle router discover${NC}"
    echo -e "     └─> Discover .local domains from router"
    echo ""
    echo -e "  4. ${CYAN}isle router domains --auto${NC}"
    echo -e "     └─> Auto-add discovered domains as .vlan mappings"
    echo ""
    echo -e "${GREEN}DOCUMENTATION:${NC}"
    echo -e "  Main docs:        $ROUTER_DIR/README.md"
    echo -e "  Test docs:        $ROUTER_DIR/tests/README.md"
    echo -e "  Architecture:     $ROUTER_DIR/PHYSICAL-ARCHITECTURE.md"
    echo -e "  Quick start:      $ROUTER_DIR/QUICKSTART.md"
    echo ""
    echo -e "${GREEN}MORE INFO:${NC}"
    echo -e "  Run ${CYAN}isle help${NC} for general CLI commands"
    echo -e "  Visit project documentation for detailed guides"
    echo ""
}

# Main command router
case "$SUBCOMMAND" in
    init)
        cmd_init "$@"
        ;;

    add-connection)
        cmd_add_connection "$@"
        ;;

    list|ls)
        cmd_list "$@"
        ;;

    up|start)
        cmd_up "$@"
        ;;

    down|stop)
        cmd_down "$@"
        ;;

    delete|rm|remove)
        cmd_delete "$@"
        ;;

    destroy)
        cmd_destroy "$@"
        ;;

    test)
        TEST_ACTION="$1"
        shift || true

        case "$TEST_ACTION" in
            cleanup)
                cmd_test_cleanup "$@"
                ;;
            reconfigure)
                cmd_test_reconfigure "$@"
                ;;
            *)
                log_error "Unknown test action: $TEST_ACTION"
                echo ""
                echo "Available test actions:"
                echo "  reconfigure - Reconfigure existing VM network"
                echo "  cleanup     - Clean up test environment"
                echo ""
                echo "Run 'isle router help' for more information"
                exit 1
                ;;
        esac
        ;;

    provision)
        cmd_provision "$@"
        ;;

    configure)
        cmd_configure "$@"
        ;;

    detect)
        cmd_detect "$@"
        ;;

    status)
        cmd_status "$@"
        ;;

    security)
        cmd_security "$@"
        ;;

    discover-mdns|discover)
        cmd_discover_mdns "$@"
        ;;

    domains|manage-domains)
        cmd_domains "$@"
        ;;

    help|--help|-h)
        cmd_help
        ;;

    "")
        log_error "No subcommand provided"
        echo ""
        echo "Usage: isle router <subcommand> [options]"
        echo ""
        echo "Common commands:"
        echo "  list         - List all routers"
        echo "  up <name>    - Start a router (requires sudo)"
        echo "  down <name>  - Stop a router (requires sudo)"
        echo "  status       - Show router status"
        echo "  help         - Show detailed help"
        echo ""
        echo "Run 'isle router help' for full documentation"
        exit 1
        ;;

    *)
        log_error "Unknown subcommand: $SUBCOMMAND"
        echo ""
        echo "Run 'isle router help' for available commands"
        exit 1
        ;;
esac
