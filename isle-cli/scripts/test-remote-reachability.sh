#!/bin/bash
#
# Isle Remote Reachability Test
# Two perspectives:
#   FROM CORE:   "Can I see remote devices on my isle?"
#   FROM REMOTE: "Am I visible to the isle network?"
#
# Auto-detects perspective from agent.mode, or use --from-core / --from-remote.
#
# Usage:
#   isle test remote                    Auto-detect perspective and test
#   isle test remote --from-core        Test as core: scan for remotes
#   isle test remote --from-remote      Test as remote: check own visibility
#   isle test remote <hostname.isle>    Test a specific host from either side

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
FAILED_TESTS=()

log_test()   { echo -e "${CYAN}[TEST]${NC} $1"; ((TESTS_RUN++)); }
log_pass()   { echo -e "${GREEN}  [PASS]${NC} $1"; ((TESTS_PASSED++)); }
log_fail()   { echo -e "${RED}  [FAIL]${NC} $1"; ((TESTS_FAILED++)); FAILED_TESTS+=("$1"); }
log_warn()   { echo -e "${YELLOW}  [WARN]${NC} $1"; }
log_info()   { echo -e "${BLUE}  [INFO]${NC} $1"; }

show_help() {
    echo -e "${BOLD}Isle Remote Reachability Test${NC}"
    echo ""
    echo "Auto-detects whether this machine is core or remote and runs"
    echo "the appropriate tests."
    echo ""
    echo "Usage:"
    echo "  isle test remote                    Auto-detect and test"
    echo "  isle test remote --from-core        I'm the core: find remotes"
    echo "  isle test remote --from-remote      I'm a remote: check my visibility"
    echo "  isle test remote <hostname>         Test a specific host"
    echo ""
    echo "From Core (looking for remotes):"
    echo "  - Queries router for .isle DNS entries"
    echo "  - Tests each remote's ping, HTTP, mDNS"
    echo "  - Verifies join-protocol is creating mappings"
    echo ""
    echo "From Remote (checking own visibility):"
    echo "  - Verifies agent has isle DHCP lease"
    echo "  - Checks avahi is publishing mDNS hostname"
    echo "  - Queries router to confirm .isle DNS entry exists"
    echo "  - Tests that the core can reach this device"
    echo ""
}

# ═══════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════

get_router_ip() {
    grep 'server=/.isle/' /etc/dnsmasq.d/split-dns.conf 2>/dev/null | sed 's|server=/.isle/||' || echo ""
}

get_router_mgmt_ip() {
    echo "192.168.1.1"
}

ssh_router() {
    local ssh_key="/etc/isle-mesh/router/ssh/isle_router_key"
    local ssh_opts="-o ConnectTimeout=5 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes"
    local pass_file="/etc/isle-mesh/router/ssh/.cached_password"

    if [[ -f "$ssh_key" ]]; then
        ssh -i "$ssh_key" $ssh_opts "root@$(get_router_mgmt_ip)" "$@" 2>/dev/null
    elif [[ -f "$pass_file" ]] && command -v sshpass &>/dev/null; then
        sshpass -p "$(cat "$pass_file")" ssh $ssh_opts "root@$(get_router_mgmt_ip)" "$@" 2>/dev/null
    else
        ssh $ssh_opts "root@$(get_router_mgmt_ip)" "$@" 2>/dev/null
    fi
}

resolve_isle_domain() {
    local fqdn="$1"
    local router_ip
    router_ip=$(get_router_ip)
    [[ -z "$router_ip" ]] && return

    if command -v dig &>/dev/null; then
        dig +short +timeout=3 +tries=1 @"$router_ip" "$fqdn" 2>/dev/null | head -1
    elif command -v nslookup &>/dev/null; then
        nslookup "$fqdn" "$router_ip" 2>/dev/null | awk '/^Address: / {print $2}' | tail -1
    fi
}

# Test a single remote host (used from both perspectives)
test_host() {
    local hostname="$1"  # bare name, no suffix
    local fqdn="${hostname}.isle"

    echo ""
    echo -e "${BOLD}--- ${fqdn} ---${NC}"

    # DNS resolution
    log_test "Router DNS for ${fqdn}"
    local resolved_ip
    resolved_ip=$(resolve_isle_domain "$fqdn")

    if [[ -z "$resolved_ip" ]]; then
        log_fail "${fqdn} not found in router DNS"
        log_info "Router's join-protocol may not have discovered it yet (scans every 30s)."
        return 1
    fi
    log_pass "${fqdn} -> ${resolved_ip}"

    # Subnet check
    if ! echo "$resolved_ip" | grep -qE '^10\.'; then
        log_warn "IP ${resolved_ip} is not on isle subnet"
    fi

    # Ping
    log_test "Ping ${resolved_ip}"
    if ping -c 1 -W 3 "$resolved_ip" &>/dev/null; then
        log_pass "Reachable"
    else
        log_fail "Not reachable at ${resolved_ip}"
        return 1
    fi

    # HTTP health
    log_test "HTTP health on ${fqdn}"
    local http_status
    http_status=$(curl -s -o /dev/null -w "%{http_code}" \
        --connect-to "${fqdn}:80:${resolved_ip}:80" \
        --max-time 5 \
        "http://${fqdn}/health" 2>/dev/null || echo "000")

    case "$http_status" in
        200) log_pass "Health endpoint OK (200)" ;;
        000) log_fail "Connection failed on port 80" ;;
        *)   log_warn "HTTP ${http_status} (agent up but no /health route)" ;;
    esac

    # mDNS cross-check
    log_test "mDNS cross-check for ${hostname}.local"
    if command -v avahi-resolve &>/dev/null; then
        local mdns_ip
        mdns_ip=$(avahi-resolve -4 -n "${hostname}.local" 2>/dev/null | awk '{print $2}')

        if [[ -n "$mdns_ip" ]]; then
            if [[ "$mdns_ip" == "$resolved_ip" ]]; then
                log_pass "mDNS matches: ${hostname}.local -> ${mdns_ip}"
            else
                log_warn "mDNS ${hostname}.local -> ${mdns_ip} vs .isle -> ${resolved_ip}"
            fi
        else
            log_warn "${hostname}.local not found via mDNS"
        fi
    else
        log_info "avahi-resolve not available, skipping"
    fi
}

# ═══════════════════════════════════════════
# FROM CORE: Scan for remote devices
# ═══════════════════════════════════════════

test_from_core() {
    echo ""
    echo -e "${BOLD}=== Core Perspective: Looking for Remote Devices ===${NC}"
    echo ""

    # Check join-protocol is running on router
    log_test "Router join-protocol service running"
    local jp_status
    jp_status=$(ssh_router "/etc/init.d/isle-join-protocol status" 2>/dev/null || echo "")

    if echo "$jp_status" | grep -q "running"; then
        log_pass "Join-protocol service is running"
    else
        log_fail "Join-protocol service not running on router"
        log_info "Deploy it with: sudo isle router configure-join-protocol"
        log_info "Without it, the router can't discover remotes via mDNS."
    fi

    # Get all .isle entries from router
    log_test "Router has .isle domain entries"
    local domains_conf
    domains_conf=$(ssh_router "cat /etc/dnsmasq.d/isle-vlan-domains.conf 2>/dev/null" || echo "")

    if [[ -z "$domains_conf" ]]; then
        log_fail "Cannot read router's isle domain config (SSH failed or file missing)"
        echo ""
        log_info "If the router is running, try:"
        log_info "  ssh root@192.168.1.1 'cat /etc/dnsmasq.d/isle-vlan-domains.conf'"
        return
    fi

    # Parse .isle entries (skip .local entries, deduplicate)
    local isle_hosts
    isle_hosts=$(echo "$domains_conf" | grep '\.isle/' | sed 's|address=/\(.*\)\.isle/\(.*\)|\1 \2|' | sort -u)

    # Filter out our own hostname (we're the core, looking for remotes)
    local my_hostname
    my_hostname=$(hostname -s 2>/dev/null | tr '[:upper:]' '[:lower:]')
    isle_hosts=$(echo "$isle_hosts" | grep -v "^${my_hostname} " || echo "$isle_hosts")

    if [[ -z "$isle_hosts" ]]; then
        log_warn "No remote .isle devices found"
        echo ""
        log_info "Possible reasons:"
        log_info "  - No remote devices have joined yet (isle join)"
        log_info "  - Remote agent's avahi isn't publishing mDNS"
        log_info "  - Join-protocol hasn't scanned yet (every 30s)"
        echo ""

        # Show what mDNS names are visible
        if command -v avahi-browse &>/dev/null; then
            log_info "Currently visible mDNS names on isle network:"
            timeout 3 avahi-browse -a -t -p 2>/dev/null \
                | grep '^=' | cut -d';' -f4 | sort -u | while read -r name; do
                    [[ -n "$name" ]] && echo "    ${name}.local"
                done || echo "    (none)"
        fi
        return
    fi

    local host_count
    host_count=$(echo "$isle_hosts" | wc -l)
    log_pass "Found ${host_count} remote .isle device(s)"

    # Test each remote
    echo "$isle_hosts" | while read -r hostname ip; do
        [[ -z "$hostname" ]] && continue
        test_host "$hostname"
    done
}

# ═══════════════════════════════════════════
# FROM REMOTE: Check own visibility
# ═══════════════════════════════════════════

test_from_remote() {
    echo ""
    echo -e "${BOLD}=== Remote Perspective: Am I Visible on the Isle? ===${NC}"
    echo ""

    # Step 1: Agent container running with isle IP
    local container_name="isle-remote-agent"
    log_test "Remote agent container running"
    if ! docker ps --filter "name=${container_name}" --filter "status=running" -q 2>/dev/null | grep -q .; then
        log_fail "isle-remote-agent is not running"
        log_info "Join an isle first: sudo isle join"
        return
    fi
    log_pass "isle-remote-agent is running"

    log_test "Agent has isle subnet IP"
    local our_ip
    our_ip=$(docker exec "$container_name" ip -4 addr show 2>/dev/null \
        | grep -oP '(?<=inet )10\.\d+\.\d+\.\d+' | head -1 || echo "")

    if [[ -n "$our_ip" ]]; then
        log_pass "Isle IP: ${our_ip}"
    else
        log_fail "No isle subnet IP (DHCP may have failed)"
        log_info "Check: docker logs isle-remote-agent | grep -i dhcp"
        return
    fi

    # Step 2: Avahi publishing our hostname
    local our_hostname=""
    if [[ -f "/etc/isle-mesh/agent/remote/hostname" ]]; then
        our_hostname=$(cat /etc/isle-mesh/agent/remote/hostname)
    else
        our_hostname=$(hostname -s 2>/dev/null | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g')
    fi

    log_test "Avahi publishing ${our_hostname}.local"
    if docker exec "$container_name" avahi-daemon --check 2>/dev/null; then
        log_pass "Avahi is running in container"

        # Verify the hostname avahi is using
        local avahi_hostname
        avahi_hostname=$(docker exec "$container_name" hostname 2>/dev/null || echo "")
        if [[ "$avahi_hostname" == "$our_hostname" ]]; then
            log_pass "Container hostname matches: ${avahi_hostname}"
        else
            log_warn "Container hostname '${avahi_hostname}' != expected '${our_hostname}'"
            log_info "mDNS will advertise as ${avahi_hostname}.local, not ${our_hostname}.local"
            our_hostname="$avahi_hostname"
        fi
    else
        log_fail "Avahi is not running — router can't discover us via mDNS"
        log_info "Check: docker logs isle-remote-agent | grep -i avahi"
    fi

    # Step 3: Check if router has picked us up
    log_test "Router has ${our_hostname}.isle in DNS"
    local router_resolved
    router_resolved=$(resolve_isle_domain "${our_hostname}.isle")

    if [[ -n "$router_resolved" ]]; then
        if [[ "$router_resolved" == "$our_ip" ]]; then
            log_pass "Router maps ${our_hostname}.isle -> ${router_resolved} (matches our IP)"
        else
            log_warn "Router maps ${our_hostname}.isle -> ${router_resolved} (we are ${our_ip})"
            log_info "The .isle entry may be stale from a previous DHCP lease."
        fi
    else
        log_fail "Router does not have ${our_hostname}.isle in DNS yet"
        log_info "The join-protocol scans every 30s. If avahi is publishing, wait and retry."
        log_info "Check router: ssh root@192.168.1.1 'logread | grep isle-join-protocol | tail -5'"
    fi

    # Step 4: Can we reach the router?
    log_test "Can reach router from agent container"
    local router_ip
    router_ip=$(get_router_ip)

    if [[ -n "$router_ip" ]]; then
        if docker exec "$container_name" ping -c 1 -W 3 "$router_ip" &>/dev/null 2>&1; then
            log_pass "Agent can ping router at ${router_ip}"
        else
            log_fail "Agent cannot reach router at ${router_ip}"
            log_info "The macvlan/bridge connection may be broken."
        fi
    else
        log_warn "Cannot determine router IP to test connectivity"
    fi

    # Step 5: Can we resolve other .isle domains?
    log_test "Can resolve .isle domains from this machine"
    # Try to resolve any .isle domain (even our own) through the forwarding chain
    if [[ -n "$router_resolved" ]]; then
        local local_resolved
        if command -v dig &>/dev/null; then
            local_resolved=$(dig +short +timeout=3 "${our_hostname}.isle" 2>/dev/null | head -1)
        elif command -v getent &>/dev/null; then
            local_resolved=$(getent hosts "${our_hostname}.isle" 2>/dev/null | awk '{print $1}')
        fi

        if [[ -n "$local_resolved" ]]; then
            log_pass "Local DNS resolves ${our_hostname}.isle -> ${local_resolved}"
        else
            log_fail "Local DNS cannot resolve ${our_hostname}.isle"
            log_info "Check: grep 'server=/.isle/' /etc/dnsmasq.d/split-dns.conf"
        fi
    fi
}

# ═══════════════════════════════════════════

show_summary() {
    echo ""
    echo -e "${BOLD}════════════════════════════════════════${NC}"
    echo -e "  Tests: ${TESTS_RUN}  ${GREEN}Pass: ${TESTS_PASSED}${NC}  ${RED}Fail: ${TESTS_FAILED}${NC}"
    echo -e "${BOLD}════════════════════════════════════════${NC}"

    if [[ ${#FAILED_TESTS[@]} -gt 0 ]]; then
        echo ""
        echo -e "${RED}Failed:${NC}"
        for t in "${FAILED_TESTS[@]}"; do
            echo -e "  ${RED}x${NC} $t"
        done
    fi
    echo ""
}

detect_perspective() {
    local mode_file="/etc/isle-mesh/agent/agent.mode"
    if [[ -f "$mode_file" ]]; then
        cat "$mode_file" 2>/dev/null
    else
        # No mode file — guess from running containers
        if docker ps --filter "name=isle-remote-agent" --filter "status=running" -q 2>/dev/null | grep -q .; then
            echo "remote"
        elif docker ps --filter "name=isle-vlan-agent" --filter "status=running" -q 2>/dev/null | grep -q .; then
            echo "core"
        else
            echo "unknown"
        fi
    fi
}

# Parse args
PERSPECTIVE=""
TARGET_HOST=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h|help)
            show_help
            exit 0
            ;;
        --from-core|core)
            PERSPECTIVE="core"
            shift
            ;;
        --from-remote|remote)
            PERSPECTIVE="remote"
            shift
            ;;
        --scan|scan)
            PERSPECTIVE="core"
            shift
            ;;
        *)
            TARGET_HOST="$1"
            shift
            ;;
    esac
done

# If a specific host was given, just test it
if [[ -n "$TARGET_HOST" ]]; then
    TARGET_HOST=$(echo "$TARGET_HOST" | sed 's/\.isle$//' | sed 's/\.local$//')
    test_host "$TARGET_HOST"
    show_summary
    exit $TESTS_FAILED
fi

# Auto-detect perspective if not specified
if [[ -z "$PERSPECTIVE" ]]; then
    PERSPECTIVE=$(detect_perspective)
    case "$PERSPECTIVE" in
        core)   log_info "Detected: core mode" ;;
        remote) log_info "Detected: remote mode" ;;
        *)      log_info "Could not detect mode — defaulting to core perspective" ; PERSPECTIVE="core" ;;
    esac
fi

case "$PERSPECTIVE" in
    core)
        test_from_core
        ;;
    remote)
        test_from_remote
        ;;
esac

show_summary
