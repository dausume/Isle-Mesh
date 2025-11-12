#!/bin/bash

#############################################################################
# Manage VLAN Domains - Interactive domain management for Isle Mesh
#
# This script discovers mDNS .local domains and helps you add them to the
# router's DNS configuration as .vlan domains.
#
# Usage: ./manage-vlan-domains.sh [router-ip] [options]
#
# Default router IP: 192.168.1.1
# Options:
#   --auto                Automatically add all discovered domains
#   --list                List currently configured .vlan domains
#   --remove <domain>     Remove a .vlan domain from router
#   --help                Show this help message
#############################################################################

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
NC='\033[0m'

# Configuration
ROUTER_IP=""
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5"
SSH_USER="root"
AUTO_MODE=false
LIST_MODE=false
REMOVE_DOMAIN=""
DNSMASQ_CONF="/etc/dnsmasq.d/isle-vlan-domains.conf"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --auto)
            AUTO_MODE=true
            shift
            ;;
        --list)
            LIST_MODE=true
            shift
            ;;
        --remove)
            REMOVE_DOMAIN="$2"
            shift 2
            ;;
        --help|-h)
            show_help
            exit 0
            ;;
        -*)
            echo -e "${RED}Unknown option: $1${NC}" >&2
            exit 1
            ;;
        *)
            if [[ -z "$ROUTER_IP" ]]; then
                ROUTER_IP="$1"
            fi
            shift
            ;;
    esac
done

# Default router IP
ROUTER_IP="${ROUTER_IP:-192.168.1.1}"

# Log functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1" >&2
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1" >&2
}

log_error() {
    echo -e "${RED}[✗]${NC} $1" >&2
}

log_warning() {
    echo -e "${YELLOW}[⚠]${NC} $1" >&2
}

print_header() {
    echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║${NC}  $1"
    echo -e "${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
}

print_section() {
    echo ""
    echo -e "${CYAN}▸ $1${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

show_help() {
    cat <<EOF
${BOLD}Isle Mesh - VLAN Domain Management${NC}

This script helps you manage .vlan domain mappings on your Isle Mesh router.
It discovers .local domains via mDNS and creates corresponding .vlan DNS entries.

${BOLD}Usage:${NC}
  $0 [router-ip] [options]

${BOLD}Options:${NC}
  --auto                Automatically add all discovered domains
  --list                List currently configured .vlan domains
  --remove <domain>     Remove a .vlan domain from router
  --help, -h            Show this help message

${BOLD}Examples:${NC}
  # Interactive mode - discover and select domains
  $0 192.168.1.1

  # Auto mode - add all discovered domains
  $0 192.168.1.1 --auto

  # List current .vlan domains
  $0 192.168.1.1 --list

  # Remove a specific domain
  $0 192.168.1.1 --remove sample.vlan

${BOLD}How It Works:${NC}
  1. Discovers .local domains via mDNS (avahi-browse)
  2. Resolves their IP addresses
  3. Creates .vlan domain mappings in dnsmasq
  4. Reloads dnsmasq to apply changes

${BOLD}Requirements:${NC}
  - SSH access to router
  - avahi-utils installed on router (avahi-browse)
  - dnsmasq running on router

EOF
}

# Execute command on router
router_exec() {
    local CMD="$1"
    ssh $SSH_OPTS "${SSH_USER}@${ROUTER_IP}" "$CMD" 2>/dev/null
}

# Check connectivity
check_connectivity() {
    if ! ping -c 1 -W 2 "$ROUTER_IP" > /dev/null 2>&1; then
        log_error "Router not reachable at $ROUTER_IP"
        return 1
    fi
    return 0
}

# Check if SSH is available
check_ssh() {
    if ! timeout 3 bash -c "echo > /dev/tcp/$ROUTER_IP/22" 2>/dev/null; then
        log_error "SSH not available on router"
        return 1
    fi
    return 0
}

# Discover mDNS domains and their IPs
discover_domains() {
    log_info "Discovering mDNS .local domains from router..."
    echo ""

    # Check if avahi is installed
    local AVAHI_CHECK=$(router_exec "which avahi-browse" || echo "")
    if [[ -z "$AVAHI_CHECK" ]]; then
        log_error "avahi-browse not found on router"
        echo ""
        echo -e "${YELLOW}Install avahi tools:${NC}"
        echo -e "  ${CYAN}ssh root@${ROUTER_IP}${NC}"
        echo -e "  ${CYAN}opkg update && opkg install avahi-daemon avahi-utils${NC}"
        echo ""
        return 1
    fi

    # Run avahi-browse and parse output
    local AVAHI_OUTPUT=$(router_exec "timeout 10 avahi-browse -a -t -r 2>/dev/null" || echo "")

    if [[ -z "$AVAHI_OUTPUT" ]]; then
        log_warning "No mDNS services discovered"
        return 1
    fi

    # Parse output and extract unique hostnames with IPs
    declare -A DOMAIN_IPS
    local current_hostname=""
    local current_ip=""

    while IFS= read -r line; do
        # Look for hostname lines
        if [[ "$line" =~ hostname\ =\ \[(.*\.local)\] ]]; then
            current_hostname="${BASH_REMATCH[1]}"
        fi

        # Look for address lines
        if [[ "$line" =~ address\ =\ \[([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)\] ]]; then
            current_ip="${BASH_REMATCH[1]}"

            # Store the mapping if we have both
            if [[ -n "$current_hostname" ]] && [[ -n "$current_ip" ]]; then
                DOMAIN_IPS["$current_hostname"]="$current_ip"
                current_hostname=""
                current_ip=""
            fi
        fi
    done <<< "$AVAHI_OUTPUT"

    # Return the discovered domains as JSON
    if [[ ${#DOMAIN_IPS[@]} -eq 0 ]]; then
        log_warning "No .local domains with IP addresses found"
        return 1
    fi

    # Export as JSON for programmatic use
    echo "{"
    local first=true
    for domain in "${!DOMAIN_IPS[@]}"; do
        if [ "$first" = false ]; then
            echo ","
        fi
        first=false
        echo -n "  \"$domain\": \"${DOMAIN_IPS[$domain]}\""
    done
    echo ""
    echo "}"

    return 0
}

# List currently configured .vlan domains
list_vlan_domains() {
    print_header "Currently Configured .vlan Domains"
    echo ""

    local domains=$(router_exec "cat $DNSMASQ_CONF 2>/dev/null || echo ''" || echo "")

    if [[ -z "$domains" ]]; then
        log_warning "No .vlan domains configured"
        echo ""
        return 0
    fi

    # Parse dnsmasq conf format: address=/domain/ip
    while IFS= read -r line; do
        if [[ "$line" =~ address=/([^/]+)/([^/]+) ]]; then
            local domain="${BASH_REMATCH[1]}"
            local ip="${BASH_REMATCH[2]}"
            echo -e "  ${GREEN}✓${NC} ${CYAN}$domain${NC} → $ip"
        fi
    done <<< "$domains"

    echo ""
}

# Add domain to router DNS
add_domain_to_router() {
    local local_domain="$1"
    local ip_address="$2"

    # Convert .local to .vlan
    local vlan_domain="${local_domain/.local/.vlan}"

    log_info "Adding $vlan_domain → $ip_address"

    # Create or update dnsmasq configuration
    router_exec "mkdir -p $(dirname $DNSMASQ_CONF)" || true
    router_exec "grep -v \"address=/$vlan_domain/\" $DNSMASQ_CONF > ${DNSMASQ_CONF}.tmp 2>/dev/null || touch ${DNSMASQ_CONF}.tmp"
    router_exec "echo 'address=/$vlan_domain/$ip_address' >> ${DNSMASQ_CONF}.tmp"
    router_exec "mv ${DNSMASQ_CONF}.tmp $DNSMASQ_CONF"

    log_success "Added $vlan_domain"
}

# Remove domain from router DNS
remove_domain_from_router() {
    local vlan_domain="$1"

    log_info "Removing $vlan_domain..."

    router_exec "sed -i \"/address=\\/$vlan_domain\\//d\" $DNSMASQ_CONF 2>/dev/null || true"

    log_success "Removed $vlan_domain"
}

# Reload dnsmasq
reload_dnsmasq() {
    log_info "Reloading dnsmasq..."
    router_exec "/etc/init.d/dnsmasq reload" || {
        log_error "Failed to reload dnsmasq"
        return 1
    }
    log_success "dnsmasq reloaded"
}

# Interactive mode
interactive_mode() {
    print_header "Isle Mesh - VLAN Domain Management"
    echo ""

    # Discover domains
    local discovered_json=$(discover_domains)

    if [[ -z "$discovered_json" ]] || [[ "$discovered_json" == "{}" ]]; then
        log_error "No domains discovered"
        exit 1
    fi

    # Parse JSON and display options
    print_section "Discovered mDNS Domains"

    declare -a domains
    declare -a ips
    local index=1

    while IFS= read -r line; do
        if [[ "$line" =~ \"([^\"]+)\":\ \"([^\"]+)\" ]]; then
            local domain="${BASH_REMATCH[1]}"
            local ip="${BASH_REMATCH[2]}"
            local vlan_domain="${domain/.local/.vlan}"

            domains+=("$domain")
            ips+=("$ip")

            echo -e "  ${YELLOW}[$index]${NC} ${CYAN}$domain${NC} → ${CYAN}$vlan_domain${NC}"
            echo -e "      IP: $ip"
            echo ""

            ((index++))
        fi
    done <<< "$discovered_json"

    # Show current .vlan domains
    print_section "Currently Configured .vlan Domains"
    list_vlan_domains

    # Prompt user
    echo ""
    echo -e "${BOLD}Select domains to add to router:${NC}"
    echo -e "  Enter numbers (e.g., 1 2 3) or 'all' for all domains"
    echo -e "  Press Enter to skip"
    echo ""
    read -p "Selection: " selection

    if [[ -z "$selection" ]]; then
        log_info "No domains selected"
        exit 0
    fi

    # Process selection
    local domains_to_add=()

    if [[ "$selection" == "all" ]]; then
        domains_to_add=("${!domains[@]}")
    else
        for num in $selection; do
            if [[ "$num" =~ ^[0-9]+$ ]] && [ "$num" -ge 1 ] && [ "$num" -le "${#domains[@]}" ]; then
                domains_to_add+=($((num - 1)))
            fi
        done
    fi

    if [[ ${#domains_to_add[@]} -eq 0 ]]; then
        log_warning "No valid domains selected"
        exit 0
    fi

    # Add selected domains
    echo ""
    print_section "Adding Domains to Router"

    for idx in "${domains_to_add[@]}"; do
        add_domain_to_router "${domains[$idx]}" "${ips[$idx]}"
    done

    # Reload dnsmasq
    echo ""
    reload_dnsmasq

    # Show final status
    echo ""
    print_section "Complete!"
    list_vlan_domains
}

# Auto mode
auto_mode() {
    print_header "Isle Mesh - Auto-Add VLAN Domains"
    echo ""

    # Discover domains
    local discovered_json=$(discover_domains)

    if [[ -z "$discovered_json" ]] || [[ "$discovered_json" == "{}" ]]; then
        log_error "No domains discovered"
        exit 1
    fi

    # Add all discovered domains
    print_section "Adding All Discovered Domains"

    while IFS= read -r line; do
        if [[ "$line" =~ \"([^\"]+)\":\ \"([^\"]+)\" ]]; then
            local domain="${BASH_REMATCH[1]}"
            local ip="${BASH_REMATCH[2]}"
            add_domain_to_router "$domain" "$ip"
        fi
    done <<< "$discovered_json"

    # Reload dnsmasq
    echo ""
    reload_dnsmasq

    echo ""
    log_success "All domains added successfully"
}

# Main function
main() {
    # Check connectivity
    if ! check_connectivity; then
        exit 1
    fi

    # Check SSH
    if ! check_ssh; then
        exit 1
    fi

    log_success "Connected to router at $ROUTER_IP"
    echo ""

    # Handle different modes
    if [[ "$LIST_MODE" == true ]]; then
        list_vlan_domains
    elif [[ -n "$REMOVE_DOMAIN" ]]; then
        remove_domain_from_router "$REMOVE_DOMAIN"
        reload_dnsmasq
    elif [[ "$AUTO_MODE" == true ]]; then
        auto_mode
    else
        interactive_mode
    fi
}

# Run main
main
