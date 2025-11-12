#!/bin/bash
#
# App Orchestrator - Handles app deployment with agent integration
#
# This script manages the workflow of:
# 1. Detecting existing agent setup
# 2. Detecting current nginx proxy configuration
# 3. Generating new config fragments for the app
# 4. Merging configs into agent
# 5. Reloading agent without downtime
#

set -euo pipefail

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Configuration
AGENT_DIR="/etc/isle-mesh/agent"
AGENT_CONFIGS_DIR="${AGENT_DIR}/configs"
AGENT_REGISTRY="${AGENT_DIR}/registry.json"
MESH_PROXY_DIR=""

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$CLI_DIR")"
MESH_PROXY_DIR="${PROJECT_ROOT}/mesh-proxy"

# Logging
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

log_step() {
    echo -e ""
    echo -e "${CYAN}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC} ${BOLD}$*${NC}"
    echo -e "${CYAN}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo -e ""
}

# Step 1: Detect if agent is running
detect_agent() {
    log_step "Step 1: Detecting Agent Setup"

    if docker ps --filter "name=isle-agent" --filter "status=running" --format '{{.Names}}' | grep -q "^isle-agent$"; then
        log_success "Agent container detected and running"

        # Get agent details
        local agent_uptime
        agent_uptime=$(docker ps --filter "name=isle-agent" --format '{{.Status}}')
        log_info "Agent uptime: $agent_uptime"

        # Check agent health
        if docker exec isle-agent wget --quiet --tries=1 --spider http://127.0.0.1/health 2>/dev/null; then
            log_success "Agent health check passed"
        else
            log_warn "Agent health check failed (may still be starting up)"
        fi

        # Check registry
        if [[ -f "$AGENT_REGISTRY" ]]; then
            local app_count
            app_count=$(jq '.apps | length' "$AGENT_REGISTRY" 2>/dev/null || echo "0")
            log_info "Currently registered apps: $app_count"

            if [[ "$app_count" -gt 0 ]]; then
                log_info "Registered domains:"
                jq -r '.apps | to_entries[] | "  - \(.key): \(.value.domain)"' "$AGENT_REGISTRY" 2>/dev/null || true
            fi
        fi

        return 0
    else
        log_warn "Agent container not detected"
        log_info "Agent needs to be started first: isle agent start"
        return 1
    fi
}

# Step 2: Detect current nginx proxy configuration
detect_proxy_config() {
    log_step "Step 2: Detecting Current Proxy Configuration"

    if [[ ! -d "$AGENT_CONFIGS_DIR" ]]; then
        log_warn "Agent configs directory does not exist"
        log_info "Creating directory: $AGENT_CONFIGS_DIR"
        sudo mkdir -p "$AGENT_CONFIGS_DIR"
        sudo chown -R "$(whoami):$(whoami)" "$AGENT_DIR" 2>/dev/null || true
    fi

    # List existing config fragments
    local fragment_count
    fragment_count=$(find "$AGENT_CONFIGS_DIR" -name "*.conf" -type f 2>/dev/null | wc -l)

    log_info "Found $fragment_count existing config fragment(s)"

    if [[ $fragment_count -gt 0 ]]; then
        log_info "Existing fragments:"
        find "$AGENT_CONFIGS_DIR" -name "*.conf" -type f 2>/dev/null | while read -r fragment; do
            local fragment_name
            fragment_name=$(basename "$fragment")
            local line_count
            line_count=$(wc -l < "$fragment")
            log_info "  - $fragment_name ($line_count lines)"
        done
    fi

    # Detect which templates are currently in use
    log_info "Analyzing template usage..."
    local templates_used=()

    if [[ $fragment_count -gt 0 ]]; then
        # Check for common template patterns in existing configs
        if grep -rq "server_name.*\.vlan" "$AGENT_CONFIGS_DIR" 2>/dev/null; then
            templates_used+=("subdomain-vlan")
        fi
        if grep -rq "ssl_certificate" "$AGENT_CONFIGS_DIR" 2>/dev/null; then
            templates_used+=("https")
        fi
        if grep -rq "upstream" "$AGENT_CONFIGS_DIR" 2>/dev/null; then
            templates_used+=("upstream")
        fi
    fi

    if [[ ${#templates_used[@]} -gt 0 ]]; then
        log_info "Templates in use: ${templates_used[*]}"
    else
        log_info "No specific templates detected (default config)"
    fi

    return 0
}

# Step 3: Generate config for new app
generate_app_config() {
    local app_name="$1"
    local app_domain="$2"
    local app_port="$3"
    local app_container="$4"

    log_step "Step 3: Generating Config for New App"

    log_info "App details:"
    log_info "  Name:      $app_name"
    log_info "  Domain:    $app_domain"
    log_info "  Port:      $app_port"
    log_info "  Container: $app_container"

    # Check mesh-proxy templates availability
    if [[ ! -d "$MESH_PROXY_DIR" ]]; then
        log_error "Mesh-proxy directory not found: $MESH_PROXY_DIR"
        return 1
    fi

    local templates_dir="${MESH_PROXY_DIR}/segments"
    if [[ ! -d "$templates_dir" ]]; then
        log_error "Templates directory not found: $templates_dir"
        return 1
    fi

    log_success "Found mesh-proxy templates at: $templates_dir"

    # List available templates
    log_info "Available templates:"
    find "$templates_dir" -name "*.j2" -type f | while read -r template; do
        log_info "  - $(basename "$template")"
    done

    # Generate config fragment for this app
    local fragment_path="${AGENT_CONFIGS_DIR}/${app_name}.conf"

    log_info "Generating config fragment: $fragment_path"

    # Simple config generation (can be enhanced with actual Jinja2 rendering later)
    cat > "$fragment_path" <<EOF
# Configuration for ${app_name}
# Generated by isle app orchestrator
# Domain: ${app_domain}

# Upstream definition
upstream ${app_name}_backend {
    server ${app_container}:${app_port};
    keepalive 32;
}

# HTTP server
server {
    listen 80;
    server_name ${app_domain};

    # Dual-domain support: .local and .vlan
    server_name ${app_domain} ${app_domain%.local}.vlan;

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
    }

    location /health {
        access_log off;
        proxy_pass http://${app_name}_backend/health;
    }
}

# HTTPS server (using self-signed cert for now)
server {
    listen 443 ssl;
    server_name ${app_domain};

    # Dual-domain support: .local and .vlan
    server_name ${app_domain} ${app_domain%.local}.vlan;

    # SSL configuration
    ssl_certificate /etc/nginx/ssl/certs/selfsigned.crt;
    ssl_certificate_key /etc/nginx/ssl/keys/selfsigned.key;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;
    ssl_ciphers HIGH:!aNULL:!MD5;

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
    }

    location /health {
        access_log off;
        proxy_pass http://${app_name}_backend/health;
    }
}
EOF

    log_success "Config fragment generated: $fragment_path"

    # Validate the fragment
    log_info "Validating generated config..."
    if validate_nginx_fragment "$fragment_path"; then
        log_success "Config validation passed"
        return 0
    else
        log_error "Config validation failed"
        return 1
    fi
}

# Validate nginx config fragment
validate_nginx_fragment() {
    local fragment="$1"

    # Create temporary test config
    local temp_conf
    temp_conf=$(mktemp)
    trap "rm -f ${temp_conf}" RETURN

    cat > "${temp_conf}" <<'NGINX_TEST_CONF'
user nginx;
worker_processes 1;
error_log /dev/null;
pid /tmp/nginx-test.pid;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    # Include fragment
    include FRAGMENT_PATH;
}
NGINX_TEST_CONF

    sed -i "s|FRAGMENT_PATH|${fragment}|" "${temp_conf}"

    # Test using docker
    if docker run --rm \
        -v "${temp_conf}:/etc/nginx/nginx.conf:ro" \
        -v "${fragment}:${fragment}:ro" \
        nginx:alpine nginx -t &>/dev/null; then
        return 0
    else
        log_error "Validation output:"
        docker run --rm \
            -v "${temp_conf}:/etc/nginx/nginx.conf:ro" \
            -v "${fragment}:${fragment}:ro" \
            nginx:alpine nginx -t 2>&1 || true
        return 1
    fi
}

# Step 4: Register app in registry
register_app() {
    local app_name="$1"
    local app_domain="$2"

    log_step "Step 4: Registering App in Registry"

    if [[ ! -f "$AGENT_REGISTRY" ]]; then
        log_info "Creating new registry..."
        echo '{"domains": {}, "subdomains": {}, "apps": {}}' | sudo tee "$AGENT_REGISTRY" > /dev/null
    fi

    # Check for domain conflicts
    local existing_domain
    existing_domain=$(jq -r ".apps[] | select(.domain == \"$app_domain\") | .domain" "$AGENT_REGISTRY" 2>/dev/null || echo "")

    if [[ -n "$existing_domain" ]]; then
        log_error "Domain conflict: $app_domain is already registered"
        return 1
    fi

    # Add app to registry
    local temp_registry
    temp_registry=$(mktemp)
    jq ".apps.\"$app_name\" = {\"domain\": \"$app_domain\", \"registered_at\": \"$(date -Iseconds)\"}" \
        "$AGENT_REGISTRY" > "$temp_registry"
    sudo mv "$temp_registry" "$AGENT_REGISTRY"

    log_success "App registered: $app_name -> $app_domain"
    return 0
}

# Step 5: Reload agent
reload_agent() {
    log_step "Step 5: Reloading Agent (Zero-Downtime)"

    if ! docker ps --filter "name=isle-agent" --filter "status=running" -q | grep -q .; then
        log_error "Agent is not running"
        return 1
    fi

    # Test complete configuration first
    log_info "Testing complete nginx configuration..."
    if ! docker exec isle-agent nginx -t 2>&1; then
        log_error "Nginx configuration test failed"
        return 1
    fi

    log_success "Configuration test passed"

    # Reload nginx
    log_info "Reloading nginx..."
    if docker exec isle-agent nginx -s reload 2>/dev/null; then
        log_success "Agent reloaded successfully"

        # Verify reload worked
        sleep 2
        if docker exec isle-agent wget --quiet --tries=1 --spider http://127.0.0.1/health 2>/dev/null; then
            log_success "Agent health check passed after reload"
            return 0
        else
            log_warn "Agent health check failed after reload (may need a moment)"
            return 0
        fi
    else
        log_error "Agent reload failed"
        return 1
    fi
}

# Main orchestration workflow
orchestrate_app_deployment() {
    local app_name="$1"
    local app_domain="$2"
    local app_port="$3"
    local app_container="$4"

    echo ""
    echo -e "${BOLD}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║       App Deployment Orchestration                            ║${NC}"
    echo -e "${BOLD}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    # Step 1: Detect agent
    if ! detect_agent; then
        log_error "Agent detection failed. Start agent first: isle agent start"
        return 1
    fi

    # Step 2: Detect current configuration
    detect_proxy_config

    # Step 3: Generate new config
    if ! generate_app_config "$app_name" "$app_domain" "$app_port" "$app_container"; then
        log_error "Config generation failed"
        return 1
    fi

    # Step 4: Register app
    if ! register_app "$app_name" "$app_domain"; then
        log_error "App registration failed"
        return 1
    fi

    # Step 5: Reload agent
    if ! reload_agent; then
        log_error "Agent reload failed"
        return 1
    fi

    # Success!
    echo ""
    echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║       ✓ App Deployment Complete!                             ║${NC}"
    echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    log_success "App $app_name is now accessible at:"
    log_success "  http://$app_domain"
    log_success "  https://$app_domain"
    log_success "  http://${app_domain%.local}.vlan (after join protocol)"
    log_success "  https://${app_domain%.local}.vlan (after join protocol)"
    echo ""

    return 0
}

# Show help
show_help() {
    cat <<EOF
${BOLD}Isle App Orchestrator${NC}

Manages the complete workflow of deploying an app with agent integration:
  1. Detects existing agent setup
  2. Detects current nginx proxy configuration
  3. Generates new config fragments for the app
  4. Merges configs into agent
  5. Reloads agent without downtime

${BOLD}Usage:${NC}
  $(basename "$0") deploy <app_name> <domain> <port> <container>
  $(basename "$0") detect
  $(basename "$0") help

${BOLD}Commands:${NC}
  deploy APP DOMAIN PORT CONTAINER
      Deploy an app with full orchestration
      Example: $(basename "$0") deploy myapp myapp.local 3000 myapp-container

  detect
      Detect and show current agent and proxy configuration

  help
      Show this help message

${BOLD}Examples:${NC}
  # Deploy a new app
  $(basename "$0") deploy sample sample.local 5000 isle-sample-app

  # Detect current setup
  $(basename "$0") detect

EOF
}

# Main command handler
main() {
    local command="${1:-help}"

    case "$command" in
        deploy)
            if [[ $# -lt 5 ]]; then
                log_error "Missing arguments"
                echo "Usage: $0 deploy <app_name> <domain> <port> <container>"
                exit 1
            fi
            orchestrate_app_deployment "$2" "$3" "$4" "$5"
            ;;

        detect)
            detect_agent
            detect_proxy_config
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
