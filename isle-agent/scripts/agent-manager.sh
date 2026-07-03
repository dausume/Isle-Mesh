#!/usr/bin/env bash
#
# Isle Agent Manager - Lifecycle management for two-component agent architecture
#
# Manages the two-component isle-agent system:
#   1. isle-host-agent: Consolidated systemd service (mDNS, registry, auto-sync)
#   2. isle-vlan-agent: Nginx container for reverse proxy
#
# The host agent consolidates:
#   - mDNS broadcasting
#   - Registry updates
#   - Registry watching (inotify/polling)
#   - Auto-sync of .local domains to mDNS list

set -euo pipefail

# Get script directory and source dependency checker
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")/isle-cli"
DEPENDENCY_CHECKER="$CLI_DIR/scripts/check-dependencies.sh"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Source dependency management if available
if [[ -f "$DEPENDENCY_CHECKER" ]]; then
    source "$DEPENDENCY_CHECKER"
fi

# Configuration
ISLE_AGENT_DIR="/etc/isle-mesh/agent"
COMPOSE_FILE="${ISLE_AGENT_DIR}/docker-compose.yml"
REGISTRY_FILE="${ISLE_AGENT_DIR}/registry.json"

# Component names
VLAN_CONTAINER="isle-vlan-agent"
REMOTE_CONTAINER="isle-remote-agent"
HOST_SERVICE="isle-host-agent"

# Component paths
HOST_AGENT_DIR="${PROJECT_ROOT}/isle-agent/isle-host-agent"
VLAN_AGENT_DIR="${PROJECT_ROOT}/isle-agent/isle-vlan-agent"

# Detect which docker compose command to use
DOCKER_COMPOSE_CMD=""
detect_docker_compose() {
    if docker compose version &>/dev/null; then
        DOCKER_COMPOSE_CMD="docker compose"
    elif command -v docker-compose &>/dev/null; then
        DOCKER_COMPOSE_CMD="docker-compose"
    else
        return 1
    fi
    return 0
}

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $*"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

# Get agent mode (core or remote)
get_agent_mode() {
    local mode_file="${ISLE_AGENT_DIR}/agent.mode"
    if [[ -f "$mode_file" ]]; then
        cat "$mode_file" 2>/dev/null
    else
        echo "core"
    fi
}

# Check if user has proper permissions for /etc/isle-mesh
# This is advisory only - we allow operation with sudo or as root
check_permissions() {
    # If running as root (via sudo or directly), skip group check
    if [[ $EUID -eq 0 ]]; then
        return 0
    fi

    # Check if user is in isle-mesh group (advisory only)
    if ! id -nG | grep -qw "isle-mesh"; then
        log_warn "User is not in the isle-mesh group"
        log_info "For better permissions management, consider running:"
        echo "    sudo isle permissions agent"
        echo ""
        # Continue anyway - don't block execution
    fi

    # Check if we can write to /etc/isle-mesh (this will be created if needed)
    # If directory exists but we can't write, that's a real error
    if [[ -d "/etc/isle-mesh" ]] && [[ ! -w "/etc/isle-mesh" ]]; then
        log_error "Cannot write to /etc/isle-mesh (permission denied)"
        echo ""
        echo "  Please run with sudo, or run this command to fix permissions:"
        echo ""
        echo "    sudo isle permissions agent"
        echo ""
        return 1
    fi

    return 0
}

# Check if Docker is installed and accessible
check_docker_available() {
    # Use new dependency management if available
    if declare -f ensure_dependency &>/dev/null; then
        if ! ensure_dependency "docker" "run the Isle agent" "true"; then
            return 1
        fi

        # Detect docker compose command
        if ! detect_docker_compose; then
            if ! ensure_dependency "docker-compose" "run the Isle agent" "true"; then
                return 1
            fi

            # Re-detect after installation
            if ! detect_docker_compose; then
                log_error "Docker Compose installation failed or incomplete"
                echo ""
                echo "  Please install manually:"
                echo "    https://docs.docker.com/compose/install/"
                echo ""
                return 1
            fi
        fi

        return 0
    fi

    # Fallback to old behavior if dependency checker not available
    if ! command -v docker &>/dev/null; then
        log_error "Docker is not installed"
        echo ""
        echo "  The Isle agent requires Docker to run."
        echo ""
        echo "  To install Docker, run:"
        echo ""
        echo "    isle install dependencies docker"
        echo ""
        echo "  Or install Docker manually:"
        echo "    https://docs.docker.com/engine/install/"
        echo ""
        return 1
    fi

    # Check if Docker daemon is accessible
    if ! docker ps &>/dev/null; then
        log_error "Cannot access Docker daemon"
        echo ""
        echo "  Docker is installed but not accessible."
        echo ""
        echo "  Common causes:"
        echo "    1. Docker daemon is not running"
        echo "    2. User lacks permissions (not in docker group)"
        echo "    3. Docker socket permissions issue"
        echo ""
        echo "  To fix permissions, run:"
        echo ""
        echo "    sudo usermod -aG docker \$USER"
        echo "    newgrp docker"
        echo ""
        echo "  To start Docker daemon:"
        echo ""
        echo "    sudo systemctl start docker"
        echo ""
        return 1
    fi

    # Detect docker compose command
    if ! detect_docker_compose; then
        if declare -f ensure_dependency &>/dev/null; then
            if ! ensure_dependency "docker-compose" "run the Isle agent" "true"; then
                return 1
            fi

            # Re-detect after installation
            if ! detect_docker_compose; then
                log_error "Docker Compose installation failed or incomplete"
                echo ""
                echo "  Please install manually:"
                echo "    https://docs.docker.com/compose/install/"
                echo ""
                return 1
            fi
        else
            log_error "Docker Compose is not installed"
            echo ""
            echo "  The Isle agent requires Docker Compose."
            echo ""
            echo "  Please install Docker Compose:"
            echo "    https://docs.docker.com/compose/install/"
            echo ""
            return 1
        fi
    fi

    return 0
}

# Initialize agent directory structure
init_agent_dir() {
    log_info "Initializing isle-agent directory structure..."

    # Check permissions (advisory)
    check_permissions || true  # Continue even if check fails

    # Create directory structure in /etc/isle-mesh/agent
    mkdir -p "${ISLE_AGENT_DIR}"/{configs,ssl/{certs,keys},logs,sync-data,nginx/configs}

    # Pre-seed nginx.conf on host if it doesn't exist (required for Docker bind mount)
    if [[ ! -f "${ISLE_AGENT_DIR}/nginx/nginx.conf" ]]; then
        local nginx_conf_src="${VLAN_AGENT_DIR}/nginx.conf"
        if [[ -f "$nginx_conf_src" ]]; then
            cp "$nginx_conf_src" "${ISLE_AGENT_DIR}/nginx/nginx.conf"
            log_info "Pre-seeded nginx.conf from source"
        else
            # Create a minimal nginx.conf so Docker bind mount works
            touch "${ISLE_AGENT_DIR}/nginx/nginx.conf"
            log_warn "Created empty nginx.conf placeholder (source not found)"
        fi
    fi

    # Create host-agent log directory (required by isle-host-agent.service ReadWritePaths)
    mkdir -p /var/log/isle-mesh

    # Initialize registry with default health app if doesn't exist
    if [[ ! -f "${REGISTRY_FILE}" ]]; then
        log_info "Creating domain registry with default health app..."
        cat > "${REGISTRY_FILE}" <<'EOF'
{
  "domains": {},
  "subdomains": {},
  "apps": {
    "health": {
      "domain": "health.local",
      "services": [],
      "modes": [],
      "updated_at": ""
    }
  }
}
EOF
        # Ensure registry is group-readable (isle-mesh group)
        chmod 664 "${REGISTRY_FILE}"
        chgrp isle-mesh "${REGISTRY_FILE}" 2>/dev/null || true

        # Update timestamp if jq is available
        if command -v jq &>/dev/null; then
            local now
            now=$(date -Iseconds)
            local temp_file=$(mktemp)
            jq --arg now "$now" \
               '.apps.health.updated_at = $now' \
               "${REGISTRY_FILE}" > "$temp_file"
            cp "$temp_file" "${REGISTRY_FILE}"
            rm -f "$temp_file"
        fi
        log_success "Registry created with health.local app"
    else
        # Ensure health app exists in existing registry
        if command -v jq &>/dev/null; then
            local has_health
            has_health=$(jq -r '.apps.health // empty' "${REGISTRY_FILE}" 2>/dev/null)

            if [[ -z "$has_health" ]]; then
                log_info "Adding default health app to existing registry..."
                local now
                now=$(date -Iseconds)
                local temp_file=$(mktemp)
                jq --arg now "$now" \
                   '.apps.health = {
                     "domain": "health.local",
                     "services": [],
                     "modes": [],
                     "updated_at": $now
                   }' "${REGISTRY_FILE}" > "$temp_file"
                cp "$temp_file" "${REGISTRY_FILE}"
            rm -f "$temp_file"
                log_success "Added health.local app to registry"
            fi
        fi
    fi

    log_success "Agent directory initialized at ${ISLE_AGENT_DIR}"
}

# Ensure mesh-mdns system is installed and running
ensure_mesh_mdns() {
    log_info "Checking mesh-mdns system..."

    # Check if mesh-mdns.service is installed
    if systemctl list-unit-files 2>/dev/null | grep -q "mesh-mdns.service"; then
        # Service exists, check if it's running
        if ! systemctl is-active --quiet mesh-mdns.service 2>/dev/null; then
            log_warn "mesh-mdns.service is installed but not running"
            log_info "Starting mesh-mdns.service..."
            if sudo systemctl start mesh-mdns.service 2>/dev/null; then
                log_success "mesh-mdns.service started"
            else
                log_error "Failed to start mesh-mdns.service"
                return 1
            fi
        else
            log_success "mesh-mdns.service is running"
        fi

        # Add health.local domain if not already present
        local health_domain="health.local"
        if ! grep -Fxq "$health_domain" /usr/local/etc/mesh-mdns-domains.list 2>/dev/null; then
            log_info "Adding ${health_domain} for agent health check..."
            echo "$health_domain" | sudo tee -a /usr/local/etc/mesh-mdns-domains.list > /dev/null
            # Reload to apply changes
            sudo systemctl restart mesh-mdns.service 2>/dev/null || true
            log_success "Added ${health_domain}"
        fi

        return 0
    fi

    # Service not installed - offer to install
    log_warn "mesh-mdns.service is not installed"
    echo ""
    echo "The mesh-mdns service is required for .local domain broadcasting."
    echo "Would you like to install it now? (y/N)"
    read -r response

    if [[ "$response" =~ ^[Yy]$ ]]; then
        log_info "Installing mesh-mdns system..."

        # Check if mdns directory and install script exist
        local mdns_dir="${PROJECT_ROOT}/mdns"
        local install_script="${mdns_dir}/scripts/install-mesh-mdns.sh"

        if [[ ! -f "$install_script" ]]; then
            log_error "mesh-mdns install script not found: $install_script"
            echo ""
            echo "Install manually with: isle mdns system install"
            return 1
        fi

        # Run install via the CLI
        if bash "${CLI_DIR}/scripts/mdns.sh" system install; then
            log_success "mesh-mdns.service installed"

            # Add health.local domain
            echo "health.local" | sudo tee -a /usr/local/etc/mesh-mdns-domains.list > /dev/null
            sudo systemctl restart mesh-mdns.service 2>/dev/null || true

            return 0
        else
            log_error "Failed to install mesh-mdns.service"
            echo ""
            echo "You can install manually later with: isle mdns system install"
            return 1
        fi
    else
        log_warn "Skipping mesh-mdns installation"
        echo ""
        echo "Note: .local domains will not be broadcasted without mesh-mdns"
        echo "Install later with: isle mdns system install"
        echo ""
        return 1
    fi
}

# Check if a router exists on the system
check_router_exists() {
    # Check if any router VM exists using virsh
    if ! command -v virsh &>/dev/null; then
        # Offer to install virtualization dependencies if needed
        if declare -f ensure_dependency &>/dev/null; then
            log_info "Virtualization tools not found"
            if ! ensure_dependency "virsh" "check for router VMs" "false"; then
                return 1
            fi
        else
            return 1
        fi
    fi

    # Try without sudo first, then with sudo
    local router_exists=false
    if virsh list --all 2>/dev/null | grep -qE "openwrt|router-core"; then
        router_exists=true
    elif sudo virsh list --all 2>/dev/null | grep -qE "openwrt|router-core"; then
        router_exists=true
    fi

    if $router_exists; then
        return 0
    else
        return 1
    fi
}

# Detect isle-br-X bridges for OpenWRT connectivity
# This is now optional - only required if a router exists
setup_isle_bridge() {
    log_info "Checking for isle-br-X bridges..."

    # Find all isle-br-* bridges
    local bridges
    bridges=$(ip link show | grep -oP 'isle-br-\d+' | sort -u || true)

    if [[ -z "$bridges" ]]; then
        # Check if router exists
        if check_router_exists; then
            log_error "No isle-br-X bridges found, but router exists"
            echo ""
            echo "  A router VM exists but no isle-br-X bridges were found."
            echo "  The router should have created these bridges."
            echo ""
            echo "  Try recreating the router:"
            echo "    sudo isle router destroy"
            echo "    sudo isle router init"
            echo ""
            return 1
        else
            log_warn "No isle-br-X bridges found (no router detected)"
            echo ""
            echo "  The agent will start without bridge connectivity."
            echo "  To enable router connectivity, create a router first:"
            echo "    sudo isle router init"
            echo ""
            return 0  # Non-fatal - agent can start without router
        fi
    fi

    # Report found bridges
    log_success "Found isle bridges:"
    for bridge in $bridges; do
        local bridge_state
        bridge_state=$(ip link show "$bridge" | grep -oP '(?<=state )\w+' || echo "UNKNOWN")
        echo "  - ${bridge}: ${bridge_state}"

        # Check if OpenWRT router is connected to this bridge
        local connected_interfaces
        connected_interfaces=$(brctl show "$bridge" 2>/dev/null | tail -n +2 | awk '{print $NF}' | grep -v "^${bridge}$" | tr '\n' ' ' || true)

        if [[ -n "${connected_interfaces}" ]]; then
            echo "    Connected interfaces: ${connected_interfaces}"
        fi
    done

    # Check specifically for isle-br-0 (primary bridge used by agent)
    if echo "$bridges" | grep -q "isle-br-0"; then
        log_success "Primary bridge isle-br-0 detected"
    else
        log_warn "Primary bridge isle-br-0 not found"
        echo ""
        echo "  The agent will attempt to use the first available isle-br-X bridge,"
        echo "  but isle-br-0 is recommended as the primary bridge."
    fi
}

# Check if vlan agent is running (core or remote)
is_vlan_running() {
    local mode
    mode=$(get_agent_mode)
    if [[ "$mode" == "remote" ]]; then
        docker ps --filter "name=${REMOTE_CONTAINER}" --filter "status=running" --format '{{.Names}}' | grep -q "^${REMOTE_CONTAINER}$"
    else
        docker ps --filter "name=${VLAN_CONTAINER}" --filter "status=running" --format '{{.Names}}' | grep -q "^${VLAN_CONTAINER}$"
    fi
}

# Check if host agent is running
is_host_running() {
    systemctl is-active --quiet "${HOST_SERVICE}" 2>/dev/null
}

# Check if all agents are running
is_running() {
    is_host_running && is_vlan_running
}

# Check if stale containers exist (stopped/exited/created)
has_stale_containers() {
    # Check for containers in exited, created, or dead status
    if docker ps -a --filter "name=${VLAN_CONTAINER}" --format '{{.Names}} {{.Status}}' | \
        grep "^${VLAN_CONTAINER}" | \
        grep -qE "(Exited|Created|Dead)"; then
        return 0
    fi
    return 1
}

# Validate and cleanup Docker network for isle-br-0
validate_docker_network() {
    local network_name="isle-br-0"
    local bridge_exists=false

    # Check if system bridge exists
    if ip link show "${network_name}" &>/dev/null; then
        bridge_exists=true
    fi

    # Check if Docker network exists
    local docker_network_exists=false
    if docker network inspect "${network_name}" &>/dev/null; then
        docker_network_exists=true
    fi

    # If Docker network exists, validate it
    if $docker_network_exists; then
        # Check if the parent bridge still exists
        if ! $bridge_exists; then
            log_warn "Docker network ${network_name} references non-existent bridge"
            log_info "Removing stale Docker network..."
            docker network rm "${network_name}" 2>/dev/null || true
            docker_network_exists=false
        else
            # Check if network has valid configuration
            local network_driver
            network_driver=$(docker network inspect "${network_name}" --format '{{.Driver}}' 2>/dev/null || echo "")

            if [[ "$network_driver" != "macvlan" ]]; then
                log_warn "Docker network ${network_name} has wrong driver: ${network_driver} (expected: macvlan)"
                log_info "Recreating Docker network..."

                # Check if any containers are connected
                local connected_containers
                connected_containers=$(docker network inspect "${network_name}" --format '{{range $k,$v := .Containers}}{{$k}} {{end}}' 2>/dev/null || echo "")

                if [[ -n "$connected_containers" ]]; then
                    log_warn "Containers still connected to network: ${connected_containers}"
                    log_info "Disconnecting containers..."
                    for container_id in $connected_containers; do
                        docker network disconnect -f "${network_name}" "$container_id" 2>/dev/null || true
                    done
                fi

                docker network rm "${network_name}" 2>/dev/null || true
                docker_network_exists=false
            fi
        fi
    fi

    # Network validation passed or was cleaned up
    return 0
}

# Cleanup stale containers and networks
cleanup_stale_resources() {
    local cleaned_something=false

    # Check for stale containers
    if has_stale_container; then
        log_warn "Found stale isle-agent container"
        log_info "Removing stale container..."
        docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true
        cleaned_something=true
    fi

    # Validate and cleanup Docker network
    if validate_docker_network; then
        if $cleaned_something; then
            log_success "Stale resources cleaned up"
        fi
    else
        log_error "Failed to validate Docker network"
        return 1
    fi

    return 0
}

# Completely cleanup network cache (force clean restart)
cleanup_network_cache() {
    local force="${1:-false}"

    log_info "Cleaning network cache..."
    echo ""

    # Warning if agent is running
    if is_running && [[ "$force" != "true" ]]; then
        log_warn "isle-agent is currently running"
        echo ""
        echo "This will stop the agent and remove all network configuration."
        echo "You will need to restart the agent after cleanup."
        echo ""
        echo -n "Continue? (y/N): "
        read -r response
        if [[ ! "$response" =~ ^[Yy]$ ]]; then
            log_info "Cleanup cancelled"
            return 1
        fi
        echo ""
    fi

    local cleaned=false

    # Step 1: Stop and remove all isle-agent containers
    log_info "[1/7] Stopping isle-agent containers..."
    if docker ps -a --filter "name=${CONTAINER_NAME}" --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        docker stop "${CONTAINER_NAME}" 2>/dev/null || true
        docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true
        log_success "Containers removed"
        cleaned=true
    else
        echo "  No containers to remove"
    fi
    echo ""

    # Step 2: Disconnect any containers from isle networks
    log_info "[2/7] Disconnecting containers from isle networks..."
    for network in isle-br-0 isle-agent-net; do
        if docker network inspect "$network" &>/dev/null; then
            local connected_containers
            connected_containers=$(docker network inspect "$network" --format '{{range $k,$v := .Containers}}{{$v.Name}} {{end}}' 2>/dev/null || echo "")

            if [[ -n "$connected_containers" ]]; then
                echo "  Disconnecting from $network: $connected_containers"
                for container in $connected_containers; do
                    docker network disconnect -f "$network" "$container" 2>/dev/null || true
                done
                cleaned=true
            fi
        fi
    done
    echo "  All containers disconnected"
    echo ""

    # Step 3: Remove Docker networks
    log_info "[3/7] Removing Docker networks..."
    for network in isle-br-0 isle-agent-net isle-sample-app_default; do
        if docker network inspect "$network" &>/dev/null; then
            docker network rm "$network" 2>/dev/null && echo "  Removed $network" || echo "  Failed to remove $network (may be in use)"
            cleaned=true
        fi
    done
    echo ""

    # Step 4: Remove system bridge interface
    log_info "[4/7] Removing system bridge interface..."
    if ip link show isle-br-0 &>/dev/null; then
        echo "  Found isle-br-0 system bridge, removing..."

        # Try without sudo first
        if ip link set isle-br-0 down 2>/dev/null && ip link delete isle-br-0 2>/dev/null; then
            log_success "System bridge removed"
            cleaned=true
        elif command -v sudo &>/dev/null; then
            # Try with sudo
            sudo ip link set isle-br-0 down 2>/dev/null || true
            sudo ip link delete isle-br-0 2>/dev/null || true
            log_success "System bridge removed (with sudo)"
            cleaned=true
        else
            log_warn "Could not remove system bridge (need root permissions)"
        fi
    else
        echo "  No system bridge found"
    fi
    echo ""

    # Step 5: Prune unused Docker networks
    log_info "[5/7] Pruning unused Docker networks..."
    docker network prune -f 2>/dev/null || true
    echo ""

    # Step 6: Remove temporary Compose files
    log_info "[6/7] Removing temporary Compose files..."
    if [[ -f "${ISLE_AGENT_DIR}/docker-compose.mdns.yml" ]]; then
        rm -f "${ISLE_AGENT_DIR}/docker-compose.mdns.yml" 2>/dev/null || true
        echo "  Removed mDNS compose file"
        cleaned=true
    else
        echo "  No temporary files found"
    fi
    echo ""

    # Step 7: Clear agent mode cache
    log_info "[7/7] Clearing agent mode cache..."
    if [[ -f "${ISLE_AGENT_DIR}/agent.mode" ]]; then
        rm -f "${ISLE_AGENT_DIR}/agent.mode" 2>/dev/null || true
        echo "  Cleared agent mode file"
        cleaned=true
    else
        echo "  No mode cache found"
    fi
    echo ""

    if $cleaned; then
        log_success "Network cache cleaned successfully"
        echo ""
        echo "Verification:"
        echo "  Docker networks:"
        docker network ls | grep -E "NETWORK|isle" || echo "    (no isle networks found)"
        echo ""
        echo "  System bridge:"
        ip link show isle-br-0 2>/dev/null || echo "    (isle-br-0 not found)"
        echo ""
        echo "Next steps:"
        echo "  1. Start the agent: isle agent start"
        echo "  2. Verify setup: isle agent verify-setup"
    else
        log_info "Network cache is already clean"
    fi

    return 0
}


# === COMPONENT SETUP FUNCTIONS ===

# Setup Component 1: Host Agent (systemd service)
setup_host_agent() {
    log_info "Setting up host agent (isle-host-agent systemd service)..."

    # Check if host agent files exist
    if [[ ! -d "${HOST_AGENT_DIR}" ]]; then
        log_error "Host agent directory not found: ${HOST_AGENT_DIR}"
        return 1
    fi

    # Ensure avahi-daemon is installed (required by isle-host-agent.service)
    if ! command -v avahi-daemon &>/dev/null; then
        log_info "avahi-daemon not found, installing..."
        if sudo apt-get install -y avahi-daemon avahi-utils &>/dev/null; then
            sudo systemctl enable avahi-daemon 2>/dev/null || true
            sudo systemctl start avahi-daemon 2>/dev/null || true
            log_success "avahi-daemon installed and started"
        else
            log_error "Failed to install avahi-daemon (required for host agent)"
            return 1
        fi
    elif ! systemctl is-active --quiet avahi-daemon 2>/dev/null; then
        log_info "Starting avahi-daemon..."
        sudo systemctl start avahi-daemon 2>/dev/null || true
    fi

    # Create required directories
    sudo mkdir -p /usr/local/bin/isle-mesh
    sudo mkdir -p /var/log/isle-mesh

    # Install host agent files
    sudo cp "${HOST_AGENT_DIR}/isle-host-agent-relay.sh" /usr/local/bin/isle-mesh/
    sudo chmod +x /usr/local/bin/isle-mesh/isle-host-agent-relay.sh

    # Copy config if doesn't exist
    if [[ ! -f /etc/isle-mesh/agent/host-agent.conf ]]; then
        sudo cp "${HOST_AGENT_DIR}/host-agent.conf" /etc/isle-mesh/agent/
        log_info "Created host agent config at /etc/isle-mesh/agent/host-agent.conf"
    else
        log_info "Host agent config already exists, skipping"
    fi

    # Copy systemd service (always overwrite to pick up fixes)
    sudo cp "${HOST_AGENT_DIR}/isle-host-agent.service" /etc/systemd/system/
    sudo systemctl daemon-reload

    # Clear any prior failed state before starting
    sudo systemctl stop isle-host-agent 2>/dev/null || true
    sudo systemctl reset-failed isle-host-agent 2>/dev/null || true

    # Enable and start the service
    log_info "Enabling and starting isle-host-agent service..."
    sudo systemctl enable isle-host-agent 2>/dev/null || true
    sudo systemctl start isle-host-agent

    # Wait a moment and check if it started
    sleep 2

    if systemctl is-active --quiet isle-host-agent; then
        log_success "Host agent service is running"
    else
        log_error "Host agent service failed to start"
        echo "  Check logs: sudo journalctl -u isle-host-agent -n 50"
        return 1
    fi

    return 0
}


# Setup Component 2: VLAN Agent (nginx container with registry-watcher)
setup_vlan_agent() {
    log_info "Setting up VLAN agent (isle-vlan-agent container)..."

    # Check if vlan agent directory exists
    if [[ ! -d "${VLAN_AGENT_DIR}" ]]; then
        log_error "VLAN agent directory not found: ${VLAN_AGENT_DIR}"
        return 1
    fi

    # Verify Dockerfile and required scripts exist
    if [[ ! -f "${VLAN_AGENT_DIR}/Dockerfile" ]]; then
        log_error "VLAN agent Dockerfile not found: ${VLAN_AGENT_DIR}/Dockerfile"
        return 1
    fi

    # Build the custom image (includes registry-watcher + generate-nginx-configs)
    log_info "Building isle-vlan-agent image..."
    if ! docker build -t isle-vlan-agent "${VLAN_AGENT_DIR}"; then
        log_error "Failed to build isle-vlan-agent image"
        return 1
    fi

    # Ensure host directories exist for volume mounts
    mkdir -p "${ISLE_AGENT_DIR}/nginx/configs"
    mkdir -p "${ISLE_AGENT_DIR}/nginx"

    log_success "VLAN agent image built"
    return 0
}

# Copy docker-compose.yml to /etc/isle-mesh/agent
copy_compose_file() {
    log_info "Copying docker-compose.yml to /etc/isle-mesh/agent..."

    # Copy from source
    local source_compose="${PROJECT_ROOT}/isle-agent/docker-compose.yml"

    if [[ ! -f "${source_compose}" ]]; then
        log_error "Source docker-compose.yml not found: ${source_compose}"
        return 1
    fi

    cp "${source_compose}" "${COMPOSE_FILE}"
    log_success "docker-compose.yml copied to ${COMPOSE_FILE}"
    return 0
}

# Start all agent components
start_agent() {
    echo ""
    log_info "=== Starting Isle Agent (Two-Component Architecture) ==="
    echo ""

    # Initialize directories if needed
    init_agent_dir

    # Ensure mesh-mdns is installed and running
    ensure_mesh_mdns || log_warn "Continuing without mesh-mdns (domains won't be broadcasted)"

    # Check if already running
    if is_running; then
        log_warn "Agent components are already running"
        echo ""
        show_status
        return 0
    fi

    # Step 1: Setup all components (if not already done)
    echo "Step 1/3: Setting up components..."
    echo ""

    # Copy compose file if it doesn't exist
    if [[ ! -f "${COMPOSE_FILE}" ]]; then
        copy_compose_file || return 1
    fi

    # Setup vlan agent (build custom image)
    setup_vlan_agent || return 1
    echo ""

    # Setup host agent (required)
    if ! is_host_running; then
        setup_host_agent || return 1
        echo ""
    else
        log_info "Host agent already running, skipping setup"
        echo ""
    fi

    # Step 2: Start containers
    echo "Step 2/3: Starting containers..."
    cd "${PROJECT_ROOT}/isle-agent"

    if ! $DOCKER_COMPOSE_CMD up -d; then
        log_error "Failed to start agent containers"
        echo ""
        echo "Check logs with: docker logs isle-vlan-agent"
        return 1
    fi

    log_success "Containers started"
    echo ""

    # Step 3: Wait for health checks
    echo "Step 3/3: Waiting for health checks..."
    echo ""

    # Wait for vlan agent health
    log_info "Checking isle-vlan-agent..."
    local vlan_healthy=false
    for i in {1..30}; do
        if curl -sf http://localhost/health >/dev/null 2>&1; then
            vlan_healthy=true
            break
        fi
        sleep 1
    done

    if ! $vlan_healthy; then
        log_error "isle-vlan-agent failed to become healthy"
        echo "Check logs: docker logs isle-vlan-agent"
        return 1
    fi
    log_success "isle-vlan-agent is healthy"
    echo ""

    log_success "All agent components are running!"
    echo ""

    show_status

    echo ""
    log_info "Next steps:"
    echo "  - View vlan agent: http://localhost/"
    echo "  - Test health endpoint: http://health.local (if mdns is running)"
    echo "  - Check status: isle agent status"
    echo ""

    return 0
}

# Stop all agent components
stop_agent() {
    log_info "Stopping isle-agent components..."

    local mode
    mode=$(get_agent_mode)
    local any_running=false

    # Check if any components are running
    if is_host_running || is_vlan_running; then
        any_running=true
    fi

    # Also check remote container directly
    if docker ps --filter "name=${REMOTE_CONTAINER}" --filter "status=running" --format '{{.Names}}' | grep -q "^${REMOTE_CONTAINER}$"; then
        any_running=true
    fi

    if ! $any_running; then
        log_warn "No agent components are running"
        return 0
    fi

    # Stop host agent (systemd service)
    if is_host_running; then
        log_info "Stopping host agent..."
        sudo systemctl stop isle-host-agent 2>/dev/null || true
        log_success "Host agent stopped"
    fi

    # Stop containers
    cd "${PROJECT_ROOT}/isle-agent"

    if [[ "$mode" == "remote" ]]; then
        # Remote mode: stop remote container and clean up macvlan
        if docker ps -a --filter "name=${REMOTE_CONTAINER}" --format '{{.Names}}' | grep -q "^${REMOTE_CONTAINER}$"; then
            log_info "Stopping ${REMOTE_CONTAINER}..."
            docker stop "${REMOTE_CONTAINER}" 2>/dev/null || true
            docker rm -f "${REMOTE_CONTAINER}" 2>/dev/null || true
            log_success "Remote agent stopped"
        fi

        # Remove macvlan network
        if docker network inspect isle-remote-macvlan &>/dev/null; then
            log_info "Removing isle-remote-macvlan network..."
            docker network rm isle-remote-macvlan 2>/dev/null || true
        fi
    else
        # Core mode: stop vlan container using compose
        if [[ -f "${COMPOSE_FILE}" ]]; then
            $DOCKER_COMPOSE_CMD down 2>/dev/null || true
        fi

        # Force remove any remaining containers
        if docker ps -a --filter "name=${VLAN_CONTAINER}" --format '{{.Names}}' | grep -q "^${VLAN_CONTAINER}$"; then
            log_info "Force removing ${VLAN_CONTAINER}..."
            docker rm -f "${VLAN_CONTAINER}" 2>/dev/null || true
        fi
    fi

    log_success "All agent components stopped"
}

# Restart the isle-agent container
restart_agent() {
    log_info "Restarting isle-agent..."
    stop_agent
    sleep 2
    start_agent
}

# Reload nginx configuration without restarting container
reload_config() {
    log_info "Reloading nginx configuration..."

    if ! is_vlan_running; then
        log_error "isle-vlan-agent is not running. Start it first with 'isle agent start'"
        return 1
    fi

    # Test config first
    if ! docker exec "${VLAN_CONTAINER}" nginx -t 2>&1; then
        log_error "nginx configuration test failed. Not reloading."
        return 1
    fi

    # Reload nginx
    docker exec "${VLAN_CONTAINER}" nginx -s reload

    log_success "nginx configuration reloaded"
}

# Machine-readable app listing for isle-manager-app UI
# Output format: one JSON object per line (JSONL)
list_apps() {
    if [[ ! -f "${REGISTRY_FILE}" ]]; then
        echo "[]"
        return
    fi

    jq -c '[.apps | to_entries[] | {
        name: .key,
        domain: .value.domain,
        modes: (.value.modes // []),
        updated_at: (.value.updated_at // ""),
        services: [(.value.services // [])[] | {
            name: .name,
            subdomain: (.subdomain // ""),
            container: (.container // ""),
            port: (.port // 0),
            protocol: (.protocol // "")
        }]
    }]' "${REGISTRY_FILE}" 2>/dev/null || echo "[]"
}

# Show agent status
show_status() {
    echo ""
    local mode
    mode=$(get_agent_mode)
    echo "=== Isle Agent Status (Mode: ${mode}) ==="
    echo ""

    # Component 1: Host Agent (Systemd Service) - REQUIRED in core mode
    echo "Component 1: isle-host-agent (systemd service - mDNS, registry, auto-sync)"
    if is_host_running; then
        log_success "  Status: RUNNING"
        local agent_mode
        agent_mode=$(grep ISLE_AGENT_MODE /etc/isle-mesh/agent/host-agent.conf 2>/dev/null | cut -d= -f2 || echo 'unknown')
        echo "  Mode: ${agent_mode}"

        # Show what the host agent is doing
        echo "  Functions:"
        case "${agent_mode}" in
            broadcast)
                echo "    - mDNS broadcasting"
                echo "    - Registry watching & auto-sync"
                ;;
            registry)
                echo "    - Registry updates"
                echo "    - Registry watching & auto-sync"
                ;;
            both)
                echo "    - mDNS broadcasting"
                echo "    - Registry updates"
                echo "    - Registry watching & auto-sync"
                ;;
            *)
                echo "    - Unknown mode"
                ;;
        esac

        # Check uptime
        local uptime_info
        uptime_info=$(systemctl show isle-host-agent -p ActiveEnterTimestamp --value 2>/dev/null)
        if [[ -n "${uptime_info}" ]]; then
            echo "  Started: ${uptime_info}"
        fi
    else
        log_error "  Status: NOT RUNNING (REQUIRED)"
        echo "  The host agent is required for proper agent operation"
        echo "  Start with: isle agent start"
    fi
    echo ""

    # Component 2: VLAN/Remote Agent (Nginx Container)
    if [[ "$mode" == "remote" ]]; then
        echo "Component 2: isle-remote-agent (nginx proxy - remote mode)"
        if docker ps --filter "name=${REMOTE_CONTAINER}" --filter "status=running" --format '{{.Names}}' | grep -q "^${REMOTE_CONTAINER}$"; then
            log_success "  Status: RUNNING"
            docker ps --filter "name=${REMOTE_CONTAINER}" --format "  ID: {{.ID}}\n  Uptime: {{.Status}}"

            local mac_addr
            mac_addr=$(docker inspect "${REMOTE_CONTAINER}" --format '{{range .NetworkSettings.Networks}}{{.MacAddress}}{{end}}' | head -n1)
            local ip_addr
            ip_addr=$(docker exec "${REMOTE_CONTAINER}" ip -4 addr show eth0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' || echo "pending")
            echo "  MAC: ${mac_addr}"
            echo "  VLAN IP: ${ip_addr}"

            # Show isle info from discovery.json
            local remote_dir="${ISLE_AGENT_DIR}/remote"
            if [[ -f "${remote_dir}/discovery.json" ]]; then
                local isle_name
                isle_name=$(jq -r '.isle_name // "unknown"' "${remote_dir}/discovery.json" 2>/dev/null)
                local router_ip
                router_ip=$(jq -r '.router_ip // "unknown"' "${remote_dir}/discovery.json" 2>/dev/null)
                echo "  Isle: ${isle_name}"
                echo "  Router: ${router_ip}"
            fi
            if [[ -f "${remote_dir}/interface.conf" ]]; then
                echo "  Interface: $(cat "${remote_dir}/interface.conf")"
            fi
        else
            log_warn "  Status: STOPPED"
        fi
    else
        echo "Component 2: isle-vlan-agent (nginx proxy)"
        if is_vlan_running; then
            log_success "  Status: RUNNING"
            docker ps --filter "name=${VLAN_CONTAINER}" --format "  ID: {{.ID}}\n  Uptime: {{.Status}}"

            # Get MAC and IP
            local mac_addr
            mac_addr=$(docker inspect "${VLAN_CONTAINER}" --format '{{range .NetworkSettings.Networks}}{{.MacAddress}}{{end}}' | head -n1)
            local ip_addr
            ip_addr=$(docker inspect "${VLAN_CONTAINER}" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' | head -n1)
            echo "  MAC: ${mac_addr}"
            echo "  IP: ${ip_addr}"
            echo "  HTTP: http://localhost/"
        else
            log_warn "  Status: STOPPED"
        fi
    fi
    echo ""

    # Registered apps and mDNS broadcast status
    echo "Registered Mesh Apps:"
    if [[ -f "${REGISTRY_FILE}" ]]; then
        local app_count
        app_count=$(jq -r '.apps | length' "${REGISTRY_FILE}" 2>/dev/null || echo "0")
        if [[ "${app_count}" -eq 0 ]]; then
            echo "  (none)"
        else
            jq -r '.apps | to_entries[] | "  - \(.key): \(.value.domain)"' "${REGISTRY_FILE}"
        fi
    else
        echo "  (registry not found)"
    fi
    echo ""

    # mDNS Broadcast List
    echo "mDNS Broadcast List:"
    local mdns_list_file="/usr/local/etc/mesh-mdns-domains.list"
    if [[ -f "${mdns_list_file}" ]]; then
        local domain_count
        domain_count=$(grep -v '^[[:space:]]*$' "${mdns_list_file}" 2>/dev/null | grep -v '^#' | wc -l)
        if [[ "${domain_count}" -eq 0 ]]; then
            echo "  (none)"
        else
            echo "  Domains being broadcast:"
            grep -v '^[[:space:]]*$' "${mdns_list_file}" 2>/dev/null | grep -v '^#' | while read -r domain; do
                # Check if this domain is in the registry
                local in_registry=""
                if [[ -f "${REGISTRY_FILE}" ]]; then
                    if jq -e --arg domain "$domain" '.apps[] | select(.domain == $domain)' "${REGISTRY_FILE}" >/dev/null 2>&1; then
                        in_registry=" [in registry]"
                    elif jq -e --arg domain "$domain" '.domains[$domain]' "${REGISTRY_FILE}" >/dev/null 2>&1; then
                        in_registry=" [in registry]"
                    fi
                fi
                echo "  - ${domain}${in_registry}"
            done
        fi
    else
        echo "  (mdns list file not found at ${mdns_list_file})"
        echo "  Note: mDNS broadcasting may not be configured"
    fi
    echo ""

    # Overall status
    if is_running; then
        log_success "Overall: All required components are running"
    else
        log_warn "Overall: Some required components are stopped"
        echo ""
        if ! is_host_running; then
            echo "  - Host agent (isle-host-agent) is not running"
        fi
        if ! is_vlan_running; then
            echo "  - VLAN agent (isle-vlan-agent) is not running"
        fi
        echo ""
        echo "  Start with: isle agent start"
    fi

    echo ""
}

# Show logs
show_logs() {
    local follow="${1:-false}"
    local component="${2:-all}"

    # Determine which logs to show
    case "${component}" in
        vlan)
            if ! is_vlan_running; then
                log_error "isle-vlan-agent is not running"
                return 1
            fi
            if [[ "${follow}" == "true" ]]; then
                docker logs -f "${VLAN_CONTAINER}"
            else
                docker logs --tail 50 "${VLAN_CONTAINER}"
            fi
            ;;
        host)
            log_info "Host agent logs:"
            sudo journalctl -u "${HOST_SERVICE}" -n 50 ${follow:+-f}
            ;;
        all|*)
            echo "=== VLAN Agent Logs ==="
            if is_vlan_running; then
                docker logs --tail 20 "${VLAN_CONTAINER}"
            else
                echo "(not running)"
            fi
            echo ""
            echo "=== Host Agent Logs ==="
            sudo journalctl -u "${HOST_SERVICE}" -n 20 --no-pager 2>/dev/null || echo "(not available)"
            echo ""
            echo "Use 'isle agent logs vlan' or 'isle agent logs host' for detailed logs"
            ;;
    esac
}

# Test nginx configuration
test_config() {
    log_info "Testing nginx configuration..."

    if ! is_vlan_running; then
        log_error "isle-vlan-agent is not running"
        echo "  Start the agent first: isle agent start"
        return 1
    fi

    # Test config in running container
    docker exec "${VLAN_CONTAINER}" nginx -t

    log_success "nginx configuration is valid"
}

# Verify all components are healthy
verify_setup() {
    log_info "Verifying agent setup..."
    echo ""

    local all_checks_passed=true

    # Check 1: Host Agent (Required)
    echo "=== Check 1: Host Agent (isle-host-agent - REQUIRED) ==="
    if is_host_running; then
        log_success "Host agent is running"
        local agent_mode
        agent_mode=$(grep ISLE_AGENT_MODE /etc/isle-mesh/agent/host-agent.conf 2>/dev/null | cut -d= -f2 || echo 'unknown')
        echo "  Mode: ${agent_mode}"

        # Check if it's been running for a bit (should be stable)
        local uptime_seconds
        uptime_seconds=$(systemctl show isle-host-agent -p ActiveEnterTimestampMonotonic --value 2>/dev/null)
        if [[ -n "${uptime_seconds}" ]] && [[ "${uptime_seconds}" != "0" ]]; then
            log_success "Service is stable"
        fi

        # Check logs for errors
        local recent_errors
        recent_errors=$(sudo journalctl -u isle-host-agent -n 20 --no-pager 2>/dev/null | grep -i "error" | wc -l)
        if [[ "${recent_errors}" -gt 0 ]]; then
            log_warn "Found ${recent_errors} recent error(s) in logs"
            echo "  Check logs: sudo journalctl -u isle-host-agent -n 50"
        fi
    else
        log_error "Host agent is not running (REQUIRED)"
        echo "  The host agent manages mDNS, registry updates, and auto-sync"
        echo "  Start with: isle agent start"
        all_checks_passed=false
    fi
    echo ""

    # Check 2: VLAN agent health
    echo "=== Check 2: VLAN Agent (isle-vlan-agent) ==="
    if is_vlan_running; then
        if curl -sf http://localhost/health >/dev/null 2>&1; then
            log_success "VLAN agent is healthy"
            if docker exec "${VLAN_CONTAINER}" nginx -t >/dev/null 2>&1; then
                log_success "Nginx configuration is valid"
            else
                log_warn "Nginx configuration has issues"
            fi
        else
            log_error "VLAN agent is not responding to health checks"
            all_checks_passed=false
        fi
    else
        log_error "VLAN agent is not running"
        all_checks_passed=false
    fi
    echo ""

    # Summary
    echo "=== Summary ==="
    if $all_checks_passed; then
        log_success "All required components are healthy!"
        echo ""
        echo "The agent system is fully operational:"
        echo "  - Host agent: Managing mDNS, registry, and auto-sync"
        echo "  - VLAN agent: Reverse proxy for mesh apps"
        echo ""
        echo "Next steps:"
        echo "  - Deploy mesh apps: isle app up"
        echo "  - View registered apps: isle agent status"
        echo "  - Check logs: isle agent logs"
    else
        log_error "Some checks failed. Review the output above."
        return 1
    fi
    echo ""
}

# Register an app with the agent
register_app() {
    local app_name="" domain="" container="" port="80" protocol="http"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --name) app_name="$2"; shift 2 ;;
            --domain) domain="$2"; shift 2 ;;
            --container) container="$2"; shift 2 ;;
            --port) port="$2"; shift 2 ;;
            --protocol) protocol="$2"; shift 2 ;;
            *) log_error "Unknown option: $1"; return 1 ;;
        esac
    done

    # Validate required args
    if [[ -z "$app_name" ]]; then
        log_error "Missing required --name argument"
        echo "Usage: register --name <name> --domain <domain> --container <container> [--port <port>] [--protocol <protocol>]"
        return 1
    fi
    if [[ -z "$domain" ]]; then
        log_error "Missing required --domain argument"
        return 1
    fi
    if [[ -z "$container" ]]; then
        log_error "Missing required --container argument"
        return 1
    fi

    # Ensure jq is available
    if ! command -v jq &>/dev/null; then
        log_error "jq is required for registry management"
        return 1
    fi

    # Ensure registry file exists
    if [[ ! -f "${REGISTRY_FILE}" ]]; then
        log_warn "Registry file not found, initializing..."
        init_agent_dir
    fi

    local now
    now=$(date -Iseconds)

    log_info "Registering app '${app_name}' (${domain})..."

    # Write entry to registry.json using jq
    # IMPORTANT: Use cp+rm instead of mv to preserve the file's inode.
    # Docker bind mounts are inode-based — mv creates a new inode and the
    # container would keep seeing the old file.
    local temp_file
    temp_file=$(mktemp)
    jq --arg name "$app_name" \
       --arg domain "$domain" \
       --arg container "$container" \
       --argjson port "$port" \
       --arg protocol "$protocol" \
       --arg now "$now" \
       '.apps[$name] = {
         "domain": $domain,
         "services": [
           { "name": $name, "subdomain": "", "container": $container, "port": $port, "protocol": $protocol }
         ],
         "modes": ["local"],
         "updated_at": $now
       }' "${REGISTRY_FILE}" > "$temp_file"
    cp "$temp_file" "${REGISTRY_FILE}"
    rm -f "$temp_file"
    chmod 664 "${REGISTRY_FILE}" 2>/dev/null || true

    log_success "App '${app_name}' registered in registry.json"

    # Add domain to mDNS broadcast list
    local mdns_list="/usr/local/etc/mesh-mdns-domains.list"
    if [[ -f "$mdns_list" ]]; then
        if ! grep -Fxq "$domain" "$mdns_list" 2>/dev/null; then
            echo "$domain" | sudo tee -a "$mdns_list" > /dev/null
            log_success "Added ${domain} to mDNS broadcast list"
            # Reload mDNS service
            sudo systemctl restart mesh-mdns.service 2>/dev/null || true
        else
            log_info "${domain} already in mDNS broadcast list"
        fi
    else
        log_warn "mDNS domain list not found at ${mdns_list}"
        log_info "Add domain manually with: isle mdns domain add ${domain}"
    fi

    # Auto-generate self-signed SSL certificate if not present
    local ssl_cert_dir="${ISLE_AGENT_DIR}/ssl/certs"
    local ssl_key_dir="${ISLE_AGENT_DIR}/ssl/keys"
    mkdir -p "$ssl_cert_dir" "$ssl_key_dir"

    if [[ ! -f "${ssl_cert_dir}/${domain}.crt" ]]; then
        log_info "Generating self-signed SSL certificate for ${domain}..."

        # Build SAN list: always include the domain itself
        local san_list="DNS:${domain}"

        # If domain ends in .local, also add the .isle variant
        local isle_domain="${domain%.local}"
        if [[ "$isle_domain" != "$domain" ]]; then
            isle_domain="${isle_domain}.isle"
            san_list="${san_list},DNS:${isle_domain}"
        fi

        if openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
            -keyout "${ssl_key_dir}/${domain}.key" \
            -out "${ssl_cert_dir}/${domain}.crt" \
            -subj "/CN=${domain}" \
            -addext "subjectAltName=${san_list}" 2>/dev/null; then
            log_success "Generated SSL certificate for ${domain}"
        else
            log_warn "Could not generate SSL cert for ${domain}"
            log_info "Generate manually: openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout ${ssl_key_dir}/${domain}.key -out ${ssl_cert_dir}/${domain}.crt -subj '/CN=${domain}'"
        fi
    else
        log_info "SSL certificate for ${domain} already exists"
    fi

    # Touch registry.json to trigger the registry-watcher in the vlan-agent
    touch "${REGISTRY_FILE}"
    log_success "Registry updated — vlan-agent will regenerate nginx configs"

    echo ""
    log_info "Registered app details:"
    echo "  Name:      ${app_name}"
    echo "  Domain:    ${domain}"
    echo "  Container: ${container}"
    echo "  Port:      ${port}"
    echo "  Protocol:  ${protocol}"
    echo ""
}

# Unregister an app from the agent
unregister_app() {
    local app_name=""
    local remove_ssl=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --name) app_name="$2"; shift 2 ;;
            --remove-ssl) remove_ssl=true; shift ;;
            *) log_error "Unknown option: $1"; return 1 ;;
        esac
    done

    if [[ -z "$app_name" ]]; then
        log_error "Missing required --name argument"
        echo "Usage: unregister --name <name> [--remove-ssl]"
        echo ""
        echo "Options:"
        echo "  --name <name>    App name as it appears in the registry"
        echo "  --remove-ssl     Also remove SSL certificates for the app's domain"
        echo ""
        # Show registered apps for convenience
        if [[ -f "${REGISTRY_FILE}" ]] && command -v jq &>/dev/null; then
            echo "Currently registered apps:"
            jq -r '.apps | to_entries[] | "  \(.key): \(.value.domain)"' "${REGISTRY_FILE}" 2>/dev/null || echo "  (could not read registry)"
        fi
        return 1
    fi

    if ! command -v jq &>/dev/null; then
        log_error "jq is required for registry management"
        return 1
    fi

    if [[ ! -f "${REGISTRY_FILE}" ]]; then
        log_error "Registry file not found: ${REGISTRY_FILE}"
        return 1
    fi

    # Check if app exists in registry
    local app_exists
    app_exists=$(jq -r --arg name "$app_name" '.apps[$name] // empty' "${REGISTRY_FILE}" 2>/dev/null)
    if [[ -z "$app_exists" ]]; then
        log_error "App '${app_name}' not found in registry"
        echo ""
        echo "Registered apps:"
        jq -r '.apps | to_entries[] | "  \(.key): \(.value.domain)"' "${REGISTRY_FILE}" 2>/dev/null
        return 1
    fi

    # Get domain before removing (for mDNS + SSL cleanup)
    local domain
    domain=$(jq -r --arg name "$app_name" '.apps[$name].domain // ""' "${REGISTRY_FILE}" 2>/dev/null)

    log_info "Unregistering app '${app_name}' (${domain})..."

    # Remove from registry.json
    local temp_file
    temp_file=$(mktemp)
    jq --arg name "$app_name" 'del(.apps[$name])' "${REGISTRY_FILE}" > "$temp_file"
    cp "$temp_file" "${REGISTRY_FILE}"
    rm -f "$temp_file"
    chmod 664 "${REGISTRY_FILE}" 2>/dev/null || true
    log_success "Removed '${app_name}' from registry"

    # Remove domain from mDNS broadcast list
    if [[ -n "$domain" ]]; then
        local mdns_list="/usr/local/etc/mesh-mdns-domains.list"
        if [[ -f "$mdns_list" ]] && grep -Fxq "$domain" "$mdns_list" 2>/dev/null; then
            # Check that no other app uses this domain
            local other_uses
            other_uses=$(jq -r --arg domain "$domain" '[.apps[] | select(.domain == $domain)] | length' "${REGISTRY_FILE}" 2>/dev/null || echo "0")
            if [[ "$other_uses" -eq 0 ]]; then
                sed -i "\|^${domain}$|d" "$mdns_list"
                log_success "Removed ${domain} from mDNS broadcast list"
                sudo systemctl restart mesh-mdns.service 2>/dev/null || true
            else
                log_info "Keeping ${domain} in mDNS list (still used by another app)"
            fi
        fi
    fi

    # Optionally remove SSL certificates
    if $remove_ssl && [[ -n "$domain" ]]; then
        local ssl_cert="${ISLE_AGENT_DIR}/ssl/certs/${domain}.crt"
        local ssl_key="${ISLE_AGENT_DIR}/ssl/keys/${domain}.key"
        if [[ -f "$ssl_cert" ]] || [[ -f "$ssl_key" ]]; then
            rm -f "$ssl_cert" "$ssl_key"
            log_success "Removed SSL certificate for ${domain}"
        fi
    fi

    # Touch registry to trigger nginx config regeneration
    touch "${REGISTRY_FILE}"
    log_success "Registry updated — vlan-agent will regenerate nginx configs"

    echo ""
    log_info "App '${app_name}' has been unregistered"
    if [[ -n "$domain" ]] && ! $remove_ssl; then
        log_info "SSL certificates for ${domain} were kept (use --remove-ssl to delete)"
    fi
    echo ""
}

# Main command handler
main() {
    local command="${1:-help}"

    # Commands that don't require Docker
    case "${command}" in
        help|--help|-h|init|register|unregister|list-apps)
            ;;
        *)
            # All other commands require Docker
            check_docker_available || exit 1
            ;;
    esac

    case "${command}" in
        start)
            start_agent
            ;;
        stop)
            stop_agent
            ;;
        restart)
            restart_agent
            ;;
        reload)
            reload_config
            ;;
        status)
            show_status
            ;;
        logs)
            show_logs "${2:-false}" "${3:-all}"
            ;;
        test)
            test_config
            ;;
        verify-setup|verify)
            verify_setup
            ;;
        setup-host)
            setup_host_agent
            ;;
        setup-vlan)
            setup_vlan_agent
            ;;
        register)
            shift  # remove 'register' from $@
            register_app "$@"
            ;;
        unregister)
            shift  # remove 'unregister' from $@
            unregister_app "$@"
            ;;
        list-apps)
            list_apps
            ;;
        cleanup-cache|clean-cache|cleanup)
            cleanup_network_cache
            ;;
        init)
            init_agent_dir
            ;;
        help|--help|-h)
            cat <<EOF
Isle Agent Manager - Manage the two-component agent architecture

Usage: $(basename "$0") <command>

=== TWO-COMPONENT ARCHITECTURE ===

The Isle Agent consists of two independent components:
  1. isle-host-agent  : Systemd service (mDNS broadcasting, registry updates, auto-sync)
  2. isle-vlan-agent  : Nginx container (reverse proxy with registry-watcher)

The host agent is a consolidated service that handles:
  - mDNS broadcasting for .local domains
  - Registry updates from domain list
  - Automatic watching of registry.json for changes
  - Auto-sync of .local domains to mDNS broadcast list

The VLAN agent watches registry.json and auto-generates nginx configs.

Commands:
  Lifecycle:
    start                   Start all agent components (automated setup)
    stop                    Stop all agent containers
    restart                 Restart all agent components
    reload                  Reload nginx config without restarting
    status                  Show status of all components

  Setup (Individual Components):
    setup-host              Setup host agent (systemd service)
    setup-vlan              Setup VLAN agent (build custom nginx image)

  App Registration:
    register                Register an app with the agent
                            Options: --name, --domain, --container, --port, --protocol
    unregister              Unregister an app from the agent
                            Options: --name [--remove-ssl]

  Verification:
    verify-setup            Verify all components are healthy
    test                    Test nginx configuration validity
    logs [follow] [component]  Show logs (component: vlan|host|all)

  Configuration:
    init                    Initialize agent directory structure
    help                    Show this help message

Workflow:
  1. Start all components (fully automated):
     $(basename "$0") start

  2. Verify all components are healthy:
     $(basename "$0") verify-setup

  3. Check status:
     $(basename "$0") status

Examples:
  $(basename "$0") start                      # Start all components (auto-setup)
  $(basename "$0") verify-setup               # Check all components are healthy
  $(basename "$0") status                     # Show status of all components
  $(basename "$0") register --name myapp --domain myapp.local --container myapp-1 --port 8080
  $(basename "$0") logs vlan                  # View VLAN agent logs
  $(basename "$0") logs host                  # View host agent logs

The 'start' command orchestrates setup and health checks for all components.

EOF
            ;;
        *)
            log_error "Unknown command: ${command}"
            echo "Run '$(basename "$0") help' for usage"
            exit 1
            ;;
    esac
}

# Run main command
main "$@"
