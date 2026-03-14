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

# Auto-detect default network interface
detect_interface() {
    log_step "Step 2: Detecting Network Interface"

    if [[ -n "$INTERFACE" ]]; then
        # Verify specified interface exists
        if ! ip link show "$INTERFACE" &>/dev/null; then
            log_error "Specified interface '$INTERFACE' not found"
            echo "  Available interfaces:"
            ip -br link show | grep -v "^lo " | awk '{print "    " $1}'
            exit 1
        fi
        log_success "Using specified interface: $INTERFACE"
    else
        # Auto-detect from default route
        INTERFACE=$(ip route show default | awk '{print $5}' | head -n1)

        if [[ -z "$INTERFACE" ]]; then
            log_error "Could not auto-detect network interface"
            echo "  No default route found. Specify manually with --interface"
            exit 1
        fi
        log_success "Auto-detected interface: $INTERFACE"
    fi

    # Show interface info
    local iface_ip
    iface_ip=$(ip -4 addr show "$INTERFACE" | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -n1)
    log_info "  Interface IP: ${iface_ip:-none}"

    # Save interface for later cleanup
    mkdir -p "$REMOTE_DIR"
    echo "$INTERFACE" > "${REMOTE_DIR}/interface.conf"
}

# Listen for discovery beacon
discover_isle() {
    log_step "Step 3: Discovering Isle"

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
}

# Generate virtual MAC address
generate_mac() {
    local vlan_hex
    vlan_hex=$(printf '%02x' "$VLAN_ID")
    local random_hex
    random_hex=$(printf '%02x' $((RANDOM % 256)))

    VIRTUAL_MAC="02:00:00:00:${vlan_hex}:${random_hex}"

    # Save MAC for consistency across restarts
    echo "$VIRTUAL_MAC" > "${REMOTE_DIR}/mac-address"

    log_info "Generated virtual MAC: ${VIRTUAL_MAC}"
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
    echo -e "  VLAN ID:     ${VLAN_ID}"
    echo -e "  Router IP:   ${ROUTER_IP}"
    echo -e "  VLAN IP:     ${vlan_ip}"
    echo -e "  Interface:   ${INTERFACE}"
    echo -e "  MAC:         ${VIRTUAL_MAC}"
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
    show_completion
}

main "$@"
