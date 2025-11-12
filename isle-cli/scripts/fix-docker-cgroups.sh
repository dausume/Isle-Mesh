#!/bin/bash

#############################################################################
# Docker Cgroup Driver Fix
#
# Detects and fixes the systemd D-Bus communication issue that prevents
# Docker containers from starting. This commonly occurs when running from
# sandboxed environments (like VS Code snap) where D-Bus communication
# is restricted.
#
# The fix switches Docker from 'systemd' to 'cgroupfs' cgroup driver.
#############################################################################

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

DAEMON_JSON="/etc/docker/daemon.json"
BACKUP_JSON="/etc/docker/daemon.json.backup-$(date +%Y%m%d-%H%M%S)"

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
    echo -e "${CYAN}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC} ${BOLD}$1${NC}"
    echo -e "${CYAN}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# Check if Docker has systemd D-Bus communication issues
check_docker_issue() {
    log_step "Checking Docker Configuration"

    # Try to run a simple container
    log_info "Testing Docker container creation..."

    if docker run --rm alpine echo "test" &> /tmp/docker-test.log 2>&1; then
        log_success "Docker is working correctly - no fix needed"
        return 1  # Return 1 to indicate no fix needed
    fi

    # Check if the error is the systemd D-Bus issue
    if grep -q "org.freedesktop.systemd1 was not provided by any .service files" /tmp/docker-test.log; then
        log_error "Detected systemd D-Bus communication issue"
        log_info "This commonly occurs in sandboxed environments (VS Code snap, etc.)"
        echo ""
        cat /tmp/docker-test.log
        echo ""
        return 0  # Return 0 to indicate fix is needed
    else
        log_error "Docker has an issue, but it's not the systemd D-Bus problem"
        echo ""
        cat /tmp/docker-test.log
        echo ""
        log_info "This script only fixes the systemd D-Bus issue"
        log_info "Please investigate the error above"
        exit 1
    fi
}

# Create backup of existing daemon.json
backup_daemon_config() {
    if [[ -f "$DAEMON_JSON" ]]; then
        log_info "Backing up existing daemon.json to $BACKUP_JSON"
        sudo cp "$DAEMON_JSON" "$BACKUP_JSON"
        log_success "Backup created"
    else
        log_info "No existing daemon.json found"
    fi
}

# Create or update daemon.json with cgroupfs driver
create_daemon_config() {
    log_step "Configuring Docker Daemon"

    if [[ -f "$DAEMON_JSON" ]]; then
        log_info "Updating existing daemon.json..."

        # Parse existing JSON and add/update the exec-opts
        TEMP_JSON=$(mktemp)

        # Use jq if available, otherwise use simple merge
        if command -v jq &> /dev/null; then
            sudo jq '. + {"exec-opts": ["native.cgroupdriver=cgroupfs"]}' "$DAEMON_JSON" > "$TEMP_JSON"
            sudo mv "$TEMP_JSON" "$DAEMON_JSON"
        else
            # Simple fallback: just overwrite with our config
            log_warning "jq not installed, creating new daemon.json (previous config will be in backup)"
            echo '{
  "exec-opts": ["native.cgroupdriver=cgroupfs"]
}' | sudo tee "$DAEMON_JSON" > /dev/null
        fi
    else
        log_info "Creating new daemon.json..."
        echo '{
  "exec-opts": ["native.cgroupdriver=cgroupfs"]
}' | sudo tee "$DAEMON_JSON" > /dev/null
    fi

    log_success "Docker daemon configuration updated"
}

# Restart Docker daemon
restart_docker() {
    log_step "Restarting Docker Daemon"

    log_info "Reloading systemd configuration..."
    if sudo systemctl daemon-reload 2>&1 | grep -q "Failed to connect"; then
        log_warning "systemctl daemon-reload failed (this is expected in some environments)"
    else
        log_success "systemd configuration reloaded"
    fi

    log_info "Restarting Docker service..."
    if sudo systemctl restart docker 2>&1 | grep -q "Failed to connect"; then
        log_warning "systemctl restart failed, trying direct restart"

        # Try stopping and starting Docker manually
        sudo pkill -SIGHUP dockerd || true
        sleep 2
    else
        log_success "Docker service restarted"
    fi

    # Wait for Docker to be ready
    log_info "Waiting for Docker to be ready..."
    sleep 3

    local retries=10
    while [[ $retries -gt 0 ]]; do
        if docker info &> /dev/null; then
            log_success "Docker daemon is ready"
            return 0
        fi
        retries=$((retries - 1))
        sleep 1
    done

    log_error "Docker daemon did not become ready"
    return 1
}

# Verify the fix worked
verify_fix() {
    log_step "Verifying Fix"

    log_info "Testing Docker container creation..."

    if docker run --rm alpine echo "Docker is working!" &> /tmp/docker-verify.log 2>&1; then
        log_success "Docker is now working correctly!"
        echo ""
        log_info "The cgroupfs driver has been successfully configured"
        return 0
    else
        log_error "Docker still has issues after the fix"
        echo ""
        cat /tmp/docker-verify.log
        echo ""
        log_info "You may need to fully restart the Docker daemon or reboot your system"
        return 1
    fi
}

# Show current Docker configuration
show_docker_config() {
    log_step "Current Docker Configuration"

    log_info "Cgroup Driver:"
    docker info 2>/dev/null | grep -E "Cgroup Driver|Cgroup Version" || echo "Unable to get cgroup info"

    echo ""
    if [[ -f "$DAEMON_JSON" ]]; then
        log_info "Current daemon.json:"
        cat "$DAEMON_JSON"
    else
        log_info "No daemon.json file exists"
    fi
}

# Main function
main() {
    case "${1:-check}" in
        check)
            # Just check if the issue exists
            if check_docker_issue; then
                echo ""
                log_warning "Docker has the systemd D-Bus communication issue"
                log_info "Run one of these commands to apply the fix:"
                echo ""
                echo "  isle fix-docker fix        (via Isle CLI - recommended)"
                echo "  sudo bash $0 fix           (direct script)"
                echo ""
                exit 1
            else
                exit 0
            fi
            ;;

        fix)
            # Check if running with sudo
            if [[ $EUID -ne 0 ]]; then
                log_error "This command must be run with sudo"
                echo ""
                echo "Usage: sudo bash $0 fix"
                echo ""
                exit 1
            fi

            log_step "Docker Cgroup Driver Fix"

            # Check if fix is needed
            if ! check_docker_issue; then
                exit 0
            fi

            backup_daemon_config
            create_daemon_config
            restart_docker

            if verify_fix; then
                echo ""
                log_success "Docker has been successfully fixed!"
                echo ""
                log_info "You can now continue with Isle Mesh setup"
                exit 0
            else
                echo ""
                log_error "The fix did not resolve the issue"
                echo ""
                log_info "Possible solutions:"
                echo "  1. Reboot your system"
                echo "  2. Completely stop and start Docker:"
                echo "     sudo systemctl stop docker"
                echo "     sudo systemctl start docker"
                echo "  3. If the issue persists, you may need to reinstall Docker"
                echo ""

                if [[ -f "$BACKUP_JSON" ]]; then
                    log_info "Your original daemon.json has been backed up to:"
                    echo "  $BACKUP_JSON"
                fi
                exit 1
            fi
            ;;

        status)
            show_docker_config
            ;;

        *)
            echo "Usage: $0 {check|fix|status}"
            echo ""
            echo "  check   - Check if Docker has the systemd D-Bus issue (default)"
            echo "  fix     - Apply the cgroupfs fix (requires sudo)"
            echo "  status  - Show current Docker configuration"
            echo ""
            exit 1
            ;;
    esac
}

main "$@"
