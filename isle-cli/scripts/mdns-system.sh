#!/bin/bash
# Isle mDNS System Management
# Manages host system mDNS infrastructure

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
MDNS_DIR="$PROJECT_ROOT/mdns"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Check if mdns directory exists
if [ ! -d "$MDNS_DIR" ]; then
    echo "Error: mdns directory not found at $MDNS_DIR"
    exit 1
fi

show_help() {
    echo -e "${BOLD}Isle mDNS System Management${NC}"
    echo ""
    echo "Manage the host system's mDNS infrastructure (systemd services, dnsmasq, etc.)."
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                    INSTALLATION & LIFECYCLE                   ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    echo -e "${CYAN}isle mdns system install${NC}"
    echo -e "${CYAN}isle mdns system up${NC}"
    echo "    Install mDNS system on the host. This sets up:"
    echo "    • systemd service (mesh-mdns.service)"
    echo "    • dnsmasq configuration for .local domains"
    echo "    • mDNS broadcasting daemon"
    echo "    • Network configuration for mDNS"
    echo ""
    echo -e "${CYAN}isle mdns system uninstall${NC}"
    echo -e "${CYAN}isle mdns system down${NC}"
    echo "    Uninstall mDNS system from the host. This removes:"
    echo "    • systemd services"
    echo "    • dnsmasq configuration"
    echo "    • Network modifications"
    echo "    • Installation flags"
    echo ""
    echo -e "${CYAN}isle mdns system status${NC}"
    echo "    Check mDNS installation and service status:"
    echo "    • systemd service state (running/stopped/not installed)"
    echo "    • Installation flags and completion markers"
    echo "    • Docker container status (if installer is running)"
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                    SERVICE MANAGEMENT                         ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    echo -e "${CYAN}isle mdns system reload${NC}"
    echo "    Restart the mesh-mdns.service to apply configuration changes."
    echo "    Use this after modifying domain broadcast lists."
    echo ""
    echo -e "${CYAN}isle mdns system logs${NC}"
    echo "    View systemd service logs for mesh-mdns.service."
    echo "    Shows real-time logs from the mDNS broadcasting daemon."
    echo "    Uses journalctl to display service output."
    echo ""
    echo -e "${CYAN}isle mdns system broadcast${NC}"
    echo "    Test mDNS broadcasting functionality. Verifies that:"
    echo "    • mesh-mdns.service is running"
    echo "    • Domains are being broadcasted correctly"
    echo "    • mDNS responses are working"
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                    EXAMPLES                                   ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    echo -e "${YELLOW}Initial setup:${NC}"
    echo "  isle mdns system install       # Install mDNS infrastructure"
    echo "  isle mdns system status        # Verify it's running"
    echo ""
    echo -e "${YELLOW}Troubleshooting:${NC}"
    echo "  isle mdns system logs          # Check for errors"
    echo "  isle mdns system broadcast     # Test broadcasting"
    echo "  isle mdns system reload        # Restart service"
    echo ""
    echo -e "${YELLOW}Cleanup:${NC}"
    echo "  isle mdns system uninstall     # Remove all mDNS components"
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                    RELATED COMMANDS                           ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    echo "After installing the system, you'll want to:"
    echo -e "  • ${GREEN}isle mdns domain help${NC}    Configure broadcast domains"
    echo -e "  • ${GREEN}isle mdns app help${NC}       Manage localhost applications"
    echo ""
    echo -e "Back to overview: ${GREEN}isle mdns help${NC}"
}

COMMAND=${1:-help}

case $COMMAND in
    install|up)
        echo "Installing Isle Mesh mDNS system..."
        echo "This will configure your host system for mDNS networking."
        echo ""

        # Check if installation script exists
        if [ ! -f "$MDNS_DIR/scripts/install-mesh-mdns.sh" ]; then
            echo "❌ Error: install-mesh-mdns.sh not found at $MDNS_DIR/scripts/"
            exit 1
        fi

        # Check if env file exists
        if [ ! -f "$MDNS_DIR/scripts/mesh-mdns.conf" ]; then
            echo "❌ Error: mesh-mdns.conf not found at $MDNS_DIR/scripts/"
            exit 1
        fi

        # Request sudo upfront so the script can run without password prompts
        echo "This installation requires administrative privileges."
        sudo -v || { echo "❌ sudo access required"; exit 1; }

        # Run installation directly on the host (not via Docker)
        ISLEMESH_DIR="$PROJECT_ROOT" sudo -E bash "$MDNS_DIR/scripts/install-mesh-mdns.sh" "$MDNS_DIR/scripts/mesh-mdns.conf"

        echo ""
        echo "✅ Installation complete! Check status with: isle mdns system status"
        ;;
    uninstall|down)
        echo "Running mDNS uninstall script..."
        echo ""

        if [ ! -f "$MDNS_DIR/scripts/uninstall-mesh-mdns.sh" ]; then
            echo "❌ Error: uninstall-mesh-mdns.sh not found at $MDNS_DIR/scripts/"
            exit 1
        fi

        # Request sudo upfront
        echo "This uninstallation requires administrative privileges."
        sudo -v || { echo "❌ sudo access required"; exit 1; }

        bash "$MDNS_DIR/scripts/uninstall-mesh-mdns.sh"

        echo ""
        echo "✅ Uninstallation complete!"
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
                echo "   Run 'isle mdns system install' to reinstall"
            elif [ -f "/etc/isle-mesh/.installed_started" ]; then
                echo "⚠️  Partial installation detected (incomplete)"
                echo "   Run 'isle mdns system install' to complete or 'isle mdns system uninstall' to clean up"
            else
                echo "ℹ️  IsleMesh mDNS is not installed"
                echo "   Run 'isle mdns system install' to set up"
            fi
            echo ""
            echo "📋 mesh-mdns.service not found"
        fi
        ;;
    logs)
        echo "Viewing mDNS service logs..."
        echo ""

        # Check if the service exists
        if systemctl list-unit-files 2>/dev/null | grep -q "mesh-mdns.service"; then
            echo "Showing logs for mesh-mdns.service:"
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            sudo journalctl -u mesh-mdns.service -f
        else
            echo "⚠️  mesh-mdns.service not found"
            echo "   The mDNS system may not be installed yet."
            echo "   Run 'isle mdns system install' to set up."
            exit 1
        fi
        ;;
    broadcast)
        echo "Testing mDNS broadcast..."
        if [ -f "$MDNS_DIR/scripts/mesh-mdns-broadcast.sh" ]; then
            bash "$MDNS_DIR/scripts/mesh-mdns-broadcast.sh"
        else
            echo "Error: mesh-mdns-broadcast.sh not found"
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
            echo "Run 'isle mdns system install' first"
            exit 1
        fi
        ;;
    help|*)
        show_help
        ;;
esac
