#!/bin/bash
# Script for managing Isle Mesh mDNS system setup using docker-compose

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Get the project root (parent of isle-cli)
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# Path to mdns directory
MDNS_DIR="$PROJECT_ROOT/mdns"

# Check if mdns directory exists
if [ ! -d "$MDNS_DIR" ]; then
    echo "Error: mdns directory not found at $MDNS_DIR"
    exit 1
fi

# Change to mdns directory
cd "$MDNS_DIR" || exit 1

# Always use docker-compose.yml
COMPOSE_FILE="docker-compose.yml"

ACTION=${1:-help}

case $ACTION in
    install|up)
        echo "Installing Isle Mesh mDNS system..."
        echo "This will configure your host system for mDNS networking."
        docker compose -f "$COMPOSE_FILE" up --build
        ;;
    uninstall|down)
        echo "Running mDNS uninstall script..."
        if [ -f "./scripts/uninstall-mesh-mdns.sh" ]; then
            bash ./scripts/uninstall-mesh-mdns.sh
        else
            echo "Warning: Uninstall script not found"
            docker compose -f "$COMPOSE_FILE" down
        fi
        ;;
    status)
        echo "Checking Isle Mesh mDNS installation status..."
        echo ""

        # Check if systemd service exists and is running (primary indicator)
        if systemctl list-unit-files 2>/dev/null | grep -q "mesh-mdns.service"; then
            if systemctl is-active --quiet mesh-mdns.service 2>/dev/null; then
                echo "✅ IsleMesh mDNS is fully installed and running"
            else
                echo "⚠️  IsleMesh mDNS is installed but not running"
                echo "   Start with: sudo systemctl start mesh-mdns.service"
            fi
            echo ""
            echo "Service status:"
            systemctl status mesh-mdns.service --no-pager || true
        else
            # Service doesn't exist, check installation flags
            if [ -f "/etc/isle-mesh/.install_complete" ]; then
                echo "⚠️  Installation flags present but service not found"
                echo "   Run 'isle mdns install' to reinstall"
            elif [ -f "/etc/isle-mesh/.installed_started" ]; then
                echo "⚠️  Partial installation detected (incomplete)"
                echo "   Run 'isle mdns install' to complete or 'isle mdns uninstall' to clean up"
            else
                echo "ℹ️  IsleMesh mDNS is not installed"
                echo "   Run 'isle mdns install' to set up"
            fi
            echo ""
            echo "📋 mesh-mdns.service not found"
        fi

        echo ""
        # Check docker container status
        echo "Docker container status:"
        docker compose -f "$COMPOSE_FILE" ps
        ;;
    logs)
        echo "Viewing mDNS installer logs..."
        docker compose -f "$COMPOSE_FILE" logs -f
        ;;
    broadcast)
        echo "Testing mDNS broadcast..."
        if [ -f "./scripts/mesh-mdns-broadcast.sh" ]; then
            bash ./scripts/mesh-mdns-broadcast.sh
        else
            echo "Error: mesh-mdns-broadcast.sh not found"
            exit 1
        fi
        ;;
    detect-domains)
        echo "Detecting domains from mesh configuration..."
        MESH_CONFIG="${2:-./isle-mesh.yml}"
        COMPOSE_FILE="${3:-./docker-compose.mesh-app.yml}"
        MODE="${4:-append}"

        if [ -f "./scripts/mesh-mdns-domains-detect.sh" ]; then
            bash ./scripts/mesh-mdns-domains-detect.sh "$MESH_CONFIG" "$COMPOSE_FILE" "$MODE"
        else
            echo "Error: mesh-mdns-domains-detect.sh not found"
            exit 1
        fi
        ;;
    add-domain)
        DOMAIN="$2"
        if [ -z "$DOMAIN" ]; then
            echo "Usage: isle mdns add-domain <domain>"
            exit 1
        fi

        if [ -f "./scripts/mesh-mdns-domains-add.sh" ]; then
            bash ./scripts/mesh-mdns-domains-add.sh "$DOMAIN"
        else
            echo "Error: mesh-mdns-domains-add.sh not found"
            exit 1
        fi
        ;;
    remove-domain)
        DOMAIN="$2"
        if [ -z "$DOMAIN" ]; then
            echo "Usage: isle mdns remove-domain <domain>"
            exit 1
        fi

        if [ -f "./scripts/mesh-mdns-domains-remove.sh" ]; then
            bash ./scripts/mesh-mdns-domains-remove.sh "$DOMAIN"
        else
            echo "Error: mesh-mdns-domains-remove.sh not found"
            exit 1
        fi
        ;;
    list-domains)
        if [ -f "./scripts/mesh-mdns-domains-list.sh" ]; then
            bash ./scripts/mesh-mdns-domains-list.sh
        else
            echo "Error: mesh-mdns-domains-list.sh not found"
            exit 1
        fi
        ;;
    reload)
        echo "Reloading mDNS broadcast service..."
        if systemctl is-active --quiet mesh-mdns.service; then
            sudo systemctl restart mesh-mdns.service
            echo "✅ Service reloaded"
        else
            echo "⚠️  mesh-mdns.service is not running"
            echo "Run 'isle mdns install' first"
            exit 1
        fi
        ;;
    help|*)
        echo "Isle Mesh mDNS System Setup"
        echo ""
        echo "Usage: isle mdns [action] [options]"
        echo ""
        echo "This manages the real Isle Mesh mDNS infrastructure for"
        echo "setting up an intranet with mDNS-based service discovery."
        echo ""
        echo "System Actions:"
        echo "  install/up    - Install and configure mDNS on the host system"
        echo "  uninstall/down- Uninstall mDNS configuration from host"
        echo "  status        - Check mDNS installation status"
        echo "  logs          - View installer logs"
        echo "  broadcast     - Test mDNS broadcast functionality"
        echo ""
        echo "Domain Management:"
        echo "  detect-domains [config] [compose] [mode]"
        echo "                - Auto-detect domains from isle-mesh.yml and docker-compose"
        echo "                  mode: append (default) or replace"
        echo "  add-domain <domain>"
        echo "                - Manually add a domain to broadcast list"
        echo "  remove-domain <domain>"
        echo "                - Remove a domain from broadcast list"
        echo "  list-domains  - Show all configured domains"
        echo "  reload        - Restart broadcast service with updated domains"
        echo ""
        echo "  help          - Show this help message"
        echo ""
        echo "Note: This requires privileged access to modify host system"
        echo "      networking configuration (dnsmasq, systemd, etc.)"
        echo ""
        echo "For a sample/demo environment, see:"
        echo "  isle sample localhost-mdns"
        ;;
esac
