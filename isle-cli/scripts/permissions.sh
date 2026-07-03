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
#   setup-core     - Grant all permissions needed to run as Isle Core
#   setup-connect  - Grant permissions needed to connect to an Isle
#   agent          - Fix agent permissions for /etc/isle-mesh
#   core           - Fix core permissions for router and runtime directories
#   docker         - Fix docker group membership
#   verify         - Verify permissions are correctly set
#   help           - Show detailed help
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
SUBCOMMAND="${1:-help}"
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

# Get the original user who ran sudo or pkexec
get_original_user() {
    if [[ -n "${SUDO_USER:-}" ]]; then
        echo "$SUDO_USER"
    elif [[ -n "${PKEXEC_UID:-}" ]]; then
        # pkexec sets PKEXEC_UID but not SUDO_USER
        getent passwd "$PKEXEC_UID" | cut -d: -f1
    else
        echo "$USER"
    fi
}

# ── Group helpers ──

# Ensure a group exists, create if not
ensure_group() {
    local group="$1"
    if ! getent group "$group" > /dev/null 2>&1; then
        log_info "Creating $group group..."
        groupadd "$group"
        log_success "$group group created"
    else
        log_success "$group group exists"
    fi
}

# Add user to a group if not already a member
ensure_user_in_group() {
    local user="$1"
    local group="$2"

    if [[ "$user" == "root" ]]; then
        return
    fi

    if ! id -nG "$user" | grep -qw "$group"; then
        log_info "Adding user '$user' to $group group..."
        usermod -aG "$group" "$user"
        log_success "User added to $group group"
        return 1  # signal that a new group was added
    else
        log_success "User '$user' is already in $group group"
        return 0
    fi
}

# ── Directory permission helpers ──

# Set directory tree to group-owned with setgid
fix_dir_permissions() {
    local dir="$1"
    local group="$2"

    if [[ ! -d "$dir" ]]; then
        log_info "Directory doesn't exist yet: $dir (will be created with correct permissions)"
        return
    fi

    log_info "Processing: $dir"

    chgrp -R "$group" "$dir" 2>/dev/null || {
        log_warning "Could not set group ownership on $dir"
    }

    # 2775 = setgid + rwxrwxr-x
    find "$dir" -type d -exec chmod 2775 {} \; 2>/dev/null || {
        log_warning "Could not set directory permissions on $dir"
    }

    # 664 = rw-rw-r--
    find "$dir" -type f -exec chmod 664 {} \; 2>/dev/null || {
        log_warning "Could not set file permissions on $dir"
    }

    log_success "Fixed: $dir"
}

# ── Subcommands ──

# Fix agent permissions (isle-mesh group + /etc/isle-mesh)
cmd_agent() {
    check_sudo

    echo -e "${CYAN}╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║          Fixing Isle-Mesh Agent Permissions                   ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    local ORIGINAL_USER
    ORIGINAL_USER="$(get_original_user)"

    ensure_group "isle-mesh"
    local GROUPS_CHANGED=false
    ensure_user_in_group "$ORIGINAL_USER" "isle-mesh" || GROUPS_CHANGED=true

    # Create /etc/isle-mesh directory if it doesn't exist
    if [[ ! -d "/etc/isle-mesh" ]]; then
        log_info "Creating /etc/isle-mesh directory..."
        mkdir -p /etc/isle-mesh/agent/{configs,ssl/{certs,keys},logs,mdns/services}
        log_success "Directory structure created"
    fi

    echo ""
    fix_dir_permissions "/etc/isle-mesh" "isle-mesh"

    echo ""
    log_success "Agent permissions fixed successfully!"
    echo ""
    echo -e "${BLUE}Summary:${NC}"
    echo "  Group:       isle-mesh"
    echo "  Directories: rwxrwxr-x (2775) with setgid bit"
    echo "  Files:       rw-rw-r-- (664)"
    echo ""

    if [[ "$GROUPS_CHANGED" == true ]]; then
        log_warning "Group changes require a new login session to take effect"
        log_info "Run: newgrp isle-mesh (or log out and log back in)"
        echo ""
    fi
}

# Fix docker group membership
cmd_docker() {
    check_sudo

    echo -e "${CYAN}╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║          Fixing Docker Permissions                            ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    local ORIGINAL_USER
    ORIGINAL_USER="$(get_original_user)"

    if ! getent group docker > /dev/null 2>&1; then
        log_error "docker group not found — is Docker installed?"
        log_info "Install Docker first, then re-run this command"
        exit 1
    fi

    log_success "docker group exists"

    local GROUPS_CHANGED=false
    ensure_user_in_group "$ORIGINAL_USER" "docker" || GROUPS_CHANGED=true

    echo ""
    log_success "Docker permissions fixed successfully!"
    echo ""

    if [[ "$GROUPS_CHANGED" == true ]]; then
        log_warning "Group changes require a new login session to take effect"
        log_info "Run: newgrp docker (or log out and log back in)"
        echo ""
    fi
}

# Fix core permissions (libvirt + kvm groups, router directories)
cmd_core() {
    check_sudo

    echo -e "${CYAN}╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║          Fixing Isle-Mesh Core Permissions                    ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    if ! getent group libvirt > /dev/null 2>&1; then
        log_error "libvirt group not found"
        log_info "Install router dependencies first: sudo isle install router"
        exit 1
    fi

    local ORIGINAL_USER
    ORIGINAL_USER="$(get_original_user)"

    local GROUPS_CHANGED=false
    ensure_user_in_group "$ORIGINAL_USER" "libvirt" || GROUPS_CHANGED=true
    ensure_user_in_group "$ORIGINAL_USER" "kvm" || GROUPS_CHANGED=true

    if [[ ! -d "$ROUTER_DIR" ]]; then
        log_error "Router directory not found: $ROUTER_DIR"
        exit 1
    fi

    echo ""
    log_info "Fixing permissions on Isle-Mesh router directories..."
    echo ""

    fix_dir_permissions "$ROUTER_DIR/images" "libvirt"
    fix_dir_permissions "$ROUTER_DIR/runtime" "libvirt"

    echo ""
    log_success "Core permissions fixed successfully!"
    echo ""
    echo -e "${BLUE}Summary:${NC}"
    echo "  Groups:      libvirt, kvm"
    echo "  Directories: rwxrwxr-x (2775) with setgid bit"
    echo "  Files:       rw-rw-r-- (664)"
    echo ""

    if [[ "$GROUPS_CHANGED" == true ]]; then
        log_warning "Group changes require a new login session to take effect"
        log_info "Run: newgrp libvirt (or log out and log back in)"
        echo ""
    fi
}

# ── Role commands (combine granular commands) ──

# Setup all permissions for Isle Core role
cmd_setup_core() {
    check_sudo

    echo -e "${CYAN}╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║          Isle Core — Full Permission Setup                    ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "This will configure permissions for running as an Isle Core node."
    echo -e "The core manages the router VM, agents, and containers."
    echo ""
    echo -e "${BLUE}Groups required:${NC} isle-mesh, docker, libvirt, kvm"
    echo ""

    local ORIGINAL_USER
    ORIGINAL_USER="$(get_original_user)"
    local GROUPS_CHANGED=false

    # ── isle-mesh group + /etc/isle-mesh ──
    echo -e "${YELLOW}── Agent Permissions ──${NC}"
    ensure_group "isle-mesh"
    ensure_user_in_group "$ORIGINAL_USER" "isle-mesh" || GROUPS_CHANGED=true

    if [[ ! -d "/etc/isle-mesh" ]]; then
        log_info "Creating /etc/isle-mesh directory..."
        mkdir -p /etc/isle-mesh/agent/{configs,ssl/{certs,keys},logs,mdns/services}
        log_success "Directory structure created"
    fi

    fix_dir_permissions "/etc/isle-mesh" "isle-mesh"
    echo ""

    # ── docker group ──
    echo -e "${YELLOW}── Docker Permissions ──${NC}"
    if getent group docker > /dev/null 2>&1; then
        ensure_user_in_group "$ORIGINAL_USER" "docker" || GROUPS_CHANGED=true
    else
        log_warning "docker group not found — install Docker, then re-run"
    fi
    echo ""

    # ── libvirt + kvm groups + router dirs ──
    echo -e "${YELLOW}── Core / Router Permissions ──${NC}"
    if getent group libvirt > /dev/null 2>&1; then
        ensure_user_in_group "$ORIGINAL_USER" "libvirt" || GROUPS_CHANGED=true
    else
        log_warning "libvirt group not found — install libvirt, then re-run"
    fi

    if getent group kvm > /dev/null 2>&1; then
        ensure_user_in_group "$ORIGINAL_USER" "kvm" || GROUPS_CHANGED=true
    else
        log_warning "kvm group not found — install qemu-kvm, then re-run"
    fi

    if [[ -d "$ROUTER_DIR" ]]; then
        fix_dir_permissions "$ROUTER_DIR/images" "libvirt"
        fix_dir_permissions "$ROUTER_DIR/runtime" "libvirt"
    else
        log_info "Router directory not found yet: $ROUTER_DIR"
    fi
    echo ""

    # ── Summary ──
    echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║  Isle Core permission setup complete!                         ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    if [[ "$GROUPS_CHANGED" == true ]]; then
        log_warning "New group memberships were added."
        log_info "You must log out and log back in (or run 'newgrp') for changes to take effect."
        echo ""
    fi

    echo -e "${BLUE}Verify with:${NC}  isle permissions verify"
    echo ""
}

# Setup permissions for connecting to an Isle (no router/VM management)
cmd_setup_connect() {
    check_sudo

    echo -e "${CYAN}╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║          Isle Connect — Permission Setup                      ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "This will configure permissions for connecting to an existing Isle."
    echo -e "Connect nodes run agents and containers but do not manage the router."
    echo ""
    echo -e "${BLUE}Groups required:${NC} isle-mesh, docker"
    echo ""

    local ORIGINAL_USER
    ORIGINAL_USER="$(get_original_user)"
    local GROUPS_CHANGED=false

    # ── isle-mesh group + /etc/isle-mesh ──
    echo -e "${YELLOW}── Agent Permissions ──${NC}"
    ensure_group "isle-mesh"
    ensure_user_in_group "$ORIGINAL_USER" "isle-mesh" || GROUPS_CHANGED=true

    if [[ ! -d "/etc/isle-mesh" ]]; then
        log_info "Creating /etc/isle-mesh directory..."
        mkdir -p /etc/isle-mesh/agent/{configs,ssl/{certs,keys},logs,mdns/services}
        log_success "Directory structure created"
    fi

    fix_dir_permissions "/etc/isle-mesh" "isle-mesh"
    echo ""

    # ── docker group ──
    echo -e "${YELLOW}── Docker Permissions ──${NC}"
    if getent group docker > /dev/null 2>&1; then
        ensure_user_in_group "$ORIGINAL_USER" "docker" || GROUPS_CHANGED=true
    else
        log_warning "docker group not found — install Docker, then re-run"
    fi
    echo ""

    # ── Summary ──
    echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║  Isle Connect permission setup complete!                      ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    if [[ "$GROUPS_CHANGED" == true ]]; then
        log_warning "New group memberships were added."
        log_info "You must log out and log back in (or run 'newgrp') for changes to take effect."
        echo ""
    fi

    echo -e "${BLUE}Verify with:${NC}  isle permissions verify"
    echo ""
}

# ── Verify ──

cmd_verify() {
    echo -e "${CYAN}╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║          Verifying Isle-Mesh Permissions                      ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    local CURRENT_USER="${USER}"
    local ALL_OK=true

    # ── Group membership checks ──
    echo -e "${YELLOW}── Group Membership ──${NC}"

    local verify_groups=("isle-mesh" "docker" "libvirt" "kvm")
    for group in "${verify_groups[@]}"; do
        if ! getent group "$group" > /dev/null 2>&1; then
            log_info "$group group does not exist (not installed)"
        elif id -nG "$CURRENT_USER" | grep -qw "$group"; then
            log_success "User '$CURRENT_USER' is in $group group"
        else
            log_error "User '$CURRENT_USER' is NOT in $group group"
            ALL_OK=false
        fi
    done

    echo ""

    # ── /etc/isle-mesh directory checks ──
    echo -e "${YELLOW}── Agent Directory (/etc/isle-mesh) ──${NC}"

    if [[ -d "/etc/isle-mesh" ]]; then
        local PERMS GROUP
        PERMS=$(stat -c "%a" /etc/isle-mesh 2>/dev/null)
        GROUP=$(stat -c "%G" /etc/isle-mesh 2>/dev/null)

        if [[ "$GROUP" == "isle-mesh" ]]; then
            log_success "Group correct (isle-mesh)"
        else
            log_error "Group should be isle-mesh, found $GROUP"
            ALL_OK=false
        fi

        if [[ "$PERMS" == "2775" ]]; then
            log_success "Permissions correct (2775)"
        else
            log_warning "Permissions should be 2775, found $PERMS"
            ALL_OK=false
        fi
    else
        log_info "/etc/isle-mesh does not exist yet"
    fi

    echo ""

    # ── Router directory checks ──
    echo -e "${YELLOW}── Core Directories ──${NC}"

    local DIRS=(
        "$ROUTER_DIR/images"
        "$ROUTER_DIR/runtime"
    )

    for dir in "${DIRS[@]}"; do
        if [[ -d "$dir" ]]; then
            local PERMS GROUP
            PERMS=$(stat -c "%a" "$dir" 2>/dev/null)
            GROUP=$(stat -c "%G" "$dir" 2>/dev/null)

            echo -e "${BLUE}Directory:${NC} $dir"
            echo "  Permissions: $PERMS"
            echo "  Group:       $GROUP"

            if [[ "$PERMS" == "2775" ]]; then
                log_success "Permissions correct (2775)"
            else
                log_warning "Permissions should be 2775, found $PERMS"
                ALL_OK=false
            fi

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

    # ── Result ──
    if [[ "$ALL_OK" == true ]]; then
        echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗"
        echo -e "║  All permissions are correctly configured! ✓                  ║"
        echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
    else
        echo -e "${YELLOW}╔═══════════════════════════════════════════════════════════════╗"
        echo -e "║  Some permissions need attention                              ║"
        echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
        echo ""
        log_info "Fix with: ${CYAN}sudo isle permissions setup-core${NC}"
        log_info "      or: ${CYAN}sudo isle permissions setup-connect${NC}"
    fi
    echo ""
}

# Machine-readable permission check for isle-manager-app
# Outputs one line per group: group=yes|no|missing
# "missing" means the group doesn't exist on the system (dependency not installed)
# Usage: isle permissions check <core|connect> [username]
cmd_check() {
    local role="${1:-core}"
    local user="${2:-${SUDO_USER:-$USER}}"

    local check_groups=("isle-mesh" "docker")
    if [[ "$role" == "core" ]]; then
        check_groups+=("libvirt" "kvm")
    fi

    for group in "${check_groups[@]}"; do
        if ! getent group "$group" > /dev/null 2>&1; then
            echo "${group}=missing"
        elif getent group "$group" | grep -qw "$user"; then
            echo "${group}=yes"
        else
            echo "${group}=no"
        fi
    done
}

# Remove user from one or all permission groups
# Usage: isle permissions revoke <group|all> [username]
cmd_revoke() {
    check_sudo

    local target="${1:-}"
    local user
    user="$(get_original_user)"

    if [[ -z "$target" ]]; then
        log_error "Usage: isle permissions revoke <group|all>"
        echo ""
        echo "  Groups: isle-mesh, docker, libvirt, kvm"
        echo "  Or use 'all' to remove from all isle-mesh related groups"
        exit 1
    fi

    echo -e "${CYAN}╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║          Revoking Isle-Mesh Permissions                       ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    local revoke_groups=()
    if [[ "$target" == "all" ]]; then
        revoke_groups=("isle-mesh" "docker" "libvirt" "kvm")
    else
        revoke_groups=("$target")
    fi

    for group in "${revoke_groups[@]}"; do
        if ! getent group "$group" > /dev/null 2>&1; then
            log_info "$group group does not exist — skipping"
            continue
        fi

        if getent group "$group" | grep -qw "$user"; then
            log_info "Removing user '$user' from $group group..."
            gpasswd -d "$user" "$group" > /dev/null 2>&1
            log_success "User removed from $group group"
        else
            log_info "User '$user' is not in $group group — skipping"
        fi
    done

    echo ""
    log_success "Revoke complete."
    log_warning "Group changes require a new login session to take effect."
    echo ""
}

# ── Help ──

cmd_help() {
    echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║              Isle Permissions Manager                         ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "Manage user and file permissions for Isle-Mesh."
    echo ""
    echo -e "${GREEN}USAGE:${NC}"
    echo -e "  isle permissions <subcommand>"
    echo ""
    echo -e "${GREEN}ROLE SETUP (start here):${NC}"
    echo -e "  ${CYAN}setup-core${NC}          Set up all permissions for an Isle Core node"
    echo -e "                       Groups: isle-mesh, docker, libvirt, kvm"
    echo -e "                       Dirs:   /etc/isle-mesh, router/images, router/runtime"
    echo -e "                       Requires: sudo"
    echo ""
    echo -e "  ${CYAN}setup-connect${NC}       Set up permissions for connecting to an Isle"
    echo -e "                       Groups: isle-mesh, docker"
    echo -e "                       Dirs:   /etc/isle-mesh"
    echo -e "                       Requires: sudo"
    echo ""
    echo -e "${GREEN}GRANULAR COMMANDS:${NC}"
    echo -e "  ${CYAN}agent${NC}               Fix agent permissions (isle-mesh group + /etc/isle-mesh)"
    echo -e "  ${CYAN}core${NC}                Fix core permissions (libvirt + kvm groups + router dirs)"
    echo -e "  ${CYAN}docker${NC}              Fix docker group membership"
    echo -e "  ${CYAN}verify${NC}              Verify all permissions are correctly configured"
    echo ""
    echo -e "${GREEN}EXAMPLES:${NC}"
    echo ""
    echo -e "  ${YELLOW}# Setting up a new Isle Core machine${NC}"
    echo -e "  sudo isle permissions setup-core"
    echo ""
    echo -e "  ${YELLOW}# Connecting a machine to an existing Isle${NC}"
    echo -e "  sudo isle permissions setup-connect"
    echo ""
    echo -e "  ${YELLOW}# Fix just one area${NC}"
    echo -e "  sudo isle permissions agent"
    echo -e "  sudo isle permissions core"
    echo -e "  sudo isle permissions docker"
    echo ""
    echo -e "  ${YELLOW}# Check what's configured${NC}"
    echo -e "  isle permissions verify"
    echo ""
    echo -e "${GREEN}PERMISSION MATRIX:${NC}"
    echo ""
    echo -e "  Group        Core  Connect  Purpose"
    echo -e "  ───────────  ────  ───────  ──────────────────────────────"
    echo -e "  isle-mesh     ✓      ✓      Agent configs (/etc/isle-mesh)"
    echo -e "  docker        ✓      ✓      Running containers"
    echo -e "  libvirt       ✓             Managing router VM"
    echo -e "  kvm           ✓             KVM hardware virtualization"
    echo ""
}

# ── Main command router ──

case "$SUBCOMMAND" in
    setup-core)
        cmd_setup_core "$@"
        ;;

    setup-connect)
        cmd_setup_connect "$@"
        ;;

    agent)
        cmd_agent "$@"
        ;;

    core)
        cmd_core "$@"
        ;;

    docker)
        cmd_docker "$@"
        ;;

    check)
        cmd_check "$@"
        ;;

    revoke)
        cmd_revoke "$@"
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
        echo "  setup-core     - Full permission setup for Isle Core"
        echo "  setup-connect  - Permission setup for connecting to an Isle"
        echo "  agent          - Fix agent permissions"
        echo "  core           - Fix core/router permissions"
        echo "  docker         - Fix docker permissions"
        echo "  verify         - Verify permissions"
        echo "  help           - Show help"
        echo ""
        echo "Run 'isle permissions help' for more information"
        exit 1
        ;;
esac
