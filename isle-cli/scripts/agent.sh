#!/bin/bash

# Isle-Mesh Agent Commands
# Unified nginx proxy container with virtual MAC for OpenWRT integration
#
# The isle-agent is a single nginx container that serves all mesh apps
# with isolated network access via virtual MAC address.

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
    echo -e "${BOLD}Isle Agent Commands${NC} - Unified Nginx Proxy Container"
    echo -e ""
    echo -e "╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║                    AGENT OVERVIEW                             ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝"
    echo -e ""
    echo -e "The Isle Agent is a unified nginx proxy container that serves ALL"
    echo -e "mesh applications on this device. It uses a virtual MAC address"
    echo -e "for isolated connectivity with the OpenWRT router."
    echo -e ""
    echo -e "Each mesh app generates a config fragment that gets merged into"
    echo -e "the agent's configuration, allowing zero-downtime updates."
    echo -e ""
    echo -e "╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║                    AGENT LIFECYCLE                            ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝"
    echo -e ""
    echo -e "  ${CYAN}isle agent start${NC}               Start the isle-agent container (mDNS mode default)"
    echo -e "  ${CYAN}isle agent stop${NC}                Stop the isle-agent container"
    echo -e "  ${CYAN}isle agent restart${NC}             Restart the isle-agent container"
    echo -e "  ${CYAN}isle agent status${NC}              Show agent status and registered apps"
    echo -e "  ${CYAN}isle agent reload${NC}              Reload nginx config (zero-downtime)"
    echo -e ""
    echo -e "╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║                    SETUP & VERIFICATION                       ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝"
    echo -e ""
    echo -e "  ${CYAN}isle agent verify-setup${NC}        Verify agent setup is complete"
    echo -e "  ${CYAN}isle agent check-router${NC}        Check OpenWRT router discovery via mDNS"
    echo -e "  ${CYAN}isle agent switch-to-lightweight${NC} Switch to lightweight mode (after setup)"
    echo -e "  ${CYAN}isle agent switch-to-mdns${NC}      Switch back to mDNS mode"
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
    echo -e "  ${CYAN}isle agent logs${NC}                Show recent agent logs"
    echo -e "  ${CYAN}isle agent logs follow${NC}         Tail agent logs in real-time"
    echo -e ""
    echo -e "╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║                    HOW IT WORKS                               ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝"
    echo -e ""
    echo -e "1. ${GREEN}Single Container${NC}: One nginx container for all mesh apps"
    echo -e "   • Reduces resource usage"
    echo -e "   • Simplifies network management"
    echo -e "   • Uses virtual MAC (02:00:00:00:0a:01) for OpenWRT DHCP"
    echo -e ""
    echo -e "2. ${GREEN}Config Fragments${NC}: Each app generates its own config"
    echo -e "   • Stored in /etc/isle-mesh/agent/configs/{app}.conf"
    echo -e "   • Merged via nginx 'include' directive"
    echo -e "   • Independent updates without recomputing other apps"
    echo -e ""
    echo -e "3. ${GREEN}Conflict Detection${NC}: Registry tracks claimed domains"
    echo -e "   • Prevents subdomain collisions"
    echo -e "   • Validates before allowing app registration"
    echo -e "   • Clear error messages with suggestions"
    echo -e ""
    echo -e "4. ${GREEN}Hot Reload${NC}: Zero-downtime config changes"
    echo -e "   • Apps spin up/down without affecting others"
    echo -e "   • nginx gracefully reloads configuration"
    echo -e "   • No dropped connections"
    echo -e ""
    echo -e "╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║                    AGENT MODES                                ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝"
    echo -e ""
    echo -e "${GREEN}mDNS Mode${NC} (Default for initial setup):"
    echo -e "  • Runs nginx + Avahi mDNS daemon (~100MB memory)"
    echo -e "  • Broadcasts services for router auto-discovery"
    echo -e "  • Required for domain registration with router"
    echo -e ""
    echo -e "${GREEN}Lightweight Mode${NC} (After setup confirmed):"
    echo -e "  • Runs nginx only (~20MB memory)"
    echo -e "  • Domain mappings persist on router"
    echo -e "  • Minimal resource usage for production"
    echo -e ""
    echo -e "╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║                    TYPICAL WORKFLOW                           ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝"
    echo -e ""
    echo -e "# 1. Start the agent (mDNS mode by default)"
    echo -e "${CYAN}isle agent start${NC}"
    echo -e ""
    echo -e "# 2. Verify setup is working"
    echo -e "${CYAN}isle agent verify-setup${NC}"
    echo -e ""
    echo -e "# 3. Switch to lightweight mode (optional)"
    echo -e "${CYAN}isle agent switch-to-lightweight${NC}"
    echo -e ""
    echo -e "# 4. Deploy mesh apps (they auto-register with agent)"
    echo -e "${CYAN}isle app up${NC}"
    echo -e ""
    echo -e "# 5. View registered apps"
    echo -e "${CYAN}isle agent status${NC}"
    echo -e ""
    echo -e "# 6. When app configs change, reload agent"
    echo -e "${CYAN}isle agent reload${NC}"
    echo -e ""
    echo -e "╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║                    LOCATION                                   ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝"
    echo -e ""
    echo -e "Configuration: ${YELLOW}/etc/isle-mesh/agent/${NC}"
    echo -e "  ├── docker-compose.yml          Agent container definition"
    echo -e "  ├── nginx.conf                  Master nginx config"
    echo -e "  ├── registry.json               Domain/subdomain registry"
    echo -e "  ├── configs/                    Per-app config fragments"
    echo -e "  │   ├── app1.conf"
    echo -e "  │   └── app2.conf"
    echo -e "  └── ssl/                        Shared SSL certificates"
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

    check-router|verify-router)
        check_agent_available
        exec "${AGENT_MANAGER}" check-router "$@"
        ;;

    verify-setup|verify)
        check_agent_available
        exec "${AGENT_MANAGER}" verify-setup "$@"
        ;;

    switch-to-lightweight|lightweight)
        check_agent_available
        exec "${AGENT_MANAGER}" switch-to-lightweight "$@"
        ;;

    switch-to-mdns|mdns-mode)
        check_agent_available
        exec "${AGENT_MANAGER}" switch-to-mdns "$@"
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
