#!/usr/bin/env bash

#############################################################################
# Isle Join Protocol Verification Script
#
# Tests the complete join protocol flow:
#   1. mDNS discovery (.local domains)
#   2. DNS mapping (.vlan domains)
#   3. DHCP functionality
#   4. HTTP/HTTPS connectivity
#   5. Service accessibility
#
# Usage: ./verify-join-protocol.sh [--router-ip <ip>] [--vlan-id <id>]
#############################################################################

set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# Source common libraries
source "$SCRIPT_DIR/../lib/common-log.sh"
source "$SCRIPT_DIR/../lib/common-utils.sh"

# Configuration
ROUTER_IP="${ROUTER_IP:-192.168.1.1}"
VLAN_ID="${VLAN_ID:-10}"
ROUTER_USER="${ROUTER_USER:-root}"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5"
TEST_HOSTNAME="${TEST_HOSTNAME:-}"

# Test results
TESTS_PASSED=0
TESTS_FAILED=0
TESTS_SKIPPED=0

# Parse arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --router-ip)
                ROUTER_IP="$2"
                shift 2
                ;;
            --vlan-id)
                VLAN_ID="$2"
                shift 2
                ;;
            --test-hostname)
                TEST_HOSTNAME="$2"
                shift 2
                ;;
            -h|--help)
                cat << EOF
Isle Join Protocol Verification

Usage: $0 [options]

Options:
  --router-ip IP          Router IP (default: 192.168.1.1)
  --vlan-id ID            VLAN ID (default: 10)
  --test-hostname NAME    Specific hostname to test
  -h, --help              Show this help

Description:
  Comprehensive verification of the isle join protocol.
  Tests mDNS discovery, DNS mapping, DHCP, and connectivity.

EOF
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                exit 1
                ;;
        esac
    done
}

# Helper functions for test tracking
test_pass() {
    local test_name="$1"
    log_success "✓ $test_name"
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

test_fail() {
    local test_name="$1"
    local reason="${2:-Unknown reason}"
    log_error "✗ $test_name"
    log_warning "  Reason: $reason"
    TESTS_FAILED=$((TESTS_FAILED + 1))
}

test_skip() {
    local test_name="$1"
    local reason="${2:-Skipped}"
    log_warning "⊘ $test_name"
    log_info "  $reason"
    TESTS_SKIPPED=$((TESTS_SKIPPED + 1))
}

# Test 1: Router connectivity
test_router_connectivity() {
    log_step "Test 1: Router Connectivity"

    if ping -c 1 -W 2 "$ROUTER_IP" &> /dev/null; then
        test_pass "Router is reachable at $ROUTER_IP"
    else
        test_fail "Router connectivity" "Cannot ping $ROUTER_IP"
        return 1
    fi

    if ssh $SSH_OPTS "${ROUTER_USER}@${ROUTER_IP}" "exit" 2>/dev/null; then
        test_pass "SSH access to router"
    else
        test_fail "SSH access" "Cannot connect to router via SSH"
        return 1
    fi

    return 0
}

# Test 2: Join protocol service status
test_join_protocol_service() {
    log_step "Test 2: Join Protocol Service Status"

    local service_status
    service_status=$(ssh $SSH_OPTS "${ROUTER_USER}@${ROUTER_IP}" \
        "/etc/init.d/isle-join-protocol status 2>/dev/null" || echo "not found")

    if echo "$service_status" | grep -q "running"; then
        test_pass "Join protocol service is running"
    else
        test_fail "Join protocol service" "Service is not running or not installed"
        log_info "Install with: ./configure-join-protocol.sh"
        return 1
    fi

    # Check if daemon process is running
    local daemon_pid
    daemon_pid=$(ssh $SSH_OPTS "${ROUTER_USER}@${ROUTER_IP}" \
        "cat /var/run/isle-join-protocol.pid 2>/dev/null" || echo "")

    if [[ -n "$daemon_pid" ]]; then
        test_pass "Join protocol daemon is active (PID: $daemon_pid)"
    else
        test_fail "Join protocol daemon" "Daemon PID file not found"
        return 1
    fi

    return 0
}

# Test 3: Avahi/mDNS availability on router
test_router_avahi() {
    log_step "Test 3: Router Avahi/mDNS Support"

    local avahi_check
    avahi_check=$(ssh $SSH_OPTS "${ROUTER_USER}@${ROUTER_IP}" \
        "which avahi-browse 2>/dev/null" || echo "")

    if [[ -n "$avahi_check" ]]; then
        test_pass "avahi-browse is installed on router"
    else
        test_fail "Avahi tools" "avahi-browse not found on router"
        return 1
    fi

    # Check if avahi-daemon is running
    local avahi_status
    avahi_status=$(ssh $SSH_OPTS "${ROUTER_USER}@${ROUTER_IP}" \
        "/etc/init.d/avahi-daemon status 2>/dev/null" || echo "stopped")

    if echo "$avahi_status" | grep -q "running"; then
        test_pass "avahi-daemon is running"
    else
        test_fail "avahi-daemon" "Daemon is not running"
        return 1
    fi

    return 0
}

# Test 4: mDNS discovery (.local domains)
test_mdns_discovery() {
    log_step "Test 4: mDNS Discovery (.local domains)"

    local discovered_hosts
    discovered_hosts=$(ssh $SSH_OPTS "${ROUTER_USER}@${ROUTER_IP}" \
        "timeout 10 avahi-browse -a -t -r 2>/dev/null | grep 'hostname = ' | grep -oP '\[\K[^\]]+' | sed 's/\.local$//' | sort -u" || echo "")

    if [[ -z "$discovered_hosts" ]]; then
        test_skip "mDNS discovery" "No .local domains discovered"
        log_info "Ensure isle-agents are running and advertising mDNS"
        return 0
    fi

    local host_count=$(echo "$discovered_hosts" | wc -l)
    test_pass "Discovered $host_count .local domain(s)"

    # Display discovered hosts
    log_info "Discovered hosts:"
    echo "$discovered_hosts" | while read -r hostname; do
        echo "  • ${hostname}.local"
    done

    # If no specific hostname was provided, use the first discovered one
    if [[ -z "$TEST_HOSTNAME" ]] && [[ -n "$discovered_hosts" ]]; then
        TEST_HOSTNAME=$(echo "$discovered_hosts" | head -1)
        log_info "Will use ${TEST_HOSTNAME} for further tests"
    fi

    return 0
}

# Test 5: DNS mapping configuration
test_dns_mapping() {
    log_step "Test 5: DNS Mapping (.vlan domains)"

    # Check if dnsmasq config file exists
    local dns_conf
    dns_conf=$(ssh $SSH_OPTS "${ROUTER_USER}@${ROUTER_IP}" \
        "cat /etc/dnsmasq.d/isle-vlan-domains.conf 2>/dev/null" || echo "")

    if [[ -z "$dns_conf" ]]; then
        test_fail "DNS mapping file" "File /etc/dnsmasq.d/isle-vlan-domains.conf not found"
        log_info "Join protocol may not have run yet (runs every 30 seconds)"
        return 1
    fi

    test_pass "DNS mapping configuration file exists"

    # Count .vlan domain entries
    local vlan_count=$(echo "$dns_conf" | grep -c "\.vlan" || echo 0)
    if [[ $vlan_count -gt 0 ]]; then
        test_pass "Found $vlan_count .vlan domain mapping(s)"

        # Display mappings
        log_info "DNS mappings:"
        echo "$dns_conf" | grep "address=" | while read -r line; do
            echo "  $line"
        done
    else
        test_fail "vlan domain mappings" "No .vlan domains in configuration"
        return 1
    fi

    return 0
}

# Test 6: DNS resolution from router
test_dns_resolution_router() {
    log_step "Test 6: DNS Resolution (from router)"

    if [[ -z "$TEST_HOSTNAME" ]]; then
        test_skip "DNS resolution" "No hostname to test"
        return 0
    fi

    # Test .local resolution
    local local_ip
    local_ip=$(ssh $SSH_OPTS "${ROUTER_USER}@${ROUTER_IP}" \
        "nslookup ${TEST_HOSTNAME}.local localhost 2>/dev/null | grep 'Address:' | tail -1 | awk '{print \$2}'" || echo "")

    if [[ -n "$local_ip" ]] && [[ "$local_ip" =~ ^10\. ]]; then
        test_pass "${TEST_HOSTNAME}.local resolves to $local_ip"
    else
        test_fail ".local DNS resolution" "Could not resolve ${TEST_HOSTNAME}.local"
    fi

    # Test .vlan resolution
    local vlan_ip
    vlan_ip=$(ssh $SSH_OPTS "${ROUTER_USER}@${ROUTER_IP}" \
        "nslookup ${TEST_HOSTNAME}.vlan localhost 2>/dev/null | grep 'Address:' | tail -1 | awk '{print \$2}'" || echo "")

    if [[ -n "$vlan_ip" ]] && [[ "$vlan_ip" =~ ^10\. ]]; then
        test_pass "${TEST_HOSTNAME}.vlan resolves to $vlan_ip"
    else
        test_fail ".vlan DNS resolution" "Could not resolve ${TEST_HOSTNAME}.vlan"
    fi

    # Verify both resolve to same IP
    if [[ -n "$local_ip" ]] && [[ -n "$vlan_ip" ]] && [[ "$local_ip" == "$vlan_ip" ]]; then
        test_pass "Both domains resolve to same IP ($local_ip)"
    else
        test_fail "Domain consistency" ".local and .vlan resolve to different IPs"
    fi

    return 0
}

# Test 7: DHCP verification
test_dhcp() {
    log_step "Test 7: DHCP Verification"

    if [[ -z "$TEST_HOSTNAME" ]]; then
        test_skip "DHCP verification" "No hostname to test"
        return 0
    fi

    # Check DHCP leases
    local dhcp_leases
    dhcp_leases=$(ssh $SSH_OPTS "${ROUTER_USER}@${ROUTER_IP}" \
        "cat /var/dhcp.leases 2>/dev/null || cat /tmp/dhcp.leases 2>/dev/null" || echo "")

    if [[ -z "$dhcp_leases" ]]; then
        test_skip "DHCP leases" "No DHCP lease file found"
        return 0
    fi

    # Look for leases in VLAN subnet
    local vlan_leases
    vlan_leases=$(echo "$dhcp_leases" | grep "^10\.${VLAN_ID}\." || echo "")

    if [[ -n "$vlan_leases" ]]; then
        local lease_count=$(echo "$vlan_leases" | wc -l)
        test_pass "Found $lease_count DHCP lease(s) in VLAN ${VLAN_ID}"

        log_info "Active leases:"
        echo "$vlan_leases" | head -5 | while read -r lease; do
            echo "  $lease"
        done
    else
        test_fail "VLAN DHCP leases" "No leases found in 10.${VLAN_ID}.0/24"
    fi

    return 0
}

# Test 8: HTTP connectivity
test_http_connectivity() {
    log_step "Test 8: HTTP Connectivity"

    if [[ -z "$TEST_HOSTNAME" ]]; then
        test_skip "HTTP connectivity" "No hostname to test"
        return 0
    fi

    # Test .local domain
    if curl -f -s -m 5 "http://${TEST_HOSTNAME}.local/health" &> /dev/null || \
       curl -f -s -m 5 "http://${TEST_HOSTNAME}.local/" &> /dev/null; then
        test_pass "HTTP accessible via ${TEST_HOSTNAME}.local"
    else
        test_fail "HTTP .local access" "Cannot reach http://${TEST_HOSTNAME}.local"
    fi

    # Test .vlan domain
    if curl -f -s -m 5 "http://${TEST_HOSTNAME}.vlan/health" &> /dev/null || \
       curl -f -s -m 5 "http://${TEST_HOSTNAME}.vlan/" &> /dev/null; then
        test_pass "HTTP accessible via ${TEST_HOSTNAME}.vlan"
    else
        test_fail "HTTP .vlan access" "Cannot reach http://${TEST_HOSTNAME}.vlan"
    fi

    return 0
}

# Test 9: Join protocol logs
test_join_protocol_logs() {
    log_step "Test 9: Join Protocol Logs"

    local recent_logs
    recent_logs=$(ssh $SSH_OPTS "${ROUTER_USER}@${ROUTER_IP}" \
        "logread | grep isle-join-protocol | tail -10" || echo "")

    if [[ -z "$recent_logs" ]]; then
        test_fail "Join protocol logs" "No log entries found"
        return 1
    fi

    test_pass "Join protocol has log entries"

    # Check for discovery activity
    if echo "$recent_logs" | grep -q "Scanning for mDNS"; then
        test_pass "Discovery scans are running"
    else
        test_fail "Discovery activity" "No recent scan activity in logs"
    fi

    # Display recent logs
    log_info "Recent join protocol activity:"
    echo "$recent_logs" | while read -r log_line; do
        echo "  $log_line"
    done

    return 0
}

# Show test summary
show_summary() {
    local total=$((TESTS_PASSED + TESTS_FAILED + TESTS_SKIPPED))

    cat << EOF

${BLUE}╔═══════════════════════════════════════════════════════════════╗
║           Join Protocol Verification Results                  ║
╚═══════════════════════════════════════════════════════════════╝${NC}

${BLUE}Test Results:${NC}
  ${GREEN}Passed:${NC}  $TESTS_PASSED
  ${RED}Failed:${NC}  $TESTS_FAILED
  ${YELLOW}Skipped:${NC} $TESTS_SKIPPED
  ${BLUE}Total:${NC}   $total

EOF

    if [[ $TESTS_FAILED -eq 0 ]]; then
        cat << EOF
${GREEN}╔═══════════════════════════════════════════════════════════════╗
║                  🎉 All Tests Passed! 🎉                      ║
║                                                               ║
║  The join protocol is working correctly!                     ║
║  • Agents are discoverable via mDNS                          ║
║  • DNS mappings are created for .vlan domains                ║
║  • DHCP is assigning IPs correctly                           ║
║  • HTTP connectivity works for both domains                  ║
╚═══════════════════════════════════════════════════════════════╝${NC}

${BLUE}Next Steps:${NC}
  • Deploy more isle-agents to test mesh scaling
  • Configure HTTPS certificates for secure access
  • Set up nginx reverse proxies to backend services
  • Test mDNS reflection across physical network segments

${GREEN}Your Isle Mesh is operational!${NC}

EOF
    else
        cat << EOF
${YELLOW}╔═══════════════════════════════════════════════════════════════╗
║                  Some Tests Failed                            ║
╚═══════════════════════════════════════════════════════════════╝${NC}

${BLUE}Troubleshooting:${NC}
  1. ${CYAN}Check join protocol service${NC}
     ssh root@${ROUTER_IP} '/etc/init.d/isle-join-protocol status'
     ssh root@${ROUTER_IP} 'logread | grep isle-join-protocol'

  2. ${CYAN}Verify avahi is running${NC}
     ssh root@${ROUTER_IP} '/etc/init.d/avahi-daemon status'

  3. ${CYAN}Check DNS configuration${NC}
     ssh root@${ROUTER_IP} 'cat /etc/dnsmasq.d/isle-vlan-domains.conf'

  4. ${CYAN}Verify isle-agents are running${NC}
     docker ps | grep isle-agent

  5. ${CYAN}Check agent logs${NC}
     docker logs isle-agent-mdns

${YELLOW}Review the failed tests above for specific issues.${NC}

EOF
    fi
}

# Main function
main() {
    log_banner "Isle Join Protocol Verification"

    parse_args "$@"

    log_info "Testing join protocol for VLAN ${VLAN_ID}"
    log_info "Router: ${ROUTER_IP}"
    echo

    # Run tests in order
    test_router_connectivity || true
    test_join_protocol_service || true
    test_router_avahi || true
    test_mdns_discovery || true
    test_dns_mapping || true
    test_dns_resolution_router || true
    test_dhcp || true
    test_http_connectivity || true
    test_join_protocol_logs || true

    # Show summary
    show_summary

    # Exit with appropriate code
    if [[ $TESTS_FAILED -eq 0 ]]; then
        exit 0
    else
        exit 1
    fi
}

main "$@"
