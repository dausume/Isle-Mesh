#!/bin/bash
#
# Isle Join Command
# Joins a remote machine to an existing isle by:
#   1. Listening for the router's discovery beacon
#   2. Creating a macvlan network on the physical interface
#   3. Starting the remote agent container
#

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$CLI_DIR")"

# Source agent-manager for shared functions
AGENT_MANAGER="${PROJECT_ROOT}/isle-agent/scripts/agent-manager.sh"

# Configuration
ISLE_AGENT_DIR="/etc/isle-mesh/agent"
REMOTE_DIR="${ISLE_AGENT_DIR}/remote"
MODE_FILE="${ISLE_AGENT_DIR}/agent.mode"
DISCOVERY_FILE="${REMOTE_DIR}/discovery.json"

# Defaults
INTERFACE=""
TIMEOUT=60
DISCOVERY_PORT=7878
DETECT=false

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[✓]${NC} $1"; }
log_error()   { echo -e "${RED}[✗]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }

log_step() {
    echo -e ""
    echo -e "${CYAN}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC} ${BOLD}$1${NC}"
    echo -e "${CYAN}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo -e ""
}

show_help() {
    cat <<EOF
${BOLD}Isle Join${NC} - Join an existing isle from a remote machine

${CYAN}USAGE:${NC}
  isle join [options]

${CYAN}OPTIONS:${NC}
  --interface, -i <iface>   Physical network interface (auto-detected if omitted)
  --timeout, -t <seconds>   Discovery beacon timeout (default: 60)
  --help, -h                Show this help message

${CYAN}DESCRIPTION:${NC}
  Joins this machine to an existing isle. The command:
    1. Listens for a discovery beacon from the isle router
    2. Creates a Docker macvlan network on the physical interface
    3. Starts a remote agent container that gets a VLAN IP via DHCP
    4. The container runs nginx + avahi for serving apps and mDNS

${CYAN}PREREQUISITES:${NC}
  - Docker installed and running
  - socat or netcat installed (for discovery listening)
  - An isle router broadcasting discovery beacons on the network
  - Root/sudo access (for macvlan network creation)

${CYAN}EXAMPLES:${NC}
  sudo isle join                          # Auto-detect interface
  sudo isle join --interface eth0         # Use specific interface
  sudo isle join --timeout 120           # Wait longer for beacon

${CYAN}AFTER JOINING:${NC}
  Register apps:  isle agent register --name myapp --domain myapp.local --container myapp-1 --port 8080
  View status:    isle agent status
  Leave isle:     sudo isle leave

EOF
}

# Parse arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --interface|-i)
                INTERFACE="$2"
                shift 2
                ;;
            --timeout|-t)
                TIMEOUT="$2"
                shift 2
                ;;
            --detect)
                DETECT=true
                shift
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                echo "Use 'isle join --help' for usage"
                exit 1
                ;;
        esac
    done
}

# Check prerequisites
check_prerequisites() {
    log_step "Step 1: Checking Prerequisites"

    # Check Docker
    if ! command -v docker &>/dev/null; then
        log_error "Docker is not installed"
        echo "  Install Docker: https://docs.docker.com/get-docker/"
        exit 1
    fi
    log_success "Docker is installed"

    if ! docker ps &>/dev/null; then
        log_error "Docker daemon is not accessible"
        echo "  Ensure Docker is running and you have permissions"
        exit 1
    fi
    log_success "Docker daemon is running"

    # Check for discovery listener tools
    if ! command -v socat &>/dev/null && ! command -v nc &>/dev/null; then
        log_error "Neither socat nor netcat is installed"
        echo "  Install socat: sudo apt-get install socat"
        exit 1
    fi
    log_success "Discovery listener tool available"

    # Check for jq
    if ! command -v jq &>/dev/null; then
        log_error "jq is not installed"
        echo "  Install jq: sudo apt-get install jq"
        exit 1
    fi
    log_success "jq is installed"

    # Check for root/sudo
    if [[ $EUID -ne 0 ]]; then
        log_error "This command requires root privileges"
        echo "  Run with: sudo isle join"
        exit 1
    fi
    log_success "Running as root"

    # A host firewall silently eats the discovery beacon (UDP 7878).
    # Opening it is part of what joining MEANS, and join is an
    # explicit sudo action — narrate and allow.
    if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -q "^Status: active"; then
        if ! ufw status 2>/dev/null | grep -q "${DISCOVERY_PORT}/udp"; then
            log_info "ufw is active — allowing UDP ${DISCOVERY_PORT} (isle discovery beacon)"
            ufw allow "${DISCOVERY_PORT}/udp" comment 'isle discovery beacon' >/dev/null 2>&1 \
                && log_success "ufw: ${DISCOVERY_PORT}/udp allowed" \
                || log_warning "could not add ufw rule — beacon may not arrive"
        fi
    fi
}

# Guard: refuse if already in core or remote mode
check_mode_guard() {
    if [[ -f "$MODE_FILE" ]]; then
        local current_mode
        current_mode=$(cat "$MODE_FILE" 2>/dev/null)

        case "$current_mode" in
            core)
                log_error "This machine is running in core mode (has a router)"
                echo "  A machine cannot be both core and remote."
                echo "  Destroy the core isle first: sudo isle destroy"
                exit 1
                ;;
            remote)
                log_error "This machine is already joined to an isle"
                echo "  Leave first: sudo isle leave"
                echo "  Then rejoin: sudo isle join"
                exit 1
                ;;
        esac
    fi

    # Also check for running containers
    if docker ps --filter "name=isle-vlan-agent" --filter "status=running" --format '{{.Names}}' | grep -q "isle-vlan-agent"; then
        log_error "isle-vlan-agent container is running (core mode)"
        echo "  Stop it first or run: sudo isle destroy"
        exit 1
    fi

    if docker ps --filter "name=isle-remote-agent" --filter "status=running" --format '{{.Names}}' | grep -q "isle-remote-agent"; then
        log_error "isle-remote-agent is already running"
        echo "  Leave first: sudo isle leave"
        exit 1
    fi
}

# Detect network interface for isle mesh
# Priority: --interface flag > link-up ethernet with no IP > default route fallback
detect_interface() {
    log_step "Step 2: Detecting Network Interface"

    if [[ -n "$INTERFACE" ]]; then
        # Explicit interface specified
        if ! ip link show "$INTERFACE" &>/dev/null; then
            log_error "Specified interface '$INTERFACE' not found"
            echo "  Available interfaces:"
            ip -br link show | grep -v "^lo " | awk '{print "    " $1}'
            exit 1
        fi
        log_success "Using specified interface: $INTERFACE"
    else
        # Strategy 1: Find ethernet interfaces with carrier (link up) but no IP
        # This catches a freshly plugged-in cable intended for the isle mesh
        local candidates
        candidates=$(find_linkup_no_ip_interfaces)

        if [[ -n "$candidates" ]]; then
            local count
            count=$(echo "$candidates" | wc -w)

            if [[ $count -eq 1 ]]; then
                INTERFACE="$candidates"
                log_success "Detected isle-ready interface: $INTERFACE (link up, no IP)"
            else
                log_info "Multiple link-up interfaces without IP found:"
                for iface in $candidates; do
                    local mac
                    mac=$(ip link show "$iface" | awk '/link\/ether/ {print $2}')
                    echo "    $iface ($mac)"
                done
                echo ""
                log_error "Cannot auto-select — specify with --interface <name>"
                exit 1
            fi
        else
            # Strategy 1.5: a WIRED NIC already holding an isle (10.x)
            # lease IS the isle cable in the lease-cabled deployment
            local leased="" l_iface l_ip
            while IFS= read -r line; do
                l_iface=$(echo "$line" | awk '{print $1}')
                l_ip=$(echo "$line" | awk '{print $3}')
                case "$l_iface" in lo|docker*|veth*|br-*|isle-*|virbr*|wl*) continue ;; esac
                [[ -d "/sys/class/net/$l_iface/wireless" ]] && continue
                [[ "$l_ip" == 10.* ]] && leased="$leased $l_iface"
            done < <(ip -br -4 addr show)
            leased=$(echo "$leased" | xargs)
            if [[ $(echo "$leased" | wc -w) -eq 1 && -n "$leased" ]]; then
                INTERFACE="$leased"
                log_success "Detected isle-leased interface: $INTERFACE (wired, 10.x lease)"
            else

            # Strategy 2: Fallback to default route interface
            # This works when the isle cable is the main connection
            INTERFACE=$(ip route show default | awk '{print $5}' | head -n1)

            if [[ -z "$INTERFACE" ]]; then
                log_error "Could not auto-detect network interface"
                echo "  No link-up interfaces without IP found."
                echo "  No default route found."
                echo "  Specify manually with --interface <name>"
                echo ""
                echo "  Available interfaces:"
                ip -br link show | grep -v "^lo " | awk '{print "    " $1 " " $2 " " $3}'
                exit 1
            fi
            log_success "Using default route interface: $INTERFACE"
            log_warning "No dedicated isle cable detected — using main network interface"
            fi
        fi
    fi

    # Show interface info
    local iface_ip
    iface_ip=$(ip -4 addr show "$INTERFACE" | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -n1)
    local iface_mac
    iface_mac=$(ip link show "$INTERFACE" | awk '/link\/ether/ {print $2}')
    local iface_state
    iface_state=$(ip -br link show "$INTERFACE" | awk '{print $2}')

    log_info "  Interface: $INTERFACE"
    log_info "  State:     $iface_state"
    log_info "  MAC:       $iface_mac"
    log_info "  IP:        ${iface_ip:-none (awaiting DHCP)}"

    # Save interface for later cleanup
    mkdir -p "$REMOTE_DIR"
    echo "$INTERFACE" > "${REMOTE_DIR}/interface.conf"
}

# Find ethernet interfaces that have carrier (cable plugged in) but no IP address
# These are the most likely candidates for a freshly connected isle mesh cable
find_linkup_no_ip_interfaces() {
    local result=""
    while IFS= read -r line; do
        local iface state
        iface=$(echo "$line" | awk '{print $1}')
        state=$(echo "$line" | awk '{print $2}')

        # Skip loopback, virtual bridges, docker, veth, wireless
        case "$iface" in
            lo|docker*|veth*|br-*|isle-*|virbr*) continue ;;
        esac

        # Must be UP (carrier detected / cable plugged in)
        if [[ "$state" != "UP" ]]; then
            continue
        fi

        # Must be a physical ethernet (not wireless)
        if [[ -d "/sys/class/net/$iface/wireless" ]]; then
            continue
        fi

        # Must have NO IPv4 address
        local has_ip
        has_ip=$(ip -4 addr show "$iface" 2>/dev/null | grep -c 'inet ' || true)
        if [[ "$has_ip" -gt 0 ]]; then
            continue
        fi

        result="$result $iface"
    done < <(ip -br link show)

    echo "$result" | xargs  # trim whitespace
}

# Non-destructive probe: is an isle reachable on this network segment?
# Uses mDNS (openwrt.local) — instant, unprivileged, and only visible on the
# isle's own L2 segment, so it respects the isle/normal-network isolation.
# Prints a machine-readable line; never changes anything. Used by the app to
# decide whether to SUGGEST joining (the device joins itself).
detect_isle_probe() {
    local ip=""
    # Prefer avahi-resolve (mDNS-specific, fails fast). Bound every lookup with a
    # short timeout so a missing isle returns in ~2s instead of the resolver's
    # full timeout — this gets polled by the UI.
    if command -v avahi-resolve &>/dev/null; then
        ip=$(timeout 2 avahi-resolve -4 -n openwrt.local 2>/dev/null | awk '{print $2}' | head -1)
    elif command -v getent &>/dev/null; then
        ip=$(timeout 2 getent hosts openwrt.local 2>/dev/null | awk '{print $1}' | head -1)
    fi
    if [[ -n "$ip" ]]; then
        echo "isle found=true router=${ip} name=openwrt"
        return 0
    fi
    echo "isle found=false"
    return 1
}

# Listen for discovery beacon with settle period
discover_isle() {
    log_step "Step 3: Discovering Isle (settle + listen)"

    # --- Settle phase: observe existing mDNS traffic ---
    log_info "Settle phase: observing existing network traffic (5s)..."
    local existing_names=""
    if command -v avahi-browse &>/dev/null; then
        existing_names=$(timeout 5 avahi-browse -a -t -p 2>/dev/null \
            | grep '^=' | cut -d';' -f4 | sort -u || echo "")
        if [[ -n "$existing_names" ]]; then
            local name_count
            name_count=$(echo "$existing_names" | wc -l)
            log_info "Found $name_count existing mDNS name(s) on network"
        else
            log_info "No existing mDNS names detected"
        fi
    else
        log_info "avahi-browse not available, skipping mDNS settle"
        sleep 5
    fi

    # Save observed names for the remote-agent to avoid collisions
    mkdir -p "$REMOTE_DIR"
    echo "$existing_names" > "${REMOTE_DIR}/observed-mdns-names.txt"

    # --- Discovery phase: listen for router beacon ---
    log_info "Listening for discovery beacon on UDP port ${DISCOVERY_PORT}..."
    log_info "Timeout: ${TIMEOUT}s"
    echo ""

    local listener_script="${PROJECT_ROOT}/isle-agent/isle-remote-agent/discovery-listener.sh"

    if [[ ! -f "$listener_script" ]]; then
        log_error "Discovery listener script not found: $listener_script"
        exit 1
    fi

    # Run discovery listener
    if DISCOVERY_PORT="$DISCOVERY_PORT" TIMEOUT="$TIMEOUT" OUTPUT_FILE="$DISCOVERY_FILE" \
        bash "$listener_script"; then
        log_success "Isle discovered!"
    elif derive_isle_from_lease; then
        log_success "Isle derived from the interface's DHCP lease (beacon not needed)"
    else
        log_error "Failed to discover an isle"
        echo ""
        echo "  Possible causes:"
        echo "    - No isle router is broadcasting on this network"
        echo "    - Firewall blocking UDP port ${DISCOVERY_PORT}"
        echo "    - Try increasing timeout: isle join --timeout 120"
        exit 1
    fi

    # Read discovery data
    ISLE_NAME=$(jq -r '.isle_name' "$DISCOVERY_FILE")
    VLAN_ID=$(jq -r '.vlan_id' "$DISCOVERY_FILE")
    ROUTER_IP=$(jq -r '.router_ip' "$DISCOVERY_FILE")
    DHCP_RANGE=$(jq -r '.dhcp_range' "$DISCOVERY_FILE")

    echo ""
    log_info "Isle details:"
    echo "  Name:       ${ISLE_NAME}"
    echo "  VLAN ID:    ${VLAN_ID}"
    echo "  Router IP:  ${ROUTER_IP}"
    echo "  DHCP Range: ${DHCP_RANGE}"

    # --- Validate: no subnet conflict with existing interfaces ---
    validate_no_subnet_conflict
}

# FALLBACK discovery: if the chosen interface already holds an isle
# DHCP lease (a 10.x address from the isle router — how our deployed
# remotes are cabled), everything the beacon would tell us is already
# ON the interface: router = gateway, subnet = the lease's network,
# vlan = the 10.VLAN.0.x convention. The beacon path needs socat on
# the router AND an open host firewall; the lease is direct evidence.
derive_isle_from_lease() {
    local ip_cidr gw subnet vlan
    ip_cidr=$(ip -4 -o addr show "$INTERFACE" 2>/dev/null | awk '{print $4; exit}')
    [[ "$ip_cidr" == 10.* ]] || return 1
    subnet=$(ip route show dev "$INTERFACE" proto kernel 2>/dev/null | awk '{print $1; exit}')
    [[ -n "$subnet" ]] || return 1
    gw=$(ip route show dev "$INTERFACE" 2>/dev/null | awk '/via/ {print $3; exit}')
    [[ -n "$gw" ]] || gw="${subnet%.*/*}.1"
    # sanity: the isle router answers DNS on :53
    if command -v dig &>/dev/null; then
        timeout 3 dig +short "@${gw}" openwrt.isle >/dev/null 2>&1 || true
    fi
    vlan=$(echo "$gw" | cut -d. -f2)
    mkdir -p "$(dirname "$DISCOVERY_FILE")"
    cat > "$DISCOVERY_FILE" <<EOF
{"isle_name":"isle","vlan_id":${vlan:-10},"router_ip":"${gw}","dhcp_range":"${subnet}"}
EOF
    LEASE_DERIVED=1
    log_info "  lease: ${ip_cidr} on ${INTERFACE}, router ${gw}, subnet ${subnet}"
    return 0
}

# Check that the isle subnet doesn't conflict with existing interface IPs
validate_no_subnet_conflict() {
    # Lease-derived discovery: the host's own lease on this subnet IS
    # the evidence we joined from — expected, not a conflict.
    if [[ "${LEASE_DERIVED:-0}" == "1" ]]; then
        log_success "Host already holds an isle lease on $INTERFACE (lease-derived join — expected)"
        return 0
    fi
    local isle_subnet
    isle_subnet=$(echo "$DHCP_RANGE" | cut -d'/' -f1 | sed 's/\.[0-9]*$//')  # e.g. "10.10.0"

    local conflict_iface=""
    while IFS= read -r line; do
        local iface ip
        iface=$(echo "$line" | awk '{print $1}')
        ip=$(echo "$line" | awk '{print $3}' | cut -d'/' -f1)

        [[ -z "$ip" ]] && continue
        [[ "$iface" == "lo" ]] && continue

        local ip_prefix
        ip_prefix=$(echo "$ip" | sed 's/\.[0-9]*$//')

        if [[ "$ip_prefix" == "$isle_subnet" ]]; then
            conflict_iface="$iface ($ip)"
        fi
    done < <(ip -br -4 addr show)

    if [[ -n "$conflict_iface" ]]; then
        log_warning "Subnet conflict detected!"
        log_warning "  Isle DHCP range: $DHCP_RANGE"
        log_warning "  Conflicting interface: $conflict_iface"
        echo ""
        echo "  The isle subnet overlaps with an existing interface."
        echo "  This may cause routing issues."
        echo ""
        if [[ -t 0 ]]; then
            read -p "  Continue anyway? (y/N): " confirm
        else
            confirm=""  # non-interactive: do not proceed past a subnet conflict
        fi
        if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
            log_error "Aborted due to subnet conflict"
            exit 1
        fi
    else
        log_success "No subnet conflicts detected"
    fi
}

# Generate virtual MAC address and isle hostname
generate_mac() {
    local vlan_hex
    vlan_hex=$(printf '%02x' "$VLAN_ID")
    local random_hex
    random_hex=$(printf '%02x' $((RANDOM % 256)))

    VIRTUAL_MAC="02:00:00:00:${vlan_hex}:${random_hex}"

    # Save MAC for consistency across restarts
    echo "$VIRTUAL_MAC" > "${REMOTE_DIR}/mac-address"

    log_info "Generated virtual MAC: ${VIRTUAL_MAC}"

    # Generate isle hostname from the machine's real hostname
    # This is what avahi will publish as <hostname>.local on the isle network,
    # and the router's join-protocol will map to <hostname>.isle
    local machine_hostname
    machine_hostname=$(hostname -s 2>/dev/null || hostname)

    # Sanitize: lowercase, alphanumeric + hyphens only, max 63 chars
    ISLE_HOSTNAME=$(echo "$machine_hostname" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g' | cut -c1-63)

    # Save for restarts
    echo "$ISLE_HOSTNAME" > "${REMOTE_DIR}/hostname"

    log_info "Isle hostname: ${ISLE_HOSTNAME}"
    log_info "  Will be discoverable as: ${ISLE_HOSTNAME}.local / ${ISLE_HOSTNAME}.isle"
}

# Initialize agent directories
init_agent_dirs() {
    log_step "Step 4: Initializing Agent"

    mkdir -p "${ISLE_AGENT_DIR}"/{configs,ssl/{certs,keys},logs,sync-data,nginx/configs}
    mkdir -p "$REMOTE_DIR"

    # Pre-seed nginx.conf if needed
    local nginx_src="${PROJECT_ROOT}/isle-agent/isle-vlan-agent/nginx.conf"
    if [[ ! -f "${ISLE_AGENT_DIR}/nginx/nginx.conf" ]] && [[ -f "$nginx_src" ]]; then
        cp "$nginx_src" "${ISLE_AGENT_DIR}/nginx/nginx.conf"
        log_info "Pre-seeded nginx.conf"
    fi

    # Initialize registry if needed
    if [[ ! -f "${ISLE_AGENT_DIR}/registry.json" ]]; then
        cat > "${ISLE_AGENT_DIR}/registry.json" <<'EOF'
{
  "domains": {},
  "subdomains": {},
  "apps": {
    "health": {
      "domain": "health.local",
      "services": [],
      "modes": [],
      "updated_at": ""
    }
  }
}
EOF
        log_info "Created default registry"
    fi

    # Create host-agent log directory
    mkdir -p /var/log/isle-mesh

    log_success "Agent directories initialized"
}

# Setup macvlan network
setup_macvlan() {
    log_step "Step 5: Setting Up Macvlan Network"

    local macvlan_script="${PROJECT_ROOT}/isle-agent/isle-remote-agent/macvlan-setup.sh"

    if [[ ! -f "$macvlan_script" ]]; then
        log_error "Macvlan setup script not found"
        exit 1
    fi

    if bash "$macvlan_script" create --interface "$INTERFACE" --subnet "$DHCP_RANGE"; then
        log_success "Macvlan network created on $INTERFACE"
    else
        log_error "Failed to create macvlan network"
        exit 1
    fi
}

# Start remote agent container
start_remote_agent() {
    log_step "Step 6: Starting Remote Agent"

    # Write mode file
    echo "remote" > "$MODE_FILE"

    # Detect docker compose command
    local compose_cmd="docker compose"
    if ! docker compose version &>/dev/null; then
        if command -v docker-compose &>/dev/null; then
            compose_cmd="docker-compose"
        else
            log_error "Docker Compose not available"
            exit 1
        fi
    fi

    local compose_file="${PROJECT_ROOT}/isle-agent/docker-compose.remote.yml"

    # Export environment for docker-compose
    export VIRTUAL_MAC
    export ISLE_NAME
    export ISLE_HOSTNAME
    export VLAN_ID
    export ROUTER_IP
    export INTERFACE

    log_info "Starting isle-remote-agent container..."
    cd "${PROJECT_ROOT}/isle-agent"

    if $compose_cmd -f "$compose_file" up -d --build; then
        log_success "Remote agent container started"
    else
        log_error "Failed to start remote agent"
        echo "  Check logs: docker logs isle-remote-agent"
        # Cleanup on failure
        rm -f "$MODE_FILE"
        exit 1
    fi
}

# Wait for DHCP + health check
wait_for_health() {
    log_step "Step 7: Waiting for Agent Health"

    log_info "Waiting for DHCP lease and health check..."
    echo ""

    local healthy=false
    for i in $(seq 1 60); do
        # Check if container is running
        if ! docker ps --filter "name=isle-remote-agent" --filter "status=running" --format '{{.Names}}' | grep -q "isle-remote-agent"; then
            if [ $i -gt 10 ]; then
                log_error "Container stopped unexpectedly"
                echo "  Check logs: docker logs isle-remote-agent"
                return 1
            fi
            sleep 1
            continue
        fi

        # Check container health
        local status
        status=$(docker inspect isle-remote-agent --format '{{.State.Health.Status}}' 2>/dev/null || echo "starting")

        if [[ "$status" == "healthy" ]]; then
            healthy=true
            break
        fi

        # Every 10 seconds, show progress
        if (( i % 10 == 0 )); then
            log_info "Still waiting... (${i}s, status: ${status})"
        fi

        sleep 1
    done

    if ! $healthy; then
        log_warning "Agent not yet healthy after 60s (may still be waiting for DHCP)"
        echo "  Check status: docker logs isle-remote-agent"
        return 0
    fi

    # Get VLAN IP
    local vlan_ip
    vlan_ip=$(docker exec isle-remote-agent ip -4 addr show eth0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' || echo "pending")

    log_success "Remote agent is healthy"
    echo "  VLAN IP: ${vlan_ip}"
}

# Configure local DNS to forward .isle queries to the router
setup_remote_dns() {
    log_step "Step 8: Configuring .isle DNS Forwarding"

    local SPLIT_DNS="/etc/dnsmasq.d/split-dns.conf"
    local RESOLVED_CONF="/etc/systemd/resolved.conf.d/split-mdns.conf"

    # Get the router's isle subnet IP (from discovery data)
    local router_isle_ip="$ROUTER_IP"

    # Ensure dnsmasq.d directory exists
    mkdir -p /etc/dnsmasq.d

    # Remove any stale localhost .isle resolution
    if [[ -f "$SPLIT_DNS" ]] && grep -q 'address=/.isle/' "$SPLIT_DNS" 2>/dev/null; then
        sed -i '/address=\/.isle\//d' "$SPLIT_DNS"
    fi

    # Add forwarding rule if not present or update if router changed
    if [[ -f "$SPLIT_DNS" ]]; then
        if ! grep -q 'server=/.isle/' "$SPLIT_DNS"; then
            echo "server=/.isle/${router_isle_ip}" >> "$SPLIT_DNS"
            log_info "Added .isle DNS forwarding to router at $router_isle_ip"
        elif ! grep -q "server=/.isle/${router_isle_ip}" "$SPLIT_DNS"; then
            sed -i "s|server=/.isle/.*|server=/.isle/${router_isle_ip}|" "$SPLIT_DNS"
            log_info "Updated .isle DNS forwarding to $router_isle_ip"
        else
            log_info ".isle DNS forwarding already configured"
        fi
    else
        # Create minimal split-dns config
        cat > "$SPLIT_DNS" <<EOF
# Isle-Mesh split DNS (configured by isle join)
server=/.isle/${router_isle_ip}
EOF
        log_info "Created split-dns.conf with .isle forwarding to $router_isle_ip"
    fi

    # Add ~isle to systemd-resolved if available
    if [[ -f "$RESOLVED_CONF" ]]; then
        if ! grep -q '~isle' "$RESOLVED_CONF"; then
            sed -i 's/Domains=\(.*\)/Domains=\1 ~isle/' "$RESOLVED_CONF"
            log_info "Added ~isle to systemd-resolved"
        fi
    elif [[ -d "/etc/systemd/resolved.conf.d" ]]; then
        mkdir -p /etc/systemd/resolved.conf.d
        cat > "$RESOLVED_CONF" <<EOF
[Resolve]
Domains=~isle
EOF
        log_info "Created systemd-resolved config for ~isle"
    fi

    # Restart DNS services
    systemctl restart dnsmasq 2>/dev/null || true
    systemctl restart systemd-resolved 2>/dev/null || true

    log_success ".isle DNS forwarding configured (router: $router_isle_ip)"
}

# Show completion message
show_completion() {
    local vlan_ip
    vlan_ip=$(docker exec isle-remote-agent ip -4 addr show eth0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' || echo "pending")

    echo ""
    echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║             Successfully Joined Isle!                         ║${NC}"
    echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${BOLD}Isle Details:${NC}"
    echo -e "  Isle Name:   ${ISLE_NAME}"
    echo -e "  Hostname:    ${ISLE_HOSTNAME}"
    echo -e "  VLAN ID:     ${VLAN_ID}"
    echo -e "  Router IP:   ${ROUTER_IP}"
    echo -e "  VLAN IP:     ${vlan_ip}"
    echo -e "  Interface:   ${INTERFACE}"
    echo -e "  MAC:         ${VIRTUAL_MAC}"
    echo ""
    echo -e "${CYAN}Discoverable As:${NC}"
    echo -e "  ${BOLD}${ISLE_HOSTNAME}.local${NC} (mDNS)"
    echo -e "  ${BOLD}${ISLE_HOSTNAME}.isle${NC}  (router DNS, after join-protocol picks it up)"
    echo ""
    echo -e "${CYAN}Verify Reachability:${NC}"
    echo -e "  ${BOLD}isle test remote${NC}"
    echo ""
    echo -e "${CYAN}Next Steps:${NC}"
    echo -e "  1. Register an app:"
    echo -e "     ${BOLD}isle agent register --name myapp --domain myapp.local --container myapp-1 --port 8080${NC}"
    echo ""
    echo -e "  2. Check status:"
    echo -e "     ${BOLD}isle agent status${NC}"
    echo ""
    echo -e "  3. View remote agent logs:"
    echo -e "     ${BOLD}docker logs isle-remote-agent${NC}"
    echo ""
    echo -e "  4. Leave the isle:"
    echo -e "     ${BOLD}sudo isle leave${NC}"
    echo ""
}

# Main
main() {
    parse_args "$@"

    # Non-destructive detection mode: report whether an isle is present, then exit.
    # (Unprivileged, no setup — safe for the app to poll.)
    if [[ "$DETECT" == true ]]; then
        detect_isle_probe
        exit $?
    fi

    echo ""
    echo -e "${BOLD}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║          Isle Mesh - Join Remote Isle                         ║${NC}"
    echo -e "${BOLD}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    check_prerequisites
    check_mode_guard
    detect_interface
    discover_isle
    generate_mac
    init_agent_dirs
    setup_macvlan
    start_remote_agent
    wait_for_health
    setup_remote_dns
    show_completion
}

main "$@"
