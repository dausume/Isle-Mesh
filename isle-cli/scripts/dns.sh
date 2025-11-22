#!/bin/bash
# Isle DNS Namespace Router
# Routes DNS-related commands (router-managed .vlan domains)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

show_help() {
    echo -e "${BOLD}Isle DNS Namespace${NC}"
    echo ""
    echo "Manage router DNS infrastructure (.vlan domain resolution via dnsmasq)."
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                   DNS LAYER OVERVIEW                          ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    echo "The DNS layer runs ${BOLD}ON THE OPENWRT ROUTER${NC} and provides:"
    echo "  • ${GREEN}.vlan${NC} domain resolution via dnsmasq"
    echo "  • Join protocol: discovers .local (mDNS) → creates .vlan (DNS)"
    echo "  • Centralized DNS server for the entire mesh network"
    echo ""
    echo -e "${BOLD}How it works:${NC}"
    echo "  1. Router runs dnsmasq as authoritative DNS server"
    echo "  2. Other devices point their DNS to the router's IP"
    echo "  3. Router resolves .vlan domains → returns IP addresses"
    echo "  4. All mesh devices can resolve all .vlan domains"
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                       COMMANDS                                ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    echo -e "${CYAN}isle dns discover${NC}      Discover services from router perspective"
    echo "                       Shows both .local (mDNS) and .vlan (DNS) domains"
    echo ""
    echo -e "${CYAN}isle dns status${NC}        Show DNS configuration and status"
    echo "                       Displays dnsmasq config and active .vlan mappings"
    echo ""
    echo -e "${CYAN}isle dns sync${NC}          Force join protocol to update DNS immediately"
    echo "                       Manually trigger .local → .vlan synchronization"
    echo ""
    echo -e "${CYAN}isle dns list${NC}          List all .vlan DNS entries"
    echo "                       Show current DNS mappings from dnsmasq"
    echo ""
    echo -e "${CYAN}isle dns verify${NC}        Test DNS resolution from router"
    echo "                       Verify .vlan domains are resolving correctly"
    echo ""
    echo -e "${CYAN}isle dns get-ip${NC}        Get router IP address for DNS configuration"
    echo "                       Shows the IP to use as DNS server on other devices"
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                    UNDERSTANDING DNS                          ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    echo -e "${BOLD}mDNS vs DNS:${NC}"
    echo "  • ${GREEN}.local domains${NC} = mDNS (Avahi broadcasts, peer-to-peer)"
    echo "  • ${GREEN}.vlan domains${NC}  = DNS (dnsmasq on router, centralized)"
    echo ""
    echo -e "${BOLD}Join Protocol Workflow:${NC}"
    echo "  1. Physical machines broadcast myserver.local via mDNS (Avahi)"
    echo "  2. Router's join protocol discovers via avahi-browse every 30s"
    echo "  3. Creates DNS mapping: myserver.local → myserver.vlan (same IP)"
    echo "  4. Writes to /etc/dnsmasq.d/isle-vlan-domains.conf"
    echo "  5. dnsmasq serves DNS queries for .vlan domains"
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                    QUICK START                                ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    echo -e "${YELLOW}1. Get router IP for DNS configuration:${NC}"
    echo "   isle dns get-ip"
    echo ""
    echo -e "${YELLOW}2. Check DNS status:${NC}"
    echo "   isle dns status"
    echo ""
    echo -e "${YELLOW}3. Discover services from router:${NC}"
    echo "   isle dns discover"
    echo ""
    echo -e "${YELLOW}4. Force DNS sync if changes aren't appearing:${NC}"
    echo "   isle dns sync"
    echo ""
    echo -e "${YELLOW}5. Verify DNS resolution:${NC}"
    echo "   isle dns verify"
    echo ""
    echo "For physical mDNS management, see: ${CYAN}isle mdns help${NC}"
}

COMMAND=${1:-help}
shift || true

case $COMMAND in
    get-ip)
        # Get router IP for DNS configuration
        echo -e "${BOLD}Router DNS Server IP${NC}"
        echo ""

        if ! virsh list --all 2>/dev/null | grep -q "openwrt-isle-router.*running"; then
            echo -e "${RED}✗ OpenWRT router is not running${NC}"
            echo ""
            echo "Start the router with: ${CYAN}isle router init${NC}"
            exit 1
        fi

        ROUTER_IP=$(virsh domifaddr openwrt-isle-router 2>/dev/null | grep -oP '(\d+\.){3}\d+' | head -1)
        if [ -z "$ROUTER_IP" ]; then
            echo -e "${RED}Error: Could not determine router IP${NC}"
            exit 1
        fi

        echo -e "${GREEN}✓ Router IP: ${ROUTER_IP}${NC}"
        echo ""
        echo -e "${BOLD}To use this router as your DNS server:${NC}"
        echo ""
        echo -e "${YELLOW}On Linux (NetworkManager):${NC}"
        echo "  nmcli con mod <connection-name> ipv4.dns \"${ROUTER_IP}\""
        echo "  nmcli con up <connection-name>"
        echo ""
        echo -e "${YELLOW}On Linux (systemd-resolved):${NC}"
        echo "  Edit /etc/systemd/resolved.conf:"
        echo "  [Resolve]"
        echo "  DNS=${ROUTER_IP}"
        echo "  Then: sudo systemctl restart systemd-resolved"
        echo ""
        echo -e "${YELLOW}On Linux (manual /etc/resolv.conf):${NC}"
        echo "  echo \"nameserver ${ROUTER_IP}\" | sudo tee /etc/resolv.conf"
        echo ""
        echo -e "${YELLOW}On macOS:${NC}"
        echo "  System Preferences → Network → Advanced → DNS"
        echo "  Add DNS server: ${ROUTER_IP}"
        echo ""
        echo -e "${YELLOW}On Windows:${NC}"
        echo "  Network Adapter Settings → Properties → IPv4 → Properties"
        echo "  Use the following DNS server addresses: ${ROUTER_IP}"
        echo ""
        echo -e "${CYAN}ℹ${NC}  After configuring, test with: ${CYAN}isle dns verify${NC}"
        ;;

    discover)
        # Discover services from router perspective (both .local and .vlan)
        echo -e "${BOLD}Discovering services from router perspective...${NC}"
        echo ""

        # Check if router is running
        if ! virsh list --all 2>/dev/null | grep -q "openwrt-isle-router.*running"; then
            echo -e "${RED}Error: OpenWRT router is not running${NC}"
            echo "Start the router with: ${CYAN}isle router init${NC}"
            exit 1
        fi

        # Get router IP
        ROUTER_IP=$(virsh domifaddr openwrt-isle-router 2>/dev/null | grep -oP '(\d+\.){3}\d+' | head -1)
        if [ -z "$ROUTER_IP" ]; then
            echo -e "${RED}Error: Could not determine router IP${NC}"
            exit 1
        fi

        echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
        echo -e "${BLUE}║${NC}  Router DNS Discovery (${ROUTER_IP})"
        echo -e "${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
        echo ""

        # SSH into router and discover
        if ! ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            root@${ROUTER_IP} << 'EOFSSH' 2>/dev/null
# Colors for router output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}▸ mDNS Services (.local)${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Discover .local domains via avahi-browse
if command -v avahi-browse >/dev/null 2>&1; then
    timeout 3 avahi-browse -at 2>/dev/null | grep "\.local" | grep -v "^=" | awk '{print $4}' | sort -u | while read -r service; do
        if [ -n "$service" ]; then
            echo -e "  ${GREEN}✓${NC} ${service}.local"
        fi
    done
else
    echo -e "  ${YELLOW}⚠${NC} avahi-browse not available on router"
fi

echo ""
echo -e "${CYAN}▸ DNS Mappings (.vlan)${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Read dnsmasq .vlan domains
if [ -f /etc/dnsmasq.d/isle-vlan-domains.conf ]; then
    grep "^address=" /etc/dnsmasq.d/isle-vlan-domains.conf 2>/dev/null | while read -r line; do
        domain=$(echo "$line" | sed 's/address=\/\([^/]*\)\/.*/\1/')
        ip=$(echo "$line" | sed 's/.*\/\([0-9.]*\)/\1/')
        echo -e "  ${GREEN}✓${NC} ${domain} → ${ip}"
    done

    count=$(grep -c "^address=" /etc/dnsmasq.d/isle-vlan-domains.conf 2>/dev/null || echo 0)
    echo ""
    echo -e "  ${CYAN}ℹ${NC} Total .vlan domains: ${count}"
else
    echo -e "  ${YELLOW}⚠${NC} No .vlan domains configured yet"
    echo -e "  ${CYAN}ℹ${NC} The join protocol will create these automatically"
fi

EOFSSH
        then
            echo -e "${RED}Error: Could not SSH to router${NC}"
            exit 1
        fi
        ;;

    status)
        # Show DNS configuration and status
        echo -e "${BOLD}DNS Configuration Status${NC}"
        echo ""

        # Check if router is running
        if ! virsh list --all 2>/dev/null | grep -q "openwrt-isle-router.*running"; then
            echo -e "${RED}✗ OpenWRT router is not running${NC}"
            echo ""
            echo "Start the router with: ${CYAN}isle router init${NC}"
            exit 1
        fi

        echo -e "${GREEN}✓ OpenWRT router is running${NC}"

        # Get router IP
        ROUTER_IP=$(virsh domifaddr openwrt-isle-router 2>/dev/null | grep -oP '(\d+\.){3}\d+' | head -1)
        if [ -n "$ROUTER_IP" ]; then
            echo -e "${GREEN}✓ Router IP: ${ROUTER_IP}${NC}"
        fi

        echo ""

        # Check dnsmasq status
        ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            root@${ROUTER_IP} << 'EOFSSH' 2>/dev/null

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}▸ dnsmasq Status${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

if /etc/init.d/dnsmasq status >/dev/null 2>&1; then
    echo -e "  ${GREEN}✓${NC} dnsmasq is running"
else
    echo -e "  ${RED}✗${NC} dnsmasq is not running"
fi

echo ""
echo -e "${CYAN}▸ Join Protocol Status${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

if pgrep -f "join-protocol.sh" >/dev/null 2>&1; then
    echo -e "  ${GREEN}✓${NC} Join protocol is running"
    echo -e "  ${CYAN}ℹ${NC} Syncs .local → .vlan every 30 seconds"
else
    echo -e "  ${RED}✗${NC} Join protocol is not running"
fi

echo ""
echo -e "${CYAN}▸ DNS Configuration Files${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

if [ -f /etc/dnsmasq.d/isle-vlan-domains.conf ]; then
    count=$(grep -c "^address=" /etc/dnsmasq.d/isle-vlan-domains.conf 2>/dev/null || echo 0)
    echo -e "  ${GREEN}✓${NC} /etc/dnsmasq.d/isle-vlan-domains.conf"
    echo -e "    ${CYAN}ℹ${NC} ${count} .vlan domain(s) configured"
else
    echo -e "  ${YELLOW}⚠${NC} /etc/dnsmasq.d/isle-vlan-domains.conf not found"
fi

EOFSSH
        ;;

    sync)
        # Force join protocol to update DNS immediately
        echo -e "${BOLD}Forcing DNS synchronization...${NC}"
        echo ""

        # Check if router is running
        if ! virsh list --all 2>/dev/null | grep -q "openwrt-isle-router.*running"; then
            echo -e "${RED}Error: OpenWRT router is not running${NC}"
            exit 1
        fi

        ROUTER_IP=$(virsh domifaddr openwrt-isle-router 2>/dev/null | grep -oP '(\d+\.){3}\d+' | head -1)

        # Trigger join protocol sync
        ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            root@${ROUTER_IP} << 'EOFSSH' 2>/dev/null

# Kill and restart join protocol to force immediate sync
pkill -f "join-protocol.sh" 2>/dev/null || true
sleep 1

# Restart join protocol in background
nohup /etc/isle/join-protocol.sh >/dev/null 2>&1 &

# Wait for sync
sleep 2

echo "✓ Join protocol restarted and DNS synced"
echo ""
echo "Check results with: isle dns discover"

EOFSSH

        echo -e "${GREEN}✓ DNS synchronization triggered${NC}"
        echo ""
        echo "Run ${CYAN}isle dns discover${NC} to see updated mappings"
        ;;

    list)
        # List all .vlan DNS entries
        echo -e "${BOLD}Current .vlan DNS Entries${NC}"
        echo ""

        if ! virsh list --all 2>/dev/null | grep -q "openwrt-isle-router.*running"; then
            echo -e "${RED}Error: OpenWRT router is not running${NC}"
            exit 1
        fi

        ROUTER_IP=$(virsh domifaddr openwrt-isle-router 2>/dev/null | grep -oP '(\d+\.){3}\d+' | head -1)

        ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            root@${ROUTER_IP} << 'EOFSSH' 2>/dev/null

if [ -f /etc/dnsmasq.d/isle-vlan-domains.conf ]; then
    echo "Domain                          IP Address"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    grep "^address=" /etc/dnsmasq.d/isle-vlan-domains.conf 2>/dev/null | while read -r line; do
        domain=$(echo "$line" | sed 's/address=\/\([^/]*\)\/.*/\1/')
        ip=$(echo "$line" | sed 's/.*\/\([0-9.]*\)/\1/')
        printf "%-30s  %s\n" "$domain" "$ip"
    done
else
    echo "No .vlan domains configured"
fi

EOFSSH
        ;;

    verify)
        # Test DNS resolution from router
        echo -e "${BOLD}Verifying DNS Resolution${NC}"
        echo ""

        if ! virsh list --all 2>/dev/null | grep -q "openwrt-isle-router.*running"; then
            echo -e "${RED}Error: OpenWRT router is not running${NC}"
            exit 1
        fi

        ROUTER_IP=$(virsh domifaddr openwrt-isle-router 2>/dev/null | grep -oP '(\d+\.){3}\d+' | head -1)

        ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            root@${ROUTER_IP} << 'EOFSSH' 2>/dev/null

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}Testing .vlan domain resolution...${NC}"
echo ""

if [ ! -f /etc/dnsmasq.d/isle-vlan-domains.conf ]; then
    echo -e "${RED}✗ No .vlan domains configured${NC}"
    exit 1
fi

# Test each .vlan domain
grep "^address=" /etc/dnsmasq.d/isle-vlan-domains.conf 2>/dev/null | while read -r line; do
    domain=$(echo "$line" | sed 's/address=\/\([^/]*\)\/.*/\1/')
    expected_ip=$(echo "$line" | sed 's/.*\/\([0-9.]*\)/\1/')

    # Try to resolve via nslookup
    resolved_ip=$(nslookup "$domain" 127.0.0.1 2>/dev/null | grep -A1 "Name:" | tail -1 | awk '{print $2}')

    printf "%-30s " "$domain"
    if [ "$resolved_ip" = "$expected_ip" ]; then
        echo -e "${GREEN}✓ OK${NC} ($resolved_ip)"
    else
        echo -e "${RED}✗ FAIL${NC} (expected: $expected_ip, got: $resolved_ip)"
    fi
done

EOFSSH
        ;;

    help|*)
        show_help
        ;;
esac
