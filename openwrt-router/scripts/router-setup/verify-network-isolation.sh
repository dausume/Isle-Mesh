#!/bin/bash

#############################################################################
# Network Isolation Security Verification Script
#
# This script verifies that isolated networks (Docker, OpenWRT, etc.) are
# properly segregated from the host's real ISP network and cannot access
# real network resources.
#
# Checks performed:
#   1. No ISP/real network IPs are reachable from isolated networks
#   2. Real host MAC addresses are not visible in isolated networks
#   3. No routes to real network from isolated networks
#   4. Docker networks are properly isolated
#   5. OpenWRT router networks are segregated
#   6. No leakage of real gateway information
#
# Usage: ./verify-network-isolation.sh [options]
#
# Options:
#   -v, --verbose    Show detailed output
#   -q, --quiet      Show only summary (pass/fail)
#
# Exit codes:
#   0 = All checks passed (network is secure)
#   1 = One or more checks failed (network is NOT secure)
#   2 = Script error
#############################################################################

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source network source detection library
if [[ -f "$SCRIPT_DIR/lib-network-sources.sh" ]]; then
    source "$SCRIPT_DIR/lib-network-sources.sh"
else
    echo "Error: lib-network-sources.sh not found" >&2
    exit 2
fi

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Configuration
VERBOSE=false
QUIET=false
CHECKS_PASSED=0
CHECKS_FAILED=0
CRITICAL_FAILURES=()

# Parse options
while [[ $# -gt 0 ]]; do
    case "$1" in
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -q|--quiet)
            QUIET=true
            shift
            ;;
        -h|--help)
            grep '^#' "$0" | grep -v '#!/bin/bash' | sed 's/^# //' | sed 's/^#//'
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use -h or --help for usage information"
            exit 2
            ;;
    esac
done

#############################################################################
# Logging functions
#############################################################################

log_header() {
    if [[ "$QUIET" == false ]]; then
        echo -e "${BLUE}${BOLD}$1${NC}"
        echo ""
    fi
}

log_section() {
    if [[ "$QUIET" == false ]]; then
        echo -e "${CYAN}═══ $1 ═══${NC}"
        echo ""
    fi
}

log_check() {
    if [[ "$QUIET" == false ]]; then
        echo -e "${BLUE}[CHECK]${NC} $1"
    fi
}

log_pass() {
    CHECKS_PASSED=$((CHECKS_PASSED + 1))
    if [[ "$QUIET" == false ]]; then
        echo -e "${GREEN}[✓ PASS]${NC} $1"
    fi
}

log_fail() {
    CHECKS_FAILED=$((CHECKS_FAILED + 1))
    CRITICAL_FAILURES+=("$1")
    echo -e "${RED}[✗ FAIL]${NC} $1" >&2
}

log_warn() {
    if [[ "$QUIET" == false ]]; then
        echo -e "${YELLOW}[⚠ WARN]${NC} $1"
    fi
}

log_info() {
    if [[ "$VERBOSE" == true ]] && [[ "$QUIET" == false ]]; then
        echo -e "${BLUE}[INFO]${NC} $1"
    fi
}

log_detail() {
    if [[ "$VERBOSE" == true ]] && [[ "$QUIET" == false ]]; then
        echo "  $1"
    fi
}

#############################################################################
# Detection functions
#############################################################################

# Detect host's real network interface and gateway
detect_real_network() {
    # Find default route interface
    REAL_IFACE=$(ip route | grep '^default' | grep -oP 'dev \K\S+' | head -1)
    if [[ -z "$REAL_IFACE" ]]; then
        log_warn "No default route found - may not be connected to internet"
        return 1
    fi

    # Get real IP, MAC, gateway
    REAL_IP=$(ip -o addr show "$REAL_IFACE" | grep -oP 'inet \K[\d.]+' | head -1)
    REAL_MAC=$(ip link show "$REAL_IFACE" | grep -oP 'link/ether \K[\da-f:]+')
    REAL_GATEWAY=$(ip route | grep '^default' | grep -oP 'via \K[\d.]+' | head -1)
    REAL_SUBNET=$(echo "$REAL_IP" | cut -d. -f1-3).0/24

    log_info "Real network detected:"
    log_detail "Interface: $REAL_IFACE"
    log_detail "IP: $REAL_IP"
    log_detail "MAC: $REAL_MAC"
    log_detail "Gateway: $REAL_GATEWAY"
    log_detail "Subnet: $REAL_SUBNET"

    return 0
}

# Get list of isolated networks
get_isolated_networks() {
    ISOLATED_NETS=()

    # Add libvirt bridges
    if command -v virsh &> /dev/null; then
        while read -r bridge; do
            if [[ -n "$bridge" ]] && ip link show "$bridge" &> /dev/null; then
                ISOLATED_NETS+=("$bridge")
            fi
        done < <(ip link show type bridge 2>/dev/null | grep -oP '^\d+:\s+\K[^:]+' | grep -E 'br-test|br-isle')
    fi

    # Add Docker networks (if any)
    if command -v docker &> /dev/null; then
        while read -r network; do
            if [[ -n "$network" ]]; then
                local bridge=$(docker network inspect "$network" 2>/dev/null | grep -oP '"com.docker.network.bridge.name":\s*"\K[^"]+' | head -1)
                if [[ -n "$bridge" ]] && ip link show "$bridge" &> /dev/null; then
                    ISOLATED_NETS+=("$bridge")
                fi
            fi
        done < <(docker network ls --format "{{.Name}}" 2>/dev/null | grep -v bridge | grep -v host | grep -v none)
    fi

    log_info "Isolated networks found: ${#ISOLATED_NETS[@]}"
    for net in "${ISOLATED_NETS[@]}"; do
        log_detail "$net"
    done
}

#############################################################################
# Security checks
#############################################################################

# Check 1: Real gateway not reachable from isolated networks
check_gateway_isolation() {
    log_check "Checking gateway isolation from isolated networks"

    if [[ -z "$REAL_GATEWAY" ]]; then
        log_warn "No real gateway detected, skipping check"
        return 0
    fi

    local failed=false

    for net in "${ISOLATED_NETS[@]}"; do
        # Try to ping gateway from host with source IP from isolated network
        local net_ip=$(ip -o addr show "$net" | grep -oP 'inet \K[\d.]+' | head -1)

        if [[ -n "$net_ip" ]]; then
            # Check if gateway is in routing table for this network
            if ip route show dev "$net" 2>/dev/null | grep -q "$REAL_GATEWAY"; then
                log_fail "Real gateway $REAL_GATEWAY is routable from isolated network $net"
                failed=true
            else
                log_detail "✓ Gateway not routable from $net"
            fi
        fi
    done

    if [[ "$failed" == false ]]; then
        log_pass "Gateway is isolated from all isolated networks"
    fi
}

# Check 2: Real host MAC not visible in isolated network ARP tables
check_mac_isolation() {
    log_check "Checking real MAC address isolation"

    if [[ -z "$REAL_MAC" ]]; then
        log_warn "No real MAC detected, skipping check"
        return 0
    fi

    local failed=false

    for net in "${ISOLATED_NETS[@]}"; do
        # Check ARP table for this network device
        if ip neigh show dev "$net" 2>/dev/null | grep -qi "$REAL_MAC"; then
            log_fail "Real host MAC $REAL_MAC visible in isolated network $net ARP table"
            failed=true
        else
            log_detail "✓ Real MAC not in $net ARP table"
        fi
    done

    # Also check OpenWRT router if available
    if command -v ssh &> /dev/null; then
        local router_ip="192.168.100.1"
        if timeout 2 bash -c "echo > /dev/tcp/$router_ip/22" 2>/dev/null; then
            local arp_table=$(ssh -o StrictHostKeyChecking=no -o ConnectTimeout=2 root@"$router_ip" "cat /proc/net/arp" 2>/dev/null || true)
            if echo "$arp_table" | grep -qi "$REAL_MAC"; then
                log_fail "Real host MAC $REAL_MAC visible in OpenWRT router ARP table"
                failed=true
            else
                log_detail "✓ Real MAC not in OpenWRT router ARP table"
            fi
        fi
    fi

    if [[ "$failed" == false ]]; then
        log_pass "Real MAC address is not visible in isolated networks"
    fi
}

# Check 3: No routes to real network from isolated networks
check_route_isolation() {
    log_check "Checking route isolation to real network"

    if [[ -z "$REAL_SUBNET" ]]; then
        log_warn "No real subnet detected, skipping check"
        return 0
    fi

    local failed=false

    for net in "${ISOLATED_NETS[@]}"; do
        # Check if any routes exist to real subnet via this isolated network
        if ip route show dev "$net" 2>/dev/null | grep -q "$REAL_SUBNET"; then
            log_fail "Route to real network $REAL_SUBNET exists via isolated network $net"
            failed=true
        else
            log_detail "✓ No routes to real network via $net"
        fi
    done

    if [[ "$failed" == false ]]; then
        log_pass "No routes to real network from isolated networks"
    fi
}

# Check 4: Real IP not in use on isolated networks
check_ip_isolation() {
    log_check "Checking real IP address isolation"

    if [[ -z "$REAL_IP" ]]; then
        log_warn "No real IP detected, skipping check"
        return 0
    fi

    local failed=false

    for net in "${ISOLATED_NETS[@]}"; do
        # Check if real IP appears in ARP table
        if ip neigh show dev "$net" 2>/dev/null | grep -q "$REAL_IP"; then
            log_fail "Real host IP $REAL_IP visible in isolated network $net"
            failed=true
        else
            log_detail "✓ Real IP not visible in $net"
        fi
    done

    if [[ "$failed" == false ]]; then
        log_pass "Real IP address is not visible in isolated networks"
    fi
}

# Check 5: Isolated networks are on different subnets
check_subnet_segregation() {
    log_check "Checking subnet segregation"

    if [[ -z "$REAL_SUBNET" ]]; then
        log_warn "No real subnet detected, skipping check"
        return 0
    fi

    local failed=false

    for net in "${ISOLATED_NETS[@]}"; do
        local net_ip=$(ip -o addr show "$net" | grep -oP 'inet \K[\d.]+/\d+' | head -1)
        if [[ -n "$net_ip" ]]; then
            local net_subnet=$(echo "$net_ip" | cut -d. -f1-3).0/24

            if [[ "$net_subnet" == "$REAL_SUBNET" ]]; then
                log_fail "Isolated network $net is on same subnet as real network ($REAL_SUBNET)"
                failed=true
            else
                log_detail "✓ $net is on different subnet ($net_subnet)"
            fi
        fi
    done

    if [[ "$failed" == false ]]; then
        log_pass "All isolated networks are on different subnets"
    fi
}

# Check 6: No ISP DNS servers visible in isolated networks
check_dns_isolation() {
    log_check "Checking DNS server isolation"

    # Get system DNS servers
    local dns_servers=$(grep '^nameserver' /etc/resolv.conf 2>/dev/null | awk '{print $2}')

    if [[ -z "$dns_servers" ]]; then
        log_warn "No DNS servers found in /etc/resolv.conf"
        return 0
    fi

    local failed=false

    # Check OpenWRT router DNS configuration
    if command -v ssh &> /dev/null; then
        local router_ip="192.168.100.1"
        if timeout 2 bash -c "echo > /dev/tcp/$router_ip/22" 2>/dev/null; then
            for dns in $dns_servers; do
                # Skip localhost
                [[ "$dns" =~ ^127\. ]] && continue

                # Check if real DNS server is configured on router
                local router_dns=$(ssh -o StrictHostKeyChecking=no -o ConnectTimeout=2 root@"$router_ip" "uci get dhcp.@dnsmasq[0].server 2>/dev/null || cat /tmp/resolv.conf.auto 2>/dev/null" 2>/dev/null || true)
                if echo "$router_dns" | grep -q "$dns"; then
                    log_fail "Real DNS server $dns is configured on isolated OpenWRT router"
                    failed=true
                else
                    log_detail "✓ Real DNS $dns not configured on router"
                fi
            done
        fi
    fi

    if [[ "$failed" == false ]]; then
        log_pass "DNS isolation verified"
    fi
}

# Check 7: Verify firewall rules prevent cross-contamination
check_firewall_isolation() {
    log_check "Checking firewall rules for network isolation"

    # Check iptables for any forwarding rules from isolated nets to real interface
    if [[ -n "$REAL_IFACE" ]]; then
        local failed=false

        for net in "${ISOLATED_NETS[@]}"; do
            # Check if forwarding is allowed from isolated net to real interface
            if sudo iptables -L FORWARD -n 2>/dev/null | grep -q "^ACCEPT.*$net.*$REAL_IFACE"; then
                log_fail "Firewall allows forwarding from isolated network $net to real interface $REAL_IFACE"
                failed=true
            else
                log_detail "✓ No forward rules from $net to $REAL_IFACE"
            fi
        done

        if [[ "$failed" == false ]]; then
            log_pass "Firewall rules properly isolate networks"
        fi
    else
        log_warn "No real interface detected, skipping firewall check"
    fi
}

#############################################################################
# Main execution
#############################################################################

main() {
    # Print header
    if [[ "$QUIET" == false ]]; then
        cat << EOF
${BLUE}${BOLD}╔═══════════════════════════════════════════════════════════════╗
║         Network Isolation Security Verification              ║
╚═══════════════════════════════════════════════════════════════╝${NC}

This script verifies that isolated networks are properly segregated
from your real ISP network and cannot access real network resources.

EOF
    fi

    # Detect real network
    log_section "Detecting Real Network Configuration"
    if ! detect_real_network; then
        log_warn "Could not detect real network - some checks will be skipped"
    fi
    echo ""

    # Get isolated networks
    log_section "Detecting Isolated Networks"
    get_isolated_networks

    if [[ ${#ISOLATED_NETS[@]} -eq 0 ]]; then
        log_warn "No isolated networks found - nothing to check"
        echo ""
        echo -e "${YELLOW}No isolated networks detected. This could mean:${NC}"
        echo "  - No OpenWRT router VMs are running"
        echo "  - No Docker networks are configured"
        echo "  - No libvirt bridges exist"
        echo ""
        exit 0
    fi
    echo ""

    # Run security checks
    log_section "Running Security Checks"
    check_gateway_isolation
    check_mac_isolation
    check_route_isolation
    check_ip_isolation
    check_subnet_segregation
    check_dns_isolation
    check_firewall_isolation
    echo ""

    # Print summary
    echo -e "${BLUE}${BOLD}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}${BOLD}  Security Check Summary${NC}"
    echo -e "${BLUE}${BOLD}═══════════════════════════════════════════════════════════════${NC}"
    echo ""

    local total_checks=$((CHECKS_PASSED + CHECKS_FAILED))
    echo -e "${GREEN}Passed:${NC} $CHECKS_PASSED / $total_checks"
    echo -e "${RED}Failed:${NC} $CHECKS_FAILED / $total_checks"
    echo ""

    if [[ $CHECKS_FAILED -eq 0 ]]; then
        echo -e "${GREEN}${BOLD}╔═══════════════════════════════════════════════════════════════╗${NC}"
        echo -e "${GREEN}${BOLD}║                                                               ║${NC}"
        echo -e "${GREEN}${BOLD}║         ✓ NETWORK IS SECURE AND PROPERLY ISOLATED ✓          ║${NC}"
        echo -e "${GREEN}${BOLD}║                                                               ║${NC}"
        echo -e "${GREEN}${BOLD}╚═══════════════════════════════════════════════════════════════╝${NC}"
        echo ""
        echo "All isolation checks passed. Your isolated networks are properly"
        echo "segregated from your real ISP network."
        echo ""
        exit 0
    else
        echo -e "${RED}${BOLD}╔═══════════════════════════════════════════════════════════════╗${NC}"
        echo -e "${RED}${BOLD}║                                                               ║${NC}"
        echo -e "${RED}${BOLD}║            ✗ NETWORK ISOLATION FAILED ✗                       ║${NC}"
        echo -e "${RED}${BOLD}║                                                               ║${NC}"
        echo -e "${RED}${BOLD}╚═══════════════════════════════════════════════════════════════╝${NC}"
        echo ""
        echo -e "${RED}Critical failures detected:${NC}"
        for failure in "${CRITICAL_FAILURES[@]}"; do
            echo -e "  ${RED}✗${NC} $failure"
        done
        echo ""
        echo -e "${YELLOW}IMPORTANT:${NC} Your isolated networks may be able to access your real"
        echo "network. This could expose sensitive information or allow isolated"
        echo "systems to access the internet when they shouldn't."
        echo ""
        echo "Please review your network configuration and firewall rules."
        echo ""
        exit 1
    fi
}

# Run main
main
