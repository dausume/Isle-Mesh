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
        if [ -f "./scripts/check-isle-mesh-installed.sh" ]; then
            bash ./scripts/check-isle-mesh-installed.sh
        else
            echo "Status check script not found"
            docker compose -f "$COMPOSE_FILE" ps
        fi
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
    help|*)
        echo "Isle Mesh mDNS System Setup"
        echo ""
        echo "Usage: isle mdns [action]"
        echo ""
        echo "This manages the real Isle Mesh mDNS infrastructure for"
        echo "setting up an intranet with mDNS-based service discovery."
        echo ""
        echo "Actions:"
        echo "  install/up    - Install and configure mDNS on the host system"
        echo "  uninstall/down- Uninstall mDNS configuration from host"
        echo "  status        - Check mDNS installation status"
        echo "  logs          - View installer logs"
        echo "  broadcast     - Test mDNS broadcast functionality"
        echo "  help          - Show this help message"
        echo ""
        echo "Note: This requires privileged access to modify host system"
        echo "      networking configuration (dnsmasq, systemd, etc.)"
        echo ""
        echo "For a sample/demo environment, see:"
        echo "  isle sample localhost-mdns"
        ;;
esac
