#!/usr/bin/env bash
#
# Isle Agent Manager - Lifecycle management for unified nginx proxy
#
# Manages the single isle-agent container that serves all mesh apps
# with virtual MAC address for OpenWRT router integration

set -euo pipefail

# Configuration
ISLE_AGENT_DIR="/etc/isle-mesh/agent"
COMPOSE_FILE="/etc/isle-mesh/agent/docker-compose.yml"
CONTAINER_NAME="isle-agent"
REGISTRY_FILE="/etc/isle-mesh/agent/registry.json"
NGINX_CONF="/etc/isle-mesh/agent/nginx.conf"

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

# Initialize agent directory structure
init_agent_dir() {
    log_info "Initializing isle-agent directory structure..."

    # Create directory structure in /etc/isle-mesh/agent
    sudo mkdir -p "${ISLE_AGENT_DIR}"/{configs,ssl/{certs,keys},logs,mdns/services}

    # Copy docker-compose.yml if it doesn't exist
    if [[ ! -f "${COMPOSE_FILE}" ]]; then
        log_info "Installing agent docker-compose.yml..."
        sudo cp "$(dirname "$0")/../docker-compose.yml" "${COMPOSE_FILE}"
    fi

    # Initialize empty registry if doesn't exist
    if [[ ! -f "${REGISTRY_FILE}" ]]; then
        log_info "Creating domain registry..."
        echo '{"domains": {}, "subdomains": {}, "apps": {}}' | sudo tee "${REGISTRY_FILE}" > /dev/null
    fi

    # Create base nginx config if doesn't exist
    if [[ ! -f "${NGINX_CONF}" ]]; then
        log_info "Creating base nginx config..."
        create_base_nginx_config
    fi

    # Set permissions
    sudo chown -R "$(whoami):$(whoami)" "${ISLE_AGENT_DIR}" 2>/dev/null || true

    log_success "Agent directory initialized at ${ISLE_AGENT_DIR}"
}

# Create base nginx configuration
create_base_nginx_config() {
    cat <<'EOF' | sudo tee "${NGINX_CONF}" > /dev/null
# Isle Agent - Base nginx configuration
# This file is auto-generated and includes all mesh-app fragments

user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {
    worker_connections 1024;
    use epoll;
    multi_accept on;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    # Logging
    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for"';
    access_log /var/log/nginx/access.log main;

    # Performance
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;

    # Security headers (default)
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;

    # Health check endpoint
    server {
        listen 80 default_server;
        server_name _;

        location /health {
            access_log off;
            return 200 "isle-agent healthy\n";
            add_header Content-Type text/plain;
        }

        location / {
            return 404 "No mesh apps registered\n";
            add_header Content-Type text/plain;
        }
    }

    # Include all mesh-app config fragments
    include /etc/nginx/configs/*.conf;
}
EOF
}

# Setup isle-br-0 bridge for OpenWRT connectivity
setup_isle_bridge() {
    local bridge_name="isle-br-0"

    log_info "Checking ${bridge_name} bridge for OpenWRT connectivity..."

    # Check if bridge already exists
    if ip link show "${bridge_name}" &>/dev/null; then
        log_success "${bridge_name} bridge already exists"
    else
        log_warn "${bridge_name} bridge does not exist"
        echo ""
        echo "  The bridge needs to be created before starting the agent."
        echo "  Run this command to create it:"
        echo ""
        echo "    sudo ip link add name ${bridge_name} type bridge && sudo ip link set ${bridge_name} up"
        echo ""
        echo "  Attempting to create bridge automatically..."

        # Try to create bridge (will fail gracefully if no sudo access)
        if ip link add name "${bridge_name}" type bridge 2>/dev/null && \
           ip link set "${bridge_name}" up 2>/dev/null; then
            log_success "${bridge_name} bridge created without sudo"
        elif command -v sudo &>/dev/null && sudo -n true 2>/dev/null; then
            # Passwordless sudo is available
            sudo ip link add name "${bridge_name}" type bridge
            sudo ip link set "${bridge_name}" up
            sudo ip link set "${bridge_name}" mtu 1500
            log_success "${bridge_name} bridge created with sudo"
        else
            log_error "Cannot create ${bridge_name} bridge (need root permissions)"
            echo ""
            echo "  Please run the command above manually, then restart the agent."
            return 1
        fi
    fi

    # Check if OpenWRT router is connected to this bridge
    local connected_interfaces
    connected_interfaces=$(brctl show "${bridge_name}" 2>/dev/null | tail -n +2 | awk '{print $NF}' | grep -v "^${bridge_name}$" || true)

    if [[ -n "${connected_interfaces}" ]]; then
        log_success "OpenWRT router detected on ${bridge_name}"
        echo "  Connected interfaces: ${connected_interfaces}"
    else
        log_warn "OpenWRT router not yet connected to ${bridge_name}"
        echo ""
        echo "  The agent will start, but won't be reachable until OpenWRT is connected."
        echo "  To connect OpenWRT VM to this bridge, run:"
        echo ""
        echo "    virsh attach-interface openwrt-isle-router bridge ${bridge_name} --model virtio --config --live"
        echo ""
        echo "  Or if OpenWRT is already running with eth1, ensure it's on this bridge."
    fi
}

# Check if agent is running
is_running() {
    docker ps --filter "name=${CONTAINER_NAME}" --filter "status=running" --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"
}

# Check if stale container exists (stopped/exited/created)
has_stale_container() {
    # Check for containers in exited, created, or dead status
    docker ps -a --filter "name=${CONTAINER_NAME}" --format '{{.Names}} {{.Status}}' | \
        grep "^${CONTAINER_NAME}" | \
        grep -qE "(Exited|Created|Dead)"
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
        sudo rm -f "${ISLE_AGENT_DIR}/docker-compose.mdns.yml" 2>/dev/null || rm -f "${ISLE_AGENT_DIR}/docker-compose.mdns.yml" 2>/dev/null || true
        echo "  Removed mDNS compose file"
        cleaned=true
    else
        echo "  No temporary files found"
    fi
    echo ""

    # Step 7: Clear agent mode cache
    log_info "[7/7] Clearing agent mode cache..."
    if [[ -f "${ISLE_AGENT_DIR}/agent.mode" ]]; then
        sudo rm -f "${ISLE_AGENT_DIR}/agent.mode" 2>/dev/null || rm -f "${ISLE_AGENT_DIR}/agent.mode" 2>/dev/null || true
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

# Check if agent can discover OpenWRT router via mDNS
check_router_mdns() {
    local quiet_mode="${1:-false}"

    if [[ "$quiet_mode" != "true" ]]; then
        log_info "Checking for OpenWRT router mDNS signal..."
    fi

    if ! is_running; then
        if [[ "$quiet_mode" != "true" ]]; then
            log_error "isle-agent is not running"
        fi
        return 1
    fi

    # Only use DNS resolution of openwrt.local to detect router
    local router_found=false
    local router_ip=""

    if docker exec "${CONTAINER_NAME}" sh -c "command -v getent" >/dev/null 2>&1; then
        local resolve_result
        resolve_result=$(docker exec "${CONTAINER_NAME}" getent hosts openwrt.local 2>/dev/null || echo "")

        if [[ -n "$resolve_result" ]]; then
            router_ip=$(echo "$resolve_result" | awk '{print $1}')
            router_found=true
        fi
    fi

    # Report results
    if [[ "$router_found" == true ]]; then
        if [[ "$quiet_mode" != "true" ]]; then
            log_success "OpenWRT router detected"
            echo "  Router: openwrt.local"
            echo "  IP: $router_ip"
        fi
        return 0
    else
        if [[ "$quiet_mode" != "true" ]]; then
            log_warn "Could not detect OpenWRT router"
            echo ""
            echo "  This may mean:"
            echo "    - OpenWRT router is not running"
            echo "    - Avahi/mDNS is not configured on the router"
            echo "    - Router is not connected to isle-br-0 bridge"
            echo "    - mDNS packets are not reaching the agent"
            echo ""
            echo "  To troubleshoot:"
            echo "    1. Verify router is running: isle router status"
            echo "    2. Check router connectivity: ping openwrt.local"
            echo "    3. Verify isle-br-0 bridge connects agent to router"
            echo ""
        fi
        return 1
    fi
}

# Get current agent mode (mdns or lightweight)
get_agent_mode() {
    local mode_file="${ISLE_AGENT_DIR}/agent.mode"
    if [[ -f "$mode_file" ]]; then
        cat "$mode_file"
    else
        echo "mdns"  # Default to mDNS for initial setup
    fi
}

# Set agent mode
set_agent_mode() {
    local mode="$1"
    local mode_file="${ISLE_AGENT_DIR}/agent.mode"
    echo "$mode" | sudo tee "$mode_file" > /dev/null
}

# Build the isle-agent-mdns image if needed
build_mdns_image() {
    local image_exists
    image_exists=$(docker images -q isle-agent-mdns:latest 2>/dev/null)

    if [[ -z "$image_exists" ]]; then
        log_info "Building isle-agent-mdns image (includes Avahi mDNS support)..."
        log_info "This may take a few minutes on first run..."

        local project_root
        project_root="$(cd "$(dirname "$0")/../.." && pwd)"
        cd "${project_root}/isle-agent-mdns"

        if docker build -t isle-agent-mdns:latest .; then
            log_success "isle-agent-mdns image built successfully"
        else
            log_error "Failed to build isle-agent-mdns image"
            return 1
        fi
    else
        log_info "isle-agent-mdns image already exists"
    fi

    return 0
}

# Start agent with mDNS support (for initial setup)
start_agent_mdns() {
    log_info "Starting isle-agent with mDNS support (setup mode)..."

    # Build the mDNS image if needed
    build_mdns_image || return 1

    # Create temporary docker-compose with mDNS image
    local temp_compose="${ISLE_AGENT_DIR}/docker-compose.mdns.yml"
    cat > "$temp_compose" <<'EOF'
version: '3.8'
services:
  isle-agent:
    image: isle-agent-mdns:latest
    container_name: isle-agent
    restart: unless-stopped
    mac_address: "02:00:00:00:0a:01"
    cap_add:
      - NET_ADMIN
      - NET_RAW
    ports:
      - "80:80"
      - "443:443"
      - "5353:5353/udp"
    volumes:
      - /etc/isle-mesh/agent/nginx.conf:/etc/nginx/nginx.conf:ro
      - /etc/isle-mesh/agent/configs:/etc/nginx/configs:ro
      - /etc/isle-mesh/agent/ssl/certs:/etc/nginx/ssl/certs:ro
      - /etc/isle-mesh/agent/ssl/keys:/etc/nginx/ssl/keys:ro
      - /etc/isle-mesh/agent/logs:/var/log/nginx
      - /etc/isle-mesh/agent/registry.json:/etc/isle-mesh/agent/registry.json:ro
      - /etc/isle-mesh/agent/mdns/services:/etc/avahi/services
      - /var/run/dbus:/var/run/dbus
    networks:
      isle-agent-net:
      isle-br-0:
        priority: 100
    labels:
      isle.component: "agent"
      isle.role: "proxy-mdns"
      isle.version: "1.0.0"
      isle.mdns: "enabled"
    healthcheck:
      test: ["CMD-SHELL", "wget --quiet --tries=1 --spider http://127.0.0.1/health && pgrep avahi-daemon"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 15s
networks:
  isle-agent-net:
    driver: bridge
    name: isle-agent-net
    ipam:
      config:
        - subnet: 172.20.0.0/16
  isle-br-0:
    driver: macvlan
    driver_opts:
      parent: isle-br-0
    name: isle-br-0
EOF

    # Start with mDNS compose file (with retry logic for network issues)
    log_info "Launching nginx + Avahi container with virtual MAC..."
    cd "${ISLE_AGENT_DIR}"

    local max_retries=2
    local retry_count=0
    local start_success=false

    while [[ $retry_count -lt $max_retries ]]; do
        if docker compose -f docker-compose.mdns.yml up -d 2>&1; then
            start_success=true
            break
        else
            retry_count=$((retry_count + 1))

            if [[ $retry_count -lt $max_retries ]]; then
                log_warn "Failed to start agent (attempt $retry_count/$max_retries)"
                log_info "Cleaning up stale resources and retrying..."

                # Remove any stale containers created during the failed attempt
                docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true

                # Brief pause before retry
                sleep 2
            else
                log_error "Failed to start agent after $max_retries attempts"
                return 1
            fi
        fi
    done

    if ! $start_success; then
        return 1
    fi

    # Mark as mDNS mode
    set_agent_mode "mdns"
}

# Start agent in lightweight mode (after setup confirmed)
start_agent_lightweight() {
    log_info "Starting isle-agent in lightweight mode (nginx only)..."

    # Start with regular compose file (with retry logic for network issues)
    log_info "Launching nginx container with virtual MAC..."
    cd "${ISLE_AGENT_DIR}"

    local max_retries=2
    local retry_count=0
    local start_success=false

    while [[ $retry_count -lt $max_retries ]]; do
        if docker compose up -d 2>&1; then
            start_success=true
            break
        else
            retry_count=$((retry_count + 1))

            if [[ $retry_count -lt $max_retries ]]; then
                log_warn "Failed to start agent (attempt $retry_count/$max_retries)"
                log_info "Cleaning up stale resources and retrying..."

                # Remove any stale containers created during the failed attempt
                docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true

                # Brief pause before retry
                sleep 2
            else
                log_error "Failed to start agent after $max_retries attempts"
                return 1
            fi
        fi
    done

    if ! $start_success; then
        return 1
    fi

    # Mark as lightweight mode
    set_agent_mode "lightweight"
}

# Start the isle-agent container (auto-detect mode)
start_agent() {
    # Initialize directories if needed
    init_agent_dir

    # Setup isle-br-0 bridge for OpenWRT connectivity
    setup_isle_bridge

    # Check if already running
    if is_running; then
        log_warn "isle-agent is already running"
        log_info "Current mode: $(get_agent_mode)"
        return 0
    fi

    # ALWAYS cleanup network cache before starting to prevent loops
    # This runs silently and handles all edge cases
    log_info "Cleaning network cache before start..."
    local network_issues=false

    # Check for common network cache issues
    if docker network inspect isle-br-0 &>/dev/null || docker network inspect isle-agent-net &>/dev/null; then
        # Networks exist - verify they're valid
        if ! validate_docker_network 2>/dev/null; then
            network_issues=true
        fi
    fi

    # Check for stale containers that might block network cleanup
    if docker ps -a --filter "name=${CONTAINER_NAME}" --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        network_issues=true
    fi

    # If any issues detected, run full cleanup (force mode, no prompts)
    if $network_issues; then
        log_warn "Detected stale network state, cleaning up..."

        # Stop any running containers first
        docker stop "${CONTAINER_NAME}" 2>/dev/null || true
        docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true

        # Disconnect all containers from isle networks
        for network in isle-br-0 isle-agent-net; do
            if docker network inspect "$network" &>/dev/null; then
                local connected_containers
                connected_containers=$(docker network inspect "$network" --format '{{range $k,$v := .Containers}}{{$v.Name}} {{end}}' 2>/dev/null || echo "")

                if [[ -n "$connected_containers" ]]; then
                    for container in $connected_containers; do
                        docker network disconnect -f "$network" "$container" 2>/dev/null || true
                    done
                fi
            fi
        done

        # Remove networks
        docker network rm isle-br-0 2>/dev/null || true
        docker network rm isle-agent-net 2>/dev/null || true

        # Remove system bridge if exists
        if ip link show isle-br-0 &>/dev/null; then
            sudo ip link set isle-br-0 down 2>/dev/null || true
            sudo ip link delete isle-br-0 2>/dev/null || true
        fi

        # Remove temp files
        sudo rm -f "${ISLE_AGENT_DIR}/docker-compose.mdns.yml" 2>/dev/null || rm -f "${ISLE_AGENT_DIR}/docker-compose.mdns.yml" 2>/dev/null || true
        sudo rm -f "${ISLE_AGENT_DIR}/agent.mode" 2>/dev/null || rm -f "${ISLE_AGENT_DIR}/agent.mode" 2>/dev/null || true

        log_success "Network cache cleaned"

        # Recreate the bridge after cleanup
        setup_isle_bridge
    else
        log_info "Network cache is clean"
    fi

    # Determine which version to start
    local mode=$(get_agent_mode)

    if [[ "$mode" == "lightweight" ]]; then
        log_info "Agent mode: lightweight (nginx only)"
        start_agent_lightweight
    else
        log_info "Agent mode: mDNS (setup/discovery)"
        log_info "Use 'isle agent switch-to-lightweight' after confirming setup"
        start_agent_mdns
    fi

    # Wait for health check
    log_info "Waiting for agent to be healthy..."
    for i in {1..30}; do
        if docker exec "${CONTAINER_NAME}" wget --quiet --tries=1 --spider http://127.0.0.1/health 2>/dev/null; then
            log_success "isle-agent started successfully"
            echo ""

            # Check for OpenWRT router mDNS connectivity
            check_router_mdns

            echo ""
            show_status
            return 0
        fi
        sleep 1
    done

    log_error "isle-agent failed to become healthy"
    return 1
}

# Stop the isle-agent container
stop_agent() {
    log_info "Stopping isle-agent..."

    if ! is_running; then
        log_warn "isle-agent is not running"
        return 0
    fi

    cd "${ISLE_AGENT_DIR}"

    # Stop both possible compose configurations to ensure cleanup
    # Try mDNS compose file first
    if [[ -f "docker-compose.mdns.yml" ]]; then
        log_info "Stopping mDNS configuration..."
        docker compose -f docker-compose.mdns.yml down 2>/dev/null || true
    fi

    # Then try regular compose file
    if [[ -f "docker-compose.yml" ]]; then
        log_info "Stopping regular configuration..."
        docker compose down 2>/dev/null || true
    fi

    # Force remove container if still exists
    if docker ps -a --filter "name=${CONTAINER_NAME}" --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        log_info "Force removing container..."
        docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true
    fi

    log_success "isle-agent stopped"
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

    if ! is_running; then
        log_error "isle-agent is not running. Start it first with 'isle agent start'"
        return 1
    fi

    # Test config first
    if ! docker exec "${CONTAINER_NAME}" nginx -t 2>&1; then
        log_error "nginx configuration test failed. Not reloading."
        return 1
    fi

    # Reload nginx
    docker exec "${CONTAINER_NAME}" nginx -s reload

    log_success "nginx configuration reloaded"
}

# Show agent status
show_status() {
    echo ""
    echo "=== Isle Agent Status ==="
    echo ""

    if is_running; then
        log_success "Status: RUNNING"

        # Container info
        echo ""
        echo "Container Details:"
        docker ps --filter "name=${CONTAINER_NAME}" --format "  ID: {{.ID}}\n  Image: {{.Image}}\n  Uptime: {{.Status}}\n  Ports: {{.Ports}}"

        # Network info
        echo ""
        echo "Network Configuration:"
        local mac_addr
        mac_addr=$(docker inspect "${CONTAINER_NAME}" --format '{{range .NetworkSettings.Networks}}{{.MacAddress}}{{end}}' | head -n1)
        local ip_addr
        ip_addr=$(docker inspect "${CONTAINER_NAME}" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' | head -n1)
        echo "  MAC Address: ${mac_addr}"
        echo "  IP Address: ${ip_addr}"

        # OpenWRT Router Detection Status
        echo ""
        echo "OpenWRT Router Detection:"
        echo "  Detection method: DNS resolution of openwrt.local"

        # Check if openwrt.local resolves
        local router_detected=false
        local router_ip=""
        if docker exec "${CONTAINER_NAME}" sh -c "command -v getent" >/dev/null 2>&1; then
            local resolve_result
            resolve_result=$(docker exec "${CONTAINER_NAME}" getent hosts openwrt.local 2>/dev/null || echo "")

            if [[ -n "$resolve_result" ]]; then
                router_ip=$(echo "$resolve_result" | awk '{print $1}')
                router_detected=true
                echo -e "  ${GREEN}✓ OpenWRT router detected${NC}"
                echo "    Router: openwrt.local"
                echo "    IP: ${router_ip}"
            else
                echo -e "  ${YELLOW}✗ OpenWRT router not detected${NC}"
                echo "    openwrt.local does not resolve"
            fi
        else
            echo -e "  ${RED}✗ Cannot check (getent not available)${NC}"
        fi

        # Isle Bridge Status
        echo ""
        echo "Bridge Status:"
        if ip link show isle-br-0 &>/dev/null; then
            local bridge_state
            bridge_state=$(ip link show isle-br-0 | grep -oP '(?<=state )\w+')
            echo "  Bridge isle-br-0: ${bridge_state}"

            local connected_interfaces
            connected_interfaces=$(brctl show isle-br-0 2>/dev/null | tail -n +2 | awk '{print $NF}' | grep -v "^isle-br-0$" | tr '\n' ', ' | sed 's/,$//' || echo "none")
            echo "  Connected interfaces: ${connected_interfaces}"
        else
            echo -e "  ${RED}✗ Bridge isle-br-0 does not exist${NC}"
        fi

        # Registered apps
        echo ""
        echo "Registered Mesh Apps:"
        if [[ -f "${REGISTRY_FILE}" ]]; then
            local app_count
            app_count=$(jq -r '.apps | length' "${REGISTRY_FILE}")
            if [[ "${app_count}" -eq 0 ]]; then
                echo "  (none)"
            else
                jq -r '.apps | to_entries[] | "  - \(.key): \(.value.domain)"' "${REGISTRY_FILE}"
            fi
        else
            echo "  (registry not found)"
        fi

    else
        log_warn "Status: STOPPED"
        echo ""
        echo "Start the agent with: isle agent start"
    fi

    echo ""
}

# Show logs
show_logs() {
    local follow="${1:-false}"

    if ! is_running; then
        log_error "isle-agent is not running"
        return 1
    fi

    if [[ "${follow}" == "true" ]]; then
        docker logs -f "${CONTAINER_NAME}"
    else
        docker logs --tail 50 "${CONTAINER_NAME}"
    fi
}

# Test nginx configuration
test_config() {
    log_info "Testing nginx configuration..."

    if ! is_running; then
        # Test config without running container
        docker run --rm -v "${NGINX_CONF}:/etc/nginx/nginx.conf:ro" nginx:alpine nginx -t
    else
        # Test config in running container
        docker exec "${CONTAINER_NAME}" nginx -t
    fi

    log_success "nginx configuration is valid"
}

# Verify setup is complete (mDNS working, router discovering agent)
verify_setup() {
    log_info "Verifying agent setup..."
    echo ""

    local all_checks_passed=true

    # Check 1: Agent is running
    echo "=== Check 1: Agent Status ==="
    if is_running; then
        log_success "Agent is running"
        local mode=$(get_agent_mode)
        echo "  Mode: $mode"
    else
        log_error "Agent is not running"
        all_checks_passed=false
    fi
    echo ""

    # Check 2: Router connectivity via mDNS
    echo "=== Check 2: Router Discovery ==="
    if check_router_mdns "quiet"; then
        log_success "Agent can discover OpenWRT router"
    else
        log_warn "Agent cannot discover router via mDNS (may still work via IP)"
        all_checks_passed=false
    fi
    echo ""

    # Check 3: Agent is broadcasting mDNS (if in mDNS mode)
    echo "=== Check 3: Agent mDNS Broadcasting ==="
    local mode=$(get_agent_mode)
    if [[ "$mode" == "mdns" ]]; then
        if docker exec "${CONTAINER_NAME}" pgrep avahi-daemon >/dev/null 2>&1; then
            log_success "Avahi daemon is running in agent"

            # Check if any services are registered
            local service_count=$(docker exec "${CONTAINER_NAME}" ls /etc/avahi/services/*.service 2>/dev/null | wc -l || echo "0")
            if [[ "$service_count" -gt 0 ]]; then
                log_success "Found $service_count mDNS service(s) registered"
            else
                log_warn "No mDNS services registered yet"
            fi
        else
            log_error "Avahi daemon is not running (expected in mDNS mode)"
            all_checks_passed=false
        fi
    else
        log_info "Agent is in lightweight mode (no mDNS broadcasting)"
    fi
    echo ""

    # Check 4: Router can discover agent (from router's perspective)
    echo "=== Check 4: Router Can Discover Agent ==="
    log_info "Testing from router's perspective..."
    # This would require SSH to router - skip for now
    log_info "To test manually from router:"
    echo "  ssh root@<router-ip> 'avahi-browse -a -t | grep isle'"
    echo ""

    # Summary
    echo "=== Summary ==="
    if $all_checks_passed; then
        log_success "All checks passed! Setup is complete."
        echo ""
        if [[ "$mode" == "mdns" ]]; then
            log_info "You can now switch to lightweight mode:"
            echo "  isle agent switch-to-lightweight"
        fi
    else
        log_warn "Some checks failed. Review the output above."
        return 1
    fi
}

# Switch from mDNS mode to lightweight mode
switch_to_lightweight() {
    log_info "Switching agent to lightweight mode..."

    if ! is_running; then
        log_error "Agent is not running"
        return 1
    fi

    local current_mode=$(get_agent_mode)
    if [[ "$current_mode" == "lightweight" ]]; then
        log_warn "Agent is already in lightweight mode"
        return 0
    fi

    # Verify setup is complete before switching
    echo ""
    log_info "Verifying setup before switching..."
    if ! check_router_mdns "quiet"; then
        log_warn "Router discovery check failed"
        echo ""
        echo -n "Continue anyway? (y/N): "
        read -r response
        if [[ ! "$response" =~ ^[Yy]$ ]]; then
            log_info "Switch cancelled"
            return 1
        fi
    fi

    echo ""
    log_info "Stopping mDNS-enabled agent..."
    stop_agent

    echo ""
    log_info "Starting lightweight agent..."
    start_agent_lightweight

    # Wait for health check
    log_info "Waiting for agent to be healthy..."
    for i in {1..30}; do
        if docker exec "${CONTAINER_NAME}" wget --quiet --tries=1 --spider http://127.0.0.1/health 2>/dev/null; then
            log_success "Agent switched to lightweight mode successfully"
            echo ""
            show_status
            return 0
        fi
        sleep 1
    done

    log_error "Agent failed to become healthy after switch"
    return 1
}

# Switch back to mDNS mode
switch_to_mdns() {
    log_info "Switching agent to mDNS mode..."

    if ! is_running; then
        log_error "Agent is not running"
        return 1
    fi

    local current_mode=$(get_agent_mode)
    if [[ "$current_mode" == "mdns" ]]; then
        log_warn "Agent is already in mDNS mode"
        return 0
    fi

    echo ""
    log_info "Stopping lightweight agent..."
    stop_agent

    echo ""
    log_info "Starting mDNS-enabled agent..."
    start_agent_mdns

    # Wait for health check
    log_info "Waiting for agent to be healthy..."
    for i in {1..30}; do
        if docker exec "${CONTAINER_NAME}" wget --quiet --tries=1 --spider http://127.0.0.1/health 2>/dev/null; then
            if docker exec "${CONTAINER_NAME}" pgrep avahi-daemon >/dev/null 2>&1; then
                log_success "Agent switched to mDNS mode successfully"
                echo ""
                show_status
                return 0
            fi
        fi
        sleep 1
    done

    log_error "Agent failed to become healthy after switch"
    return 1
}

# Main command handler
main() {
    local command="${1:-help}"

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
            show_logs "${2:-false}"
            ;;
        test)
            test_config
            ;;
        check-router|verify-router)
            check_router_mdns
            ;;
        verify-setup|verify)
            verify_setup
            ;;
        switch-to-lightweight|lightweight)
            switch_to_lightweight
            ;;
        switch-to-mdns|mdns)
            switch_to_mdns
            ;;
        cleanup-cache|clean-cache|cleanup)
            cleanup_network_cache
            ;;
        init)
            init_agent_dir
            ;;
        help|--help|-h)
            cat <<EOF
Isle Agent Manager - Manage the unified nginx proxy container

Usage: $(basename "$0") <command>

Commands:
  Lifecycle:
    start                   Start the isle-agent container (mDNS mode by default)
                           Automatically cleans network cache before starting
    stop                    Stop the isle-agent container
    restart                 Restart the isle-agent container
    reload                  Reload nginx config without restarting (zero-downtime)
    status                  Show agent status and registered apps

  Setup & Verification:
    verify-setup            Verify agent setup is complete (mDNS broadcasting, router discovery)
    check-router            Check if agent can discover OpenWRT router via mDNS
    switch-to-lightweight   Switch from mDNS mode to lightweight mode (after setup confirmed)
    switch-to-mdns          Switch from lightweight mode back to mDNS mode

  Troubleshooting:
    cleanup-cache           Force cleanup of all network cache (Docker networks, system bridge)
                           Use this if agent won't start or gets stuck in a loop
    logs [follow]           Show agent logs (add 'follow' to tail logs)
    test                    Test nginx configuration validity

  Configuration:
    init                    Initialize agent directory structure
    help                    Show this help message

Agent Modes:
  mDNS Mode (default):
    - Runs nginx + Avahi mDNS daemon
    - Broadcasts services for router auto-discovery
    - Uses more resources (~100MB memory)
    - Required for initial setup and domain registration

  Lightweight Mode (after setup):
    - Runs nginx only
    - Minimal resource usage (~20MB memory)
    - Domain mappings persist on router
    - Switch to this after verifying setup

Workflow:
  1. Start agent (defaults to mDNS mode):
     $(basename "$0") start

  2. Verify setup is working:
     $(basename "$0") verify-setup

  3. Switch to lightweight mode (optional):
     $(basename "$0") switch-to-lightweight

Examples:
  $(basename "$0") start                      # Start agent (mDNS mode, auto-cleans cache)
  $(basename "$0") verify-setup               # Check setup is complete
  $(basename "$0") switch-to-lightweight      # Switch to lightweight mode
  $(basename "$0") status                     # Check agent status and mode
  $(basename "$0") logs follow                # Tail agent logs
  $(basename "$0") cleanup-cache              # Force cleanup network cache (troubleshooting)

The isle-agent is a single container that serves all mesh apps with a
virtual MAC address for OpenWRT router integration.

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
