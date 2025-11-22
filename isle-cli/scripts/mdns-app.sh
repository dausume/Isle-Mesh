#!/bin/bash
# Isle mDNS App Management
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
CYAN='\033[0;36m'
BOLD='\033[1m'
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
    echo -e "${BOLD}Isle mDNS Application Management${NC}"
    echo ""
    echo "Manage localhost-only applications (no router/DHCP required)."
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                    APPLICATION DISCOVERY                      ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    echo -e "${CYAN}isle mdns app list${NC}"
    echo "    List all localhost-mdns applications registered in ~/.isle/apps."
    echo "    Shows: app name, type, base domain, and running status."
    echo ""
    echo -e "${CYAN}isle mdns app list-isle${NC}"
    echo "    List all isle applications (for comparison)."
    echo "    Shows: app name, type, base domain, VLAN ID, and status."
    echo ""
    echo -e "${CYAN}isle mdns app list-all${NC}"
    echo "    List all applications (both localhost-mdns and isle types)."
    echo "    Useful for seeing everything at once."
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                    APPLICATION LIFECYCLE                      ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    echo -e "${CYAN}isle mdns app up <app-name>${NC}"
    echo "    Start a localhost-mdns application."
    echo "    Reads metadata from ~/.isle/apps/<app-name>/metadata.json"
    echo "    and starts containers using docker-compose."
    echo ""
    echo -e "${CYAN}isle mdns app down <app-name>${NC}"
    echo "    Stop a localhost-mdns application."
    echo "    Stops all containers for the specified app."
    echo ""
    echo -e "${CYAN}isle mdns app status <app-name>${NC}"
    echo "    Show status of application containers."
    echo "    Equivalent to 'docker compose ps' for the app."
    echo ""
    echo -e "${CYAN}isle mdns app logs <app-name>${NC}"
    echo "    Follow logs for an application."
    echo "    Shows real-time logs from all containers in the app."
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                    APPLICATION TYPES                          ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    echo -e "${BOLD}localhost-mdns${NC}"
    echo "  • Runs on host network (no VLAN isolation)"
    echo "  • No router or DHCP required"
    echo "  • mDNS broadcasting for .local domains"
    echo "  • Good for: development, testing, localhost services"
    echo ""
    echo -e "${BOLD}isle${NC}"
    echo "  • Runs on dedicated VLAN with router"
    echo "  • Full network isolation"
    echo "  • DHCP-assigned IPs"
    echo "  • Good for: production-like environments, multi-host setups"
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                    EXAMPLES                                   ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    echo -e "${YELLOW}List and start an app:${NC}"
    echo "  isle mdns app list                  # See available apps"
    echo "  isle mdns app up my-web-app         # Start specific app"
    echo "  isle mdns app logs my-web-app       # Watch logs"
    echo ""
    echo -e "${YELLOW}Check app status:${NC}"
    echo "  isle mdns app status my-web-app     # See container states"
    echo "  isle mdns app down my-web-app       # Stop when done"
    echo ""
    echo -e "${YELLOW}Compare app types:${NC}"
    echo "  isle mdns app list-all              # See both types"
    echo "  isle mdns app list                  # Only localhost-mdns apps"
    echo "  isle mdns app list-isle             # Only isle apps"
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                    APPLICATION METADATA                       ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    echo -e "Apps are stored in: ${CYAN}~/.isle/apps/<app-name>/${NC}"
    echo ""
    echo -e "Each app has a ${CYAN}metadata.json${NC} file containing:"
    echo "  • type: \"localhost-mdns\" or \"isle\""
    echo "  • base_domain: Base domain for the app"
    echo "  • mode: \"production\" or \"development\""
    echo "  • dev_path: Path to development files (if mode=development)"
    echo "  • compose_file: Name of docker-compose file"
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                    RELATED COMMANDS                           ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    echo "Before starting apps:"
    echo -e "  • ${GREEN}isle mdns system install${NC}     Install mDNS infrastructure"
    echo -e "  • ${GREEN}isle mdns domain detect${NC}      Configure app domains"
    echo ""
    echo "To create new apps:"
    echo -e "  • ${GREEN}isle app init${NC}                Initialize new mesh-app"
    echo -e "  • ${GREEN}isle app scaffold${NC}            Convert existing docker-compose"
    echo ""
    echo -e "Back to overview: ${GREEN}isle mdns help${NC}"
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
