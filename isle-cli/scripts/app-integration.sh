#!/bin/bash
#
# Isle App Integration with Agent
# Ensures apps are properly connected to the agent and configured
#
# This script wraps app deployment to automatically:
# 1. Detect if agent exists
# 2. Ensure app connects to isle-agent-net
# 3. Generate nginx config fragment
# 4. Register app with agent
# 5. Reload agent

set -euo pipefail

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="/etc/isle-mesh/agent"
AGENT_CONFIGS_DIR="${AGENT_DIR}/configs"
AGENT_REGISTRY="${AGENT_DIR}/registry.json"

# Logging
log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }
log_step() {
    echo ""
    echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}$*${NC}"
    echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
    echo ""
}

# Detect if agent is running
is_agent_running() {
    docker ps --filter "name=isle-agent" --filter "status=running" --format '{{.Names}}' 2>/dev/null | grep -q "^isle-agent$"
}

# Detect agent and show status
detect_agent() {
    log_info "Checking for Isle Agent..."

    if ! is_agent_running; then
        log_warn "Isle Agent is not running"
        log_info "Apps should be deployed after starting the agent"
        log_info "Start agent with: ${CYAN}isle agent start${NC}"
        return 1
    fi

    log_success "Agent is running"

    # Get agent network info
    local agent_ip
    agent_ip=$(docker inspect isle-agent --format='{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' 2>/dev/null | awk '{print $1}')
    log_info "Agent IP: $agent_ip"

    # Check how many apps are registered
    if [[ -f "$AGENT_REGISTRY" ]]; then
        local app_count
        app_count=$(jq '.apps | length' "$AGENT_REGISTRY" 2>/dev/null || echo "0")
        log_info "Registered apps: $app_count"
    fi

    return 0
}

# Check if docker-compose.yml exists in current directory
find_compose_file() {
    local compose_file=""

    # Look for common docker-compose file names
    for name in docker-compose.yml docker-compose.yaml compose.yml compose.yaml; do
        if [[ -f "$name" ]]; then
            compose_file="$name"
            break
        fi
    done

    if [[ -z "$compose_file" ]]; then
        log_error "No docker-compose file found in current directory"
        return 1
    fi

    echo "$compose_file"
}

# Extract app information from docker-compose.yml
extract_app_info() {
    local compose_file="$1"

    # Extract app name from directory
    local app_name
    app_name=$(basename "$(pwd)")

    # Try to get container name from compose file
    local container_name
    container_name=$(grep "container_name:" "$compose_file" | head -1 | awk '{print $2}' | tr -d '"' || echo "$app_name")

    # Try to get domain from labels or environment
    local domain
    domain=$(grep -E "isle\.mesh\.domain=|DOMAIN=" "$compose_file" | head -1 | sed -E 's/.*[=:]\s*"?([^"]+)"?.*/\1/' || echo "${app_name}.local")

    # Try to get port from labels or ports mapping
    local port
    port=$(grep -E "isle\.mesh\.port=|^\s+- \"?[0-9]+:[0-9]+" "$compose_file" | head -1 | sed -E 's/.*[=:]"?([0-9]+).*/\1/' || echo "80")

    echo "$app_name|$container_name|$domain|$port"
}

# Check if docker-compose.yml has isle-agent-net network
check_network_config() {
    local compose_file="$1"

    if grep -q "isle-agent-net" "$compose_file"; then
        return 0
    else
        return 1
    fi
}

# Add isle-agent-net network to docker-compose.yml
add_agent_network() {
    local compose_file="$1"

    log_info "Adding isle-agent-net to $compose_file..."

    # Backup original file
    cp "$compose_file" "${compose_file}.backup"

    # Check if file already has networks section
    if grep -q "^networks:" "$compose_file"; then
        # Networks section exists, check if services use it
        if ! grep -A 10 "^services:" "$compose_file" | grep -q "networks:"; then
            # Add network to first service
            sed -i '/^services:/,/^[^ ]/ {
                /^  [a-zA-Z]/ {
                    N
                    s/\n/\n    networks:\n      - isle-agent-net\n/
                    P
                    D
                }
            }' "$compose_file"
        fi

        # Add isle-agent-net to networks definition if not present
        if ! grep -A 5 "^networks:" "$compose_file" | grep -q "isle-agent-net:"; then
            # Append to networks section
            sed -i '/^networks:/a\  isle-agent-net:\n    external: true\n    name: isle-agent-net' "$compose_file"
        fi
    else
        # No networks section, add everything
        cat >> "$compose_file" << 'EOF'

networks:
  isle-agent-net:
    external: true
    name: isle-agent-net
EOF

        # Add networks to all services
        # This is a simplified approach - might need refinement for complex files
        awk '/^services:/ { in_services=1; }
             in_services && /^  [a-z]/ && !done {
                 print;
                 print "    networks:";
                 print "      - isle-agent-net";
                 done=1;
                 next;
             }
             { print }' "$compose_file" > "${compose_file}.tmp"
        mv "${compose_file}.tmp" "$compose_file"
    fi

    log_success "Network configuration added"
    log_info "Backup saved: ${compose_file}.backup"
}

# Generate nginx config fragment for app
generate_nginx_config() {
    local app_name="$1"
    local container_name="$2"
    local domain="$3"
    local port="$4"

    local config_file="${AGENT_CONFIGS_DIR}/${app_name}.conf"

    log_info "Generating nginx config: $config_file"

    # Ensure configs directory exists
    sudo mkdir -p "$AGENT_CONFIGS_DIR" 2>/dev/null || mkdir -p "$AGENT_CONFIGS_DIR"

    # Generate config
    local config_content
    config_content=$(cat <<NGINX_CONFIG
# Configuration for ${app_name}
# Generated: $(date -Iseconds)
# Domain: ${domain}

# Upstream definition
upstream ${app_name}_backend {
    server ${container_name}:${port};
    keepalive 32;
}

# HTTP server
server {
    listen 80;

    # Dual-domain support: .local and .vlan
    server_name ${domain} ${domain%.local}.vlan;

    location / {
        proxy_pass http://${app_name}_backend;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";

        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    location /health {
        access_log off;
        proxy_pass http://${app_name}_backend/health;
    }
}

# HTTPS server (TODO: Add SSL cert generation)
# Uncomment after generating SSL certificates
# server {
#     listen 443 ssl;
#     server_name ${domain} ${domain%.local}.vlan;
#
#     ssl_certificate /etc/nginx/ssl/certs/${app_name}.crt;
#     ssl_certificate_key /etc/nginx/ssl/keys/${app_name}.key;
#
#     ssl_protocols TLSv1.2 TLSv1.3;
#     ssl_prefer_server_ciphers on;
#     ssl_ciphers HIGH:!aNULL:!MD5;
#
#     location / {
#         proxy_pass http://${app_name}_backend;
#         proxy_set_header Host \$host;
#         proxy_set_header X-Real-IP \$remote_addr;
#         proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
#         proxy_set_header X-Forwarded-Proto \$scheme;
#
#         proxy_http_version 1.1;
#         proxy_set_header Upgrade \$http_upgrade;
#         proxy_set_header Connection "upgrade";
#     }
# }
NGINX_CONFIG
)

    # Write config (with sudo if needed)
    if [[ -w "$AGENT_CONFIGS_DIR" ]]; then
        echo "$config_content" > "$config_file"
    else
        echo "$config_content" | sudo tee "$config_file" > /dev/null
    fi

    log_success "Nginx config generated"
}

# Register app in agent registry
register_app() {
    local app_name="$1"
    local domain="$2"

    log_info "Registering app in agent registry..."

    # Initialize registry if it doesn't exist
    if [[ ! -f "$AGENT_REGISTRY" ]]; then
        local registry_content='{"domains": {}, "subdomains": {}, "apps": {}}'
        if [[ -w "$AGENT_DIR" ]]; then
            echo "$registry_content" > "$AGENT_REGISTRY"
        else
            echo "$registry_content" | sudo tee "$AGENT_REGISTRY" > /dev/null
        fi
    fi

    # Check for domain conflicts
    local existing_app
    existing_app=$(jq -r ".apps | to_entries[] | select(.value.domain == \"$domain\") | .key" "$AGENT_REGISTRY" 2>/dev/null || echo "")

    if [[ -n "$existing_app" && "$existing_app" != "$app_name" ]]; then
        log_error "Domain conflict: $domain is already registered to $existing_app"
        return 1
    fi

    # Add/update app in registry
    local temp_registry
    temp_registry=$(mktemp)
    jq ".apps.\"$app_name\" = {\"domain\": \"$domain\", \"registered_at\": \"$(date -Iseconds)\", \"container\": \"$app_name\"}" \
        "$AGENT_REGISTRY" > "$temp_registry"

    if [[ -w "$AGENT_REGISTRY" ]]; then
        mv "$temp_registry" "$AGENT_REGISTRY"
    else
        sudo mv "$temp_registry" "$AGENT_REGISTRY"
    fi

    log_success "App registered: $app_name -> $domain"
}

# Reload agent nginx config
reload_agent() {
    log_info "Reloading agent nginx configuration..."

    # Test config first
    if ! docker exec isle-agent nginx -t 2>&1 | tail -2; then
        log_error "Nginx config test failed"
        return 1
    fi

    # Reload
    if docker exec isle-agent nginx -s reload 2>/dev/null; then
        log_success "Agent reloaded successfully"
        return 0
    else
        log_error "Failed to reload agent"
        return 1
    fi
}

# Main integration workflow
integrate_app() {
    log_step "App Integration with Isle Agent"

    # Step 1: Detect agent
    local agent_exists=false
    if detect_agent; then
        agent_exists=true
    fi

    # Step 2: Find docker-compose file
    local compose_file
    compose_file=$(find_compose_file) || return 1
    log_info "Using compose file: $compose_file"

    # Step 3: Extract app info
    local app_info
    app_info=$(extract_app_info "$compose_file")
    IFS='|' read -r app_name container_name domain port <<< "$app_info"

    log_info "App details:"
    log_info "  Name:      $app_name"
    log_info "  Container: $container_name"
    log_info "  Domain:    $domain"
    log_info "  Port:      $port"

    # Step 4: Check/fix network configuration
    if ! check_network_config "$compose_file"; then
        log_warn "App not configured for agent network"

        if [[ "$agent_exists" == "true" ]]; then
            log_info "Adding isle-agent-net to app configuration..."
            add_agent_network "$compose_file"
            log_success "Network configuration updated - app will connect to agent"
        else
            log_warn "Skipping network configuration (agent not running)"
            log_info "Start agent first, then redeploy app"
        fi
    else
        log_success "App already configured for agent network"
    fi

    # Step 5: Generate nginx config if agent exists
    if [[ "$agent_exists" == "true" ]]; then
        generate_nginx_config "$app_name" "$container_name" "$domain" "$port"
        register_app "$app_name" "$domain"
        reload_agent

        echo ""
        log_step "Integration Complete"
        log_success "App is ready to deploy with agent integration"
        log_info "Deploy with: ${CYAN}docker compose up -d${NC}"
        echo ""
        log_info "Once running, app will be accessible at:"
        log_info "  ${CYAN}http://${domain}${NC}"
        log_info "  ${CYAN}http://${domain%.local}.vlan${NC} (after router join)"
    else
        echo ""
        log_step "Agent Not Running"
        log_warn "App configuration updated, but agent integration skipped"
        log_info ""
        log_info "To complete integration:"
        log_info "  1. Start agent: ${CYAN}isle agent start${NC}"
        log_info "  2. Rerun integration: ${CYAN}$0 integrate${NC}"
        log_info "  3. Deploy app: ${CYAN}docker compose up -d${NC}"
    fi
}

# Fix existing deployed app
fix_deployed_app() {
    log_step "Fixing Deployed App Integration"

    # Find compose file
    local compose_file
    compose_file=$(find_compose_file) || return 1

    # Extract app info
    local app_info
    app_info=$(extract_app_info "$compose_file")
    IFS='|' read -r app_name container_name domain port <<< "$app_info"

    log_info "App: $app_name (container: $container_name)"

    # Check if container is running
    if ! docker ps --format '{{.Names}}' | grep -q "^${container_name}$"; then
        log_error "Container $container_name is not running"
        log_info "Deploy it first, then run this fix"
        return 1
    fi

    # Check if agent is running
    if ! is_agent_running; then
        log_error "Agent is not running"
        log_info "Start agent first: isle agent start"
        return 1
    fi

    log_info "Both app and agent are running"

    # Check network connectivity
    local app_networks
    app_networks=$(docker inspect "$container_name" --format='{{range .NetworkSettings.Networks}}{{.NetworkID}} {{end}}')
    local agent_network
    agent_network=$(docker inspect isle-agent --format='{{range .NetworkSettings.Networks}}{{.NetworkID}}{{end}}' | awk '{print $1}')

    if echo "$app_networks" | grep -q "$agent_network"; then
        log_success "App is already on agent network"
    else
        log_warn "App is not on agent network - fixing..."

        # Update compose file
        if ! check_network_config "$compose_file"; then
            add_agent_network "$compose_file"
        fi

        # Recreate container
        log_info "Recreating app container..."
        docker compose down
        docker compose up -d

        log_success "App reconnected to agent network"
    fi

    # Ensure nginx config exists
    if [[ ! -f "${AGENT_CONFIGS_DIR}/${app_name}.conf" ]]; then
        log_info "Creating nginx config..."
        generate_nginx_config "$app_name" "$container_name" "$domain" "$port"
        register_app "$app_name" "$domain"
        reload_agent
    else
        log_success "Nginx config already exists"
    fi

    echo ""
    log_step "Fix Complete"
    log_success "App should now be accessible via agent at:"
    log_info "  http://${domain}"
}

# Show help
show_help() {
    cat <<EOF
${BOLD}Isle App Integration${NC}

Automatically integrates apps with the Isle Agent for unified proxy access.

${BOLD}Usage:${NC}
  $(basename "$0") integrate    Prepare current app for agent integration
  $(basename "$0") fix          Fix already-deployed app to work with agent
  $(basename "$0") check        Check current app's agent integration status
  $(basename "$0") help         Show this help

${BOLD}Commands:${NC}
  integrate   Configure app for agent integration (run before deployment)
              - Detects agent status
              - Adds isle-agent-net to docker-compose.yml
              - Generates nginx config fragment
              - Registers app with agent

  fix         Fix already-deployed app to integrate with agent
              - Checks if app and agent are running
              - Reconnects app to agent network if needed
              - Creates nginx config if missing
              - Reloads agent

  check       Check if app is properly integrated with agent
              - Shows agent status
              - Shows app network configuration
              - Shows nginx config status

${BOLD}Examples:${NC}
  # Before deploying a new app
  cd my-app/
  $(basename "$0") integrate
  docker compose up -d

  # Fix an app that was deployed before agent
  cd my-app/
  $(basename "$0") fix

  # Check integration status
  cd my-app/
  $(basename "$0") check

${BOLD}Workflow:${NC}
  1. Start agent: ${CYAN}isle agent start${NC}
  2. Prepare app: ${CYAN}isle app integrate${NC} (from app directory)
  3. Deploy app: ${CYAN}docker compose up -d${NC}
  4. Access at: ${CYAN}http://app.local${NC}

EOF
}

# Main command handler
main() {
    local command="${1:-integrate}"

    case "$command" in
        integrate)
            integrate_app
            ;;
        fix)
            fix_deployed_app
            ;;
        check)
            detect_agent
            find_compose_file >/dev/null && log_success "Found docker-compose file"
            if check_network_config "$(find_compose_file)"; then
                log_success "App configured for agent network"
            else
                log_warn "App not configured for agent network"
            fi
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            log_error "Unknown command: $command"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
