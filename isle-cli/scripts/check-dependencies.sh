#!/usr/bin/env bash
#
# Isle-Mesh Dependency Management
#
# Provides functions to check, prompt, and install dependencies for Isle commands
# Can be sourced by other scripts or run standalone

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Log functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $*"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

# Dependency metadata
# Format: dependency_name|package_name|description|post_install_action
declare -A DEPENDENCIES=(
    # Core dependencies
    ["docker"]="docker.io|Docker container runtime|docker_post_install"
    ["docker-compose"]="docker-compose-v2|Docker Compose tool|docker_compose_post_install"

    # Virtualization dependencies
    ["virsh"]="qemu-kvm,libvirt-daemon-system,libvirt-clients,bridge-utils|KVM/QEMU virtualization for router|virsh_post_install"
    ["virt-install"]="virtinst|VM installation tool|none"

    # Network utilities
    ["brctl"]="bridge-utils|Network bridge utilities|none"
    ["ip"]="iproute2|IP routing utilities|none"

    # Configuration tools
    ["jq"]="jq|JSON processor|none"
    ["yq"]="yq|YAML processor (snap)|yq_post_install"

    # Python dependencies
    ["python3"]="python3,python3-pip|Python 3 runtime|none"
    ["python3-yaml"]="python3-yaml|Python YAML library|none"
    ["python3-jinja2"]="python3-jinja2|Python Jinja2 templating|none"

    # mDNS dependencies
    ["avahi-daemon"]="avahi-daemon|Avahi mDNS daemon|avahi_post_install"
    ["avahi-publish"]="avahi-utils|Avahi utilities|none"
)

# Check if a command exists
command_exists() {
    command -v "$1" &>/dev/null
}

# Check if a dependency is installed
is_dependency_installed() {
    local dep_name="$1"

    case "$dep_name" in
        docker)
            command_exists docker && docker ps &>/dev/null
            ;;
        docker-compose)
            docker compose version &>/dev/null || docker-compose --version &>/dev/null
            ;;
        virsh)
            command_exists virsh
            ;;
        yq)
            command_exists yq || snap list yq &>/dev/null
            ;;
        *)
            command_exists "$dep_name"
            ;;
    esac
}

# Get dependency info
get_dependency_info() {
    local dep_name="$1"
    local field="$2"  # package_name, description, or post_install

    if [[ ! -v DEPENDENCIES[$dep_name] ]]; then
        echo "unknown"
        return 1
    fi

    local info="${DEPENDENCIES[$dep_name]}"
    IFS='|' read -r package_name description post_install <<< "$info"

    case "$field" in
        package_name) echo "$package_name" ;;
        description) echo "$description" ;;
        post_install) echo "$post_install" ;;
        *) echo "unknown" ;;
    esac
}

# Post-install action: Docker
docker_post_install() {
    log_info "Configuring Docker..."

    # Add current user to docker group
    local current_user="${SUDO_USER:-$USER}"

    if ! groups "$current_user" | grep -q docker; then
        log_info "Adding $current_user to docker group..."
        sudo usermod -aG docker "$current_user"

        log_success "User added to docker group"
        echo ""
        log_warn "IMPORTANT: Docker group membership requires a new login session"
        echo ""
        echo "You have two options:"
        echo ""
        echo "  1. Run: newgrp docker"
        echo "     (applies docker group in current shell only)"
        echo ""
        echo "  2. Log out and log back in"
        echo "     (applies docker group system-wide)"
        echo ""

        # Ask user if they want to continue with newgrp
        read -p "Would you like to run 'newgrp docker' now? (y/N): " -r
        echo ""
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            log_info "Note: After 'newgrp docker', you may need to re-run your original command"
            echo ""
            exec newgrp docker
        fi

        return 1  # Signal that terminal needs restart
    fi

    # Start and enable Docker service
    if ! systemctl is-active --quiet docker; then
        log_info "Starting Docker service..."
        sudo systemctl start docker
        sudo systemctl enable docker
        log_success "Docker service started and enabled"
    fi

    return 0
}

# Post-install action: Docker Compose
docker_compose_post_install() {
    # Verify docker compose works (plugin or standalone)
    if docker compose version &>/dev/null; then
        log_success "Docker Compose (plugin) is working"
        return 0
    elif docker-compose --version &>/dev/null; then
        log_success "Docker Compose (standalone) is working"
        return 0
    else
        log_error "Docker Compose installation may have failed"
        return 1
    fi
}

# Post-install action: virsh/KVM
virsh_post_install() {
    log_info "Configuring virtualization..."

    local current_user="${SUDO_USER:-$USER}"

    # Add user to libvirt and kvm groups
    local groups_added=false
    if ! groups "$current_user" | grep -q libvirt; then
        sudo usermod -aG libvirt "$current_user"
        groups_added=true
    fi

    if ! groups "$current_user" | grep -q kvm; then
        sudo usermod -aG kvm "$current_user"
        groups_added=true
    fi

    if $groups_added; then
        log_success "User added to libvirt and kvm groups"
        log_warn "Group membership will take effect after logout/login"
    fi

    # Start and enable libvirtd
    if ! systemctl is-active --quiet libvirtd; then
        log_info "Starting libvirt daemon..."
        sudo systemctl start libvirtd
        sudo systemctl enable libvirtd
        log_success "libvirt daemon started and enabled"
    fi

    return 0
}

# Post-install action: yq (installed via snap)
yq_post_install() {
    log_info "Installing yq via snap..."

    # Check if snapd is installed
    if ! command_exists snap; then
        log_info "Installing snapd..."
        sudo apt-get install -y snapd
    fi

    # Install yq via snap
    sudo snap install yq

    log_success "yq installed via snap"
    return 0
}

# Post-install action: Avahi
avahi_post_install() {
    log_info "Configuring Avahi mDNS..."

    # Start and enable avahi-daemon
    if ! systemctl is-active --quiet avahi-daemon; then
        log_info "Starting Avahi daemon..."
        sudo systemctl start avahi-daemon
        sudo systemctl enable avahi-daemon
        log_success "Avahi daemon started and enabled"
    fi

    return 0
}

# Install a single dependency
install_dependency() {
    local dep_name="$1"

    local package_name=$(get_dependency_info "$dep_name" "package_name")
    local description=$(get_dependency_info "$dep_name" "description")
    local post_install_fn=$(get_dependency_info "$dep_name" "post_install")

    if [[ "$package_name" == "unknown" ]]; then
        log_error "Unknown dependency: $dep_name"
        return 1
    fi

    log_info "Installing $dep_name ($description)..."

    # Special case: yq is installed via snap
    if [[ "$dep_name" == "yq" ]]; then
        if [[ "$post_install_fn" != "none" ]]; then
            $post_install_fn
            return $?
        fi
        return 0
    fi

    # Standard apt installation
    # shellcheck disable=SC2086
    if sudo apt-get update && sudo apt-get install -y ${package_name//,/ }; then
        log_success "$dep_name installed successfully"

        # Run post-install action if defined
        if [[ "$post_install_fn" != "none" ]]; then
            if $post_install_fn; then
                return 0
            else
                return 1  # Post-install failed or requires restart
            fi
        fi

        return 0
    else
        log_error "Failed to install $dep_name"
        return 1
    fi
}

# Check if dependency is installed, prompt to install if not
# Returns: 0 if dependency is available (installed or user declined)
#          1 if dependency is missing and user declined or installation failed
ensure_dependency() {
    local dep_name="$1"
    local context="${2:-use this feature}"  # What operation needs this dependency
    local required="${3:-true}"  # Is this a required dependency?

    # Check if already installed
    if is_dependency_installed "$dep_name"; then
        return 0
    fi

    local description=$(get_dependency_info "$dep_name" "description")

    # Not installed - show prompt
    echo ""
    echo -e "${YELLOW}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║           Dependency Not Installed: $dep_name${NC}"
    echo -e "${YELLOW}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    if [[ "$required" == "true" ]]; then
        echo -e "${RED}$dep_name is required to $context${NC}"
    else
        echo -e "${BLUE}$dep_name is recommended to $context${NC}"
    fi

    echo ""
    echo "Dependency: $dep_name"
    echo "Description: $description"
    echo ""

    # Special messaging for Docker
    if [[ "$dep_name" == "docker" ]]; then
        echo -e "${YELLOW}Note: After installing Docker, you may need to log out and back in${NC}"
        echo -e "${YELLOW}      or run 'newgrp docker' to apply group permissions.${NC}"
        echo ""
    fi

    # Ask user if they want to install
    if [[ "$required" == "true" ]]; then
        read -p "Install $dep_name now? (Y/n): " -r
        echo ""

        # Default to yes for required dependencies
        if [[ ! $REPLY =~ ^[Nn]$ ]]; then
            if install_dependency "$dep_name"; then
                # Check if it's available now
                if is_dependency_installed "$dep_name"; then
                    log_success "$dep_name is now available"
                    return 0
                else
                    log_warn "$dep_name was installed but may require logout/login"
                    echo ""
                    echo "Please log out and log back in, then try again."
                    echo ""
                    return 1
                fi
            else
                log_error "Installation failed"
                return 1
            fi
        else
            log_warn "Skipping $dep_name installation"
            return 1
        fi
    else
        read -p "Install $dep_name now? (y/N): " -r
        echo ""

        # Default to no for optional dependencies
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            if install_dependency "$dep_name"; then
                log_success "$dep_name installed successfully"
                return 0
            else
                log_error "Installation failed"
                return 1
            fi
        else
            log_info "Continuing without $dep_name"
            return 0  # Return success for optional dependencies
        fi
    fi
}

# Check multiple dependencies at once
# Usage: ensure_dependencies "dep1 dep2 dep3" "context"
ensure_dependencies() {
    local deps="$1"
    local context="${2:-use this feature}"
    local required="${3:-true}"

    local all_satisfied=true

    for dep in $deps; do
        if ! ensure_dependency "$dep" "$context" "$required"; then
            if [[ "$required" == "true" ]]; then
                all_satisfied=false
            fi
        fi
    done

    if [[ "$all_satisfied" == "false" ]]; then
        return 1
    fi

    return 0
}

# Show status of all known dependencies
show_dependency_status() {
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                 Isle-Mesh Dependency Status                  ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""

    printf "%-20s %-10s %-40s\n" "Dependency" "Status" "Description"
    printf "%-20s %-10s %-40s\n" "--------------------" "----------" "----------------------------------------"

    for dep_name in "${!DEPENDENCIES[@]}"; do
        local description=$(get_dependency_info "$dep_name" "description")
        local status

        if is_dependency_installed "$dep_name"; then
            status="${GREEN}✓ Installed${NC}"
        else
            status="${RED}✗ Missing${NC}"
        fi

        printf "%-20s %-20s %-40s\n" "$dep_name" "$(echo -e "$status")" "$description"
    done | sort

    echo ""
}

# Install all missing dependencies
install_all_dependencies() {
    local context="${1:-install all Isle-Mesh dependencies}"

    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║           Install All Isle-Mesh Dependencies                  ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""

    local missing_deps=()

    # Find all missing dependencies
    for dep_name in "${!DEPENDENCIES[@]}"; do
        if ! is_dependency_installed "$dep_name"; then
            missing_deps+=("$dep_name")
        fi
    done

    if [[ ${#missing_deps[@]} -eq 0 ]]; then
        log_success "All dependencies are already installed"
        return 0
    fi

    echo "The following dependencies are missing:"
    echo ""
    for dep in "${missing_deps[@]}"; do
        local description=$(get_dependency_info "$dep" "description")
        echo "  • $dep - $description"
    done
    echo ""

    read -p "Install all missing dependencies? (y/N): " -r
    echo ""

    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "Installation cancelled"
        return 1
    fi

    local failed_deps=()

    for dep in "${missing_deps[@]}"; do
        if ! install_dependency "$dep"; then
            failed_deps+=("$dep")
        fi
    done

    if [[ ${#failed_deps[@]} -eq 0 ]]; then
        log_success "All dependencies installed successfully"
        return 0
    else
        log_error "Failed to install: ${failed_deps[*]}"
        return 1
    fi
}

# Main CLI when run standalone
main() {
    local command="${1:-help}"

    case "$command" in
        check)
            # Check specific dependency
            if [[ -n "${2:-}" ]]; then
                if is_dependency_installed "$2"; then
                    echo "✓ $2 is installed"
                    exit 0
                else
                    echo "✗ $2 is not installed"
                    exit 1
                fi
            else
                show_dependency_status
            fi
            ;;
        install)
            # Install specific dependency
            if [[ -n "${2:-}" ]]; then
                ensure_dependency "$2" "manual installation" "true"
            else
                install_all_dependencies
            fi
            ;;
        status)
            show_dependency_status
            ;;
        help|--help|-h)
            cat <<EOF
Isle-Mesh Dependency Management

Usage: $(basename "$0") <command> [dependency]

Commands:
  check [dep]    Check if a dependency is installed (or all if no dep specified)
  install [dep]  Install a dependency (or all if no dep specified)
  status         Show status of all dependencies
  help           Show this help message

Available dependencies:
  docker         Docker container runtime
  docker-compose Docker Compose plugin
  virsh          KVM/QEMU virtualization
  virt-install   VM installation tool
  brctl          Bridge utilities
  ip             IP routing utilities
  jq             JSON processor
  yq             YAML processor
  python3        Python 3 runtime
  avahi-daemon   mDNS daemon

Examples:
  $(basename "$0") check docker        # Check if Docker is installed
  $(basename "$0") install docker      # Install Docker
  $(basename "$0") install             # Install all missing dependencies
  $(basename "$0") status              # Show all dependency status

This script can also be sourced by other scripts to use the ensure_dependency() function:
  source $(basename "$0")
  ensure_dependency "docker" "run containers" "true"

EOF
            ;;
        *)
            log_error "Unknown command: $command"
            echo "Run '$(basename "$0") help' for usage"
            exit 1
            ;;
    esac
}

# Run main if executed directly (not sourced)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
