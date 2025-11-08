#!/bin/bash

# Isle-Mesh App Commands
# All mesh application management commands

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$CLI_DIR")"

# Script paths
ISLE_CORE="$SCRIPT_DIR/isle-core.sh"
SCAFFOLD="$SCRIPT_DIR/scaffold.sh"
CONFIG="$SCRIPT_DIR/config.sh"
DISCOVER="$SCRIPT_DIR/discover.sh"
SSL="$SCRIPT_DIR/ssl.sh"
MESH_PROXY="$SCRIPT_DIR/mesh-proxy.sh"
EMBED_JINJA="$SCRIPT_DIR/embed-jinja.sh"
MDNS="$SCRIPT_DIR/mdns.sh"
SAMPLE="$SCRIPT_DIR/sample.sh"

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

show_help() {
    echo -e "${BOLD}Isle App Commands${NC} - Mesh Application Management"
    echo -e ""
    echo -e "╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║                    PROJECT LIFECYCLE                          ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝"
    echo -e ""
    echo -e "Getting Started:"
    echo -e "  ${CYAN}isle app init [options]${NC}           Initialize a new mesh-app project"
    echo -e "    -f, --file FILE                   Convert from existing docker-compose.yml"
    echo -e "    -o, --output DIR                  Output directory (default: current)"
    echo -e "    -d, --domain DOMAIN               Base domain (default: mesh-app.local)"
    echo -e "    -n, --name NAME                   Project name (auto-detected)"
    echo -e ""
    echo -e "  ${CYAN}isle app scaffold <file> [opts]${NC}   Convert docker-compose to mesh-app"
    echo -e "    -o, --output DIR                  Output directory"
    echo -e "    -d, --domain DOMAIN               Base domain"
    echo -e "    -n, --name NAME                   Project name"
    echo -e ""
    echo -e "Managing Services (like docker-compose):"
    echo -e "  ${CYAN}isle app up [--build]${NC}             Start mesh-app services"
    echo -e "  ${CYAN}isle app down [-v]${NC}                Stop mesh-app services"
    echo -e "  ${CYAN}isle app logs [service]${NC}           View service logs"
    echo -e "  ${CYAN}isle app ps${NC}                       List running services"
    echo -e "  ${CYAN}isle app prune [-f]${NC}               Clean up all mesh resources"
    echo -e ""
    echo -e "╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║                    CONFIGURATION & DISCOVERY                  ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝"
    echo -e ""
    echo -e "Configuration:"
    echo -e "  ${CYAN}isle app config set-project <path>${NC} Set current mesh-app project"
    echo -e "  ${CYAN}isle app config get-project${NC}        Get current project path"
    echo -e "  ${CYAN}isle app config show${NC}               Show all configuration"
    echo -e ""
    echo -e "Discovery:"
    echo -e "  ${CYAN}isle app discover [command]${NC}        Discover .local domains"
    echo -e "    all                               Discover from all sources (default)"
    echo -e "    docker                            Check Docker container labels"
    echo -e "    nginx                             Check Nginx configurations"
    echo -e "    hosts                             Check /etc/hosts entries"
    echo -e "    mdns                              Check mDNS/Avahi services"
    echo -e "    test                              Discover and test URL accessibility"
    echo -e "    export [file]                     Export discovered domains to JSON"
    echo -e ""
    echo -e "╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║                    ADVANCED TOOLS                             ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝"
    echo -e ""
    echo -e "SSL Certificates:"
    echo -e "  ${CYAN}isle app ssl [action]${NC}              Manage SSL certificates"
    echo -e "    generate                          Generate basic SSL certificate"
    echo -e "    generate-mesh                     Generate mesh SSL with subdomains"
    echo -e "    list                              List all certificates"
    echo -e "    info <name>                       Show certificate info"
    echo -e "    verify <name>                     Verify certificate"
    echo -e "    clean                             Remove all certificates"
    echo -e ""
    echo -e "Mesh Proxy:"
    echo -e "  ${CYAN}isle app mesh-proxy [action]${NC}       Manage mesh-proxy"
    echo -e "    up                                Start the mesh-proxy services"
    echo -e "    down                              Stop the mesh-proxy services"
    echo -e "    build                             Build the mesh-proxy builder"
    echo -e "    logs                              View mesh-proxy logs"
    echo -e ""
    echo -e "Embed Jinja (Framework Automation):"
    echo -e "  ${CYAN}isle app embed-jinja [action]${NC}      Manage embed-jinja"
    echo -e "    up/start                          Start embed-jinja auto workflow"
    echo -e "    down/stop                         Stop embed-jinja services"
    echo -e "    logs                              View workflow logs"
    echo -e "    app-logs                          View application logs"
    echo -e "    status                            Show service status"
    echo -e "    clean                             Clean and reset"
    echo -e ""
    echo -e "System:"
    echo -e "  ${CYAN}isle app mdns [action]${NC}             Manage Isle Mesh mDNS system"
    echo -e "    install/up                        Install mDNS on host system"
    echo -e "    uninstall/down                    Uninstall mDNS from host"
    echo -e "    status                            Check installation status"
    echo -e ""
    echo -e "  ${CYAN}isle app sample <name> [action]${NC}    Manage sample/demo environments"
    echo -e "    localhost-mdns                    Hand-crafted localhost mDNS demo"
    echo -e "    list                              List available samples"
    echo -e ""
    echo -e "╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║                    QUICK START EXAMPLES                       ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝"
    echo -e ""
    echo -e "${YELLOW}1. Convert existing docker-compose app:${NC}"
    echo -e "   isle app init -f docker-compose.yml -d myapp.local"
    echo -e "   isle app up --build"
    echo -e ""
    echo -e "${YELLOW}2. Create new mesh-app from scratch:${NC}"
    echo -e "   mkdir my-mesh-app && cd my-mesh-app"
    echo -e "   isle app init"
    echo -e "   # Edit docker-compose.mesh-app.yml to add your services"
    echo -e "   isle app up"
    echo -e ""
    echo -e "${YELLOW}3. Manage running mesh-app:${NC}"
    echo -e "   isle app logs backend              # View backend logs"
    echo -e "   isle app ps                        # List services"
    echo -e "   isle app down                      # Stop all services"
    echo -e "   isle app prune                     # Clean up resources"
    echo -e ""
    echo -e "${YELLOW}4. Advanced usage:${NC}"
    echo -e "   isle app scaffold app.yml -o ./mesh-output"
    echo -e "   isle app ssl generate-mesh config/ssl.env.conf"
    echo -e "   isle app mdns install"
    echo -e "   isle app discover test"
    echo -e ""
}

# Main command router
COMMAND=$1
shift || true

case $COMMAND in
    # Core project commands
    init|up|down|logs|ps|prune)
        exec bash "$ISLE_CORE" "$COMMAND" "$@"
        ;;

    # Project tools
    scaffold)
        exec bash "$SCAFFOLD" "$@"
        ;;

    config)
        exec bash "$CONFIG" "$@"
        ;;

    discover)
        exec bash "$DISCOVER" "$@"
        ;;

    ssl)
        exec bash "$SSL" "$@"
        ;;

    mesh-proxy|proxy)
        exec bash "$MESH_PROXY" "$@"
        ;;

    embed-jinja|jinja)
        exec bash "$EMBED_JINJA" "$@"
        ;;

    mdns)
        exec bash "$MDNS" "$@"
        ;;

    sample)
        exec bash "$SAMPLE" "$@"
        ;;

    help|-h|--help|"")
        show_help
        ;;

    *)
        echo -e "${RED}Unknown app command: $COMMAND${NC}"
        echo ""
        echo "Use 'isle app help' to see available commands."
        exit 1
        ;;
esac
