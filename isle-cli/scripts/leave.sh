#!/bin/bash
#
# Isle Leave Command
# Tears down a remote agent and disconnects from the isle.
#

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$CLI_DIR")"

# Configuration
ISLE_AGENT_DIR="/etc/isle-mesh/agent"
REMOTE_DIR="${ISLE_AGENT_DIR}/remote"
MODE_FILE="${ISLE_AGENT_DIR}/agent.mode"
REMOTE_CONTAINER="isle-remote-agent"
MACVLAN_NETWORK="isle-remote-macvlan"

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

show_help() {
    cat <<EOF
${BOLD}Isle Leave${NC} - Leave an isle and tear down the remote agent

${CYAN}USAGE:${NC}
  isle leave [options]

${CYAN}OPTIONS:${NC}
  --force, -f    Skip confirmation prompt
  --help, -h     Show this help message

${CYAN}DESCRIPTION:${NC}
  Tears down the remote agent and disconnects from the isle:
    1. Stops the isle-remote-agent container
    2. Stops the isle-host-agent service (if running)
    3. Removes the isle-remote-macvlan Docker network
    4. Cleans up remote state files
    5. Clears agent mode

${CYAN}EXAMPLES:${NC}
  sudo isle leave            # Leave with confirmation
  sudo isle leave --force    # Leave without confirmation

EOF
}

# Parse arguments
FORCE=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --force|-f)
            FORCE=true
            shift
            ;;
        --help|-h)
            show_help
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            echo "Use 'isle leave --help' for usage"
            exit 1
            ;;
    esac
done

# Guard: refuse if not in remote mode
check_mode_guard() {
    if [[ -f "$MODE_FILE" ]]; then
        local current_mode
        current_mode=$(cat "$MODE_FILE" 2>/dev/null)

        if [[ "$current_mode" != "remote" ]]; then
            log_error "This machine is not in remote mode (current: ${current_mode:-none})"
            echo "  'isle leave' is only for machines that joined an isle with 'isle join'"
            echo "  To tear down a core isle, use: sudo isle destroy"
            exit 1
        fi
    else
        # No mode file — check if remote container exists anyway
        if ! docker ps -a --filter "name=${REMOTE_CONTAINER}" --format '{{.Names}}' | grep -q "^${REMOTE_CONTAINER}$"; then
            log_error "No remote agent found — this machine has not joined an isle"
            echo "  Join an isle first with: sudo isle join"
            exit 1
        fi
        log_warning "No mode file found, but remote container exists. Proceeding with cleanup."
    fi
}

# Check root
if [[ $EUID -ne 0 ]]; then
    log_error "This command requires root privileges"
    echo "  Run with: sudo isle leave"
    exit 1
fi

echo ""
echo -e "${BOLD}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║          Isle Mesh - Leave Isle                               ║${NC}"
echo -e "${BOLD}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""

check_mode_guard

# Show what isle we're leaving
if [[ -f "${REMOTE_DIR}/discovery.json" ]]; then
    ISLE_NAME=$(jq -r '.isle_name // "unknown"' "${REMOTE_DIR}/discovery.json" 2>/dev/null)
    echo -e "  Leaving isle: ${BOLD}${ISLE_NAME}${NC}"
    echo ""
fi

# Confirmation
if [[ "$FORCE" != "true" ]]; then
    echo -e "${YELLOW}This will disconnect from the isle and stop serving apps on the VLAN.${NC}"
    read -p "Are you sure? (yes/no): " CONFIRM
    if [[ "$CONFIRM" != "yes" ]]; then
        echo -e "${GREEN}Leave cancelled.${NC}"
        exit 0
    fi
    echo ""
fi

# Step 1: Stop remote agent container
echo -e "${CYAN}[1/6] Stopping remote agent container...${NC}"
if docker ps -a --filter "name=${REMOTE_CONTAINER}" --format '{{.Names}}' | grep -q "^${REMOTE_CONTAINER}$"; then
    docker stop "$REMOTE_CONTAINER" 2>/dev/null || true
    docker rm "$REMOTE_CONTAINER" 2>/dev/null || true
    log_success "Remote agent container stopped and removed"
else
    log_info "Remote agent container not found (already removed)"
fi
echo ""

# Step 2: Stop host agent service
echo -e "${CYAN}[2/6] Stopping host agent service...${NC}"
if systemctl is-active --quiet isle-host-agent 2>/dev/null; then
    systemctl stop isle-host-agent 2>/dev/null || true
    log_success "Host agent service stopped"
else
    log_info "Host agent service not running"
fi
echo ""

# Step 3: Remove macvlan network
echo -e "${CYAN}[3/6] Removing macvlan network...${NC}"
if docker network inspect "$MACVLAN_NETWORK" &>/dev/null; then
    # Disconnect any remaining containers
    connected=$(docker network inspect "$MACVLAN_NETWORK" --format '{{range $k,$v := .Containers}}{{$v.Name}} {{end}}' 2>/dev/null || echo "")
    if [[ -n "$connected" ]]; then
        for container in $connected; do
            docker network disconnect -f "$MACVLAN_NETWORK" "$container" 2>/dev/null || true
        done
    fi
    docker network rm "$MACVLAN_NETWORK" 2>/dev/null || true
    log_success "Macvlan network removed"
else
    log_info "Macvlan network not found (already removed)"
fi

# Also clean up isle-agent-net if no other containers use it
if docker network inspect isle-agent-net &>/dev/null; then
    agent_net_containers=$(docker network inspect isle-agent-net --format '{{range $k,$v := .Containers}}{{$v.Name}} {{end}}' 2>/dev/null || echo "")
    if [[ -z "$agent_net_containers" ]]; then
        docker network rm isle-agent-net 2>/dev/null || true
        log_info "Cleaned up unused isle-agent-net"
    fi
fi
echo ""

# Step 4: Clean up remote state
echo -e "${CYAN}[4/6] Cleaning up remote state...${NC}"
if [[ -d "$REMOTE_DIR" ]]; then
    rm -rf "$REMOTE_DIR"
    log_success "Remote state directory removed"
else
    log_info "No remote state to clean up"
fi
echo ""

# Step 5: Remove .isle DNS forwarding
echo -e "${CYAN}[5/6] Removing .isle DNS forwarding...${NC}"
SPLIT_DNS="/etc/dnsmasq.d/split-dns.conf"
if [[ -f "$SPLIT_DNS" ]] && grep -q 'server=/.isle/' "$SPLIT_DNS" 2>/dev/null; then
    sed -i '/server=\/.isle\//d' "$SPLIT_DNS"
    systemctl restart dnsmasq 2>/dev/null || true
    log_success "Removed .isle DNS forwarding"
else
    log_info "No .isle DNS forwarding to remove"
fi
# Remove ~isle from systemd-resolved
RESOLVED_CONF="/etc/systemd/resolved.conf.d/split-mdns.conf"
if [[ -f "$RESOLVED_CONF" ]] && grep -q '~isle' "$RESOLVED_CONF" 2>/dev/null; then
    sed -i 's/ ~isle//g' "$RESOLVED_CONF"
    systemctl restart systemd-resolved 2>/dev/null || true
    log_info "Removed ~isle from systemd-resolved"
fi
echo ""

# Step 6: Clear agent mode
echo -e "${CYAN}[6/6] Clearing agent mode...${NC}"
if [[ -f "$MODE_FILE" ]]; then
    rm -f "$MODE_FILE"
    log_success "Agent mode cleared"
else
    log_info "No mode file to clear"
fi
echo ""

# Summary
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║              Successfully Left Isle                          ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  All remote agent components have been removed."
echo ""
echo -e "  To rejoin: ${BOLD}sudo isle join${NC}"
echo -e "  To create a new isle: ${BOLD}sudo isle create${NC}"
echo ""
