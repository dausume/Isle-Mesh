#!/bin/bash

#############################################################################
# Isle Permissions Manager
#
# This script manages file permissions for Isle-Mesh directories to ensure
# proper access for libvirt, docker, and user processes.
#
# Usage: isle permissions <subcommand>
#
# Subcommands:
#   core         - Fix core permissions for router and runtime directories
#   verify       - Verify permissions are correctly set
#   help         - Show detailed help
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

# Get project root (parent of isle-cli)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ISLE_CLI_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$ISLE_CLI_ROOT/.." && pwd)"
ROUTER_DIR="$PROJECT_ROOT/openwrt-router"

# Parse subcommand
SUBCOMMAND="$1"
shift || true

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

# Check if running with sudo
check_sudo() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This command requires sudo privileges"
        log_info "Run with: sudo isle permissions $SUBCOMMAND"
        exit 1
    fi
}

# Fix core permissions
cmd_core() {
    check_sudo

    echo -e "${CYAN}╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║          Fixing Isle-Mesh Core Permissions                    ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    # Check if libvirt group exists
    if ! getent group libvirt > /dev/null 2>&1; then
        log_error "libvirt group not found"
        log_info "Install router dependencies first: sudo isle install router"
        exit 1
    fi

    # Get the original user who ran sudo
    local ORIGINAL_USER="${SUDO_USER:-$USER}"

    # Check if user is in libvirt group
    if [[ "$ORIGINAL_USER" != "root" ]]; then
        if ! id -nG "$ORIGINAL_USER" | grep -qw "libvirt"; then
            log_warning "User '$ORIGINAL_USER' is not in the libvirt group"
            log_info "Adding user to libvirt group..."
            usermod -aG libvirt "$ORIGINAL_USER"
            log_success "User added to libvirt group"
            echo ""
            log_warning "Group changes require a new login session to take effect"
            log_info "Run: newgrp libvirt (or log out and log back in)"
            echo ""
        fi
    fi

    # Fix permissions on router directories
    if [[ ! -d "$ROUTER_DIR" ]]; then
        log_error "Router directory not found: $ROUTER_DIR"
        exit 1
    fi

    log_info "Fixing permissions on Isle-Mesh directories..."
    echo ""

    # Array of directories to fix
    local DIRS=(
        "$ROUTER_DIR/images"
        "$ROUTER_DIR/runtime"
    )

    for dir in "${DIRS[@]}"; do
        if [[ -d "$dir" ]]; then
            log_info "Processing: $dir"

            # Set group ownership to libvirt
            chgrp -R libvirt "$dir" 2>/dev/null || {
                log_warning "Could not set group ownership on $dir"
            }

            # Set directory permissions with setgid bit
            # 2775 = setgid + rwxrwxr-x
            find "$dir" -type d -exec chmod 2775 {} \; 2>/dev/null || {
                log_warning "Could not set directory permissions on $dir"
            }

            # Set file permissions
            # 664 = rw-rw-r--
            find "$dir" -type f -exec chmod 664 {} \; 2>/dev/null || {
                log_warning "Could not set file permissions on $dir"
            }

            log_success "Fixed: $dir"
        else
            log_info "Directory doesn't exist yet: $dir (will be created with correct permissions)"
        fi
    done

    echo ""
    log_success "Core permissions fixed successfully!"
    echo ""
    echo -e "${BLUE}Summary:${NC}"
    echo "  Group:       libvirt"
    echo "  Directories: rwxrwxr-x (2775) with setgid bit"
    echo "  Files:       rw-rw-r-- (664)"
    echo ""
    echo -e "${BLUE}What this means:${NC}"
    echo "  • libvirt (qemu) can read/write VM images and configs"
    echo "  • Your user can manage files without sudo"
    echo "  • New files automatically inherit the libvirt group"
    echo ""
    echo -e "${BLUE}Next step:${NC}"
    echo -e "  Try provisioning again: ${CYAN}sudo isle router provision router-core${NC}"
    echo ""
}

# Verify permissions
cmd_verify() {
    echo -e "${CYAN}╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║          Verifying Isle-Mesh Permissions                      ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    local ALL_OK=true

    # Check if libvirt group exists
    if ! getent group libvirt > /dev/null 2>&1; then
        log_error "libvirt group not found"
        ALL_OK=false
    else
        log_success "libvirt group exists"
    fi

    # Check user membership
    local CURRENT_USER="${USER}"
    if id -nG "$CURRENT_USER" | grep -qw "libvirt"; then
        log_success "User '$CURRENT_USER' is in libvirt group"
    else
        log_error "User '$CURRENT_USER' is NOT in libvirt group"
        log_info "Run: sudo isle permissions core"
        ALL_OK=false
    fi

    echo ""
    log_info "Checking directory permissions..."
    echo ""

    # Array of directories to check
    local DIRS=(
        "$ROUTER_DIR/images"
        "$ROUTER_DIR/runtime"
    )

    for dir in "${DIRS[@]}"; do
        if [[ -d "$dir" ]]; then
            local PERMS=$(stat -c "%a" "$dir" 2>/dev/null)
            local GROUP=$(stat -c "%G" "$dir" 2>/dev/null)

            echo -e "${BLUE}Directory:${NC} $dir"
            echo "  Permissions: $PERMS"
            echo "  Group:       $GROUP"

            # Check if permissions are correct (2775)
            if [[ "$PERMS" == "2775" ]]; then
                log_success "Permissions correct (2775)"
            else
                log_warning "Permissions should be 2775, found $PERMS"
                ALL_OK=false
            fi

            # Check if group is correct
            if [[ "$GROUP" == "libvirt" ]]; then
                log_success "Group correct (libvirt)"
            else
                log_warning "Group should be libvirt, found $GROUP"
                ALL_OK=false
            fi
            echo ""
        else
            log_info "Directory doesn't exist: $dir"
            echo ""
        fi
    done

    if [[ "$ALL_OK" == true ]]; then
        echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗"
        echo -e "║  All permissions are correctly configured! ✓                  ║"
        echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
    else
        echo -e "${YELLOW}╔═══════════════════════════════════════════════════════════════╗"
        echo -e "║  Some permissions need attention                              ║"
        echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
        echo ""
        log_info "Run to fix: ${CYAN}sudo isle permissions core${NC}"
    fi
    echo ""
}

# Help
cmd_help() {
    echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║              Isle Permissions Manager                         ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "Manage file permissions for Isle-Mesh directories."
    echo ""
    echo -e "${GREEN}USAGE:${NC}"
    echo -e "  isle permissions <subcommand>"
    echo ""
    echo -e "${GREEN}SUBCOMMANDS:${NC}"
    echo -e "  ${CYAN}core${NC}                Fix core permissions for router directories"
    echo -e "                       - Sets group to libvirt"
    echo -e "                       - Sets directory permissions to 2775 (rwxrwxr-x)"
    echo -e "                       - Sets file permissions to 664 (rw-rw-r--)"
    echo -e "                       - Enables setgid bit for automatic group inheritance"
    echo -e "                       Requires: sudo"
    echo ""
    echo -e "  ${CYAN}verify${NC}              Verify permissions are correctly configured"
    echo -e "                       - Checks libvirt group membership"
    echo -e "                       - Verifies directory permissions"
    echo -e "                       - Reports any issues found"
    echo ""
    echo -e "  ${CYAN}help${NC}                Show this help message"
    echo ""
    echo -e "${GREEN}EXAMPLES:${NC}"
    echo ""
    echo -e "  ${YELLOW}# Fix permissions (run this if you get 'Permission denied' errors)${NC}"
    echo -e "  sudo isle permissions core"
    echo ""
    echo -e "  ${YELLOW}# Verify everything is set up correctly${NC}"
    echo -e "  isle permissions verify"
    echo ""
    echo -e "${GREEN}WHEN TO USE:${NC}"
    echo ""
    echo -e "  Run ${CYAN}sudo isle permissions core${NC} when:"
    echo -e "    • You get 'Permission denied' or 'Failed to open file' errors"
    echo -e "    • After installing router dependencies"
    echo -e "    • Files were created with wrong ownership (root:root)"
    echo -e "    • libvirt cannot access VM images or configs"
    echo ""
    echo -e "${GREEN}WHAT IT FIXES:${NC}"
    echo ""
    echo -e "  The ${CYAN}core${NC} command ensures:"
    echo -e "    • Your user is in the libvirt group"
    echo -e "    • Directories are accessible by libvirt (qemu)"
    echo -e "    • New files automatically get the correct group"
    echo -e "    • Both you and libvirt can read/write files"
    echo ""
    echo -e "${GREEN}DIRECTORIES MANAGED:${NC}"
    echo -e "  • $ROUTER_DIR/images"
    echo -e "  • $ROUTER_DIR/runtime"
    echo ""
}

# Main command router
case "$SUBCOMMAND" in
    core)
        cmd_core "$@"
        ;;

    verify)
        cmd_verify "$@"
        ;;

    help|--help|-h|"")
        cmd_help
        ;;

    *)
        log_error "Unknown subcommand: $SUBCOMMAND"
        echo ""
        echo "Usage: isle permissions <subcommand>"
        echo ""
        echo "Available subcommands:"
        echo "  core     - Fix core permissions"
        echo "  verify   - Verify permissions"
        echo "  help     - Show help"
        echo ""
        echo "Run 'isle permissions help' for more information"
        exit 1
        ;;
esac
