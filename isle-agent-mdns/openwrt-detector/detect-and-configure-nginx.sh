#!/bin/bash
#
# OpenWRT Router Detection & Nginx Configuration Script
#
# This script:
# 1. Checks if OpenWRT router VM exists and is running locally
# 2. Gets the router's IP address if available
# 3. Generates nginx configuration for fallback proxy
# 4. Reloads nginx if config changed
#
# Run this script manually, via cron, or systemd timer
#

set -e

# Configuration
ROUTER_VM_NAME="${ROUTER_VM_NAME:-openwrt-isle-router}"
NGINX_CONFIG_PATH="${NGINX_CONFIG_PATH:-/etc/nginx/conf.d/openwrt-fallback.conf}"
NGINX_ENABLED="${NGINX_ENABLED:-true}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

log_error() {
    echo -e "${RED}[✗]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[⚠]${NC} $1"
}

# Check if router VM exists and is running
check_router_status() {
    local vm_name="$1"

    # Check if virsh is available
    if ! command -v virsh &> /dev/null; then
        echo "not_installed"
        return 1
    fi

    # Try without sudo first (for users in libvirt group)
    local virsh_cmd="virsh"
    if ! virsh list --all &>/dev/null; then
        # Need sudo
        if ! sudo -n virsh list --all &>/dev/null 2>&1; then
            # Can't run virsh even with sudo
            echo "no_permission"
            return 1
        fi
        virsh_cmd="sudo virsh"
    fi

    # Check if VM exists
    if ! $virsh_cmd list --all 2>/dev/null | grep -q "$vm_name"; then
        echo "not_found"
        return 1
    fi

    # Check if VM is running
    if $virsh_cmd list --state-running 2>/dev/null | grep -q "$vm_name"; then
        echo "running"
        return 0
    else
        echo "stopped"
        return 1
    fi
}

# Get router MAC address
get_router_mac() {
    local vm_name="$1"
    local virsh_cmd="virsh"

    if ! virsh dumpxml "$vm_name" &>/dev/null; then
        virsh_cmd="sudo virsh"
    fi

    $virsh_cmd dumpxml "$vm_name" 2>/dev/null | \
        grep "mac address" | \
        head -1 | \
        sed -n "s/.*mac address='\([^']*\)'.*/\1/p"
}

# Get router IP from ARP table
get_router_ip() {
    local mac="$1"

    if [ -z "$mac" ]; then
        return 1
    fi

    # Look up in ARP table
    local ip=$(arp -n | grep -i "$mac" | awk '{print $1}' | head -1)

    # Validate IP format
    if [[ "$ip" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
        echo "$ip"
        return 0
    fi

    return 1
}

# Test router connectivity
test_router_connectivity() {
    local ip="$1"

    if [ -z "$ip" ]; then
        return 1
    fi

    ping -c 1 -W 2 "$ip" &>/dev/null
}

# Generate nginx config
generate_nginx_config() {
    local router_status="$1"
    local router_ip="$2"

    local timestamp=$(date -Iseconds)

    if [ "$router_status" != "running" ] || [ -z "$router_ip" ]; then
        # Router not available - generate disabled config
        cat << EOF
# OpenWRT Fallback Proxy - DISABLED
# Generated at $timestamp

# Router Status: $router_status
# Router VM: $ROUTER_VM_NAME

EOF
        case "$router_status" in
            "not_installed")
                cat << EOF
# ❌ libvirt/virsh not installed
#    Install with: sudo apt-get install qemu-kvm libvirt-daemon-system libvirt-clients
EOF
                ;;
            "no_permission")
                cat << EOF
# ❌ No permission to access libvirt
#    Add your user to libvirt group: sudo usermod -aG libvirt \$USER
#    Then log out and log back in
EOF
                ;;
            "not_found")
                cat << EOF
# ❌ Router VM not found
#    Initialize router with: sudo isle router init
EOF
                ;;
            "stopped")
                cat << EOF
# ⚠️  Router VM exists but is not running
#    Start router with: sudo isle router up $ROUTER_VM_NAME
EOF
                ;;
            *)
                cat << EOF
# ⚠️  Router is running but not reachable
#    Check status with: isle router status
EOF
                ;;
        esac

        cat << EOF

# Fallback proxy is DISABLED

# Placeholder server to prevent nginx errors
server {
    listen 8080;
    server_name _;

    location / {
        return 503 "OpenWRT router not available. Status: $router_status";
        add_header Content-Type text/plain;
    }
}
EOF
    else
        # Router available - generate proxy config
        cat << EOF
# OpenWRT Fallback Proxy - ENABLED
# Generated at $timestamp

# Router Status: RUNNING
# Router VM: $ROUTER_VM_NAME
# Router IP: $router_ip

map \$http_upgrade \$connection_upgrade {
    default upgrade;
    '' close;
}

# Fallback server for unknown domains - proxy to OpenWRT router
server {
    listen 80 default_server;
    server_name _;

    location / {
        # Proxy to OpenWRT router
        proxy_pass http://$router_ip:80;

        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;

        # Timeouts
        proxy_connect_timeout 5s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;

        # Header to indicate fallback proxy
        add_header X-Isle-Proxy "openwrt-fallback" always;
    }
}

# HTTPS fallback (if needed)
server {
    listen 443 ssl default_server;
    server_name _;

    # Self-signed cert (replace with real certs if needed)
    ssl_certificate /etc/nginx/ssl/default.crt;
    ssl_certificate_key /etc/nginx/ssl/default.key;

    location / {
        proxy_pass https://$router_ip:443;
        proxy_ssl_verify off;  # OpenWRT uses self-signed cert

        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;

        add_header X-Isle-Proxy "openwrt-fallback-https" always;
    }
}
EOF
    fi
}

# Write config and reload nginx
write_and_reload_nginx() {
    local config="$1"
    local config_path="$2"

    # Create directory if it doesn't exist
    local config_dir=$(dirname "$config_path")
    if [ ! -d "$config_dir" ]; then
        sudo mkdir -p "$config_dir"
    fi

    # Write config to temp file first
    local temp_file=$(mktemp)
    echo "$config" > "$temp_file"

    # Check if config changed
    if [ -f "$config_path" ] && cmp -s "$temp_file" "$config_path"; then
        log_info "Config unchanged, skipping reload"
        rm "$temp_file"
        return 0
    fi

    # Move to final location
    if sudo mv "$temp_file" "$config_path"; then
        log_success "Wrote config to $config_path"

        # Test nginx config
        if sudo nginx -t &>/dev/null; then
            # Reload nginx
            if sudo nginx -s reload &>/dev/null; then
                log_success "Nginx reloaded successfully"
                return 0
            else
                log_error "Failed to reload nginx"
                return 1
            fi
        else
            log_error "Nginx config test failed"
            sudo nginx -t
            return 1
        fi
    else
        log_error "Failed to write config"
        rm -f "$temp_file"
        return 1
    fi
}

# Main execution
main() {
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║     OpenWRT Router Detection & Nginx Configuration           ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""

    if [ "$NGINX_ENABLED" != "true" ]; then
        log_warning "Nginx proxy is disabled (NGINX_ENABLED=$NGINX_ENABLED)"
        exit 0
    fi

    log_info "Checking router: $ROUTER_VM_NAME"

    # Check router status
    ROUTER_STATUS=$(check_router_status "$ROUTER_VM_NAME")
    ROUTER_IP=""

    case "$ROUTER_STATUS" in
        "running")
            log_success "Router VM is running"

            # Get MAC address
            MAC=$(get_router_mac "$ROUTER_VM_NAME")
            if [ -n "$MAC" ]; then
                log_info "Router MAC: $MAC"

                # Get IP from ARP
                ROUTER_IP=$(get_router_ip "$MAC")
                if [ -n "$ROUTER_IP" ]; then
                    log_info "Router IP: $ROUTER_IP"

                    # Test connectivity
                    if test_router_connectivity "$ROUTER_IP"; then
                        log_success "Router is reachable at $ROUTER_IP"
                    else
                        log_warning "Router not responding to ping"
                        ROUTER_IP=""
                    fi
                else
                    log_warning "Router IP not found in ARP table"
                fi
            else
                log_warning "Could not get router MAC address"
            fi
            ;;
        "not_installed")
            log_error "libvirt/virsh not installed"
            ;;
        "no_permission")
            log_error "No permission to access libvirt"
            ;;
        "not_found")
            log_error "Router VM not found"
            ;;
        "stopped")
            log_warning "Router VM is stopped"
            ;;
    esac

    echo ""
    log_info "Generating nginx configuration..."

    # Generate config
    CONFIG=$(generate_nginx_config "$ROUTER_STATUS" "$ROUTER_IP")

    # Write and reload
    if write_and_reload_nginx "$CONFIG" "$NGINX_CONFIG_PATH"; then
        echo ""
        log_success "Configuration complete!"

        if [ "$ROUTER_STATUS" = "running" ] && [ -n "$ROUTER_IP" ]; then
            echo ""
            echo "✅ Nginx fallback proxy is ENABLED"
            echo "   Router: $ROUTER_VM_NAME at $ROUTER_IP"
        else
            echo ""
            echo "⚠️  Nginx fallback proxy is DISABLED"
            echo "   Reason: $ROUTER_STATUS"
        fi
    else
        log_error "Failed to update configuration"
        exit 1
    fi

    echo ""
}

# Run main function
main "$@"
