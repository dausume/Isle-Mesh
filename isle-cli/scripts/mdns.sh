#!/bin/bash
# Isle mDNS Namespace Router
# Routes commands to appropriate scope handlers

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

# Sub-script paths
MDNS_SYSTEM_SCRIPT="$SCRIPT_DIR/mdns-system.sh"
MDNS_DOMAIN_SCRIPT="$SCRIPT_DIR/mdns-domain.sh"
MDNS_APP_SCRIPT="$SCRIPT_DIR/mdns-app.sh"
MDNS_SAMPLE_SCRIPT="$SCRIPT_DIR/mdns-sample.sh"

show_help() {
    echo -e "${BOLD}Isle mDNS Namespace${NC}"
    echo ""
    echo "Manage mDNS infrastructure, domains, applications, and samples."
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                         SCOPES                                ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    echo -e "${CYAN}isle mdns system${NC}    System-level mDNS infrastructure"
    echo "                    Install systemd services, manage mesh-mdns daemon,"
    echo "                    configure host networking for mDNS broadcasting."
    echo ""
    echo -e "${CYAN}isle mdns domain${NC}    Domain broadcasting management"
    echo "                    Add, remove, and list domains for mDNS broadcasting."
    echo "                    Auto-detect domains from application configs."
    echo ""
    echo -e "${CYAN}isle mdns discover${NC}  Discover mDNS services on local network"
    echo "                    Scan for .local domains via Avahi (physical machine perspective)"
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                    DETAILED HELP                              ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    echo "For detailed commands in each scope:"
    echo ""
    echo -e "  ${GREEN}isle mdns system help${NC}    Show all system infrastructure commands"
    echo -e "  ${GREEN}isle mdns domain help${NC}    Show all domain management commands"
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                    QUICK START                                ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    echo -e "${YELLOW}1. Install mDNS system:${NC}"
    echo "   isle mdns system install"
    echo ""
    echo -e "${YELLOW}2. Check installation status:${NC}"
    echo "   isle mdns system status"
    echo ""
    echo -e "${YELLOW}3. Add domains for broadcasting:${NC}"
    echo "   isle mdns domain add myapp.local"
    echo "   isle mdns domain list"
    echo ""
    echo -e "${YELLOW}4. Start a localhost app:${NC}"
    echo "   isle mdns app list"
    echo "   isle mdns app up <app-name>"
    echo ""
    echo -e "${YELLOW}5. Run demo environment:${NC}"
    echo "   isle mdns sample up"
    echo "   isle mdns sample logs"
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                    COMMON WORKFLOWS                           ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    echo -e "${BOLD}Setup new development environment:${NC}"
    echo "  isle mdns system install       # Install infrastructure"
    echo "  isle mdns system status        # Verify installation"
    echo "  isle mdns domain detect        # Auto-detect app domains"
    echo "  isle mdns system reload        # Apply domain changes"
    echo ""
    echo -e "${BOLD}Manage localhost applications:${NC}"
    echo "  isle mdns app list             # See available apps"
    echo "  isle mdns app up myapp         # Start application"
    echo "  isle mdns app logs myapp       # Monitor logs"
    echo "  isle mdns app down myapp       # Stop application"
    echo ""
    echo -e "${BOLD}Test with demo environment:${NC}"
    echo "  isle mdns sample up            # Start demo stack"
    echo "  isle mdns sample status        # Check containers"
    echo "  isle mdns sample logs          # View all logs"
    echo "  isle mdns sample down          # Stop demo"
    echo ""
    echo "For more information: https://github.com/yourusername/IsleMesh/docs"
}

SCOPE=${1:-help}
shift || true

case $SCOPE in
    system)
        exec bash "$MDNS_SYSTEM_SCRIPT" "$@"
        ;;
    domain)
        exec bash "$MDNS_DOMAIN_SCRIPT" "$@"
        ;;
    app)
        exec bash "$MDNS_APP_SCRIPT" "$@"
        ;;
    sample)
        exec bash "$MDNS_SAMPLE_SCRIPT" "$@"
        ;;
    discover)
        # Physical machine mDNS discovery
        echo -e "${BOLD}Discovering mDNS services from physical machine...${NC}"
        echo ""

        # Use avahi-browse to discover .local services
        if ! command -v avahi-browse &> /dev/null; then
            echo -e "${RED}Error: avahi-browse not found${NC}"
            echo "Install with: ${CYAN}sudo apt-get install avahi-utils${NC}"
            exit 1
        fi

        echo -e "${CYAN}▸ Scanning for .local mDNS services...${NC}"
        echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""

        # Discover HTTP/HTTPS services
        timeout 5 avahi-browse -at 2>/dev/null | grep -E "(_http\._tcp|_https\._tcp)" | grep "\.local" | awk '{print $4}' | sort -u | while read -r service; do
            if [ -n "$service" ]; then
                echo -e "  ${GREEN}✓${NC} ${service}.local"
                echo -e "    ${CYAN}→${NC} http://${service}.local"
                echo -e "    ${CYAN}→${NC} https://${service}.local"
            fi
        done

        # Also discover generic hosts
        echo ""
        echo -e "${CYAN}▸ All .local hosts:${NC}"
        echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""

        timeout 5 avahi-browse -at 2>/dev/null | grep "\.local" | grep -v "^=" | awk '{print $4}' | sort -u | head -20 | while read -r host; do
            if [ -n "$host" ]; then
                echo -e "  ${GREEN}✓${NC} ${host}.local"
            fi
        done

        echo ""
        echo -e "${YELLOW}ℹ${NC}  For router DNS perspective (.isle domains), use: ${CYAN}isle dns discover${NC}"
        ;;
    help|*)
        show_help
        ;;
esac
