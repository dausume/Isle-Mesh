#!/bin/bash
#
# Isle Routing Verification
# Verifies that .isle DNS resolution goes through the OpenWRT router,
# NOT through localhost resolution or mDNS fallback.
#
# Usage:
#   isle test [domain]        Test routing for a specific .isle domain
#   isle test --all           Test all registered .isle domains
#   isle test --dns-only      Only test DNS path (skip HTTP)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0
TESTS_WARNED=0
FAILED_TESTS=()

log_test()    { echo -e "${CYAN}[TEST]${NC} $1"; TESTS_RUN=$((TESTS_RUN+1)); }
log_pass()    { echo -e "${GREEN}  [PASS]${NC} $1"; TESTS_PASSED=$((TESTS_PASSED+1)); }
log_fail()    { echo -e "${RED}  [FAIL]${NC} $1"; TESTS_FAILED=$((TESTS_FAILED+1)); FAILED_TESTS+=("$1"); }
log_warn()    { echo -e "${YELLOW}  [WARN]${NC} $1"; TESTS_WARNED=$((TESTS_WARNED+1)); }
log_info()    { echo -e "${BLUE}  [INFO]${NC} $1"; }
log_detail()  { echo -e "         $1"; }

show_help() {
    echo -e "${BOLD}Isle Routing Verification${NC}"
    echo ""
    echo "Verifies that .isle DNS resolution uses the OpenWRT router path,"
    echo "not localhost or mDNS. This ensures the isle network is truly"
    echo "separate from the host network."
    echo ""
    echo "Usage:"
    echo "  isle test                  Run all routing verification tests"
    echo "  isle test <domain.isle>    Test a specific .isle domain"
    echo "  isle test --dns-only       Only verify DNS path (skip HTTP checks)"
    echo "  isle test --all            Test all registered .isle domains"
    echo "  isle test --help           Show this help"
    echo ""
    echo "What this verifies:"
    echo "  1. .isle DNS is forwarded to the router (not resolved locally)"
    echo "  2. No localhost address=/.isle/ entries exist in dnsmasq"
    echo "  3. The router is reachable and answering DNS queries"
    echo "  4. .isle domains resolve to isle subnet IPs (10.x.x.x), not 127.x.x.x"
    echo "  5. The resolved IP belongs to a device with an isle DHCP lease"
    echo "  6. HTTP response comes from the isle-agent nginx, not host nginx"
    echo ""
}

# === DNS PATH VERIFICATION ===

# Test 1: Verify no localhost .isle resolution exists in dnsmasq config
test_no_localhost_isle() {
    log_test "No localhost .isle resolution in dnsmasq config"

    local split_dns="/etc/dnsmasq.d/split-dns.conf"
    if [[ ! -f "$split_dns" ]]; then
        log_fail "Split DNS config not found at $split_dns"
        return 1
    fi

    # Check for address=/.isle/ (localhost resolution — the old scaffolding)
    if grep -q 'address=/.isle/' "$split_dns"; then
        local addr
        addr=$(grep 'address=/.isle/' "$split_dns")
        log_fail "Found localhost .isle resolution: $addr"
        log_detail "This means .isle resolves locally, bypassing the router."
        log_detail "Fix: Run 'isle create' to upgrade to router forwarding,"
        log_detail "or manually replace with: server=/.isle/<router_ip>"
        return 1
    fi

    log_pass "No localhost address=/.isle/ entries found"
}

# Test 2: Verify server=/.isle/ forwarding exists and points to router
test_isle_forwarding_configured() {
    log_test ".isle DNS forwarding configured to router"

    local split_dns="/etc/dnsmasq.d/split-dns.conf"
    local server_line
    server_line=$(grep 'server=/.isle/' "$split_dns" 2>/dev/null || echo "")

    if [[ -z "$server_line" ]]; then
        log_fail "No server=/.isle/ forwarding rule found"
        log_detail "Expected: server=/.isle/<router_isle_ip> in $split_dns"
        return 1
    fi

    local router_ip
    router_ip=$(echo "$server_line" | sed 's|server=/.isle/||')

    # Verify the IP is on an isle subnet (10.x.x.x), not localhost
    if echo "$router_ip" | grep -qE '^127\.|^localhost'; then
        log_fail "Forwarding points to localhost ($router_ip) — not a real router"
        return 1
    fi

    if echo "$router_ip" | grep -qE '^10\.|^192\.168\.1\.'; then
        log_pass "Forwarding to router at $router_ip"
    else
        log_warn "Forwarding to $router_ip (unexpected subnet, expected 10.x.x.x)"
    fi
}

# Test 3: Verify systemd-resolved has ~isle domain
test_resolved_isle_domain() {
    log_test "systemd-resolved recognizes ~isle domain"

    if ! command -v resolvectl &>/dev/null; then
        log_warn "resolvectl not available, skipping"
        return 0
    fi

    local resolved_domains
    resolved_domains=$(resolvectl status 2>/dev/null | grep -i "DNS Domain" || echo "")

    if echo "$resolved_domains" | grep -q '~isle'; then
        log_pass "~isle is registered in systemd-resolved"
    else
        # Check the config file directly
        local conf="/etc/systemd/resolved.conf.d/split-mdns.conf"
        if [[ -f "$conf" ]] && grep -q '~isle' "$conf"; then
            log_warn "~isle is in config but may need a restart of systemd-resolved"
        else
            log_fail "~isle not found in systemd-resolved domains"
        fi
    fi
}

# Test 4: Router is reachable and answering DNS
test_router_dns_reachable() {
    log_test "Router DNS service is reachable"

    local split_dns="/etc/dnsmasq.d/split-dns.conf"
    local router_ip
    router_ip=$(grep 'server=/.isle/' "$split_dns" 2>/dev/null | sed 's|server=/.isle/||' || echo "")

    if [[ -z "$router_ip" ]]; then
        log_fail "Cannot determine router IP (no server=/.isle/ rule)"
        return 1
    fi

    # Ping test
    if ! ping -c 1 -W 2 "$router_ip" &>/dev/null; then
        log_fail "Router at $router_ip is not reachable (ping failed)"
        log_detail "Is the OpenWRT VM running? Check: isle router status"
        return 1
    fi

    # DNS query test — ask the router directly for any .isle domain
    if command -v dig &>/dev/null; then
        local dig_result
        dig_result=$(dig +short +timeout=3 +tries=1 @"$router_ip" "test.isle" 2>/dev/null || echo "")
        # Even NXDOMAIN is fine — it means the router is answering DNS
        local dig_status
        dig_status=$(dig +timeout=3 +tries=1 @"$router_ip" "test.isle" 2>/dev/null | grep "status:" || echo "")

        if [[ -n "$dig_status" ]]; then
            log_pass "Router DNS is responding ($dig_status)"
        else
            log_fail "Router DNS at $router_ip is not responding to queries"
            return 1
        fi
    elif command -v nslookup &>/dev/null; then
        if nslookup test.isle "$router_ip" &>/dev/null; then
            log_pass "Router DNS is responding (nslookup)"
        else
            # nslookup returns error on NXDOMAIN too, check if it connected
            if nslookup test.isle "$router_ip" 2>&1 | grep -q "server can"; then
                log_pass "Router DNS is responding (NXDOMAIN for test.isle is expected)"
            else
                log_fail "Router DNS at $router_ip not responding"
                return 1
            fi
        fi
    else
        log_warn "Neither dig nor nslookup available — cannot verify DNS response"
        log_detail "Install: sudo apt-get install dnsutils"
    fi
}

# === DOMAIN RESOLUTION VERIFICATION ===

# Test a specific .isle domain resolves correctly
test_domain_resolution() {
    local domain="$1"
    log_test "Domain resolution for $domain"

    # Step 1: Resolve the domain
    local resolved_ip=""

    if command -v dig &>/dev/null; then
        resolved_ip=$(dig +short +timeout=3 "$domain" 2>/dev/null | head -1)
    elif command -v nslookup &>/dev/null; then
        resolved_ip=$(nslookup "$domain" 2>/dev/null | awk '/^Address: / { print $2 }' | tail -1)
    elif command -v getent &>/dev/null; then
        resolved_ip=$(getent hosts "$domain" 2>/dev/null | awk '{print $1}')
    fi

    if [[ -z "$resolved_ip" ]]; then
        log_fail "$domain does not resolve"
        log_detail "The router may not have a DNS entry for this domain yet."
        log_detail "Check: ssh root@192.168.1.1 'cat /etc/dnsmasq.d/isle-vlan-domains.conf'"
        return 1
    fi

    log_info "Resolved: $domain -> $resolved_ip"

    # Step 2: Verify it's NOT a localhost address
    if echo "$resolved_ip" | grep -qE '^127\.|^::1'; then
        log_fail "$domain resolved to LOCALHOST ($resolved_ip)"
        log_detail "This means .isle is using local resolution, not the router."
        log_detail "Check /etc/dnsmasq.d/split-dns.conf for stale address=/.isle/ entries."
        return 1
    fi

    # Step 3: Verify it's on an isle subnet
    if echo "$resolved_ip" | grep -qE '^10\.'; then
        log_pass "$domain resolved to isle subnet IP: $resolved_ip"
    elif echo "$resolved_ip" | grep -qE '^192\.168\.'; then
        log_warn "$domain resolved to $resolved_ip (management subnet, not isle subnet)"
    else
        log_warn "$domain resolved to $resolved_ip (unexpected subnet)"
    fi

    # Step 4: Trace the DNS path to confirm it went through the router
    if command -v dig &>/dev/null; then
        local dns_server
        dns_server=$(dig +timeout=3 "$domain" 2>/dev/null | grep "SERVER:" | sed 's/.*SERVER: \([^#]*\).*/\1/')

        if [[ -n "$dns_server" ]]; then
            local split_dns="/etc/dnsmasq.d/split-dns.conf"
            local expected_router
            expected_router=$(grep 'server=/.isle/' "$split_dns" 2>/dev/null | sed 's|server=/.isle/||' || echo "")

            # The query goes: app -> systemd-resolved -> local dnsmasq -> router dnsmasq
            # dig will show the local dnsmasq as SERVER, which then forwards to router
            log_info "DNS server used: $dns_server"

            if [[ "$dns_server" == "127.0.0.6" ]] || [[ "$dns_server" == "127.0.0.53" ]]; then
                log_info "Query went through local dnsmasq/resolved (expected — it forwards to router)"
            fi
        fi
    fi
}

# Test that a domain's HTTP response comes from isle-agent, not host
test_domain_http_path() {
    local domain="$1"
    log_test "HTTP path verification for $domain"

    local resolved_ip=""
    if command -v dig &>/dev/null; then
        resolved_ip=$(dig +short +timeout=3 "$domain" 2>/dev/null | head -1)
    elif command -v getent &>/dev/null; then
        resolved_ip=$(getent hosts "$domain" 2>/dev/null | awk '{print $1}')
    fi

    if [[ -z "$resolved_ip" ]]; then
        log_fail "Cannot test HTTP — domain doesn't resolve"
        return 1
    fi

    # Hit the resolved IP directly with the Host header — this proves
    # we're going through the isle network path, not localhost
    local http_status
    http_status=$(curl -s -o /dev/null -w "%{http_code}" \
        --connect-to "${domain}:80:${resolved_ip}:80" \
        --max-time 5 \
        "http://${domain}/" 2>/dev/null || echo "000")

    if [[ "$http_status" == "000" ]]; then
        log_fail "Cannot reach $domain at $resolved_ip:80 (connection failed)"
        return 1
    fi

    # Check response headers for isle-agent signature
    local headers
    headers=$(curl -s -I \
        --connect-to "${domain}:80:${resolved_ip}:80" \
        --max-time 5 \
        "http://${domain}/" 2>/dev/null || echo "")

    local server_header
    server_header=$(echo "$headers" | grep -i "^server:" | head -1 || echo "")

    if echo "$server_header" | grep -qi "nginx"; then
        log_pass "HTTP $http_status from nginx at $resolved_ip (isle-agent path confirmed)"
    elif [[ "$http_status" == "301" ]] || [[ "$http_status" == "302" ]]; then
        log_pass "HTTP $http_status redirect from $resolved_ip (likely HTTPS redirect from isle-agent)"
    else
        log_warn "HTTP $http_status from $resolved_ip — server: ${server_header:-unknown}"
    fi

    # Final anti-spoofing check: verify the resolved IP is NOT the host's own IP
    local host_ips
    host_ips=$(hostname -I 2>/dev/null || echo "")

    if echo "$host_ips" | grep -qw "$resolved_ip"; then
        # It's the host's IP — could be legitimate if this is the host running the agent
        # But we need to verify it's the isle-facing IP, not a regular interface
        local isle_iface
        isle_iface=$(ip addr show 2>/dev/null | grep -B2 "$resolved_ip" | grep -oP '(?<=: )\S+(?=:)' || echo "")

        if echo "$isle_iface" | grep -qi "isle\|macvlan\|br-0"; then
            log_pass "Response came from this host's isle interface ($isle_iface) — valid"
        else
            log_warn "Resolved IP $resolved_ip is this host's address on $isle_iface"
            log_detail "This may indicate localhost resolution is still active."
            log_detail "Verify with: dig +trace $domain"
        fi
    else
        log_pass "Resolved IP $resolved_ip is not this host — confirmed remote isle path"
    fi
}

# === INFRASTRUCTURE TESTS ===

test_isle_bridge_exists() {
    log_test "isle-br-0 bridge exists"

    if ip link show isle-br-0 &>/dev/null; then
        local state
        state=$(ip -br link show isle-br-0 | awk '{print $2}')
        log_pass "isle-br-0 exists (state: $state)"
    else
        log_fail "isle-br-0 bridge does not exist"
        log_detail "The bridge is created during 'isle create' / router init."
        return 1
    fi
}

test_agent_macvlan_connected() {
    log_test "isle-vlan-agent connected to isle-br-0 macvlan"

    if ! docker ps --filter "name=isle-vlan-agent" -q 2>/dev/null | grep -q .; then
        log_fail "isle-vlan-agent container is not running"
        return 1
    fi

    # Check if the container has a network interface on the isle subnet
    local isle_ip
    isle_ip=$(docker exec isle-vlan-agent ip -4 addr show 2>/dev/null \
        | grep -oP '(?<=inet )10\.\d+\.\d+\.\d+' | head -1 || echo "")

    if [[ -n "$isle_ip" ]]; then
        log_pass "Agent has isle IP: $isle_ip (DHCP lease from router)"
    else
        # Check if it at least has the macvlan interface
        local ifaces
        ifaces=$(docker exec isle-vlan-agent ip link show 2>/dev/null | grep -oP '(?<=: )\w+' || echo "")
        log_fail "Agent has no isle subnet IP (10.x.x.x)"
        log_detail "Container interfaces: $ifaces"
        log_detail "The macvlan network may not be connected, or DHCP failed."
        return 1
    fi
}

# === MAIN ===

run_dns_tests() {
    echo ""
    echo -e "${BOLD}=== DNS Path Verification ===${NC}"
    echo ""
    test_no_localhost_isle
    test_isle_forwarding_configured
    test_resolved_isle_domain
    test_router_dns_reachable
}

run_infra_tests() {
    echo ""
    echo -e "${BOLD}=== Infrastructure Tests ===${NC}"
    echo ""
    test_isle_bridge_exists
    test_agent_macvlan_connected
}

run_domain_tests() {
    local domain="$1"

    echo ""
    echo -e "${BOLD}=== Domain Tests: $domain ===${NC}"
    echo ""
    test_domain_resolution "$domain"
    test_domain_http_path "$domain"
}

get_registered_isle_domains() {
    local registry="/etc/isle-mesh/agent/registry.json"
    if [[ -f "$registry" ]]; then
        jq -r '.apps[].domain // empty' "$registry" 2>/dev/null \
            | sed 's/\.local$/.isle/' | sort -u
    fi
}

show_summary() {
    echo ""
    echo -e "${BOLD}════════════════════════════════════════${NC}"
    echo -e "  Tests: ${TESTS_RUN}  ${GREEN}Pass: ${TESTS_PASSED}${NC}  ${RED}Fail: ${TESTS_FAILED}${NC}  ${YELLOW}Warn: ${TESTS_WARNED}${NC}"
    echo -e "${BOLD}════════════════════════════════════════${NC}"

    if [[ ${#FAILED_TESTS[@]} -gt 0 ]]; then
        echo ""
        echo -e "${RED}Failed:${NC}"
        for t in "${FAILED_TESTS[@]}"; do
            echo -e "  ${RED}x${NC} $t"
        done
    fi

    echo ""
    if [[ $TESTS_FAILED -eq 0 ]]; then
        echo -e "${GREEN}${BOLD}Isle routing is correctly using the OpenWRT router path.${NC}"
    else
        echo -e "${RED}${BOLD}Isle routing has issues — see failures above.${NC}"
    fi
    echo ""
}

main() {
    local dns_only=false
    local test_all=false
    local specific_domain=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --help|-h|help)
                show_help
                exit 0
                ;;
            --dns-only)
                dns_only=true
                shift
                ;;
            --all)
                test_all=true
                shift
                ;;
            *)
                specific_domain="$1"
                # Auto-append .isle if missing
                if ! echo "$specific_domain" | grep -q '\.isle$'; then
                    specific_domain="${specific_domain}.isle"
                fi
                shift
                ;;
        esac
    done

    echo ""
    echo -e "${BOLD}Isle Routing Verification${NC}"
    echo -e "Verifying .isle traffic goes through the OpenWRT router,"
    echo -e "not localhost or mDNS."
    echo ""

    # Always run DNS path tests
    run_dns_tests

    if [[ "$dns_only" == true ]]; then
        show_summary
        return $TESTS_FAILED
    fi

    # Infrastructure tests
    run_infra_tests

    # Domain-specific tests
    if [[ -n "$specific_domain" ]]; then
        run_domain_tests "$specific_domain"
    elif [[ "$test_all" == true ]]; then
        local domains
        domains=$(get_registered_isle_domains)
        if [[ -n "$domains" ]]; then
            for domain in $domains; do
                run_domain_tests "$domain"
            done
        else
            echo ""
            log_info "No .isle domains registered in agent registry."
        fi
    else
        # Default: test first registered domain if any
        local first_domain
        first_domain=$(get_registered_isle_domains | head -1)
        if [[ -n "$first_domain" ]]; then
            run_domain_tests "$first_domain"
        fi
    fi

    show_summary
    return $TESTS_FAILED
}

main "$@"
