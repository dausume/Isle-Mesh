#!/bin/bash

#############################################################################
# Query OpenWRT Router Status
#
# This script SSHes into the OpenWRT router to get detailed status info
# including network interfaces, connected devices, ethernet port status,
# and firewall configuration.
#
# Usage: ./query-router-status.sh [router-ip]
#
# Default router IP: 192.168.100.1
#############################################################################

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source network source detection library
if [[ -f "$SCRIPT_DIR/lib-network-sources.sh" ]]; then
    source "$SCRIPT_DIR/lib-network-sources.sh"
else
    echo "Error: lib-network-sources.sh not found" >&2
    exit 1
fi

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Configuration
ROUTER_IP="${1:-192.168.100.1}"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5"
SSH_USER="root"

# Log functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1" >&2
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1" >&2
}

log_error() {
    echo -e "${RED}[✗]${NC} $1" >&2
}

log_warning() {
    echo -e "${YELLOW}[⚠]${NC} $1" >&2
}

# Check if router is reachable
check_connectivity() {
    if ! ping -c 1 -W 2 "$ROUTER_IP" > /dev/null 2>&1; then
        log_error "Router not reachable at $ROUTER_IP"
        return 1
    fi
    return 0
}

# Check if SSH is available
check_ssh() {
    if ! timeout 3 bash -c "echo > /dev/tcp/$ROUTER_IP/22" 2>/dev/null; then
        log_warning "SSH not available on router"
        log_info "SSH may not be enabled in OpenWRT or dropbear is not running"
        return 1
    fi
    return 0
}

# Execute command on router
router_exec() {
    local CMD="$1"
    ssh $SSH_OPTS "${SSH_USER}@${ROUTER_IP}" "$CMD" 2>/dev/null
}

# Get router info
get_router_info() {
    echo -e "${BLUE}═══ Router Information ═══${NC}"
    echo ""

    # Get OpenWRT version
    local VERSION=$(router_exec "cat /etc/openwrt_release | grep DISTRIB_DESCRIPTION" | cut -d= -f2 | tr -d "'\"")
    if [[ -n "$VERSION" ]]; then
        echo -e "${GREEN}Version:${NC}       $VERSION"
    else
        echo -e "${YELLOW}Version:${NC}       Unable to query"
    fi

    # Get uptime
    local UPTIME=$(router_exec "uptime" | awk -F'up ' '{print $2}' | awk -F',' '{print $1}')
    if [[ -n "$UPTIME" ]]; then
        echo -e "${GREEN}Uptime:${NC}        $UPTIME"
    fi

    # Get load average
    local LOAD=$(router_exec "uptime" | awk -F'load average: ' '{print $2}')
    if [[ -n "$LOAD" ]]; then
        echo -e "${GREEN}Load Avg:${NC}      $LOAD"
    fi

    # Get memory info
    local MEM_INFO=$(router_exec "free | grep Mem")
    if [[ -n "$MEM_INFO" ]]; then
        local MEM_TOTAL=$(echo "$MEM_INFO" | awk '{print $2}')
        local MEM_USED=$(echo "$MEM_INFO" | awk '{print $3}')
        local MEM_FREE=$(echo "$MEM_INFO" | awk '{print $4}')
        local MEM_PERCENT=$(awk "BEGIN {printf \"%.1f\", ($MEM_USED/$MEM_TOTAL)*100}")

        echo -e "${GREEN}Memory:${NC}        $MEM_USED KB / $MEM_TOTAL KB (${MEM_PERCENT}% used)"
    fi

    echo ""
}

# Get network interface details
get_network_interfaces() {
    echo -e "${BLUE}═══ Network Interfaces (from router) ═══${NC}"
    echo ""

    # Get interface list
    local INTERFACES=$(router_exec "ls /sys/class/net" | tr '\n' ' ')

    for IFACE in $INTERFACES; do
        # Skip loopback
        [[ "$IFACE" == "lo" ]] && continue

        echo -e "${CYAN}Interface: $IFACE${NC}"

        # Get IP address
        local IP_ADDR=$(router_exec "ip addr show $IFACE" | grep "inet " | awk '{print $2}')
        if [[ -n "$IP_ADDR" ]]; then
            local IP_ONLY="${IP_ADDR%/*}"
            local IP_SOURCE=$(detect_ip_source "$IP_ONLY" "$ROUTER_IP")
            local IP_SRC_FORMATTED=$(format_source "$IP_SOURCE")
            echo -e "  IP Address:    $IP_ADDR $IP_SRC_FORMATTED"
        else
            echo "  IP Address:    Not configured"
        fi

        # Get MAC address
        local MAC=$(router_exec "cat /sys/class/net/$IFACE/address" 2>/dev/null)
        if [[ -n "$MAC" ]]; then
            local MAC_SOURCE=$(detect_mac_source "$MAC" "$ROUTER_IP")
            local MAC_SRC_FORMATTED=$(format_source "$MAC_SOURCE")
            echo -e "  MAC Address:   $MAC $MAC_SRC_FORMATTED"
        fi

        # Get link status
        local CARRIER=$(router_exec "cat /sys/class/net/$IFACE/carrier" 2>/dev/null)
        if [[ "$CARRIER" == "1" ]]; then
            echo -e "  Link Status:   ${GREEN}UP${NC}"

            # Get speed if available
            local SPEED=$(router_exec "cat /sys/class/net/$IFACE/speed" 2>/dev/null)
            if [[ -n "$SPEED" ]] && [[ "$SPEED" != "-1" ]]; then
                echo "  Link Speed:    ${SPEED} Mbps"
            fi

            # Get duplex if available
            local DUPLEX=$(router_exec "cat /sys/class/net/$IFACE/duplex" 2>/dev/null)
            if [[ -n "$DUPLEX" ]]; then
                echo "  Duplex:        $DUPLEX"
            fi
        else
            echo -e "  Link Status:   ${YELLOW}DOWN${NC}"
        fi

        # Get RX/TX stats
        local RX_BYTES=$(router_exec "cat /sys/class/net/$IFACE/statistics/rx_bytes" 2>/dev/null)
        local TX_BYTES=$(router_exec "cat /sys/class/net/$IFACE/statistics/tx_bytes" 2>/dev/null)

        if [[ -n "$RX_BYTES" ]] && [[ -n "$TX_BYTES" ]]; then
            # Convert to human readable
            local RX_MB=$(awk "BEGIN {printf \"%.2f\", $RX_BYTES/1024/1024}")
            local TX_MB=$(awk "BEGIN {printf \"%.2f\", $TX_BYTES/1024/1024}")
            echo "  RX/TX:         ${RX_MB} MB / ${TX_MB} MB"
        fi

        echo ""
    done
}

# Get connected devices from router's ARP table
get_connected_devices() {
    echo -e "${BLUE}═══ Connected Devices (from router) ═══${NC}"
    echo ""

    # Get ARP table
    local ARP_TABLE=$(router_exec "cat /proc/net/arp" | tail -n +2)

    if [[ -z "$ARP_TABLE" ]]; then
        echo -e "${YELLOW}No devices found in ARP table${NC}"
        echo ""
        return
    fi

    echo "$ARP_TABLE" | while read line; do
        local IP=$(echo "$line" | awk '{print $1}')
        local HW_TYPE=$(echo "$line" | awk '{print $2}')
        local FLAGS=$(echo "$line" | awk '{print $3}')
        local MAC=$(echo "$line" | awk '{print $4}')
        local IFACE=$(echo "$line" | awk '{print $6}')

        # Only show completed entries (not incomplete)
        if [[ "$FLAGS" == "0x0" ]]; then
            continue
        fi

        # Detect sources
        local IP_SOURCE=$(detect_ip_source "$IP" "$ROUTER_IP")
        local MAC_SOURCE=$(detect_mac_source "$MAC" "$ROUTER_IP")
        local IP_SRC_FORMATTED=$(format_source "$IP_SOURCE")
        local MAC_SRC_FORMATTED=$(format_source "$MAC_SOURCE")

        echo -e "${GREEN}Device:${NC}"
        echo -e "  IP Address:    $IP $IP_SRC_FORMATTED"
        echo -e "  MAC Address:   $MAC $MAC_SRC_FORMATTED"
        echo "  Interface:     $IFACE"
        echo ""
    done
}

# Get firewall status and rules
get_firewall_status() {
    echo -e "${BLUE}═══ Firewall Status ═══${NC}"
    echo ""

    # Check if firewall is running
    local FW_RUNNING=$(router_exec "pgrep firewall" 2>/dev/null)
    if [[ -n "$FW_RUNNING" ]]; then
        echo -e "${GREEN}Firewall:${NC}      Running"
    else
        echo -e "${YELLOW}Firewall:${NC}      Not running"
    fi

    # Get basic iptables rules count
    local FILTER_RULES=$(router_exec "iptables -L -n | grep -c '^Chain'" 2>/dev/null)
    if [[ -n "$FILTER_RULES" ]]; then
        echo -e "${GREEN}Filter Rules:${NC}  $FILTER_RULES chains configured"
    fi

    # Check for NAT rules
    local NAT_RULES=$(router_exec "iptables -t nat -L -n | grep -c MASQUERADE" 2>/dev/null)
    if [[ -n "$NAT_RULES" ]] && [[ "$NAT_RULES" -gt 0 ]]; then
        echo -e "${GREEN}NAT:${NC}           $NAT_RULES MASQUERADE rules active"
    else
        echo -e "${YELLOW}NAT:${NC}           No MASQUERADE rules"
    fi

    # Check for forwarding rules
    local FORWARD_RULES=$(router_exec "iptables -L FORWARD -n | grep -c '^ACCEPT\|^DROP\|^REJECT'" 2>/dev/null)
    if [[ -n "$FORWARD_RULES" ]]; then
        echo -e "${GREEN}Forward Rules:${NC} $FORWARD_RULES rules"
    fi

    echo ""
}

# Get DHCP status and leases
get_dhcp_status() {
    echo -e "${BLUE}═══ DHCP Status ═══${NC}"
    echo ""

    # Check if dnsmasq is running (OpenWRT's DHCP server)
    local DNSMASQ_RUNNING=$(router_exec "pgrep dnsmasq" 2>/dev/null)
    if [[ -n "$DNSMASQ_RUNNING" ]]; then
        echo -e "${GREEN}DHCP Server:${NC}   Running (dnsmasq)"

        # Get DHCP leases
        local LEASES=$(router_exec "cat /tmp/dhcp.leases" 2>/dev/null | wc -l)
        if [[ -n "$LEASES" ]] && [[ "$LEASES" -gt 0 ]]; then
            echo -e "${GREEN}Active Leases:${NC} $LEASES"
            echo ""
            echo -e "${CYAN}Recent DHCP Leases:${NC}"
            router_exec "cat /tmp/dhcp.leases" 2>/dev/null | tail -5 | while read lease; do
                local EXPIRE=$(echo "$lease" | awk '{print $1}')
                local MAC=$(echo "$lease" | awk '{print $2}')
                local IP=$(echo "$lease" | awk '{print $3}')
                local NAME=$(echo "$lease" | awk '{print $4}')

                # Detect sources
                local IP_SOURCE=$(detect_ip_source "$IP" "$ROUTER_IP")
                local MAC_SOURCE=$(detect_mac_source "$MAC" "$ROUTER_IP")
                local IP_SRC_FORMATTED=$(format_source "$IP_SOURCE")
                local MAC_SRC_FORMATTED=$(format_source "$MAC_SOURCE")

                echo -e "  $IP $IP_SRC_FORMATTED - $MAC $MAC_SRC_FORMATTED ($NAME)"
            done
        else
            echo -e "${YELLOW}Active Leases:${NC} 0"
        fi
    else
        echo -e "${YELLOW}DHCP Server:${NC}   Not running"
    fi

    echo ""
}

# Main function
main() {
    log_info "Querying router at $ROUTER_IP..."
    echo ""

    # Check connectivity
    if ! check_connectivity; then
        exit 1
    fi

    # Check if SSH is available
    if ! check_ssh; then
        log_warning "Cannot query router details without SSH access"
        log_info "Enable SSH on OpenWRT or ensure dropbear is running"
        exit 1
    fi

    log_success "Connected to router"
    echo ""

    # Get all status information
    get_router_info
    get_network_interfaces
    get_connected_devices
    get_firewall_status
    get_dhcp_status

    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  Router query complete${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
}

# Run main
main
