#!/bin/bash

#############################################################################
# Discover mDNS .local domains from OpenWRT Router
#
# This script SSHes into the OpenWRT router and uses avahi-browse to
# discover all mDNS .local domains visible from within the router.
#
# Usage: ./discover-mdns-domains.sh [router-ip] [options]
#
# Default router IP: 192.168.100.1
# Options:
#   --show-command    Show the SSH command to copy/paste
#   --raw             Show raw avahi-browse output
#############################################################################

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Configuration
ROUTER_IP=""
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5"
SSH_USER="root"
SHOW_COMMAND=false
RAW_OUTPUT=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --show-command)
            SHOW_COMMAND=true
            shift
            ;;
        --raw)
            RAW_OUTPUT=true
            shift
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
ROUTER_IP="${ROUTER_IP:-192.168.100.1}"

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

# Check if router is reachable
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
        log_warning "SSH not available on router"
        log_info "SSH may not be enabled in OpenWRT or dropbear is not running"
        return 1
    fi
    return 0
}

# Execute command on router
router_exec() {
    local CMD="$1"
    ssh $SSH_OPTS "${SSH_USER}@${ROUTER_IP}" "$CMD" 2>/dev/null
}

# Show the SSH command for manual execution
show_ssh_command() {
    echo -e "${CYAN}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║           Copy/Paste Command (Manual Execution)               ║${NC}"
    echo -e "${CYAN}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${YELLOW}To manually discover mDNS domains, SSH into the router and run:${NC}"
    echo ""
    echo -e "  ${GREEN}ssh root@${ROUTER_IP}${NC}"
    echo ""
    echo -e "Then run one of these commands:"
    echo ""
    echo -e "  ${CYAN}# Browse all services${NC}"
    echo -e "  ${GREEN}avahi-browse -a -t${NC}"
    echo ""
    echo -e "  ${CYAN}# Browse all services with resolved info${NC}"
    echo -e "  ${GREEN}avahi-browse -a -t -r${NC}"
    echo ""
    echo -e "  ${CYAN}# Browse HTTP services only${NC}"
    echo -e "  ${GREEN}avahi-browse -t _http._tcp${NC}"
    echo ""
    echo -e "  ${CYAN}# Browse HTTPS services only${NC}"
    echo -e "  ${GREEN}avahi-browse -t _https._tcp${NC}"
    echo ""
}

# Discover mDNS domains
discover_mdns() {
    log_info "Discovering mDNS .local domains from router at $ROUTER_IP..."
    echo ""

    # Check if avahi is installed on the router
    local AVAHI_CHECK=$(router_exec "which avahi-browse" || echo "")
    if [[ -z "$AVAHI_CHECK" ]]; then
        log_error "avahi-browse not found on router"
        echo ""
        echo -e "${YELLOW}Avahi tools are not installed on the router.${NC}"
        echo ""
        echo "To install avahi tools, SSH into the router and run:"
        echo -e "  ${CYAN}opkg update${NC}"
        echo -e "  ${CYAN}opkg install avahi-daemon avahi-utils${NC}"
        echo ""
        return 1
    fi

    log_success "Avahi tools found on router"
    echo ""

    if [[ "$RAW_OUTPUT" == true ]]; then
        # Show raw output
        log_info "Running: avahi-browse -a -t -r"
        echo ""
        router_exec "avahi-browse -a -t -r"
        return 0
    fi

    # Run avahi-browse with timeout and parse output
    log_info "Scanning for mDNS services (this may take 5-10 seconds)..."
    echo ""

    local AVAHI_OUTPUT=$(router_exec "timeout 10 avahi-browse -a -t -r 2>/dev/null || avahi-browse -a -t -r 2>/dev/null" || echo "")

    if [[ -z "$AVAHI_OUTPUT" ]]; then
        log_warning "No mDNS services discovered"
        echo ""
        echo -e "${YELLOW}This could mean:${NC}"
        echo "  - No devices are advertising mDNS services"
        echo "  - Avahi daemon is not running on the router"
        echo "  - mDNS reflection is not configured properly"
        echo ""
        echo "To check avahi daemon status on the router:"
        echo -e "  ${CYAN}ssh root@${ROUTER_IP} '/etc/init.d/avahi-daemon status'${NC}"
        echo ""
        return 1
    fi

    # Parse and format the output
    echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║           Discovered mDNS Services (.local domains)          ║${NC}"
    echo -e "${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    # Extract unique .local hostnames
    local HOSTS=$(echo "$AVAHI_OUTPUT" | grep -oP '[\w\-]+\.local' | sort -u)

    if [[ -z "$HOSTS" ]]; then
        log_warning "No .local domains found in avahi output"
        echo ""
        return 1
    fi

    # Group by hostname and show services
    echo "$HOSTS" | while read -r hostname; do
        echo -e "${GREEN}Host:${NC} ${CYAN}$hostname${NC}"

        # Find services for this host
        local SERVICES=$(echo "$AVAHI_OUTPUT" | grep -B2 "$hostname" | grep "^=" | awk '{print $4}' | sort -u)

        if [[ -n "$SERVICES" ]]; then
            echo -e "${YELLOW}Services:${NC}"
            echo "$SERVICES" | while read -r service; do
                [[ -z "$service" ]] && continue
                echo "  • $service"
            done
        fi

        # Try to find IP address
        local IP_ADDR=$(echo "$AVAHI_OUTPUT" | grep -A10 "$hostname" | grep "address" | grep -oP '\[[\d\.]+\]' | tr -d '[]' | head -1)
        if [[ -n "$IP_ADDR" ]]; then
            echo -e "${YELLOW}IP Address:${NC} $IP_ADDR"
        fi

        echo ""
    done

    # Summary
    local HOST_COUNT=$(echo "$HOSTS" | wc -l)
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  Found $HOST_COUNT .local domain(s)${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
}

# Main function
main() {
    # Show command if requested
    if [[ "$SHOW_COMMAND" == true ]]; then
        show_ssh_command
        exit 0
    fi

    # Check connectivity
    if ! check_connectivity; then
        exit 1
    fi

    # Check if SSH is available
    if ! check_ssh; then
        log_warning "Cannot discover mDNS domains without SSH access"
        log_info "Enable SSH on OpenWRT or ensure dropbear is running"
        echo ""
        show_ssh_command
        exit 1
    fi

    log_success "Connected to router"
    echo ""

    # Discover mDNS domains
    discover_mdns
}

# Run main
main
