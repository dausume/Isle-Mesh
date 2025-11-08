#!/bin/bash

#############################################################################
# Isle Uninstall Script
#
# Uninstalls Isle-Mesh CLI and optionally removes system components and dependencies
#
# Usage: isle uninstall [--all] [--remove-deps] [--keep-shared]
#
# Options:
#   --all           Also remove system components (port detection, etc.)
#   --remove-deps   Remove isle-mesh specific dependencies (KVM, libvirt, etc.)
#   --keep-shared   Keep shared dependencies (Docker, jq, wget, etc.)
#
#############################################################################

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Show help function
show_help() {
    echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║              Isle-Mesh Uninstall                              ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "Safely uninstall Isle-Mesh and optionally remove system dependencies."
    echo ""
    echo -e "${GREEN}USAGE:${NC}"
    echo -e "  isle uninstall [OPTIONS]"
    echo ""
    echo -e "${GREEN}OPTIONS:${NC}"
    echo -e "  ${CYAN}--all${NC}           Remove all system components (port detection, etc.)"
    echo -e "  ${CYAN}--remove-deps${NC}   Remove Isle-Mesh specific dependencies (KVM, libvirt, etc.)"
    echo -e "  ${CYAN}--keep-shared${NC}   Keep shared dependencies (wget, jq, yq, avahi-utils)"
    echo -e "  ${CYAN}--help${NC}          Show this help message"
    echo ""
    echo -e "${GREEN}WHAT GETS REMOVED:${NC}"
    echo ""
    echo -e "  ${YELLOW}Basic uninstall (no options):${NC}"
    echo -e "    • Unlinks the isle CLI from npm"
    echo -e "    • Prompts before removing system components"
    echo -e "    • Prompts before removing dependencies"
    echo ""
    echo -e "  ${YELLOW}With --all:${NC}"
    echo -e "    • Port detection system and udev rules"
    echo -e "    • System service configurations"
    echo ""
    echo -e "  ${YELLOW}With --remove-deps:${NC}"
    echo -e "    ${CYAN}Isle-Mesh specific (safe to remove):${NC}"
    echo -e "      • qemu-kvm / qemu"
    echo -e "      • libvirt-daemon-system / libvirt"
    echo -e "      • libvirt-clients / virt-install"
    echo -e "      • bridge-utils"
    echo -e "      • libvirtd service"
    echo -e "      • User from libvirt group"
    echo ""
    echo -e "    ${CYAN}Shared utilities (prompts before removal):${NC}"
    echo -e "      • wget (file download utility - very common)"
    echo -e "      • jq (JSON processor)"
    echo -e "      • yq (YAML processor)"
    echo -e "      • avahi-utils (mDNS/DNS-SD discovery)"
    echo ""
    echo -e "    ${CYAN}NOT removed by Isle-Mesh (informational only):${NC}"
    echo -e "      • Docker (not installed by Isle-Mesh)"
    echo -e "      • Node.js/npm (not installed by Isle-Mesh)"
    echo ""
    echo -e "${GREEN}EXAMPLES:${NC}"
    echo ""
    echo -e "  ${YELLOW}# Basic uninstall (interactive prompts)${NC}"
    echo -e "  isle uninstall"
    echo ""
    echo -e "  ${YELLOW}# Remove everything including system components${NC}"
    echo -e "  sudo isle uninstall --all --remove-deps"
    echo ""
    echo -e "  ${YELLOW}# Remove all but keep shared utilities${NC}"
    echo -e "  sudo isle uninstall --all --remove-deps --keep-shared"
    echo ""
    echo -e "${GREEN}NOTES:${NC}"
    echo -e "  • Shared utilities may be used by other applications"
    echo -e "  • The script will prompt before removing shared dependencies"
    echo -e "  • Use --keep-shared to automatically preserve shared utilities"
    echo -e "  • Sudo is required for --all and --remove-deps options"
    echo ""
    exit 0
}

# Parse options
REMOVE_ALL=false
REMOVE_DEPS=false
KEEP_SHARED=false

for arg in "$@"; do
    case "$arg" in
        --all)
            REMOVE_ALL=true
            ;;
        --remove-deps)
            REMOVE_DEPS=true
            ;;
        --keep-shared)
            KEEP_SHARED=true
            ;;
        --help|-h|help)
            show_help
            ;;
    esac
done

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[✗]${NC} $1"
}

log_step() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
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
        echo "unknown"
    fi
}

# Check if other packages depend on a given package
check_package_dependencies() {
    local package="$1"
    local pkg_manager=$(detect_package_manager)

    case "$pkg_manager" in
        apt)
            apt-cache rdepends "$package" 2>/dev/null | grep -v "^$package$" | grep -v "Reverse Depends:" | head -5
            ;;
        yum|dnf)
            $pkg_manager repoquery --whatrequires "$package" 2>/dev/null | head -5
            ;;
        pacman)
            pactree -r "$package" 2>/dev/null | grep -v "^$package$" | head -5
            ;;
    esac
}

echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗"
echo -e "║              Isle-Mesh Uninstall                              ║"
echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Step 1: Unlink npm package
log_step "Removing Isle CLI"

log_info "Unlinking isle CLI from npm..."
if npm unlink -g polari-cli 2>/dev/null; then
    log_success "Isle CLI unlinked"
else
    log_warning "Isle CLI was not linked globally"
fi

# Ask about system components
if [[ "$REMOVE_ALL" == false ]]; then
    echo ""
    log_warning "System components (port detection, etc.) are still installed"
    echo ""
    read -p "Remove system components too? (y/N): " remove_system

    if [[ "$remove_system" =~ ^[Yy]$ ]]; then
        REMOVE_ALL=true
    fi
fi

# Step 2: Remove system components if requested
if [[ "$REMOVE_ALL" == true ]]; then
    log_step "Removing System Components"
    log_info "No system components to remove"
    log_success "System components check complete"
fi

# Ask about dependencies
if [[ "$REMOVE_DEPS" == false ]]; then
    echo ""
    log_warning "Isle-Mesh installed several system dependencies"
    echo ""
    read -p "Remove Isle-Mesh dependencies? (y/N): " remove_deps_response

    if [[ "$remove_deps_response" =~ ^[Yy]$ ]]; then
        REMOVE_DEPS=true
    fi
fi

# Step 3: Remove dependencies if requested
if [[ "$REMOVE_DEPS" == true ]]; then
    # Check if running as sudo
    if [[ $EUID -ne 0 ]]; then
        log_error "Sudo required to remove system dependencies"
        echo ""
        echo -e "Run: ${CYAN}sudo isle uninstall --all --remove-deps${NC}"
        exit 1
    fi

    log_step "Removing Isle-Mesh Dependencies"

    local PKG_MANAGER=$(detect_package_manager)

    if [[ "$PKG_MANAGER" == "unknown" ]]; then
        log_error "Unable to detect package manager"
        log_info "Please manually remove the following packages if no longer needed:"
        echo "  - qemu-kvm (or qemu)"
        echo "  - libvirt-daemon-system (or libvirt)"
        echo "  - libvirt-clients (or virt-install)"
        echo "  - bridge-utils"
    else
        log_info "Detected package manager: $PKG_MANAGER"
        echo ""

        # Define isle-mesh specific dependencies
        case "$PKG_MANAGER" in
            apt)
                ISLE_PACKAGES="qemu-kvm libvirt-daemon-system libvirt-clients bridge-utils"
                ;;
            yum|dnf)
                ISLE_PACKAGES="qemu-kvm libvirt virt-install bridge-utils"
                ;;
            pacman)
                ISLE_PACKAGES="qemu libvirt virt-install bridge-utils"
                ;;
        esac

        # Stop and disable libvirtd service
        log_info "Stopping libvirtd service..."
        if systemctl is-active --quiet libvirtd 2>/dev/null; then
            systemctl stop libvirtd
            log_success "libvirtd stopped"
        fi

        if systemctl is-enabled --quiet libvirtd 2>/dev/null; then
            systemctl disable libvirtd
            log_success "libvirtd disabled"
        fi

        # Remove user from libvirt group
        local ORIGINAL_USER="${SUDO_USER:-$USER}"
        if [[ "$ORIGINAL_USER" != "root" ]]; then
            if groups "$ORIGINAL_USER" 2>/dev/null | grep -q libvirt; then
                log_info "Removing user '$ORIGINAL_USER' from libvirt group..."
                gpasswd -d "$ORIGINAL_USER" libvirt 2>/dev/null
                log_success "User removed from libvirt group"
            fi
        fi

        echo ""
        log_info "Removing Isle-Mesh specific packages..."

        for package in $ISLE_PACKAGES; do
            # Check if package is installed
            case "$PKG_MANAGER" in
                apt)
                    if dpkg -l | grep -q "^ii  $package "; then
                        log_info "Removing $package..."
                        apt-get remove -y "$package" 2>/dev/null && log_success "$package removed" || log_warning "Failed to remove $package"
                    else
                        log_info "$package not installed"
                    fi
                    ;;
                yum|dnf)
                    if $PKG_MANAGER list installed "$package" &>/dev/null; then
                        log_info "Removing $package..."
                        $PKG_MANAGER remove -y "$package" 2>/dev/null && log_success "$package removed" || log_warning "Failed to remove $package"
                    else
                        log_info "$package not installed"
                    fi
                    ;;
                pacman)
                    if pacman -Q "$package" &>/dev/null; then
                        log_info "Removing $package..."
                        pacman -R --noconfirm "$package" 2>/dev/null && log_success "$package removed" || log_warning "Failed to remove $package"
                    else
                        log_info "$package not installed"
                    fi
                    ;;
            esac
        done

        # Optionally clean up unused dependencies
        echo ""
        log_info "Cleaning up unused dependencies..."
        case "$PKG_MANAGER" in
            apt)
                apt-get autoremove -y 2>/dev/null
                ;;
            yum|dnf)
                $PKG_MANAGER autoremove -y 2>/dev/null
                ;;
            pacman)
                pacman -Qdtq | pacman -R --noconfirm - 2>/dev/null || true
                ;;
        esac

        log_success "Isle-Mesh dependencies removed"
    fi

    # Handle shared dependencies
    if [[ "$KEEP_SHARED" == false ]]; then
        log_step "Checking Shared Dependencies"

        echo ""
        log_warning "The following utilities may be used by other applications:"
        echo ""
        echo "  • ${CYAN}wget${NC}         - File download utility (very common)"
        echo "  • ${CYAN}jq${NC}           - JSON processor"
        echo "  • ${CYAN}yq${NC}           - YAML processor"
        echo "  • ${CYAN}avahi-utils${NC}  - mDNS/DNS-SD discovery tools"
        echo "  • ${CYAN}Docker${NC}       - Container platform (NOT installed by Isle-Mesh)"
        echo "  • ${CYAN}Node.js/npm${NC}  - JavaScript runtime (NOT installed by Isle-Mesh)"
        echo ""

        log_info "Checking which shared utilities are installed..."
        echo ""

        # Check each shared dependency
        SHARED_INSTALLED=""

        if command -v wget &> /dev/null; then
            echo -e "  ${GREEN}✓${NC} wget is installed"
            SHARED_INSTALLED="$SHARED_INSTALLED wget"
        fi

        if command -v jq &> /dev/null; then
            echo -e "  ${GREEN}✓${NC} jq is installed"
            SHARED_INSTALLED="$SHARED_INSTALLED jq"
        fi

        if command -v yq &> /dev/null; then
            echo -e "  ${GREEN}✓${NC} yq is installed"
            SHARED_INSTALLED="$SHARED_INSTALLED yq"
        fi

        if command -v avahi-browse &> /dev/null; then
            echo -e "  ${GREEN}✓${NC} avahi-utils is installed"
            SHARED_INSTALLED="$SHARED_INSTALLED avahi-utils"
        fi

        if [[ -n "$SHARED_INSTALLED" ]]; then
            echo ""
            log_warning "These utilities may be used by other applications on your system"
            echo ""
            read -p "Do you want to remove these shared utilities? (y/N): " remove_shared

            if [[ "$remove_shared" =~ ^[Yy]$ ]]; then
                echo ""
                log_info "Removing shared utilities..."

                for util in $SHARED_INSTALLED; do
                    # Map utility name to package name if different
                    case "$util" in
                        avahi-utils)
                            package_name="avahi-utils"
                            ;;
                        *)
                            package_name="$util"
                            ;;
                    esac

                    log_info "Removing $package_name..."
                    case "$PKG_MANAGER" in
                        apt)
                            apt-get remove -y "$package_name" 2>/dev/null && log_success "$package_name removed" || log_warning "Failed to remove $package_name"
                            ;;
                        yum|dnf)
                            $PKG_MANAGER remove -y "$package_name" 2>/dev/null && log_success "$package_name removed" || log_warning "Failed to remove $package_name"
                            ;;
                        pacman)
                            pacman -R --noconfirm "$package_name" 2>/dev/null && log_success "$package_name removed" || log_warning "Failed to remove $package_name"
                            ;;
                    esac
                done

                log_success "Shared utilities removed"
            else
                log_info "Shared utilities preserved"
            fi
        else
            log_info "No shared utilities found"
        fi

        # Check Docker and Node.js (not installed by Isle-Mesh, just informational)
        echo ""
        log_info "Checking Docker and Node.js status..."
        echo ""

        if command -v docker &> /dev/null; then
            echo -e "  ${CYAN}[INFO]${NC} Docker is installed (NOT installed by Isle-Mesh)"
            echo "         Remove manually if no longer needed: https://docs.docker.com/engine/install/"
        fi

        if command -v node &> /dev/null; then
            echo -e "  ${CYAN}[INFO]${NC} Node.js is installed (NOT installed by Isle-Mesh)"
            echo "         Remove manually if no longer needed"
        fi
    fi
fi

echo ""
log_step "Uninstall Complete"

log_success "Isle-Mesh has been uninstalled"
echo ""

if [[ "$REMOVE_ALL" == false ]]; then
    echo "System components still installed. Remove with:"
    echo -e "  ${CYAN}sudo isle uninstall --all${NC}"
    echo ""
fi

if [[ "$REMOVE_DEPS" == false ]]; then
    echo "Dependencies still installed. Remove with:"
    echo -e "  ${CYAN}sudo isle uninstall --all --remove-deps${NC}"
    echo ""
fi

echo "Thank you for using Isle-Mesh!"
echo ""