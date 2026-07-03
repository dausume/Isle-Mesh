#!/bin/bash
#
# Isle Test Dispatcher
# Runs diagnostic tests for isle-mesh subsystems.
#
# Usage:
#   isle test                 Run all test suites
#   isle test isle [args]     Test .isle routing (router DNS, not localhost)
#   isle test mdns [args]     Test mDNS/agent connectivity
#   isle test all             Run both suites
#   isle test check           Machine-readable output for isle-manager-app
#   isle test --help          Show help

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

show_help() {
    echo -e "${BOLD}Isle Test${NC} - Diagnostic Test Suites"
    echo ""
    echo "Usage:"
    echo "  isle test                 Run all test suites"
    echo "  isle test isle [args]     Test .isle routing through router"
    echo "  isle test mdns            Test mDNS/agent connectivity"
    echo "  isle test remote          Test remote device reachability via .isle"
    echo "  isle test all             Run all suites sequentially"
    echo "  isle test check           Machine-readable results (for manager app)"
    echo ""
    echo "Isle routing test options:"
    echo "  isle test isle                  DNS + infra + first registered domain"
    echo "  isle test isle <domain.isle>    Test a specific domain"
    echo "  isle test isle --all            Test all registered domains"
    echo "  isle test isle --dns-only       Only verify DNS path"
    echo ""
    echo "What each suite tests:"
    echo ""
    echo "  ${CYAN}isle${NC}  - .isle DNS forwarded to router (not localhost)"
    echo "         Router DNS is reachable and answering"
    echo "         Domains resolve to isle subnet (10.x.x.x)"
    echo "         HTTP goes through isle-agent, not host"
    echo "         isle-br-0 bridge + macvlan connected"
    echo ""
    echo "  ${CYAN}mdns${NC}  - Agent container running + healthy"
    echo "         Avahi/mDNS resolution from agent"
    echo "         Router discovery via mDNS"
    echo "         Nginx proxy endpoints responding"
    echo "         Registry and config structure valid"
    echo ""
}

# Machine-readable test output for the manager app
# Outputs: suite.test_name=pass|fail|skip|warn
run_check_mode() {
    local results=""
    local exit_code=0

    # --- Isle routing checks ---
    local split_dns="/etc/dnsmasq.d/split-dns.conf"

    # isle.no_localhost: no address=/.isle/ in dnsmasq
    if [[ -f "$split_dns" ]] && grep -q 'address=/.isle/' "$split_dns" 2>/dev/null; then
        results+="isle.no_localhost=fail\n"
        exit_code=1
    elif [[ -f "$split_dns" ]]; then
        results+="isle.no_localhost=pass\n"
    else
        results+="isle.no_localhost=skip\n"
    fi

    # isle.forwarding: server=/.isle/ exists
    if [[ -f "$split_dns" ]] && grep -q 'server=/.isle/' "$split_dns" 2>/dev/null; then
        local router_ip
        router_ip=$(grep 'server=/.isle/' "$split_dns" | sed 's|server=/.isle/||')
        results+="isle.forwarding=pass\n"
        results+="isle.router_ip=${router_ip}\n"

        # isle.router_reachable: can ping router
        if ping -c 1 -W 2 "$router_ip" &>/dev/null; then
            results+="isle.router_reachable=pass\n"
        else
            results+="isle.router_reachable=fail\n"
            exit_code=1
        fi

        # isle.router_dns: router answers DNS queries
        if command -v dig &>/dev/null; then
            if dig +timeout=3 +tries=1 @"$router_ip" "test.isle" &>/dev/null; then
                results+="isle.router_dns=pass\n"
            else
                results+="isle.router_dns=fail\n"
                exit_code=1
            fi
        else
            results+="isle.router_dns=skip\n"
        fi
    else
        results+="isle.forwarding=fail\n"
        results+="isle.router_reachable=skip\n"
        results+="isle.router_dns=skip\n"
        exit_code=1
    fi

    # isle.bridge: isle-br-0 exists
    if ip link show isle-br-0 &>/dev/null; then
        results+="isle.bridge=pass\n"
    else
        results+="isle.bridge=fail\n"
        exit_code=1
    fi

    # isle.agent_macvlan: agent has isle IP
    if docker ps --filter "name=isle-vlan-agent" -q 2>/dev/null | grep -q .; then
        local isle_ip
        isle_ip=$(docker exec isle-vlan-agent ip -4 addr show 2>/dev/null \
            | grep -oP '(?<=inet )10\.\d+\.\d+\.\d+' | head -1 || echo "")
        if [[ -n "$isle_ip" ]]; then
            results+="isle.agent_macvlan=pass\n"
            results+="isle.agent_ip=${isle_ip}\n"
        else
            results+="isle.agent_macvlan=fail\n"
            exit_code=1
        fi
    else
        results+="isle.agent_macvlan=skip\n"
    fi

    # --- mDNS/Agent checks ---

    # mdns.agent_running
    if docker ps --filter "name=isle-vlan-agent" --filter "status=running" -q 2>/dev/null | grep -q .; then
        results+="mdns.agent_running=pass\n"
    else
        results+="mdns.agent_running=fail\n"
        exit_code=1
    fi

    # mdns.agent_health
    if docker exec isle-vlan-agent wget --quiet --tries=1 --spider http://127.0.0.1/health 2>/dev/null; then
        results+="mdns.agent_health=pass\n"
    else
        results+="mdns.agent_health=fail\n"
        exit_code=1
    fi

    # mdns.service_running
    if systemctl is-active --quiet mesh-mdns.service 2>/dev/null; then
        results+="mdns.service_running=pass\n"
    else
        results+="mdns.service_running=fail\n"
        exit_code=1
    fi

    # mdns.host_agent_running
    if systemctl is-active --quiet isle-host-agent.service 2>/dev/null; then
        results+="mdns.host_agent=pass\n"
    else
        results+="mdns.host_agent=fail\n"
        exit_code=1
    fi

    # mdns.registry_valid
    local registry="/etc/isle-mesh/agent/registry.json"
    if [[ -f "$registry" ]] && jq -e '.apps' "$registry" &>/dev/null; then
        local app_count
        app_count=$(jq '.apps | length' "$registry")
        results+="mdns.registry=pass\n"
        results+="mdns.app_count=${app_count}\n"
    elif [[ -f "$registry" ]]; then
        results+="mdns.registry=fail\n"
        exit_code=1
    else
        results+="mdns.registry=skip\n"
    fi

    # mdns.router_vm
    if virsh list --state-running 2>/dev/null | grep -q "openwrt-isle-router" || \
       sudo virsh list --state-running 2>/dev/null | grep -q "openwrt-isle-router"; then
        results+="mdns.router_vm=pass\n"
    else
        results+="mdns.router_vm=fail\n"
        exit_code=1
    fi

    # --- Remote reachability checks ---

    # Detect mode
    local mode=""
    if [[ -f "/etc/isle-mesh/agent/agent.mode" ]]; then
        mode=$(cat /etc/isle-mesh/agent/agent.mode)
    fi
    results+="remote.mode=${mode:-none}\n"

    if [[ "$mode" == "remote" ]]; then
        # Remote perspective: am I visible?
        local container="isle-remote-agent"
        if docker ps --filter "name=${container}" --filter "status=running" -q 2>/dev/null | grep -q .; then
            results+="remote.agent_running=pass\n"

            local our_ip
            our_ip=$(docker exec "$container" ip -4 addr show 2>/dev/null \
                | grep -oP '(?<=inet )10\.\d+\.\d+\.\d+' | head -1 || echo "")
            if [[ -n "$our_ip" ]]; then
                results+="remote.has_isle_ip=pass\n"
                results+="remote.isle_ip=${our_ip}\n"
            else
                results+="remote.has_isle_ip=fail\n"
                exit_code=1
            fi

            if docker exec "$container" avahi-daemon --check 2>/dev/null; then
                results+="remote.avahi_publishing=pass\n"
            else
                results+="remote.avahi_publishing=fail\n"
                exit_code=1
            fi

            # Check if router knows us
            local our_hostname
            our_hostname=$(cat /etc/isle-mesh/agent/remote/hostname 2>/dev/null || hostname -s | tr '[:upper:]' '[:lower:]')
            local router_ip
            router_ip=$(grep 'server=/.isle/' /etc/dnsmasq.d/split-dns.conf 2>/dev/null | sed 's|server=/.isle/||' || echo "")
            if [[ -n "$router_ip" ]] && command -v dig &>/dev/null; then
                local router_answer
                router_answer=$(dig +short +timeout=3 @"$router_ip" "${our_hostname}.isle" 2>/dev/null | head -1)
                if [[ -n "$router_answer" ]]; then
                    results+="remote.router_knows_us=pass\n"
                    results+="remote.router_entry=${our_hostname}.isle=${router_answer}\n"
                else
                    results+="remote.router_knows_us=fail\n"
                    exit_code=1
                fi
            else
                results+="remote.router_knows_us=skip\n"
            fi
        else
            results+="remote.agent_running=fail\n"
            results+="remote.has_isle_ip=skip\n"
            results+="remote.avahi_publishing=skip\n"
            results+="remote.router_knows_us=skip\n"
            exit_code=1
        fi
    elif [[ "$mode" == "core" ]]; then
        # Core perspective: count visible remotes
        local isle_conf
        isle_conf=$(ssh -o BatchMode=yes -o ConnectTimeout=3 -o StrictHostKeyChecking=no \
            -i /etc/isle-mesh/router/ssh/isle_router_key \
            "root@192.168.1.1" "cat /etc/dnsmasq.d/isle-vlan-domains.conf 2>/dev/null" 2>/dev/null || echo "")

        if [[ -n "$isle_conf" ]]; then
            local remote_count
            remote_count=$(echo "$isle_conf" | grep -c '\.isle/' || echo "0")
            results+="remote.visible_devices=${remote_count}\n"
            results+="remote.router_dns_readable=pass\n"
        else
            results+="remote.visible_devices=0\n"
            results+="remote.router_dns_readable=fail\n"
        fi
    fi

    echo -e "$results"
    return $exit_code
}

case "${1:-all}" in
    help|--help|-h)
        show_help
        ;;
    isle)
        shift
        bash "$SCRIPT_DIR/test-isle-routing.sh" "$@"
        ;;
    mdns|agent)
        shift
        bash "$SCRIPT_DIR/test-connectivity.sh" "$@"
        ;;
    remote)
        shift
        bash "$SCRIPT_DIR/test-remote-reachability.sh" "$@"
        ;;
    check)
        run_check_mode
        ;;
    all|"")
        echo -e "${BOLD}Running all Isle-Mesh diagnostic tests...${NC}"
        echo ""

        echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${CYAN} Suite 1: mDNS / Agent Connectivity${NC}"
        echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""
        bash "$SCRIPT_DIR/test-connectivity.sh" || true

        echo ""
        echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${CYAN} Suite 2: Isle Routing Verification${NC}"
        echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""
        bash "$SCRIPT_DIR/test-isle-routing.sh" || true
        ;;
    *)
        # Could be a domain name — pass to isle routing test
        bash "$SCRIPT_DIR/test-isle-routing.sh" "$@"
        ;;
esac
