#!/bin/bash
#
# Isle Remote Agent - Macvlan Network Setup
# Creates/removes Docker macvlan network on a physical interface
# for remote agent connectivity to the Isle VLAN.
#
# Usage:
#   macvlan-setup.sh create --interface eth0 --subnet 10.10.0.0/24
#   macvlan-setup.sh teardown
#

set -euo pipefail

NETWORK_NAME="isle-remote-macvlan"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }

create_network() {
    local interface=""
    local subnet=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --interface) interface="$2"; shift 2 ;;
            --subnet)    subnet="$2"; shift 2 ;;
            *) log_error "Unknown option: $1"; return 1 ;;
        esac
    done

    if [[ -z "$interface" ]]; then
        log_error "Missing required --interface argument"
        return 1
    fi

    if [[ -z "$subnet" ]]; then
        log_error "Missing required --subnet argument"
        return 1
    fi

    # Verify interface exists
    if ! ip link show "$interface" &>/dev/null; then
        log_error "Network interface '$interface' not found"
        log_info "Available interfaces:"
        ip -br link show | grep -v "^lo " | awk '{print "  " $1}'
        return 1
    fi

    # Check if network already exists
    if docker network inspect "$NETWORK_NAME" &>/dev/null; then
        log_warn "Docker network '$NETWORK_NAME' already exists"

        # Verify it's using the correct interface
        local current_parent
        current_parent=$(docker network inspect "$NETWORK_NAME" --format '{{index .Options "parent"}}' 2>/dev/null || echo "")

        if [[ "$current_parent" == "$interface" ]]; then
            log_success "Network already configured on interface '$interface'"
            return 0
        else
            log_warn "Network uses different interface: $current_parent (expected: $interface)"
            log_info "Removing and recreating..."
            teardown_network
        fi
    fi

    log_info "Creating macvlan network '$NETWORK_NAME' on interface '$interface'..."
    log_info "  Subnet: $subnet"

    # Confine docker-IPAM's TRANSIENT static assignment (the entrypoint
    # flushes it and DHCPs a real lease) to the subnet's top /30 —
    # letting IPAM roam the whole subnet self-assigned the core
    # agent's .2 on a second host: an active IP conflict.
    local base
    base=$(echo "$subnet" | cut -d/ -f1 | cut -d. -f1-3)
    docker network create \
        --driver macvlan \
        --opt parent="$interface" \
        --subnet "$subnet" \
        --ip-range "${base}.252/30" \
        "$NETWORK_NAME"

    log_success "Macvlan network created"
    docker network inspect "$NETWORK_NAME" --format '  Network ID: {{.ID}}'
}

teardown_network() {
    if ! docker network inspect "$NETWORK_NAME" &>/dev/null; then
        log_info "Network '$NETWORK_NAME' does not exist, nothing to remove"
        return 0
    fi

    # Check for connected containers
    local connected
    connected=$(docker network inspect "$NETWORK_NAME" --format '{{range $k,$v := .Containers}}{{$v.Name}} {{end}}' 2>/dev/null || echo "")

    if [[ -n "$connected" ]]; then
        log_warn "Disconnecting containers: $connected"
        for container in $connected; do
            docker network disconnect -f "$NETWORK_NAME" "$container" 2>/dev/null || true
        done
    fi

    log_info "Removing Docker network '$NETWORK_NAME'..."
    docker network rm "$NETWORK_NAME"
    log_success "Macvlan network removed"
}

show_status() {
    if docker network inspect "$NETWORK_NAME" &>/dev/null; then
        log_success "Network '$NETWORK_NAME' exists"
        docker network inspect "$NETWORK_NAME" --format \
            "  Driver: {{.Driver}}
  Parent: {{index .Options \"parent\"}}
  Subnet: {{range .IPAM.Config}}{{.Subnet}}{{end}}"

        local connected
        connected=$(docker network inspect "$NETWORK_NAME" --format '{{range $k,$v := .Containers}}{{$v.Name}} ({{$v.IPv4Address}}) {{end}}' 2>/dev/null || echo "")
        if [[ -n "$connected" ]]; then
            echo "  Connected: $connected"
        else
            echo "  Connected: (none)"
        fi
    else
        log_info "Network '$NETWORK_NAME' does not exist"
    fi
}

case "${1:-help}" in
    create)
        shift
        create_network "$@"
        ;;
    teardown|remove|destroy)
        teardown_network
        ;;
    status)
        show_status
        ;;
    help|--help|-h)
        cat <<EOF
Isle Remote Agent - Macvlan Network Setup

Usage:
  $(basename "$0") create --interface <iface> --subnet <cidr>
  $(basename "$0") teardown
  $(basename "$0") status

Commands:
  create      Create macvlan network on specified interface
  teardown    Remove macvlan network and disconnect containers
  status      Show current network status

Options (for create):
  --interface   Physical network interface (e.g., eth0, enp3s0)
  --subnet      VLAN subnet in CIDR notation (e.g., 10.10.0.0/24)

EOF
        ;;
    *)
        log_error "Unknown command: $1"
        echo "Run '$(basename "$0") help' for usage"
        exit 1
        ;;
esac
