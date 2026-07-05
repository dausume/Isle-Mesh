#!/bin/sh
# Isle Mesh Discovery Beacon
# Runs on OpenWRT router to broadcast discovery packets
# Remote nginx proxy agents listen and auto-configure bridges

# Configuration (populated by template engine)
ISLE_NAME="{{ISLE_NAME}}"
VLAN_ID="{{VLAN_ID}}"
ROUTER_IP="{{ROUTER_IP}}"
DHCP_RANGE="{{DHCP_RANGE}}"
BROADCAST_INTERVAL="${BROADCAST_INTERVAL:-30}"
DISCOVERY_PORT="${DISCOVERY_PORT:-7878}"

LOG_FILE="/var/log/isle-discovery.log"

log_msg() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
    logger -t isle-discovery "$*"
}

# Check if socat is available, fallback to nc
check_broadcast_tool() {
    if command -v socat >/dev/null 2>&1; then
        echo "socat"
    elif command -v nc >/dev/null 2>&1; then
        echo "nc"
    else
        log_msg "ERROR: Neither socat nor nc available for broadcasting"
        return 1
    fi
}

# Broadcast discovery packet
broadcast_discovery() {
    local tool
    tool=$(check_broadcast_tool) || return 1

    local message="ISLE_MESH_DISCOVERY|isle=${ISLE_NAME}|vlan=${VLAN_ID}|router=${ROUTER_IP}|dhcp=${DHCP_RANGE}"

    # Bind the broadcast to the isle L3 interface (the bridge that holds ROUTER_IP), which
    # floods to EVERY isle port including cables added later (eth2...). A plain
    # 255.255.255.255 broadcast exits only the kernel's default interface, so a remote node
    # on a newly-added cable never receives the beacon (DHCP still works because dnsmasq
    # LISTENS per-port; the beacon is an OUTBOUND broadcast that must be steered).
    local dev
    dev="$(ip -4 -o addr show 2>/dev/null | awk -v ip="${ROUTER_IP}/" 'index($4, ip)==1 {print $2; exit}')"

    case "$tool" in
        socat)
            if [ -n "$dev" ]; then
                echo "$message" | socat - UDP4-DATAGRAM:255.255.255.255:${DISCOVERY_PORT},broadcast,so-bindtodevice="$dev" 2>/dev/null
            else
                echo "$message" | socat - UDP4-DATAGRAM:255.255.255.255:${DISCOVERY_PORT},broadcast 2>/dev/null
            fi
            ;;
        nc)
            echo "$message" | nc -u -b 255.255.255.255 ${DISCOVERY_PORT} 2>/dev/null
            ;;
    esac

    log_msg "Broadcasted on ${dev:-default}: $message"
}

# Main loop
main() {
    log_msg "Starting Isle Mesh Discovery Beacon"
    log_msg "Isle: ${ISLE_NAME}, vLAN: ${VLAN_ID}, Router: ${ROUTER_IP}"

    while true; do
        broadcast_discovery
        sleep "${BROADCAST_INTERVAL}"
    done
}

main "$@"
