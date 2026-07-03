#!/bin/bash

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Get the project root (parent of isle-cli)
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

show_help() {
    cat << EOF
${BOLD}Isle Destroy${NC} - Complete IsleMesh Teardown

╔═══════════════════════════════════════════════════════════════╗
║                    OVERVIEW                                   ║
╚═══════════════════════════════════════════════════════════════╝

Safely tears down all IsleMesh components in the correct order:
  1. Stop all mesh applications
  2. Destroy the isle-agent
  3. Destroy the router and network bridges

${CYAN}USAGE:${NC}
  isle destroy [options]

${CYAN}OPTIONS:${NC}
  --force, -f           Skip confirmation prompts
  --purge               Wipe the ENTIRE isle-mesh footprint (configs, DNS,
                        services, networks, state) — everything EXCEPT the
                        isle CLI itself. This is the full "Wipe Island".
  --keep-agent          Keep agent running (only destroy apps and router)
  --keep-router         Keep router running (only destroy apps and agent)
  --apps-only           Only destroy mesh applications
  --help, -h            Show this help message

${CYAN}EXAMPLES:${NC}

  ${YELLOW}# Full teardown with confirmation${NC}
  sudo isle destroy

  ${YELLOW}# Force teardown without prompts${NC}
  sudo isle destroy --force

  ${YELLOW}# Destroy only apps, keep infrastructure${NC}
  isle destroy --apps-only

  ${YELLOW}# Destroy apps and router, keep agent${NC}
  sudo isle destroy --keep-agent

${CYAN}SAFETY:${NC}
  • Prompts for confirmation before destructive actions (unless --force)
  • Shows what will be destroyed before proceeding
  • Gracefully handles missing components
  • Router destruction requires sudo

${CYAN}NOTE:${NC}
  This command is the opposite of ${CYAN}isle create${NC} - it tears down
  everything that was set up during initialization.

EOF
}

# Parse command-line arguments
FORCE=false
KEEP_AGENT=false
KEEP_ROUTER=false
APPS_ONLY=false
PURGE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --force|-f)
            FORCE=true
            shift
            ;;
        --purge)
            PURGE=true
            shift
            ;;
        --keep-agent)
            KEEP_AGENT=true
            shift
            ;;
        --keep-router)
            KEEP_ROUTER=true
            shift
            ;;
        --apps-only)
            APPS_ONLY=true
            shift
            ;;
        --help|-h)
            show_help
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use 'isle destroy --help' for usage information"
            exit 1
            ;;
    esac
done

# Validation: conflicting options
if [ "$APPS_ONLY" = true ] && ([ "$KEEP_AGENT" = true ] || [ "$KEEP_ROUTER" = true ]); then
    echo -e "${RED}Error: --apps-only cannot be used with --keep-agent or --keep-router${NC}"
    exit 1
fi

echo -e "${BOLD}${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${BLUE}║              Isle Mesh - Complete Teardown                    ║${NC}"
echo -e "${BOLD}${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Show what will be destroyed
echo -e "${CYAN}The following components will be destroyed:${NC}"
echo ""
if [ "$APPS_ONLY" = false ]; then
    echo -e "  ${YELLOW}✓${NC} All mesh applications"
    if [ "$KEEP_AGENT" = false ]; then
        echo -e "  ${YELLOW}✓${NC} Isle agent (unified nginx proxy)"
    fi
    if [ "$KEEP_ROUTER" = false ]; then
        echo -e "  ${YELLOW}✓${NC} OpenWRT router and network bridges"
    fi
else
    echo -e "  ${YELLOW}✓${NC} All mesh applications only"
fi
if [ "$PURGE" = true ]; then
    echo -e "  ${YELLOW}✓${NC} ${BOLD}PURGE:${NC} all configs (/etc/isle-mesh), DNS split files, isle"
    echo -e "      services (host-agent, mesh-mdns, registry-sync, device-relay,"
    echo -e "      port-detection), udev rules, leftover networks, state & logs"
    echo -e "      ${GREEN}(the isle CLI itself is kept)${NC}"
fi
echo ""

# Confirmation prompt unless --force
if [ "$FORCE" = false ]; then
    echo -e "${YELLOW}This action cannot be undone.${NC}"
    read -p "Are you sure you want to continue? (yes/no): " CONFIRM
    if [ "$CONFIRM" != "yes" ]; then
        echo -e "${GREEN}Teardown cancelled.${NC}"
        exit 0
    fi
    echo ""
fi

# Function to check if a Docker container exists
container_exists() {
    docker ps -a --format '{{.Names}}' | grep -q "^${1}$"
}

# Function to check if mesh apps are running
check_mesh_apps() {
    # Check for any containers with mesh-app labels or networks
    docker ps -a --filter "label=isle.mesh.app" --format '{{.Names}}' 2>/dev/null | head -n 1
}

# Step 1: Destroy mesh applications
echo -e "${BOLD}${CYAN}[1/3] Destroying mesh applications...${NC}"
MESH_APPS=$(check_mesh_apps)
if [ -n "$MESH_APPS" ]; then
    echo -e "${YELLOW}  → Found mesh applications, stopping them...${NC}"

    # Find all directories with docker-compose.mesh-app.yml and run down
    find "$PROJECT_ROOT" -maxdepth 3 -name "docker-compose.mesh-app.yml" -type f 2>/dev/null | while read -r compose_file; do
        APP_DIR=$(dirname "$compose_file")
        echo -e "${BLUE}  → Stopping app in: $APP_DIR${NC}"
        (cd "$APP_DIR" && docker-compose -f docker-compose.mesh-app.yml down -v 2>/dev/null || true)
    done

    # Also check current directory
    if [ -f "docker-compose.mesh-app.yml" ]; then
        echo -e "${BLUE}  → Stopping app in current directory${NC}"
        docker-compose -f docker-compose.mesh-app.yml down -v 2>/dev/null || true
    fi

    echo -e "${GREEN}  ✓ Mesh applications stopped${NC}"
else
    echo -e "${BLUE}  → No mesh applications found${NC}"
fi
echo ""

# Exit early if apps-only mode
if [ "$APPS_ONLY" = true ]; then
    echo -e "${BOLD}${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}${GREEN}║              Teardown Complete (Apps Only)                    ║${NC}"
    echo -e "${BOLD}${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
    exit 0
fi

# Step 2: Destroy agent (two-component: vlan-agent container + host-agent service)
if [ "$KEEP_AGENT" = false ]; then
    echo -e "${BOLD}${CYAN}[2/3] Destroying isle-agent...${NC}"
    AGENT_FOUND=false

    # Stop and remove vlan-agent container
    if container_exists "isle-vlan-agent"; then
        echo -e "${YELLOW}  → Stopping isle-vlan-agent container...${NC}"
        docker stop isle-vlan-agent 2>/dev/null || true
        docker rm isle-vlan-agent 2>/dev/null || true
        AGENT_FOUND=true
    fi

    # Stop and remove remote-agent container (remote mode)
    if container_exists "isle-remote-agent"; then
        echo -e "${YELLOW}  → Stopping isle-remote-agent container...${NC}"
        docker stop isle-remote-agent 2>/dev/null || true
        docker rm isle-remote-agent 2>/dev/null || true
        AGENT_FOUND=true
    fi

    # Also check for legacy isle-agent container
    if container_exists "isle-agent"; then
        echo -e "${YELLOW}  → Stopping legacy isle-agent container...${NC}"
        docker stop isle-agent 2>/dev/null || true
        docker rm isle-agent 2>/dev/null || true
        AGENT_FOUND=true
    fi

    # Also remove any leftover isle-agent-sync container
    if container_exists "isle-agent-sync"; then
        echo -e "${YELLOW}  → Removing leftover isle-agent-sync container...${NC}"
        docker stop isle-agent-sync 2>/dev/null || true
        docker rm isle-agent-sync 2>/dev/null || true
    fi

    # Stop and remove sample app container
    if container_exists "isle-sample-app"; then
        echo -e "${YELLOW}  → Stopping isle-sample-app container...${NC}"
        docker stop isle-sample-app 2>/dev/null || true
        docker rm isle-sample-app 2>/dev/null || true
    fi

    # Stop host-agent systemd service
    if systemctl is-active --quiet isle-host-agent 2>/dev/null; then
        echo -e "${YELLOW}  → Stopping isle-host-agent service...${NC}"
        sudo systemctl stop isle-host-agent 2>/dev/null || true
        sudo systemctl disable isle-host-agent 2>/dev/null || true
        AGENT_FOUND=true
    fi

    # Clean up docker networks
    docker network rm isle-agent-net 2>/dev/null || true
    docker network rm isle-remote-macvlan 2>/dev/null || true
    docker network rm isle-sample-app_default 2>/dev/null || true

    # Clean up remote state
    if [ -d "/etc/isle-mesh/agent/remote" ]; then
        rm -rf /etc/isle-mesh/agent/remote
        echo -e "${BLUE}  → Cleaned up remote agent state${NC}"
    fi

    # Clear agent mode file
    if [ -f "/etc/isle-mesh/agent/agent.mode" ]; then
        rm -f /etc/isle-mesh/agent/agent.mode
        echo -e "${BLUE}  → Cleared agent mode${NC}"
    fi

    if [ "$AGENT_FOUND" = true ]; then
        echo -e "${GREEN}  ✓ Agent components destroyed${NC}"
    else
        echo -e "${BLUE}  → No agent components found${NC}"
    fi
else
    echo -e "${BOLD}${CYAN}[2/3] Skipping agent (--keep-agent specified)${NC}"
fi
echo ""

# Step 3: Destroy router
if [ "$KEEP_ROUTER" = false ]; then
    echo -e "${BOLD}${CYAN}[3/3] Destroying router and network bridges...${NC}"

    # Check if virsh command exists
    if command -v virsh &> /dev/null; then
        # Check if any routers exist
        ROUTER_COUNT=$(virsh list --all 2>/dev/null | grep -c "openwrt-isle" || echo "0")
        if [ "$ROUTER_COUNT" -gt 0 ]; then
            echo -e "${YELLOW}  → Router found, destroying...${NC}"
            if [ "$FORCE" = true ]; then
                sudo bash "$PROJECT_ROOT/isle-cli/scripts/router.sh" destroy --force 2>/dev/null || true
            else
                sudo bash "$PROJECT_ROOT/isle-cli/scripts/router.sh" destroy || true
            fi
            echo -e "${GREEN}  ✓ Router destroyed${NC}"
        else
            echo -e "${BLUE}  → No router found${NC}"
        fi
    else
        echo -e "${BLUE}  → Libvirt/virsh not installed, skipping router${NC}"
    fi
else
    echo -e "${BOLD}${CYAN}[3/3] Skipping router (--keep-router specified)${NC}"
fi
echo ""

# Step 4: Purge the rest of the installed footprint (keep the CLI)
if [ "$PURGE" = true ]; then
    echo -e "${BOLD}${CYAN}[4/4] Purging isle-mesh footprint (keeping the CLI)...${NC}"

    # Leftover docker networks (destroy leaves isle-br-0 behind)
    for net in isle-br-0 isle-agent-net isle-remote-macvlan; do
        if docker network ls --format '{{.Name}}' 2>/dev/null | grep -q "^${net}$"; then
            docker network rm "$net" 2>/dev/null && echo -e "${BLUE}  → removed network ${net}${NC}" || true
        fi
    done

    # Systemd services we install — stop, disable, remove unit files
    for svc in isle-host-agent mesh-mdns agent-registry-sync isle-device-relay isle-port-detection; do
        sudo systemctl stop "$svc" 2>/dev/null || true
        sudo systemctl disable "$svc" 2>/dev/null || true
        sudo rm -f "/etc/systemd/system/${svc}.service" 2>/dev/null || true
    done
    sudo systemctl daemon-reload 2>/dev/null || true
    echo -e "${BLUE}  → removed isle systemd services${NC}"

    # Cable-plug detection (udev rules + helper binaries)
    sudo rm -f /etc/udev/rules.d/99-isle-mesh-ports.rules /etc/udev/rules.d/99-isle-mesh-usb.rules 2>/dev/null || true
    sudo rm -f /usr/local/bin/isle-port-event /usr/local/bin/isle-port-event-handler \
               /usr/local/bin/isle-port-init /usr/local/bin/isle-add-connection 2>/dev/null || true
    command -v udevadm >/dev/null 2>&1 && sudo udevadm control --reload-rules 2>/dev/null || true

    # DNS split configuration
    sudo rm -f /etc/dnsmasq.d/split-dns.conf /etc/systemd/resolved.conf.d/split-mdns.conf 2>/dev/null || true
    sudo systemctl restart dnsmasq 2>/dev/null || true
    sudo systemctl restart systemd-resolved 2>/dev/null || true
    echo -e "${BLUE}  → removed DNS split config${NC}"

    # mDNS broadcast + host-agent runtime scripts and domain list
    # (NOTE: /usr/local/bin/isle-mesh holds installed scripts; /usr/local/bin/isle
    #  is the CLI symlink and is intentionally NOT touched.)
    sudo rm -rf /usr/local/bin/isle-mesh /usr/local/etc/mesh-mdns-domains.list 2>/dev/null || true

    # Runtime state + logs
    sudo rm -rf /var/lib/isle-mesh /var/log/isle-mesh 2>/dev/null || true

    # Configuration tree (last)
    sudo rm -rf /etc/isle-mesh 2>/dev/null || true
    echo -e "${BLUE}  → removed /etc/isle-mesh and runtime state${NC}"

    echo -e "${GREEN}  ✓ Footprint purged — the isle CLI was kept${NC}"
    echo ""
fi

# Final summary
echo -e "${BOLD}${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${GREEN}║              Teardown Complete                                ║${NC}"
echo -e "${BOLD}${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${CYAN}All specified IsleMesh components have been destroyed.${NC}"
echo ""
echo -e "To rebuild your mesh environment, run: ${CYAN}isle create${NC}"
echo ""
