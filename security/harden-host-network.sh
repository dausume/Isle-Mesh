#!/bin/bash
#
# Isle-Mesh Host Network Hardening
# Ensures isle-mesh traffic is invisible to the ISP-facing interface.
#
# What this does:
#   1. Restricts avahi/mDNS to internal interfaces only (not WiFi/ethernet to ISP)
#   2. Adds iptables rules to block isle-mesh traffic on the ISP interface
#   3. Blocks external access to isle-mesh ports (80, 443, 7878, 5353)
#   4. Prevents isle subnet traffic from being routed to the ISP
#
# Usage: sudo bash harden-host-network.sh [--apply | --check | --remove]

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
log_pass()    { echo -e "${GREEN}[OK]${NC} $*"; }
log_fail()    { echo -e "${RED}[!!]${NC} $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }

# Detect the ISP-facing interface (the one with the default route)
detect_isp_interface() {
    ip route show default | awk '{print $5}' | head -1
}

# Get all isle-mesh internal interfaces
get_isle_interfaces() {
    echo "br-mgmt isle-br-0 lo"
}

IPTABLES_CHAIN="ISLE-MESH-BLOCK"

check_hardening() {
    echo -e "${BOLD}=== Isle-Mesh Host Network Security Check ===${NC}"
    echo ""

    local isp_iface
    isp_iface=$(detect_isp_interface)
    log_info "ISP-facing interface: ${isp_iface:-NONE}"
    echo ""

    # 1. Check avahi interface restrictions
    echo -e "${BOLD}1. mDNS / Avahi${NC}"
    local avahi_conf="/etc/avahi/avahi-daemon.conf"
    if [[ -f "$avahi_conf" ]]; then
        if grep -q "^deny-interfaces=" "$avahi_conf"; then
            local denied
            denied=$(grep "^deny-interfaces=" "$avahi_conf" | cut -d= -f2)
            if echo "$denied" | grep -q "$isp_iface"; then
                log_pass "Avahi denies ISP interface ($isp_iface)"
            else
                log_fail "Avahi deny-interfaces exists but doesn't include $isp_iface"
            fi
        elif grep -q "^allow-interfaces=" "$avahi_conf"; then
            local allowed
            allowed=$(grep "^allow-interfaces=" "$avahi_conf" | cut -d= -f2)
            if ! echo "$allowed" | grep -q "$isp_iface"; then
                log_pass "Avahi allow-interfaces excludes ISP interface"
            else
                log_fail "Avahi allow-interfaces includes $isp_iface"
            fi
        else
            log_fail "Avahi has no interface restrictions — broadcasts on all interfaces"
        fi
    else
        log_warn "Avahi config not found"
    fi

    # 2. Check port bindings
    echo ""
    echo -e "${BOLD}2. Port Exposure${NC}"

    local exposed_80
    exposed_80=$(ss -tlnp 2>/dev/null | grep ':80 ' | grep '0.0.0.0' || echo "")
    local exposed_443
    exposed_443=$(ss -tlnp 2>/dev/null | grep ':443 ' | grep '0.0.0.0' || echo "")

    if [[ -z "$exposed_80" ]] && [[ -z "$exposed_443" ]]; then
        log_pass "Ports 80/443 not exposed on 0.0.0.0"
    else
        [[ -n "$exposed_80" ]] && log_fail "Port 80 exposed on 0.0.0.0 (visible to ISP)"
        [[ -n "$exposed_443" ]] && log_fail "Port 443 exposed on 0.0.0.0 (visible to ISP)"
    fi

    local exposed_7878
    exposed_7878=$(ss -ulnp 2>/dev/null | grep ':7878' | grep '0.0.0.0' || echo "")
    if [[ -z "$exposed_7878" ]]; then
        log_pass "Discovery port 7878 not exposed on host"
    else
        log_fail "Discovery port 7878 exposed on 0.0.0.0"
    fi

    # 3. Check iptables rules
    echo ""
    echo -e "${BOLD}3. Firewall Rules${NC}"

    if iptables -L "$IPTABLES_CHAIN" -n &>/dev/null; then
        log_pass "Isle-mesh firewall chain exists"
    else
        log_fail "No isle-mesh firewall rules (run with --apply)"
    fi

    # 4. Check routing
    echo ""
    echo -e "${BOLD}4. Routing Isolation${NC}"

    if ip route show | grep -q "10.10.0.0.*$isp_iface"; then
        log_fail "Isle subnet (10.10.0.0/24) has a route via ISP interface"
    else
        log_pass "No route from isle subnet to ISP interface"
    fi

    if ip route show | grep -q "192.168.1.0.*$isp_iface"; then
        log_fail "Management subnet has a route via ISP interface"
    else
        log_pass "No route from management subnet to ISP interface"
    fi

    # 5. Check OpenWRT VM isolation
    echo ""
    echo -e "${BOLD}5. Router VM Isolation${NC}"

    local router_default
    router_default=$(ssh -i /etc/isle-mesh/router/ssh/isle_router_key \
        -o BatchMode=yes -o ConnectTimeout=3 -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        root@192.168.1.1 "ip route show default" 2>/dev/null || echo "")

    if [[ -z "$router_default" ]]; then
        log_pass "Router has no default gateway (air-gapped from ISP)"
    else
        log_fail "Router has a default gateway: $router_default"
    fi

    echo ""
}

apply_hardening() {
    local isp_iface
    isp_iface=$(detect_isp_interface)

    if [[ -z "$isp_iface" ]]; then
        log_fail "Cannot detect ISP-facing interface"
        exit 1
    fi

    echo -e "${BOLD}Applying isle-mesh network hardening...${NC}"
    echo ""
    log_info "ISP-facing interface: $isp_iface"
    echo ""

    # 1. Restrict avahi to internal interfaces
    log_info "Restricting avahi to internal interfaces..."
    local avahi_conf="/etc/avahi/avahi-daemon.conf"

    if [[ -f "$avahi_conf" ]]; then
        # Remove any existing allow/deny lines
        sed -i '/^allow-interfaces=/d' "$avahi_conf"
        sed -i '/^deny-interfaces=/d' "$avahi_conf"
        # Add deny for ISP interface under [server] section
        sed -i "/^\[server\]/a deny-interfaces=$isp_iface" "$avahi_conf"
        systemctl restart avahi-daemon 2>/dev/null || true
        log_pass "Avahi now denies $isp_iface"
    else
        log_warn "Avahi config not found — skipping"
    fi

    # 2. Create iptables chain for isle-mesh protection
    log_info "Setting up firewall rules..."

    # Create chain if it doesn't exist
    iptables -N "$IPTABLES_CHAIN" 2>/dev/null || iptables -F "$IPTABLES_CHAIN"

    # Block inbound to isle-mesh ports from ISP interface
    iptables -A "$IPTABLES_CHAIN" -i "$isp_iface" -p tcp --dport 80 -j DROP
    iptables -A "$IPTABLES_CHAIN" -i "$isp_iface" -p tcp --dport 443 -j DROP
    iptables -A "$IPTABLES_CHAIN" -i "$isp_iface" -p udp --dport 7878 -j DROP
    iptables -A "$IPTABLES_CHAIN" -i "$isp_iface" -p udp --dport 5353 -j DROP

    # Block outbound isle subnet traffic to ISP
    iptables -A "$IPTABLES_CHAIN" -o "$isp_iface" -s 10.10.0.0/24 -j DROP
    iptables -A "$IPTABLES_CHAIN" -o "$isp_iface" -s 192.168.1.0/24 -j DROP

    # Block forwarding from isle to ISP
    iptables -A "$IPTABLES_CHAIN" -i br-mgmt -o "$isp_iface" -j DROP
    iptables -A "$IPTABLES_CHAIN" -i isle-br-0 -o "$isp_iface" -j DROP

    # Insert chain into INPUT and FORWARD
    if ! iptables -C INPUT -j "$IPTABLES_CHAIN" 2>/dev/null; then
        iptables -I INPUT 1 -j "$IPTABLES_CHAIN"
    fi
    if ! iptables -C FORWARD -j "$IPTABLES_CHAIN" 2>/dev/null; then
        iptables -I FORWARD 1 -j "$IPTABLES_CHAIN"
    fi
    if ! iptables -C OUTPUT -j "$IPTABLES_CHAIN" 2>/dev/null; then
        iptables -I OUTPUT 1 -j "$IPTABLES_CHAIN"
    fi

    log_pass "Firewall rules applied"

    # 3. Persist iptables rules
    if command -v netfilter-persistent &>/dev/null; then
        netfilter-persistent save 2>/dev/null || true
        log_info "Firewall rules saved via netfilter-persistent"
    elif command -v iptables-save &>/dev/null; then
        mkdir -p /etc/iptables
        iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
        log_info "Firewall rules saved to /etc/iptables/rules.v4"
    else
        log_warn "Cannot persist firewall rules — they will be lost on reboot"
        log_info "Install: sudo apt-get install iptables-persistent"
    fi

    echo ""
    log_pass "Host network hardening applied"
    echo ""

    # Run check to verify
    check_hardening
}

remove_hardening() {
    echo -e "${BOLD}Removing isle-mesh network hardening...${NC}"
    echo ""

    # Remove iptables chain
    iptables -D INPUT -j "$IPTABLES_CHAIN" 2>/dev/null || true
    iptables -D FORWARD -j "$IPTABLES_CHAIN" 2>/dev/null || true
    iptables -D OUTPUT -j "$IPTABLES_CHAIN" 2>/dev/null || true
    iptables -F "$IPTABLES_CHAIN" 2>/dev/null || true
    iptables -X "$IPTABLES_CHAIN" 2>/dev/null || true
    log_pass "Firewall rules removed"

    # Restore avahi
    local avahi_conf="/etc/avahi/avahi-daemon.conf"
    if [[ -f "$avahi_conf" ]]; then
        sed -i '/^deny-interfaces=/d' "$avahi_conf"
        systemctl restart avahi-daemon 2>/dev/null || true
        log_pass "Avahi interface restrictions removed"
    fi

    echo ""
    log_pass "Hardening removed"
}

case "${1:---check}" in
    --apply|apply)
        if [[ $EUID -ne 0 ]]; then
            echo "Must run as root: sudo $0 --apply"
            exit 1
        fi
        apply_hardening
        ;;
    --remove|remove)
        if [[ $EUID -ne 0 ]]; then
            echo "Must run as root: sudo $0 --remove"
            exit 1
        fi
        remove_hardening
        ;;
    --check|check)
        check_hardening
        ;;
    --help|help|-h)
        cat <<EOF
Isle-Mesh Host Network Hardening

Usage: sudo $0 [--apply | --check | --remove]

  --check    Show current security status (default)
  --apply    Apply hardening (restrict avahi, add firewall rules)
  --remove   Remove hardening (restore defaults)

What it protects against:
  - ISP seeing isle-mesh services on ports 80/443
  - mDNS broadcasts leaking to ISP network
  - Isle subnet traffic routing to ISP
  - Discovery beacons reaching ISP network

EOF
        ;;
esac
