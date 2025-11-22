#!/bin/bash
# Isle CLI - localhost-mdns management commands
# Manages localhost-mdns applications (localhost-only, no router/DHCP)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
APPS_DIR="${APPS_DIR:-$HOME/.isle/apps}"
SCAFFOLDING_DIR="$SCRIPT_DIR/../scaffolding/localhost-mdns"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

# Get app metadata
get_app_metadata() {
    local app_name=$1
    local metadata_file="$APPS_DIR/$app_name/metadata.json"

    if [ -f "$metadata_file" ]; then
        cat "$metadata_file"
    else
        echo "{}"
    fi
}

# Check if app is localhost-mdns type
is_localhost_app() {
    local app_name=$1
    local metadata=$(get_app_metadata "$app_name")
    local app_type=$(echo "$metadata" | jq -r '.type // "unknown"')

    [ "$app_type" = "localhost-mdns" ]
}

# List all localhost-mdns apps
list_localhost_apps() {
    log_info "Listing localhost-mdns applications..."
    echo ""

    if [ ! -d "$APPS_DIR" ]; then
        log_warn "No apps directory found at $APPS_DIR"
        return
    fi

    local found=0

    printf "%-20s %-15s %-30s %s\n" "APP NAME" "TYPE" "BASE DOMAIN" "STATUS"
    printf "%-20s %-15s %-30s %s\n" "--------" "----" "-----------" "------"

    for app_dir in "$APPS_DIR"/*; do
        if [ -d "$app_dir" ]; then
            local app_name=$(basename "$app_dir")
            local metadata=$(get_app_metadata "$app_name")
            local app_type=$(echo "$metadata" | jq -r '.type // "unknown"')

            if [ "$app_type" = "localhost-mdns" ]; then
                local base_domain=$(echo "$metadata" | jq -r '.base_domain // "N/A"')
                local mode=$(echo "$metadata" | jq -r '.mode // "production"')

                # Determine compose file location based on mode
                local compose_file
                if [ "$mode" = "development" ]; then
                    local dev_path=$(echo "$metadata" | jq -r '.dev_path // ""')
                    local compose_filename=$(echo "$metadata" | jq -r '.compose_file // "docker-compose.yml"')
                    if [ -n "$dev_path" ] && [ -d "$dev_path" ]; then
                        compose_file="$dev_path/$compose_filename"
                    else
                        compose_file="$app_dir/docker-compose.yml"
                    fi
                else
                    compose_file="$app_dir/docker-compose.yml"
                fi

                # Check if app is running
                local status="stopped"
                if [ -f "$compose_file" ]; then
                    if docker compose -f "$compose_file" ps --format json 2>/dev/null | jq -e 'select(.State == "running")' > /dev/null 2>&1; then
                        status="${GREEN}running${NC}"
                    else
                        status="${YELLOW}stopped${NC}"
                    fi
                fi

                printf "%-20s %-15s %-30s %b\n" "$app_name" "$app_type" "$base_domain" "$status"
                found=$((found + 1))
            fi
        fi
    done

    echo ""
    if [ $found -eq 0 ]; then
        log_warn "No localhost-mdns apps found"
    else
        log_success "Found $found localhost-mdns app(s)"
    fi
}

# List all isle apps
list_isle_apps() {
    log_info "Listing isle applications..."
    echo ""

    if [ ! -d "$APPS_DIR" ]; then
        log_warn "No apps directory found at $APPS_DIR"
        return
    fi

    local found=0

    printf "%-20s %-15s %-30s %-10s %s\n" "APP NAME" "TYPE" "BASE DOMAIN" "VLAN" "STATUS"
    printf "%-20s %-15s %-30s %-10s %s\n" "--------" "----" "-----------" "----" "------"

    for app_dir in "$APPS_DIR"/*; do
        if [ -d "$app_dir" ]; then
            local app_name=$(basename "$app_dir")
            local metadata=$(get_app_metadata "$app_name")
            local app_type=$(echo "$metadata" | jq -r '.type // "unknown"')

            if [ "$app_type" = "isle" ]; then
                local base_domain=$(echo "$metadata" | jq -r '.base_domain // "N/A"')
                local vlan_id=$(echo "$metadata" | jq -r '.networking.vlan_id // "N/A"')
                local mode=$(echo "$metadata" | jq -r '.mode // "production"')

                # Determine compose file location based on mode
                local compose_file
                if [ "$mode" = "development" ]; then
                    local dev_path=$(echo "$metadata" | jq -r '.dev_path // ""')
                    local compose_filename=$(echo "$metadata" | jq -r '.compose_file // "docker-compose.yml"')
                    if [ -n "$dev_path" ] && [ -d "$dev_path" ]; then
                        compose_file="$dev_path/$compose_filename"
                    else
                        compose_file="$app_dir/docker-compose.yml"
                    fi
                else
                    compose_file="$app_dir/docker-compose.yml"
                fi

                # Check if app is running
                local status="stopped"
                if [ -f "$compose_file" ]; then
                    if docker compose -f "$compose_file" ps --format json 2>/dev/null | jq -e 'select(.State == "running")' > /dev/null 2>&1; then
                        status="${GREEN}running${NC}"
                    else
                        status="${YELLOW}stopped${NC}"
                    fi
                fi

                printf "%-20s %-15s %-30s %-10s %b\n" "$app_name" "$app_type" "$base_domain" "$vlan_id" "$status"
                found=$((found + 1))
            fi
        fi
    done

    echo ""
    if [ $found -eq 0 ]; then
        log_warn "No isle apps found"
    else
        log_success "Found $found isle app(s)"
    fi
}

# List all apps
list_all_apps() {
    log_info "Listing all applications..."
    echo ""

    list_localhost_apps
    echo ""
    list_isle_apps
}

# Start a localhost-mdns app
start_localhost_app() {
    local app_name=$1

    if [ -z "$app_name" ]; then
        log_error "App name required"
        echo "Usage: isle localhost up <app-name>"
        exit 1
    fi

    local app_dir="$APPS_DIR/$app_name"

    if [ ! -d "$app_dir" ]; then
        log_error "App not found: $app_name"
        exit 1
    fi

    if ! is_localhost_app "$app_name"; then
        log_error "App '$app_name' is not a localhost-mdns app"
        log_info "Use 'isle app up $app_name' for isle apps"
        exit 1
    fi

    log_info "Starting localhost-mdns app: $app_name"

    # Get metadata and determine compose file location
    local metadata=$(get_app_metadata "$app_name")
    local mode=$(echo "$metadata" | jq -r '.mode // "production"')
    local compose_file
    local work_dir

    if [ "$mode" = "development" ]; then
        local dev_path=$(echo "$metadata" | jq -r '.dev_path // ""')
        local compose_filename=$(echo "$metadata" | jq -r '.compose_file // "docker-compose.yml"')
        if [ -n "$dev_path" ] && [ -d "$dev_path" ]; then
            compose_file="$dev_path/$compose_filename"
            work_dir="$dev_path"
        else
            compose_file="$app_dir/docker-compose.yml"
            work_dir="$app_dir"
        fi
    else
        compose_file="$app_dir/docker-compose.yml"
        work_dir="$app_dir"
    fi

    if [ ! -f "$compose_file" ]; then
        log_error "Compose file not found: $compose_file"
        exit 1
    fi

    cd "$work_dir"
    docker compose -f "$compose_file" up -d

    log_success "App '$app_name' started"
}

# Stop a localhost-mdns app
stop_localhost_app() {
    local app_name=$1

    if [ -z "$app_name" ]; then
        log_error "App name required"
        echo "Usage: isle localhost down <app-name>"
        exit 1
    fi

    local app_dir="$APPS_DIR/$app_name"

    if [ ! -d "$app_dir" ]; then
        log_error "App not found: $app_name"
        exit 1
    fi

    if ! is_localhost_app "$app_name"; then
        log_error "App '$app_name' is not a localhost-mdns app"
        log_info "Use 'isle app down $app_name' for isle apps"
        exit 1
    fi

    log_info "Stopping localhost-mdns app: $app_name"

    # Get metadata and determine compose file location
    local metadata=$(get_app_metadata "$app_name")
    local mode=$(echo "$metadata" | jq -r '.mode // "production"')
    local compose_file
    local work_dir

    if [ "$mode" = "development" ]; then
        local dev_path=$(echo "$metadata" | jq -r '.dev_path // ""')
        local compose_filename=$(echo "$metadata" | jq -r '.compose_file // "docker-compose.yml"')
        if [ -n "$dev_path" ] && [ -d "$dev_path" ]; then
            compose_file="$dev_path/$compose_filename"
            work_dir="$dev_path"
        else
            compose_file="$app_dir/docker-compose.yml"
            work_dir="$app_dir"
        fi
    else
        compose_file="$app_dir/docker-compose.yml"
        work_dir="$app_dir"
    fi

    if [ ! -f "$compose_file" ]; then
        log_error "Compose file not found: $compose_file"
        exit 1
    fi

    cd "$work_dir"
    docker compose -f "$compose_file" down

    log_success "App '$app_name' stopped"
}

# Show help
show_help() {
    cat << EOF
Isle CLI - localhost-mdns management

Manage localhost-mdns applications (localhost-only, no router/DHCP)

Usage: isle localhost <command> [arguments]

Commands:
  list              - List all localhost-mdns apps
  list-isle         - List all isle apps
  list-all          - List all apps (both types)
  up <app>          - Start a localhost-mdns app
  down <app>        - Stop a localhost-mdns app
  status <app>      - Show status of localhost-mdns app
  logs <app>        - Show logs for localhost-mdns app
  help              - Show this help message

Examples:
  isle localhost list
  isle localhost up my-app
  isle localhost down my-app
  isle localhost list-all

App Types:
  - localhost-mdns  : Localhost-only apps (no router, no DHCP)
  - isle            : vLAN apps with DHCP and router integration

Scaffolding:
  Localhost-mdns templates: $SCAFFOLDING_DIR
  Isle templates: $SCRIPT_DIR/../scaffolding/isle

EOF
}

# Main command dispatcher
COMMAND=${1:-help}

case $COMMAND in
    list)
        list_localhost_apps
        ;;
    list-isle)
        list_isle_apps
        ;;
    list-all)
        list_all_apps
        ;;
    up)
        start_localhost_app "$2"
        ;;
    down)
        stop_localhost_app "$2"
        ;;
    status)
        if [ -z "$2" ]; then
            log_error "App name required"
            exit 1
        fi
        app_name="$2"
        metadata=$(get_app_metadata "$app_name")
        mode=$(echo "$metadata" | jq -r '.mode // "production"')
        if [ "$mode" = "development" ]; then
            dev_path=$(echo "$metadata" | jq -r '.dev_path // ""')
            compose_filename=$(echo "$metadata" | jq -r '.compose_file // "docker-compose.yml"')
            if [ -n "$dev_path" ] && [ -d "$dev_path" ]; then
                docker compose -f "$dev_path/$compose_filename" ps
            else
                docker compose -f "$APPS_DIR/$app_name/docker-compose.yml" ps
            fi
        else
            docker compose -f "$APPS_DIR/$app_name/docker-compose.yml" ps
        fi
        ;;
    logs)
        if [ -z "$2" ]; then
            log_error "App name required"
            exit 1
        fi
        app_name="$2"
        metadata=$(get_app_metadata "$app_name")
        mode=$(echo "$metadata" | jq -r '.mode // "production"')
        if [ "$mode" = "development" ]; then
            dev_path=$(echo "$metadata" | jq -r '.dev_path // ""')
            compose_filename=$(echo "$metadata" | jq -r '.compose_file // "docker-compose.yml"')
            if [ -n "$dev_path" ] && [ -d "$dev_path" ]; then
                docker compose -f "$dev_path/$compose_filename" logs -f
            else
                docker compose -f "$APPS_DIR/$app_name/docker-compose.yml" logs -f
            fi
        else
            docker compose -f "$APPS_DIR/$app_name/docker-compose.yml" logs -f
        fi
        ;;
    help|*)
        show_help
        ;;
esac
