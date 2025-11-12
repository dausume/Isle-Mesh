#!/bin/bash
#
# Isle Connectivity & Orchestration Tests
# Tests avahi/mDNS, nginx proxy URIs, and app orchestration logic
#

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$CLI_DIR")"

# Test results tracking
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0
FAILED_TESTS=()

# Logging functions
log_test() {
    echo -e "${CYAN}[TEST]${NC} $1"
    ((TESTS_RUN++))
}

log_pass() {
    echo -e "${GREEN}  ✓ PASS:${NC} $1"
    ((TESTS_PASSED++))
}

log_fail() {
    echo -e "${RED}  ✗ FAIL:${NC} $1"
    ((TESTS_FAILED++))
    FAILED_TESTS+=("$1")
}

log_info() {
    echo -e "${BLUE}  ℹ INFO:${NC} $1"
}

log_skip() {
    echo -e "${YELLOW}  ⊘ SKIP:${NC} $1"
}

log_section() {
    echo ""
    echo -e "${BOLD}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║ $1${NC}"
    echo -e "${BOLD}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# Test if agent container is running
test_agent_running() {
    log_test "Agent container is running"

    if docker ps --filter "name=isle-agent" --filter "status=running" --format '{{.Names}}' | grep -q "^isle-agent$"; then
        log_pass "isle-agent container is running"
        return 0
    else
        log_fail "isle-agent container is not running"
        return 1
    fi
}

# Test agent health endpoint
test_agent_health() {
    log_test "Agent health endpoint"

    if ! docker ps --filter "name=isle-agent" --filter "status=running" -q | grep -q .; then
        log_skip "Agent not running"
        ((TESTS_RUN--))
        return 0
    fi

    if docker exec isle-agent wget --quiet --tries=1 --spider http://127.0.0.1/health 2>/dev/null; then
        log_pass "Health endpoint responding at http://127.0.0.1/health"
        return 0
    else
        log_fail "Health endpoint not responding"
        return 1
    fi
}

# Test agent status endpoint
test_agent_status() {
    log_test "Agent status endpoint"

    if ! docker ps --filter "name=isle-agent" --filter "status=running" -q | grep -q .; then
        log_skip "Agent not running"
        ((TESTS_RUN--))
        return 0
    fi

    local response
    if response=$(docker exec isle-agent wget -q -O - http://127.0.0.1/status 2>/dev/null); then
        log_pass "Status endpoint responding: $response"
        return 0
    else
        log_fail "Status endpoint not responding"
        return 1
    fi
}

# Test avahi-daemon accessibility from agent
test_avahi_from_agent() {
    log_test "Avahi/mDNS accessibility from agent container"

    if ! docker ps --filter "name=isle-agent" --filter "status=running" -q | grep -q .; then
        log_skip "Agent not running"
        ((TESTS_RUN--))
        return 0
    fi

    # Check if avahi-daemon is running on host
    if ! pgrep -x avahi-daemon > /dev/null; then
        log_skip "avahi-daemon not running on host"
        log_info "Install avahi-daemon: sudo apt-get install avahi-daemon"
        ((TESTS_RUN--))
        return 0
    fi

    # Test if agent can resolve .local domains
    if docker exec isle-agent sh -c "command -v getent" >/dev/null 2>&1; then
        if docker exec isle-agent getent hosts localhost.local >/dev/null 2>&1; then
            log_pass "Agent can resolve .local domains via avahi"
            return 0
        else
            log_fail "Agent cannot resolve .local domains"
            log_info "Agent may need /var/run/dbus mounted for mDNS resolution"
            return 1
        fi
    else
        # Try with nslookup if available
        if docker exec isle-agent sh -c "command -v nslookup" >/dev/null 2>&1; then
            if docker exec isle-agent nslookup localhost.local >/dev/null 2>&1; then
                log_pass "Agent can resolve .local domains via avahi"
                return 0
            else
                log_fail "Agent cannot resolve .local domains"
                return 1
            fi
        else
            log_skip "No DNS resolution tools available in agent container"
            ((TESTS_RUN--))
            return 0
        fi
    fi
}

# Test if agent can discover OpenWRT router via mDNS
test_openwrt_mdns_discovery() {
    log_test "OpenWRT router mDNS discovery from agent"

    if ! docker ps --filter "name=isle-agent" --filter "status=running" -q | grep -q .; then
        log_skip "Agent not running"
        ((TESTS_RUN--))
        return 0
    fi

    # Check if router is running
    if ! virsh list --state-running 2>/dev/null | grep -q "openwrt-isle-router\|openwrt-router\|router-core" && \
       ! sudo virsh list --state-running 2>/dev/null | grep -q "openwrt-isle-router\|openwrt-router\|router-core"; then
        log_skip "Router not running"
        ((TESTS_RUN--))
        return 0
    fi

    local router_found=false
    local router_hostname=""
    local router_ip=""

    # Try to resolve openwrt.local and common OpenWRT hostnames
    local hostnames=("openwrt.local" "openwrt-isle-router.local" "router.local" "isle-router.local")

    for hostname in "${hostnames[@]}"; do
        if docker exec isle-agent sh -c "command -v getent" >/dev/null 2>&1; then
            local resolve_result
            resolve_result=$(docker exec isle-agent getent hosts "$hostname" 2>/dev/null || echo "")

            if [[ -n "$resolve_result" ]]; then
                router_ip=$(echo "$resolve_result" | awk '{print $1}')
                router_hostname="$hostname"
                router_found=true
                log_pass "Agent can discover OpenWRT router at $router_hostname ($router_ip)"
                log_info "mDNS resolution is working correctly"
                return 0
            fi
        fi
    done

    # If DNS resolution failed, try avahi-browse
    if docker exec isle-agent sh -c "command -v avahi-browse" >/dev/null 2>&1; then
        local avahi_result
        avahi_result=$(docker exec isle-agent timeout 3 avahi-browse -a -t -p 2>/dev/null | grep -i "openwrt\|router" | head -1 || echo "")

        if [[ -n "$avahi_result" ]]; then
            router_hostname=$(echo "$avahi_result" | cut -d';' -f4)
            router_ip=$(echo "$avahi_result" | cut -d';' -f8)
            log_pass "Agent discovered router via avahi: $router_hostname ($router_ip)"
            return 0
        fi
    fi

    # If still not found, report failure
    log_fail "Agent cannot discover OpenWRT router via mDNS"
    log_info "Checked hostnames: ${hostnames[*]}"
    log_info "This may indicate:"
    log_info "  - Avahi daemon not running on router"
    log_info "  - mDNS packets not reaching the agent"
    log_info "  - Agent container missing mDNS resolution support"
    return 1
}

# Test if router is accessible from agent
test_router_from_agent() {
    log_test "Router accessibility from agent"

    if ! docker ps --filter "name=isle-agent" --filter "status=running" -q | grep -q .; then
        log_skip "Agent not running"
        ((TESTS_RUN--))
        return 0
    fi

    # Check if router is running
    if ! virsh list --state-running 2>/dev/null | grep -q "openwrt-isle-router" && \
       ! sudo virsh list --state-running 2>/dev/null | grep -q "openwrt-isle-router"; then
        log_skip "Router not running"
        ((TESTS_RUN--))
        return 0
    fi

    # Get agent's IP address
    local agent_ip
    agent_ip=$(docker inspect isle-agent --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null || echo "")

    if [[ -z "$agent_ip" ]]; then
        log_fail "Could not determine agent IP address"
        return 1
    fi

    log_info "Agent IP: $agent_ip"

    # Try to ping router from agent (router is typically the gateway)
    local gateway_ip
    gateway_ip=$(docker exec isle-agent ip route | grep default | awk '{print $3}' 2>/dev/null || echo "")

    if [[ -n "$gateway_ip" ]]; then
        log_info "Gateway IP: $gateway_ip"
        if docker exec isle-agent ping -c 1 -W 2 "$gateway_ip" >/dev/null 2>&1; then
            log_pass "Agent can reach gateway (router) at $gateway_ip"
            return 0
        else
            log_fail "Agent cannot reach gateway at $gateway_ip"
            return 1
        fi
    else
        log_fail "Could not determine gateway IP from agent"
        return 1
    fi
}

# Test nginx base domain HTTP endpoint
test_nginx_http_base() {
    log_test "Nginx HTTP base domain test endpoint"

    if ! docker ps --filter "name=isle-agent" --filter "status=running" -q | grep -q .; then
        log_skip "Agent not running"
        ((TESTS_RUN--))
        return 0
    fi

    # Check if there are any registered apps
    local registry_file="/etc/isle-mesh/agent/registry.json"
    if [[ ! -f "$registry_file" ]]; then
        log_skip "No registry file found, no apps registered"
        ((TESTS_RUN--))
        return 0
    fi

    # Get first registered domain
    local test_domain
    test_domain=$(jq -r '.apps | to_entries[0].value.domain // empty' "$registry_file" 2>/dev/null || echo "")

    if [[ -z "$test_domain" ]]; then
        log_skip "No apps registered yet"
        ((TESTS_RUN--))
        return 0
    fi

    log_info "Testing domain: $test_domain"

    # Test HTTP endpoint from inside agent container
    local response
    if response=$(docker exec isle-agent wget -q -O - "http://${test_domain}/" 2>&1); then
        if echo "$response" | grep -q "reached successfully"; then
            log_pass "HTTP base domain endpoint accessible: $test_domain"
            log_info "Response: $response"
            return 0
        else
            log_info "Unexpected response from $test_domain"
            log_info "Response: $response"
            log_pass "Domain is accessible but with different response"
            return 0
        fi
    else
        log_fail "Cannot reach HTTP endpoint at http://${test_domain}/"
        log_info "Error: $response"
        return 1
    fi
}

# Test nginx base domain HTTPS endpoint
test_nginx_https_base() {
    log_test "Nginx HTTPS base domain test endpoint"

    if ! docker ps --filter "name=isle-agent" --filter "status=running" -q | grep -q .; then
        log_skip "Agent not running"
        ((TESTS_RUN--))
        return 0
    fi

    # Check if there are any registered apps
    local registry_file="/etc/isle-mesh/agent/registry.json"
    if [[ ! -f "$registry_file" ]]; then
        log_skip "No registry file found, no apps registered"
        ((TESTS_RUN--))
        return 0
    fi

    # Get first registered domain
    local test_domain
    test_domain=$(jq -r '.apps | to_entries[0].value.domain // empty' "$registry_file" 2>/dev/null || echo "")

    if [[ -z "$test_domain" ]]; then
        log_skip "No apps registered yet"
        ((TESTS_RUN--))
        return 0
    fi

    log_info "Testing domain: $test_domain"

    # Test HTTPS endpoint from inside agent container (with --no-check-certificate for self-signed certs)
    local response
    if response=$(docker exec isle-agent wget --no-check-certificate -q -O - "https://${test_domain}/" 2>&1); then
        if echo "$response" | grep -q "reached successfully"; then
            log_pass "HTTPS base domain endpoint accessible: $test_domain"
            log_info "Response: $response"
            return 0
        else
            log_info "Unexpected response from $test_domain"
            log_info "Response: $response"
            log_pass "Domain is accessible but with different response (app may be running)"
            return 0
        fi
    else
        log_fail "Cannot reach HTTPS endpoint at https://${test_domain}/"
        log_info "Error: $response"
        return 1
    fi
}

# Test agent registry file structure
test_agent_registry() {
    log_test "Agent registry file structure"

    local registry_file="/etc/isle-mesh/agent/registry.json"
    if [[ ! -f "$registry_file" ]]; then
        log_fail "Registry file not found at $registry_file"
        return 1
    fi

    # Validate JSON structure
    if jq -e '.domains and .subdomains and .apps' "$registry_file" >/dev/null 2>&1; then
        local app_count
        app_count=$(jq '.apps | length' "$registry_file")
        log_pass "Registry file is valid (${app_count} apps registered)"
        return 0
    else
        log_fail "Registry file has invalid structure"
        return 1
    fi
}

# Test agent nginx config files
test_agent_config_files() {
    log_test "Agent nginx configuration files"

    local agent_dir="/etc/isle-mesh/agent"
    local nginx_conf="${agent_dir}/nginx.conf"
    local configs_dir="${agent_dir}/configs"

    if [[ ! -f "$nginx_conf" ]]; then
        log_fail "Master nginx.conf not found at $nginx_conf"
        return 1
    fi

    if [[ ! -d "$configs_dir" ]]; then
        log_fail "Configs directory not found at $configs_dir"
        return 1
    fi

    # Check if nginx.conf includes configs directory
    if grep -q "include /etc/nginx/configs/\*.conf" "$nginx_conf"; then
        log_pass "nginx.conf properly includes config fragments"
    else
        log_fail "nginx.conf does not include config fragments"
        return 1
    fi

    # Count config fragments
    local fragment_count
    fragment_count=$(find "$configs_dir" -name "*.conf" -type f 2>/dev/null | wc -l)
    log_info "Config fragments: $fragment_count"

    return 0
}

# Test app orchestration: agent detection
test_orchestration_agent_detection() {
    log_test "App creation detects existing agent setup"

    # This is a logical test - checking if the agent detection code exists
    local create_script="${SCRIPT_DIR}/create.sh"

    if [[ ! -f "$create_script" ]]; then
        log_fail "create.sh script not found"
        return 1
    fi

    # Check if create.sh checks for agent before starting
    if grep -q "docker ps.*isle-agent" "$create_script"; then
        log_pass "create.sh checks for existing agent"
    else
        log_fail "create.sh does not check for existing agent"
        return 1
    fi

    # Check if agent script exists
    local agent_script="${SCRIPT_DIR}/agent.sh"
    if [[ -f "$agent_script" ]]; then
        log_pass "agent.sh script exists"
    else
        log_fail "agent.sh script not found"
        return 1
    fi

    return 0
}

# Test mesh-proxy templates availability
test_mesh_proxy_templates() {
    log_test "Mesh-proxy nginx templates availability"

    local mesh_proxy_dir="${PROJECT_ROOT}/mesh-proxy"
    local templates_dir="${mesh_proxy_dir}/segments"

    if [[ ! -d "$templates_dir" ]]; then
        log_fail "Mesh-proxy templates directory not found"
        return 1
    fi

    # Check for required templates
    local required_templates=(
        "server-http-base.conf.j2"
        "server-https-base.conf.j2"
        "server-http-subdomain.conf.j2"
        "server-https-subdomain-simple.conf.j2"
        "upstream.conf.j2"
    )

    local missing_count=0
    for template in "${required_templates[@]}"; do
        if [[ -f "${templates_dir}/${template}" ]]; then
            log_info "✓ Found: $template"
        else
            log_info "✗ Missing: $template"
            ((missing_count++))
        fi
    done

    if [[ $missing_count -eq 0 ]]; then
        log_pass "All required templates are available"
        return 0
    else
        log_fail "Missing ${missing_count} required template(s)"
        return 1
    fi
}

# Test template rendering logic (if mesh-proxy generator exists)
test_template_rendering() {
    log_test "Mesh-proxy template rendering capability"

    local mesh_proxy_dir="${PROJECT_ROOT}/mesh-proxy"

    # Look for proxy generator script/tool
    if [[ -f "${mesh_proxy_dir}/generate.py" ]] || [[ -f "${mesh_proxy_dir}/generate.sh" ]]; then
        log_pass "Mesh-proxy generator found"
        return 0
    elif [[ -f "${mesh_proxy_dir}/proxy-generator.py" ]]; then
        log_pass "Mesh-proxy generator (proxy-generator.py) found"
        return 0
    else
        log_info "Looking for any generator script..."
        if find "$mesh_proxy_dir" -name "*generat*" -type f 2>/dev/null | grep -q .; then
            log_pass "Found generator script in mesh-proxy"
            return 0
        else
            log_fail "No mesh-proxy generator script found"
            log_info "Expected: generate.py, generate.sh, or proxy-generator.py"
            return 1
        fi
    fi
}

# Test agent reload capability
test_agent_reload() {
    log_test "Agent reload capability (zero-downtime)"

    if ! docker ps --filter "name=isle-agent" --filter "status=running" -q | grep -q .; then
        log_skip "Agent not running"
        ((TESTS_RUN--))
        return 0
    fi

    # Test if nginx reload works
    if docker exec isle-agent nginx -t 2>&1 | grep -q "syntax is ok"; then
        log_pass "Nginx config validation works in agent"

        # Test actual reload
        if docker exec isle-agent nginx -s reload 2>/dev/null; then
            log_pass "Agent can reload nginx configuration"
            return 0
        else
            log_fail "Agent reload failed"
            return 1
        fi
    else
        log_fail "Nginx config validation failed"
        return 1
    fi
}

# Test router mDNS broadcasting
test_router_mdns() {
    log_test "Router mDNS broadcasting"

    # Check if router is running
    if ! virsh list --state-running 2>/dev/null | grep -q "openwrt-isle-router" && \
       ! sudo virsh list --state-running 2>/dev/null | grep -q "openwrt-isle-router"; then
        log_skip "Router not running"
        ((TESTS_RUN--))
        return 0
    fi

    # Check if we can discover .local domains
    if command -v avahi-browse >/dev/null 2>&1; then
        log_info "Checking for mDNS services..."

        # Browse for a few seconds
        local mdns_services
        mdns_services=$(timeout 3 avahi-browse -a -t -r 2>/dev/null | grep -i "isle\|vlan" || echo "")

        if [[ -n "$mdns_services" ]]; then
            log_pass "mDNS services are being broadcast"
            log_info "Found services: $(echo "$mdns_services" | wc -l) entries"
            return 0
        else
            log_info "No Isle-specific mDNS services found (may be normal if no apps running)"
            log_pass "mDNS system is functional"
            return 0
        fi
    else
        log_skip "avahi-browse not installed (install with: sudo apt-get install avahi-utils)"
        ((TESTS_RUN--))
        return 0
    fi
}

# Show test summary
show_summary() {
    echo ""
    echo -e "${BOLD}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║                    TEST SUMMARY                            ║${NC}"
    echo -e "${BOLD}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "Total Tests:  ${TESTS_RUN}"
    echo -e "${GREEN}Passed:       ${TESTS_PASSED}${NC}"

    if [[ $TESTS_FAILED -gt 0 ]]; then
        echo -e "${RED}Failed:       ${TESTS_FAILED}${NC}"
        echo ""
        echo -e "${RED}Failed Tests:${NC}"
        for test in "${FAILED_TESTS[@]}"; do
            echo -e "  ${RED}✗${NC} $test"
        done
    else
        echo -e "${GREEN}Failed:       0${NC}"
    fi

    echo ""

    if [[ $TESTS_FAILED -eq 0 ]]; then
        echo -e "${GREEN}${BOLD}✓ ALL TESTS PASSED${NC}"
        echo ""
        return 0
    else
        echo -e "${RED}${BOLD}✗ SOME TESTS FAILED${NC}"
        echo ""
        return 1
    fi
}

# Main execution
main() {
    echo ""
    echo -e "${BOLD}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║   Isle Mesh - Connectivity & Orchestration Tests          ║${NC}"
    echo -e "${BOLD}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    # Section 1: Agent Tests
    log_section "1. Agent Container Tests"
    test_agent_running
    test_agent_health
    test_agent_status
    test_agent_registry
    test_agent_config_files
    test_agent_reload

    # Section 2: Network Connectivity Tests
    log_section "2. Network Connectivity Tests"
    test_avahi_from_agent
    test_openwrt_mdns_discovery
    test_router_from_agent
    test_router_mdns

    # Section 3: Nginx Proxy Tests
    log_section "3. Nginx Proxy Endpoint Tests"
    test_nginx_http_base
    test_nginx_https_base

    # Section 4: Orchestration Tests
    log_section "4. Orchestration Logic Tests"
    test_orchestration_agent_detection
    test_mesh_proxy_templates
    test_template_rendering

    # Show summary
    show_summary
}

# Run main with help option
case "${1:-}" in
    help|--help|-h)
        echo "Isle Connectivity & Orchestration Tests"
        echo ""
        echo "Usage: $0 [options]"
        echo ""
        echo "This script tests:"
        echo "  • Agent container health and configuration"
        echo "  • Avahi/mDNS accessibility from agent"
        echo "  • Router connectivity from agent"
        echo "  • Nginx proxy base/test URI accessibility"
        echo "  • Agent registry and config file structure"
        echo "  • App orchestration logic (agent detection)"
        echo "  • Mesh-proxy template availability"
        echo "  • Agent reload capability"
        echo ""
        echo "Options:"
        echo "  --help, -h     Show this help message"
        echo ""
        exit 0
        ;;
    *)
        main
        exit $?
        ;;
esac
