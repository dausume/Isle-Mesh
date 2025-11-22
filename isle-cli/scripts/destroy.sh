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

while [[ $# -gt 0 ]]; do
    case $1 in
        --force|-f)
            FORCE=true
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

# Step 2: Destroy agent
if [ "$KEEP_AGENT" = false ]; then
    echo -e "${BOLD}${CYAN}[2/3] Destroying isle-agent...${NC}"
    if container_exists "isle-agent"; then
        echo -e "${YELLOW}  → Agent container found, destroying...${NC}"
        bash "$PROJECT_ROOT/isle-cli/scripts/agent.sh" destroy --full 2>/dev/null || true
        echo -e "${GREEN}  ✓ Agent destroyed${NC}"
    else
        echo -e "${BLUE}  → No agent container found${NC}"
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

# Final summary
echo -e "${BOLD}${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${GREEN}║              Teardown Complete                                ║${NC}"
echo -e "${BOLD}${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${CYAN}All specified IsleMesh components have been destroyed.${NC}"
echo ""
echo -e "To rebuild your mesh environment, run: ${CYAN}isle create${NC}"
echo ""
