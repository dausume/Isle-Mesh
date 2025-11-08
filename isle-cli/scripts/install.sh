#!/bin/bash

#############################################################################
# Isle Install - System Dependencies Installer
#
# This script installs system dependencies required for Isle features.
#
# Usage: isle install <feature>
#
# Features:
#   router       - Install KVM/QEMU/libvirt for router virtualization
#   all          - Install all dependencies
#
#############################################################################

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Get project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ISLE_CLI_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Parse feature
FEATURE="$1"

# Logging functions
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
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_step() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# Check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This command requires sudo privileges"
        log_info "Run with: sudo isle install $FEATURE"
        exit 1
    fi
}

# Detect package manager
detect_package_manager() {
    if command -v apt-get &> /dev/null; then
        echo "apt"
    elif command -v yum &> /dev/null; then
        echo "yum"
    elif command -v dnf &> /dev/null; then
        echo "dnf"
    elif command -v pacman &> /dev/null; then
        echo "pacman"
    else
        log_error "Unsupported package manager"
        log_info "This script supports: apt (Debian/Ubuntu), yum/dnf (RHEL/Fedora), pacman (Arch)"
        exit 1
    fi
}

# Install router dependencies
install_router() {
    log_step "Installing Router Dependencies"

    local PKG_MANAGER=$(detect_package_manager)
    log_info "Detected package manager: $PKG_MANAGER"
    echo ""

    # Define packages for each package manager
    case "$PKG_MANAGER" in
        apt)
            local PACKAGES="qemu-kvm libvirt-daemon-system libvirt-clients bridge-utils wget"
            log_info "Updating package lists..."
            apt-get update
            echo ""

            log_info "Installing packages: $PACKAGES"
            apt-get install -y $PACKAGES
            ;;

        yum|dnf)
            local PACKAGES="qemu-kvm libvirt virt-install bridge-utils wget"
            log_info "Installing packages: $PACKAGES"
            $PKG_MANAGER install -y $PACKAGES
            ;;

        pacman)
            local PACKAGES="qemu libvirt virt-install bridge-utils wget"
            log_info "Installing packages: $PACKAGES"
            pacman -S --noconfirm $PACKAGES
            ;;
    esac

    echo ""
    log_success "Packages installed successfully"

    # Enable and start libvirtd
    log_step "Configuring libvirt Service"

    if systemctl is-enabled --quiet libvirtd 2>/dev/null; then
        log_info "libvirtd already enabled"
    else
        log_info "Enabling libvirtd service..."
        systemctl enable libvirtd
        log_success "libvirtd enabled"
    fi

    if systemctl is-active --quiet libvirtd; then
        log_success "libvirtd is running"
    else
        log_info "Starting libvirtd service..."
        systemctl start libvirtd
        sleep 2
        log_success "libvirtd started"
    fi

    # Add user to libvirt group
    log_step "Configuring User Permissions"

    # Get the original user who ran sudo
    local ORIGINAL_USER="${SUDO_USER:-$USER}"

    if [[ "$ORIGINAL_USER" == "root" ]]; then
        log_warning "Running as root user, skipping user group configuration"
        log_info "If you want to use libvirt as a regular user, manually run:"
        log_info "  sudo usermod -aG libvirt <your-username>"
    else
        log_info "Adding user '$ORIGINAL_USER' to 'libvirt' group..."
        usermod -aG libvirt "$ORIGINAL_USER"
        log_success "User added to libvirt group"

        echo ""
        log_warning "Group changes require a new login session to take effect"
        log_info "To activate immediately, run: ${CYAN}newgrp libvirt${NC}"
        log_info "Or log out and log back in"
    fi

    # Fix permissions on existing directories
    log_step "Configuring File Permissions"

    local PROJECT_ROOT="$(cd "$ISLE_CLI_ROOT/.." && pwd)"
    local ROUTER_DIR="$PROJECT_ROOT/openwrt-router"

    if [[ -d "$ROUTER_DIR" ]]; then
        log_info "Setting proper permissions on openwrt-router directories..."

        # Fix permissions on key directories
        for dir in "$ROUTER_DIR/images" "$ROUTER_DIR/runtime"; do
            if [[ -d "$dir" ]]; then
                log_info "Fixing permissions: $dir"
                chgrp -R libvirt "$dir" 2>/dev/null || true
                chmod -R 2775 "$dir" 2>/dev/null || true
                # Fix files in the directory
                find "$dir" -type f -exec chmod 664 {} \; 2>/dev/null || true
                find "$dir" -type f -exec chgrp libvirt {} \; 2>/dev/null || true
            fi
        done

        log_success "Permissions configured for Isle-Mesh directories"
    fi

    # Verify installation
    log_step "Verifying Installation"

    local ALL_OK=true

    for cmd in virsh qemu-img wget ip; do
        if command -v $cmd &> /dev/null; then
            log_success "$cmd installed"
        else
            log_error "$cmd not found"
            ALL_OK=false
        fi
    done

    if [[ "$ALL_OK" == true ]]; then
        echo ""
        log_success "All router dependencies installed successfully!"
        echo ""
        log_info "Next step: Provision your core router"
        log_info "Run: ${CYAN}sudo isle router provision router-core${NC}"
        echo ""
        log_info "Note: router-core is a fully sandboxed isle (vLAN) with access to"
        log_info "      isolate ethernet/wifi routing networks. It is secured at the"
        log_info "      network level and should only be used on a secure LAN by"
        log_info "      trusted individuals."
        echo ""
    else
        echo ""
        log_error "Some dependencies failed to install"
        exit 1
    fi
}

# Install app dependencies
install_app() {
    log_step "Checking App Dependencies"

    log_info "Isle mesh applications use Docker and Docker Compose."
    echo ""

    # Check if docker is available
    if command -v docker &> /dev/null; then
        log_success "Docker is already installed"
    else
        log_warning "Docker is not installed"
        log_info "Please install Docker before using isle app commands:"
        log_info "  https://docs.docker.com/get-docker/"
        echo ""
    fi

    # Check if docker-compose is available
    if command -v docker-compose &> /dev/null || docker compose version &> /dev/null 2>&1; then
        log_success "Docker Compose is already installed"
    else
        log_warning "Docker Compose is not installed"
        log_info "Please install Docker Compose:"
        log_info "  https://docs.docker.com/compose/install/"
        echo ""
    fi

    echo ""
    log_info "No additional system dependencies are needed for isle app commands."
    log_success "App dependencies check complete!"
    echo ""
}

# Install agent dependencies
install_agent() {
    log_step "Agent Dependencies (Coming Soon)"

    log_warning "The isle agent is currently under development."
    log_info "Future dependencies may include:"
    log_info "  • Network bridge management tools (already available)"
    log_info "  • Container runtime integration libraries"
    echo ""
    log_info "For now, no additional dependencies are required."
    echo ""
}

# Install all dependencies
install_all() {
    log_info "Installing all Isle dependencies..."
    echo ""

    install_app
    install_router
    install_agent
}

# Show help
show_help() {
    echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║              Isle Install - System Dependencies              ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "Install system dependencies required for Isle features."
    echo ""
    echo -e "${GREEN}USAGE:${NC}"
    echo -e "  sudo isle install <feature>"
    echo ""
    echo -e "${GREEN}FEATURES:${NC}"
    echo -e "  ${CYAN}app${NC}                 Check mesh application dependencies"
    echo -e "                       - Docker (container runtime)"
    echo -e "                       - Docker Compose (orchestration)"
    echo -e "                       Note: Checks only, does not install Docker"
    echo ""
    echo -e "  ${CYAN}router${NC}              Install router virtualization dependencies"
    echo -e "                       - qemu-kvm (KVM virtualization)"
    echo -e "                       - libvirt-daemon-system (VM management)"
    echo -e "                       - libvirt-clients (VM control tools)"
    echo -e "                       - bridge-utils (Network bridging)"
    echo -e "                       - wget (Image downloads)"
    echo ""
    echo -e "  ${CYAN}agent${NC}               Agent dependencies (coming soon)"
    echo -e "                       - Future bridge management tools"
    echo ""
    echo -e "  ${CYAN}all${NC}                 Install/check all dependencies"
    echo ""
    echo -e "${GREEN}EXAMPLES:${NC}"
    echo -e "  ${YELLOW}# Install router dependencies${NC}"
    echo -e "  sudo isle install router"
    echo ""
    echo -e "  ${YELLOW}# Install all dependencies${NC}"
    echo -e "  sudo isle install all"
    echo ""
    echo -e "${GREEN}WHAT GETS INSTALLED:${NC}"
    echo ""
    echo -e "  Router feature installs:"
    echo -e "    • KVM/QEMU virtualization platform"
    echo -e "    • libvirt for VM management"
    echo -e "    • Network bridging tools"
    echo -e "    • Adds your user to the libvirt group"
    echo -e "    • Enables and starts libvirtd service"
    echo ""
    echo -e "${GREEN}AFTER INSTALLATION:${NC}"
    echo ""
    echo -e "  1. Apply group changes: ${CYAN}newgrp libvirt${NC} (or log out/in)"
    echo -e "  2. Provision core router: ${CYAN}sudo isle router provision router-core${NC}"
    echo ""
    echo -e "  ${YELLOW}Note:${NC} router-core is a fully sandboxed isle (vLAN) with access to"
    echo -e "        isolate ethernet/wifi routing networks. It is secured at the"
    echo -e "        network level and should only be used on a secure LAN by"
    echo -e "        trusted individuals."
    echo ""
    echo -e "${GREEN}SUPPORTED SYSTEMS:${NC}"
    echo -e "  • Debian/Ubuntu (apt)"
    echo -e "  • RHEL/Fedora (yum/dnf)"
    echo -e "  • Arch Linux (pacman)"
    echo ""
}

# Main command router
case "$FEATURE" in
    app)
        install_app
        ;;

    router)
        check_root
        install_router
        ;;

    agent)
        install_agent
        ;;

    all)
        check_root
        install_all
        ;;

    help|--help|-h|"")
        show_help
        ;;

    *)
        log_error "Unknown feature: $FEATURE"
        echo ""
        echo "Usage: isle install <feature>"
        echo ""
        echo "Available features:"
        echo "  app      - Check mesh application dependencies"
        echo "  router   - Install router virtualization dependencies"
        echo "  agent    - Agent dependencies (coming soon)"
        echo "  all      - Install/check all dependencies"
        echo ""
        echo "Run 'isle install help' for more information"
        exit 1
        ;;
esac
