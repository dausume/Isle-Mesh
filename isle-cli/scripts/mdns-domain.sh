#!/bin/bash
# Isle mDNS Domain Management
# Manages mDNS domain broadcasting configuration

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
MDNS_DIR="$PROJECT_ROOT/mdns"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Check if mdns directory exists
if [ ! -d "$MDNS_DIR" ]; then
    echo "Error: mdns directory not found at $MDNS_DIR"
    exit 1
fi

show_help() {
    echo -e "${BOLD}Isle mDNS Domain Management${NC}"
    echo ""
    echo "Manage which domains are broadcasted via mDNS."
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                    DOMAIN OPERATIONS                          ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    echo -e "${CYAN}isle mdns domain add <domain>${NC}"
    echo "    Manually add a domain to the broadcast list."
    echo ""
    echo "    Example:"
    echo "      isle mdns domain add myapp.local"
    echo "      isle mdns domain add api.test.local"
    echo ""
    echo -e "${CYAN}isle mdns domain remove <domain>${NC}"
    echo "    Remove a domain from the broadcast list."
    echo ""
    echo "    Example:"
    echo "      isle mdns domain remove myapp.local"
    echo ""
    echo -e "${CYAN}isle mdns domain list${NC}"
    echo "    Show all currently configured broadcast domains."
    echo "    Lists domains that mesh-mdns.service will broadcast."
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                    AUTO-DETECTION                             ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    echo -e "${CYAN}isle mdns domain detect [config] [compose] [mode]${NC}"
    echo "    Auto-detect domains from application configuration files."
    echo ""
    echo "    Parameters:"
    echo "      config  - Path to isle-mesh.yml (default: ./isle-mesh.yml)"
    echo "      compose - Path to docker-compose (default: ./docker-compose.mesh-app.yml)"
    echo "      mode    - 'append' (add to existing) or 'replace' (default: append)"
    echo ""
    echo "    Examples:"
    echo "      isle mdns domain detect"
    echo "      isle mdns domain detect ./config/isle-mesh.yml"
    echo "      isle mdns domain detect ./isle-mesh.yml ./docker-compose.yml replace"
    echo ""
    echo "    This command scans configuration files for domain definitions and"
    echo "    automatically adds them to the broadcast list."
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                    WORKFLOW                                   ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    echo -e "${YELLOW}Typical workflow:${NC}"
    echo "  1. isle mdns domain add myapp.local      # Add a domain"
    echo "  2. isle mdns domain list                 # Verify it was added"
    echo "  3. isle mdns system reload               # Apply changes"
    echo ""
    echo -e "${YELLOW}Auto-detect from app:${NC}"
    echo "  1. cd /path/to/your/app"
    echo "  2. isle mdns domain detect               # Scan configs"
    echo "  3. isle mdns domain list                 # Review detected domains"
    echo "  4. isle mdns system reload               # Apply changes"
    echo ""
    echo -e "${YELLOW}Clean up:${NC}"
    echo "  isle mdns domain remove old-app.local    # Remove unused domain"
    echo "  isle mdns system reload                  # Apply changes"
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                    IMPORTANT NOTES                            ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    echo -e "• After adding or removing domains, run ${GREEN}isle mdns system reload${NC}"
    echo "  to restart the broadcast service and apply changes."
    echo ""
    echo "• Domains must end in .local for mDNS to work properly."
    echo ""
    echo "• Use 'detect' mode during app initialization to automatically"
    echo "  configure domains based on your app's configuration."
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                    RELATED COMMANDS                           ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    echo -e "  • ${GREEN}isle mdns system reload${NC}    Apply domain changes"
    echo -e "  • ${GREEN}isle mdns system broadcast${NC}  Test broadcasting"
    echo -e "  • ${GREEN}isle mdns app list${NC}          See apps using these domains"
    echo ""
    echo -e "Back to overview: ${GREEN}isle mdns help${NC}"
}

COMMAND=${1:-help}

case $COMMAND in
    detect)
        MESH_CONFIG="${2:-./isle-mesh.yml}"
        COMPOSE_FILE="${3:-./docker-compose.mesh-app.yml}"
        MODE="${4:-append}"

        echo "Detecting domains from mesh configuration..."
        if [ -f "$MDNS_DIR/scripts/mesh-mdns-domains-detect.sh" ]; then
            bash "$MDNS_DIR/scripts/mesh-mdns-domains-detect.sh" "$MESH_CONFIG" "$COMPOSE_FILE" "$MODE"
        else
            echo "Error: mesh-mdns-domains-detect.sh not found"
            exit 1
        fi
        ;;
    add)
        DOMAIN="$2"
        if [ -z "$DOMAIN" ]; then
            echo "Usage: isle mdns domain add <domain>"
            exit 1
        fi

        if [ -f "$MDNS_DIR/scripts/mesh-mdns-domains-add.sh" ]; then
            bash "$MDNS_DIR/scripts/mesh-mdns-domains-add.sh" "$DOMAIN"
        else
            echo "Error: mesh-mdns-domains-add.sh not found"
            exit 1
        fi
        ;;
    remove)
        DOMAIN="$2"
        if [ -z "$DOMAIN" ]; then
            echo "Usage: isle mdns domain remove <domain>"
            exit 1
        fi

        if [ -f "$MDNS_DIR/scripts/mesh-mdns-domains-remove.sh" ]; then
            bash "$MDNS_DIR/scripts/mesh-mdns-domains-remove.sh" "$DOMAIN"
        else
            echo "Error: mesh-mdns-domains-remove.sh not found"
            exit 1
        fi
        ;;
    list)
        if [ -f "$MDNS_DIR/scripts/mesh-mdns-domains-list.sh" ]; then
            bash "$MDNS_DIR/scripts/mesh-mdns-domains-list.sh"
        else
            echo "Error: mesh-mdns-domains-list.sh not found"
            exit 1
        fi
        ;;
    help|*)
        show_help
        ;;
esac
