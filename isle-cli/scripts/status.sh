#!/bin/bash

# Isle Unified Status Command
# Provides comprehensive status of all Isle Mesh components

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$CLI_DIR")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Status symbols
CHECK_MARK="${GREEN}✓${NC}"
CROSS_MARK="${RED}✗${NC}"
WARNING_MARK="${YELLOW}⚠${NC}"
INFO_MARK="${BLUE}ℹ${NC}"

log_section() {
    echo ""
    echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║  $1${NC}"
    echo -e "${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

log_subsection() {
    echo ""
    echo -e "${CYAN}═══ $1 ═══${NC}"
    echo ""
}

log_info() {
    echo -e "  ${INFO_MARK} $1"
}

log_success() {
    echo -e "  ${CHECK_MARK} $1"
}

log_warn() {
    echo -e "  ${WARNING_MARK} $1"
}

log_error() {
    echo -e "  ${CROSS_MARK} $1"
}

# Check if command exists
command_exists() {
    command -v "$1" &> /dev/null
}

# Check router status (local or remote) - returns data only
check_router_status() {
    local router_type="none"
    local router_vm=""
    local router_ip=""
    local router_detected=false

    # Check for local router VM first
    if command_exists virsh; then
        local virsh_cmd="virsh"
        if ! virsh list --all &>/dev/null 2>&1; then
            virsh_cmd="sudo virsh"
        fi

        # Check for known router VMs
        for vm_name in "openwrt-isle-router" "openwrt-router" "openwrt-test" "router-core"; do
            if $virsh_cmd list --all 2>/dev/null | grep -q "$vm_name"; then
                router_vm="$vm_name"

                # Check if running
                if $virsh_cmd list --state-running 2>/dev/null | grep -q "$vm_name"; then
                    router_type="local-running"

                    # Try to get IP via MAC lookup
                    local mac_address=$($virsh_cmd dumpxml "$vm_name" 2>/dev/null | grep "mac address" | head -1 | sed -n "s/.*mac address='\([^']*\)'.*/\1/p")
                    if [[ -n "$mac_address" ]]; then
                        router_ip=$(arp -n | grep -i "$mac_address" | awk '{print $1}' | head -1)
                    fi
                    router_detected=true
                else
                    router_type="local-stopped"
                fi
                break
            fi
        done
    fi

    # If no local router, check for remote router via mDNS
    if [[ "$router_type" == "none" ]] && command_exists avahi-browse; then
        # Silently check for remote router (no log output - this function returns data only)

        # Try to resolve openwrt.local
        if command_exists getent; then
            local resolve_result
            resolve_result=$(getent hosts openwrt.local 2>/dev/null || echo "")

            if [[ -n "$resolve_result" ]]; then
                router_ip=$(echo "$resolve_result" | awk '{print $1}')
                router_type="remote"
                router_detected=true
            fi
        fi

        # Alternative: use avahi-resolve
        if [[ "$router_type" == "none" ]] && command_exists avahi-resolve; then
            local resolve_result
            resolve_result=$(avahi-resolve -n openwrt.local 2>/dev/null || echo "")

            if [[ -n "$resolve_result" ]]; then
                router_ip=$(echo "$resolve_result" | awk '{print $2}')
                router_type="remote"
                router_detected=true
            fi
        fi
    fi

    # Return router info for use by other checks (no display output)
    echo "$router_type|$router_ip|$router_vm"
}

# Display router status section
show_router_status_section() {
    local router_type="$1"
    local router_ip="$2"
    local router_vm="$3"

    log_section "Router Status"

    case "$router_type" in
        "local-running")
            log_success "Local router VM running: ${router_vm}"
            if [[ -n "$router_ip" ]]; then
                echo "    IP Address: ${router_ip}"

                # Test connectivity
                if ping -c 1 -W 2 "$router_ip" &>/dev/null; then
                    log_success "Router is reachable (ping successful)"
                else
                    log_warn "Router not responding to ping"
                fi
            else
                log_warn "Could not determine router IP address"
            fi
            ;;
        "local-stopped")
            log_warn "Local router VM exists but is not running: ${router_vm}"
            echo -e "    Start with: ${CYAN}sudo isle router up ${router_vm}${NC}"
            ;;
        "remote")
            log_success "Remote router detected via mDNS"
            echo "    Hostname: openwrt.local"
            echo "    IP Address: ${router_ip}"

            # Test connectivity
            if ping -c 1 -W 2 "$router_ip" &>/dev/null; then
                log_success "Router is reachable (ping successful)"
            else
                log_warn "Router not responding to ping"
            fi
            ;;
        "none")
            log_error "No router detected (local or remote)"
            echo ""
            echo "    To set up a local router:"
            echo -e "      ${CYAN}sudo isle router init${NC}"
            echo ""
            echo "    Or connect to an existing router on your network."
            ;;
    esac
}

# Check mDNS status and detected services
check_mdns_status() {
    log_section "mDNS Status"

    # Check if mesh-mdns.service is installed and running
    if systemctl list-unit-files 2>/dev/null | grep -q "mesh-mdns.service"; then
        if systemctl is-active --quiet mesh-mdns.service 2>/dev/null; then
            log_success "mesh-mdns.service is running"
        else
            log_warn "mesh-mdns.service is installed but not running"
            echo -e "    Start with: ${CYAN}sudo systemctl start mesh-mdns.service${NC}"
        fi
    else
        log_warn "mesh-mdns.service not installed"
        echo -e "    Install with: ${CYAN}isle mdns system install${NC}"
    fi

    log_subsection "Broadcasted Domains"

    # Check broadcast domains list
    local domains_file="/etc/isle-mesh/domains-to-broadcast.txt"
    if [[ -f "$domains_file" ]]; then
        local domain_count=$(grep -v '^#' "$domains_file" 2>/dev/null | grep -v '^$' | wc -l)
        if [[ $domain_count -gt 0 ]]; then
            log_success "Broadcasting ${domain_count} domain(s):"
            grep -v '^#' "$domains_file" | grep -v '^$' | while read domain; do
                echo "    • ${domain}"
            done
        else
            log_warn "No domains configured for broadcasting"
        fi
    else
        log_info "Broadcast domains file not found: ${domains_file}"
    fi

    log_subsection "Detected mDNS Services"

    # Check for mDNS detection tools
    if command_exists avahi-browse; then
        log_info "Scanning for .local services (3 second timeout)..."
        echo ""

        # Browse for all services with timeout
        local services
        services=$(timeout 3 avahi-browse -a -t -r -p 2>/dev/null | grep "^=" | grep "IPv4" || echo "")

        if [[ -n "$services" ]]; then
            # Parse and display unique hostnames
            local unique_hosts
            unique_hosts=$(echo "$services" | awk -F';' '{print $7}' | sort -u)

            local host_count=$(echo "$unique_hosts" | wc -l)
            log_success "Detected ${host_count} mDNS host(s):"
            echo ""

            echo "$unique_hosts" | while read hostname; do
                if [[ -n "$hostname" ]]; then
                    # Get IP for this hostname
                    local host_ip
                    host_ip=$(echo "$services" | grep ";${hostname};" | awk -F';' '{print $8}' | head -1)
                    echo "    • ${hostname} → ${host_ip}"

                    # List services for this host
                    local host_services
                    host_services=$(echo "$services" | grep ";${hostname};" | awk -F';' '{print $4}' | sort -u)
                    echo "$host_services" | while read service; do
                        if [[ -n "$service" && "$service" != "IPv4" ]]; then
                            echo "      - ${service}"
                        fi
                    done
                fi
            done
        else
            log_warn "No mDNS services detected"
        fi
    else
        log_info "avahi-browse not available - skipping service detection"
        echo -e "    Install with: ${CYAN}sudo apt-get install avahi-utils${NC}"
    fi
}

# Check isle-agent status and connectivity
check_agent_status() {
    log_section "Isle Agent Status"

    local agent_container="isle-agent"

    # Check if agent is running
    if docker ps --filter "name=${agent_container}" --filter "status=running" --format '{{.Names}}' | grep -q "^${agent_container}$"; then
        log_success "Agent container is running"

        # Get network info
        local mac_addr
        mac_addr=$(docker inspect "${agent_container}" --format '{{range .NetworkSettings.Networks}}{{.MacAddress}}{{end}}' 2>/dev/null | head -n1)
        local ip_addr
        ip_addr=$(docker inspect "${agent_container}" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null | head -n1)

        if [[ -n "$mac_addr" ]]; then
            echo "    MAC Address: ${mac_addr}"
        fi
        if [[ -n "$ip_addr" ]]; then
            echo "    IP Address: ${ip_addr}"
        fi

        log_subsection "Router Connectivity from Agent"

        # Check if agent can resolve and reach router
        local router_resolved=false
        local router_routable=false

        if docker exec "${agent_container}" sh -c "command -v getent" >/dev/null 2>&1; then
            local resolve_result
            resolve_result=$(docker exec "${agent_container}" getent hosts openwrt.local 2>/dev/null || echo "")

            if [[ -n "$resolve_result" ]]; then
                local router_ip
                router_ip=$(echo "$resolve_result" | awk '{print $1}')
                log_success "Agent can resolve openwrt.local → ${router_ip}"
                router_resolved=true

                # Test if agent can ping router
                if docker exec "${agent_container}" ping -c 1 -W 2 "$router_ip" &>/dev/null; then
                    log_success "Agent can reach router (ping successful)"
                    router_routable=true
                else
                    log_warn "Agent cannot ping router"
                fi
            else
                log_warn "Agent cannot resolve openwrt.local"
            fi
        else
            log_info "Cannot test DNS resolution (getent not available in container)"
        fi

        # Check bridge connectivity
        log_subsection "Bridge Status"

        if ip link show isle-br-0 &>/dev/null; then
            local bridge_state
            bridge_state=$(ip link show isle-br-0 | grep -oP '(?<=state )\w+')
            log_success "Bridge isle-br-0 exists (state: ${bridge_state})"

            local connected_interfaces
            connected_interfaces=$(brctl show isle-br-0 2>/dev/null | tail -n +2 | awk '{print $NF}' | grep -v "^isle-br-0$" | tr '\n' ', ' | sed 's/,$//' || echo "none")
            echo "    Connected interfaces: ${connected_interfaces}"
        else
            log_error "Bridge isle-br-0 does not exist"
            echo "    The agent needs this bridge to connect to the router"
        fi

        # Check registered apps
        log_subsection "Registered Mesh Apps"

        local registry_file="/etc/isle-mesh/agent/registry.json"
        if [[ -f "$registry_file" ]]; then
            local app_count
            app_count=$(jq -r '.apps | length' "$registry_file" 2>/dev/null || echo "0")
            if [[ $app_count -gt 0 ]]; then
                log_success "${app_count} app(s) registered:"
                jq -r '.apps | to_entries[] | "    • \(.key): \(.value.domain)"' "$registry_file" 2>/dev/null || log_warn "Could not parse registry"
            else
                log_info "No apps registered yet"
            fi
        else
            log_info "Registry file not found"
        fi

    else
        log_warn "Agent container is not running"
        echo -e "    Start with: ${CYAN}isle agent start${NC}"
    fi
}

# Test connectivity through known .local routes
check_connectivity_tests() {
    log_section "Connectivity Tests"

    # Get router IP from earlier check
    local router_info="$1"
    local router_type=$(echo "$router_info" | cut -d'|' -f1)
    local router_ip=$(echo "$router_info" | cut -d'|' -f2)

    if [[ -z "$router_ip" || "$router_type" == "none" || "$router_type" == "local-stopped" ]]; then
        log_warn "Cannot run connectivity tests - no active router detected"
        return
    fi

    log_subsection "DHCP Connectivity"

    # Check if we can reach router on DHCP network
    if [[ -n "$router_ip" ]]; then
        if ping -c 1 -W 2 "$router_ip" &>/dev/null; then
            log_success "Can reach router at ${router_ip}"
        else
            log_error "Cannot reach router at ${router_ip}"
        fi
    else
        log_warn "Router IP not available for connectivity test"
    fi

    # Check if agent container can route to .local domains
    local agent_container="isle-agent"
    if docker ps --filter "name=${agent_container}" --filter "status=running" --format '{{.Names}}' | grep -q "^${agent_container}$"; then
        log_subsection ".local Domain Routing (from agent)"

        # Try to resolve and ping openwrt.local from agent
        if docker exec "${agent_container}" sh -c "command -v ping" >/dev/null 2>&1; then
            if docker exec "${agent_container}" ping -c 1 -W 2 openwrt.local &>/dev/null; then
                log_success "Agent can route to openwrt.local via DHCP"
            else
                log_warn "Agent cannot route to openwrt.local"
            fi
        fi

        # Test other .local domains if they exist
        local domains_file="/etc/isle-mesh/domains-to-broadcast.txt"
        if [[ -f "$domains_file" ]]; then
            local test_domains
            test_domains=$(grep -v '^#' "$domains_file" 2>/dev/null | grep -v '^$' | grep '\.local$' | head -3)

            if [[ -n "$test_domains" ]]; then
                echo ""
                log_info "Testing sample .local domains:"
                echo "$test_domains" | while read domain; do
                    if docker exec "${agent_container}" sh -c "command -v getent" >/dev/null 2>&1; then
                        if docker exec "${agent_container}" getent hosts "$domain" &>/dev/null; then
                            log_success "${domain} resolves"
                        else
                            log_warn "${domain} does not resolve"
                        fi
                    fi
                done
            fi
        fi
    fi
}

# Main status command
show_status() {
    echo ""
    echo -e "${BOLD}${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}${BLUE}              Isle Mesh - System Status Report${NC}"
    echo -e "${BOLD}${BLUE}═══════════════════════════════════════════════════════════════${NC}"

    # Capture router info first, but suppress output
    local router_info
    router_info=$(check_router_status 2>&1 | tail -1)

    # Extract just the data line (last line)
    local router_type=$(echo "$router_info" | cut -d'|' -f1)
    local router_ip=$(echo "$router_info" | cut -d'|' -f2)
    local router_vm=$(echo "$router_info" | cut -d'|' -f3)

    # Now display sections in order, starting with router
    # We need to re-run check_router_status but have it only display, not return
    show_router_status_section "$router_type" "$router_ip" "$router_vm"
    check_mdns_status
    check_agent_status
    check_connectivity_tests "$router_info"

    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}Status check complete${NC}"
    echo ""
    echo "For detailed component status, use:"
    echo "  isle router status      - Router details"
    echo "  isle agent status       - Agent details"
    echo "  isle mdns system status - mDNS details"
    echo ""
}

# Show help
show_help() {
    echo ""
    echo -e "${BOLD}Isle Status - Unified System Status${NC}"
    echo ""
    echo "Usage: isle status"
    echo ""
    echo "Provides a comprehensive overview of your Isle Mesh installation:"
    echo ""
    echo "  1. Router Status"
    echo "     - Checks for local VM or remote router"
    echo "     - Shows IP and connectivity"
    echo ""
    echo "  2. mDNS Status"
    echo "     - Service status"
    echo "     - Broadcasted domains"
    echo "     - Detected services"
    echo ""
    echo "  3. Isle Agent Status"
    echo "     - Container status"
    echo "     - Router connectivity"
    echo "     - Registered apps"
    echo ""
    echo "  4. Connectivity Tests"
    echo "     - DHCP connectivity"
    echo "     - Domain routing tests"
    echo ""
    echo "For more details:"
    echo "  isle router status"
    echo "  isle agent status"
    echo "  isle mdns system status"
    echo ""
}

# Machine-readable status check for isle-manager-app
# Outputs: component=status lines
cmd_check() {
    # Router
    local router_info
    router_info=$(check_router_status 2>/dev/null | tail -1)
    local router_type=$(echo "$router_info" | cut -d'|' -f1)

    case "$router_type" in
        local-running) echo "router=running" ;;
        local-stopped) echo "router=stopped" ;;
        remote)        echo "router=remote" ;;
        *)             echo "router=none" ;;
    esac

    # Agent container (check both names)
    local agent_running=false
    for name in "isle-vlan-agent" "isle-agent"; do
        if docker ps --filter "name=${name}" --filter "status=running" --format '{{.Names}}' 2>/dev/null | grep -q "^${name}$"; then
            agent_running=true
            break
        fi
    done
    if $agent_running; then
        echo "agent=running"
    else
        echo "agent=stopped"
    fi

    # Host agent (systemd service)
    if systemctl is-active --quiet isle-host-agent 2>/dev/null; then
        echo "host-agent=running"
    else
        echo "host-agent=stopped"
    fi

    # mDNS service
    if systemctl is-active --quiet mesh-mdns.service 2>/dev/null; then
        echo "mdns=running"
    elif systemctl list-unit-files 2>/dev/null | grep -q "mesh-mdns.service"; then
        echo "mdns=stopped"
    else
        echo "mdns=none"
    fi

    # Isle bridge
    if ip link show isle-br-0 &>/dev/null; then
        echo "bridge=up"
    else
        echo "bridge=down"
    fi

    # Registered apps
    local registry_file="/etc/isle-mesh/agent/registry.json"
    if [[ -f "$registry_file" ]]; then
        local app_count
        app_count=$(jq -r '.apps | length' "$registry_file" 2>/dev/null || echo "0")
        echo "apps=${app_count}"
    else
        echo "apps=0"
    fi
}

# Parse command
COMMAND=${1:-show}

case $COMMAND in
    show|"")
        show_status
        ;;
    check)
        cmd_check
        ;;
    help|-h|--help)
        show_help
        ;;
    *)
        echo "Unknown command: $COMMAND"
        echo "Use 'isle status help' for usage information"
        exit 1
        ;;
esac
