#!/usr/bin/env bash
#
# merge-configs.sh - Merge and validate all mesh-app config fragments
#
# This script validates all app config fragments and ensures the master
# nginx.conf properly includes them. It also cleans up the registry for
# removed apps.

set -euo pipefail

# Configuration
ISLE_AGENT_DIR="/etc/isle-mesh/agent"
CONFIGS_DIR="${ISLE_AGENT_DIR}/configs"
NGINX_CONF="${ISLE_AGENT_DIR}/nginx.conf"
REGISTRY_FILE="${ISLE_AGENT_DIR}/registry.json"
CONTAINER_NAME="isle-agent"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# Ensure directories exist
ensure_dirs() {
    sudo mkdir -p "${CONFIGS_DIR}"
}

# List all app config fragments
list_fragments() {
    if [[ ! -d "${CONFIGS_DIR}" ]]; then
        echo ""
        return
    fi

    find "${CONFIGS_DIR}" -name "*.conf" -type f 2>/dev/null || true
}

# Validate a single config fragment
validate_fragment() {
    local fragment="$1"
    local fragment_name
    fragment_name=$(basename "${fragment}")

    log_info "Validating ${fragment_name}..."

    # Create temporary nginx config that includes this fragment
    local temp_conf
    temp_conf=$(mktemp)
    trap "rm -f ${temp_conf}" RETURN

    cat > "${temp_conf}" <<EOF
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

    # Include the fragment being tested
    include ${fragment};
}
EOF

    # Test the config using docker
    if docker run --rm \
        -v "${temp_conf}:/etc/nginx/nginx.conf:ro" \
        -v "${fragment}:${fragment}:ro" \
        nginx:alpine nginx -t &>/dev/null; then
        log_success "${fragment_name} is valid"
        return 0
    else
        log_error "${fragment_name} has syntax errors"
        docker run --rm \
            -v "${temp_conf}:/etc/nginx/nginx.conf:ro" \
            -v "${fragment}:${fragment}:ro" \
            nginx:alpine nginx -t 2>&1 | grep -v "test is" || true
        return 1
    fi
}

# Validate all fragments
validate_all_fragments() {
    local fragments
    fragments=$(list_fragments)

    if [[ -z "${fragments}" ]]; then
        log_warn "No config fragments found in ${CONFIGS_DIR}"
        return 0
    fi

    log_info "Validating all config fragments..."
    local error_count=0

    while IFS= read -r fragment; do
        if ! validate_fragment "${fragment}"; then
            ((error_count++))
        fi
    done <<< "${fragments}"

    if [[ ${error_count} -gt 0 ]]; then
        log_error "Found ${error_count} invalid fragment(s)"
        return 1
    fi

    log_success "All fragments are valid"
    return 0
}

# Rebuild master nginx config
rebuild_master_config() {
    log_info "Rebuilding master nginx configuration..."

    local fragment_count
    fragment_count=$(list_fragments | wc -l)

    cat > "${NGINX_CONF}" <<'EOF'
# Isle Agent - Master nginx configuration
# Auto-generated - DO NOT EDIT MANUALLY
# This file includes all mesh-app config fragments

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
    client_max_body_size 100M;

    # Security headers (default)
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;

    # Gzip compression
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css text/xml text/javascript
               application/json application/javascript application/xml+rss
               application/rss+xml font/truetype font/opentype
               application/vnd.ms-fontobject image/svg+xml;

    # Health check endpoint
    server {
        listen 80 default_server;
        server_name _;

        location /health {
            access_log off;
            return 200 "isle-agent healthy\n";
            add_header Content-Type text/plain;
        }

        location /status {
            access_log off;
            return 200 "isle-agent running\nApps: FRAGMENT_COUNT\n";
            add_header Content-Type text/plain;
        }

        location / {
            return 404 "No route found\n";
            add_header Content-Type text/plain;
        }
    }

    # Include all mesh-app config fragments
    include /etc/nginx/configs/*.conf;
}
EOF

    # Update fragment count in config
    sed -i "s/FRAGMENT_COUNT/${fragment_count}/" "${NGINX_CONF}"

    log_success "Master config rebuilt (${fragment_count} app fragments)"
}

# Clean up registry for removed apps
cleanup_registry() {
    if [[ ! -f "${REGISTRY_FILE}" ]]; then
        log_warn "Registry file not found, skipping cleanup"
        return 0
    fi

    log_info "Cleaning up registry..."

    # Get list of apps from config files
    local existing_apps=()
    while IFS= read -r fragment; do
        local app_name
        app_name=$(basename "${fragment}" .conf)
        existing_apps+=("${app_name}")
    done < <(list_fragments)

    # Get list of apps from registry
    local registry_apps
    registry_apps=$(jq -r '.apps | keys[]' "${REGISTRY_FILE}" 2>/dev/null || echo "")

    # Remove apps from registry that don't have config files
    local removed_count=0
    for app in ${registry_apps}; do
        if [[ ! " ${existing_apps[*]} " =~ " ${app} " ]]; then
            log_info "Removing deleted app from registry: ${app}"
            jq "del(.apps.\"${app}\") |
                del(.domains[] | select(. == \"${app}\")) |
                del(.subdomains[] | select(. == \"${app}\"))" \
                "${REGISTRY_FILE}" > "${REGISTRY_FILE}.tmp"
            mv "${REGISTRY_FILE}.tmp" "${REGISTRY_FILE}"
            ((removed_count++))
        fi
    done

    if [[ ${removed_count} -gt 0 ]]; then
        log_success "Removed ${removed_count} deleted app(s) from registry"
    else
        log_info "Registry is up to date"
    fi
}

# Merge all configs and prepare for reload
merge_and_prepare() {
    ensure_dirs

    # Validate all fragments first
    if ! validate_all_fragments; then
        log_error "Config validation failed. Fix errors before merging."
        return 1
    fi

    # Rebuild master config
    rebuild_master_config

    # Clean up registry
    cleanup_registry

    # Final validation of complete config
    log_info "Performing final validation of complete configuration..."
    if docker run --rm \
        -v "${NGINX_CONF}:/etc/nginx/nginx.conf:ro" \
        -v "${CONFIGS_DIR}:/etc/nginx/configs:ro" \
        nginx:alpine nginx -t &>/dev/null; then
        log_success "Complete configuration is valid"
    else
        log_error "Complete configuration has errors:"
        docker run --rm \
            -v "${NGINX_CONF}:/etc/nginx/nginx.conf:ro" \
            -v "${CONFIGS_DIR}:/etc/nginx/configs:ro" \
            nginx:alpine nginx -t 2>&1 || true
        return 1
    fi

    log_success "Configuration merge complete and validated"
    echo ""
    echo "To apply changes, reload the isle-agent:"
    echo "  sudo isle agent reload"
}

# Show fragment summary
show_summary() {
    echo ""
    echo "=== Isle Agent Config Summary ==="
    echo ""

    local fragments
    fragments=$(list_fragments)
    local fragment_count
    fragment_count=$(echo "${fragments}" | grep -c . || echo 0)

    echo "Config Fragments: ${fragment_count}"
    echo "Location: ${CONFIGS_DIR}"
    echo ""

    if [[ ${fragment_count} -gt 0 ]]; then
        echo "Registered Apps:"
        while IFS= read -r fragment; do
            local app_name
            app_name=$(basename "${fragment}" .conf)
            local line_count
            line_count=$(wc -l < "${fragment}")
            echo "  - ${app_name} (${line_count} lines)"
        done <<< "${fragments}"
    else
        echo "No apps registered"
    fi
    echo ""
}

# Main command handler
main() {
    local command="${1:-merge}"

    case "${command}" in
        merge)
            merge_and_prepare
            ;;
        validate)
            validate_all_fragments
            ;;
        rebuild)
            rebuild_master_config
            ;;
        cleanup)
            cleanup_registry
            ;;
        summary)
            show_summary
            ;;
        help|--help|-h)
            cat <<EOF
Isle Agent Config Merger - Merge and validate mesh-app config fragments

Usage: $(basename "$0") <command>

Commands:
  merge      Validate fragments, rebuild master config, cleanup registry (default)
  validate   Validate all config fragments without merging
  rebuild    Rebuild master nginx config
  cleanup    Clean up registry for removed apps
  summary    Show summary of registered apps
  help       Show this help message

The merge process:
  1. Validates all app config fragments
  2. Rebuilds master nginx.conf with includes
  3. Cleans up registry for removed apps
  4. Validates the complete configuration

After merging, use 'isle agent reload' to apply changes.

EOF
            ;;
        *)
            log_error "Unknown command: ${command}"
            echo "Run '$(basename "$0") help' for usage"
            exit 1
            ;;
    esac
}

main "$@"
