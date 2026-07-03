#!/bin/bash

# Isle-Mesh Agent Commands
# Two-component agent architecture for mesh proxy and config management
#
# Components:
#   1. isle-host-agent: Systemd service (mDNS broadcasting, registry, auto-sync)
#   2. isle-vlan-agent: Nginx container (reverse proxy with registry-watcher)

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$CLI_DIR")"
AGENT_DIR="${PROJECT_ROOT}/isle-agent"
AGENT_MANAGER="${AGENT_DIR}/scripts/agent-manager.sh"
CONFIG_MERGER="${AGENT_DIR}/scripts/merge-configs.sh"

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

show_help() {
    echo -e "${BOLD}Isle Agent Commands${NC} - Two-Component Agent Architecture"
    echo -e ""
    echo -e "╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║              TWO-COMPONENT ARCHITECTURE                       ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝"
    echo -e ""
    echo -e "The Isle Agent consists of two independent components:"
    echo -e ""
    echo -e "1. ${GREEN}isle-host-agent${NC} (Systemd Service)"
    echo -e "   • Broadcasts mDNS for service discovery"
    echo -e "   • Manages registry updates and auto-sync"
    echo -e "   • Watches registry.json for changes"
    echo -e ""
    echo -e "2. ${GREEN}isle-vlan-agent${NC} (Nginx Container)"
    echo -e "   • Reverse proxy for all mesh apps"
    echo -e "   • Built-in registry-watcher auto-generates nginx configs"
    echo -e "   • Virtual MAC: 02:00:00:00:0a:01"
    echo -e "   • Ports: 80 (HTTP), 443 (HTTPS)"
    echo -e ""
    echo -e "╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║                    AGENT LIFECYCLE                            ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝"
    echo -e ""
    echo -e "  ${CYAN}isle agent start${NC}               Start all agent components (automated setup)"
    echo -e "  ${CYAN}isle agent stop${NC}                Stop all agent containers"
    echo -e "  ${CYAN}isle agent restart${NC}             Restart all agent components"
    echo -e "  ${CYAN}isle agent status${NC}              Show status of all components"
    echo -e "  ${CYAN}isle agent reload${NC}              Reload nginx config (zero-downtime)"
    echo -e ""
    echo -e "╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║             SETUP, REGISTRATION & VERIFICATION                ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝"
    echo -e ""
    echo -e "  ${CYAN}isle agent verify-setup${NC}        Verify all components are healthy"
    echo -e "  ${CYAN}isle agent setup-host${NC}          Setup host agent (systemd service)"
    echo -e "  ${CYAN}isle agent setup-vlan${NC}          Setup VLAN agent (build custom nginx image)"
    echo -e "  ${CYAN}isle agent register${NC}            Register an app with the agent"
    echo -e "      --name <name>  --domain <domain>  --container <container>"
    echo -e "      [--port <port>]  [--protocol <protocol>]"
    echo -e "  ${CYAN}isle agent unregister${NC}          Unregister an app from the agent"
    echo -e "      --name <name>  [--remove-ssl]"
    echo -e ""
    echo -e "╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║                    CONFIGURATION                              ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝"
    echo -e ""
    echo -e "  ${CYAN}isle agent merge${NC}               Merge all app configs and validate"
    echo -e "  ${CYAN}isle agent validate${NC}            Validate all config fragments"
    echo -e "  ${CYAN}isle agent test${NC}                Test nginx configuration"
    echo -e "  ${CYAN}isle agent summary${NC}             Show summary of registered apps"
    echo -e ""
    echo -e "╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║                    LOGGING & DEBUG                            ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝"
    echo -e ""
    echo -e "  ${CYAN}isle agent logs${NC}                Show logs from all components"
    echo -e "  ${CYAN}isle agent logs vlan${NC}           Show VLAN agent logs"
    echo -e "  ${CYAN}isle agent logs host${NC}           Show host agent logs (systemd)"
    echo -e ""
    echo -e "╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║                    HOW IT WORKS                               ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝"
    echo -e ""
    echo -e "1. ${GREEN}Component Architecture${NC}: Two independent services"
    echo -e "   • Modular design for flexibility"
    echo -e "   • Each component can be managed separately"
    echo -e ""
    echo -e "2. ${GREEN}Config Generation${NC}: Dynamic nginx configuration"
    echo -e "   • Registry-watcher in vlan-agent watches registry.json"
    echo -e "   • Auto-generates configs in /etc/isle-mesh/agent/nginx/configs/"
    echo -e "   • Nginx reloads automatically on changes"
    echo -e ""
    echo -e "3. ${GREEN}Hot Reload${NC}: Zero-downtime config changes"
    echo -e "   • Apps register/deregister without affecting others"
    echo -e "   • Nginx gracefully reloads configuration"
    echo -e "   • No dropped connections"
    echo -e ""
    echo -e "╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║                    TYPICAL WORKFLOW                           ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝"
    echo -e ""
    echo -e "# 1. Start all agent components (fully automated)"
    echo -e "${CYAN}isle agent start${NC}"
    echo -e ""
    echo -e "# 2. Verify all components are healthy"
    echo -e "${CYAN}isle agent verify-setup${NC}"
    echo -e ""
    echo -e "# 3. Register an app"
    echo -e "${CYAN}isle agent register --name myapp --domain myapp.local --container myapp-1 --port 8080${NC}"
    echo -e ""
    echo -e "# 4. View registered apps and component status"
    echo -e "${CYAN}isle agent status${NC}"
    echo -e ""
    echo -e "╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║                    LOCATION                                   ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝"
    echo -e ""
    echo -e "Configuration: ${YELLOW}/etc/isle-mesh/agent/${NC}"
    echo -e "  ├── docker-compose.yml          Container orchestration"
    echo -e "  ├── registry.json               Domain/subdomain registry"
    echo -e "  ├── nginx/configs/              Auto-generated nginx fragments"
    echo -e "  ├── ssl/                        Shared SSL certificates"
    echo -e "  ├── logs/                       Nginx logs"
    echo -e "  └── sync-data/                  Sync agent persistence"
    echo -e ""
}

# Check if agent scripts exist
check_agent_available() {
    if [[ ! -f "${AGENT_MANAGER}" ]]; then
        echo -e "${RED}Error: Isle agent scripts not found${NC}"
        echo -e "Expected location: ${AGENT_MANAGER}"
        echo -e ""
        echo -e "Make sure the isle-agent directory exists in the project root."
        exit 1
    fi
}

# Main command router
COMMAND=$1
shift || true

case $COMMAND in
    help|-h|--help)
        show_help
        ;;

    # Agent lifecycle commands - delegate to agent-manager.sh
    start)
        check_agent_available
        exec "${AGENT_MANAGER}" start "$@"
        ;;

    stop)
        check_agent_available
        exec "${AGENT_MANAGER}" stop "$@"
        ;;

    restart)
        check_agent_available
        exec "${AGENT_MANAGER}" restart "$@"
        ;;

    status)
        check_agent_available
        exec "${AGENT_MANAGER}" status "$@"
        ;;

    reload)
        check_agent_available
        exec "${AGENT_MANAGER}" reload "$@"
        ;;

    logs)
        check_agent_available
        exec "${AGENT_MANAGER}" logs "$@"
        ;;

    test)
        check_agent_available
        exec "${AGENT_MANAGER}" test "$@"
        ;;

    verify-setup|verify)
        check_agent_available
        exec "${AGENT_MANAGER}" verify-setup "$@"
        ;;

    setup-host)
        check_agent_available
        exec "${AGENT_MANAGER}" setup-host "$@"
        ;;

    setup-vlan)
        check_agent_available
        exec "${AGENT_MANAGER}" setup-vlan "$@"
        ;;

    register)
        check_agent_available
        exec "${AGENT_MANAGER}" register "$@"
        ;;

    unregister)
        check_agent_available
        exec "${AGENT_MANAGER}" unregister "$@"
        ;;

    list-apps)
        check_agent_available
        exec "${AGENT_MANAGER}" list-apps "$@"
        ;;

    # Config management commands - delegate to merge-configs.sh
    merge)
        check_agent_available
        exec "${CONFIG_MERGER}" merge "$@"
        ;;

    validate)
        check_agent_available
        exec "${CONFIG_MERGER}" validate "$@"
        ;;

    summary)
        check_agent_available
        exec "${CONFIG_MERGER}" summary "$@"
        ;;

    destroy)
        check_agent_available
        exec "${AGENT_MANAGER}" destroy "$@"
        ;;

    "")
        # Show status if no command given
        check_agent_available
        exec "${AGENT_MANAGER}" status
        ;;

    *)
        echo -e "${RED}Unknown agent command: $COMMAND${NC}"
        echo ""
        echo "Use 'isle agent help' to see available commands."
        exit 1
        ;;
esac
