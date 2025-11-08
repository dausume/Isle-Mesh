#!/usr/bin/env bash
# common-utils.sh - Shared utility functions for Isle Mesh OpenWRT scripts
# Usage: source "$(dirname $0)/../lib/common-utils.sh"

if [[ -n "${_COMMON_UTILS_SH:-}" ]]; then return 0; fi
_COMMON_UTILS_SH=1

# Source logging if not already loaded
if [[ -z "${_COMMON_LOG_SH:-}" ]]; then
    SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
    source "$SCRIPT_DIR/common-log.sh"
fi

# State and configuration directories
STATE_DIR="${STATE_DIR:-/var/lib/isle-mesh}"
CONFIG_DIR="${CONFIG_DIR:-/etc/isle-mesh}"
LOG_DIR="${LOG_DIR:-/var/log/isle-mesh}"

# Ensure directories exist
init_directories() {
    mkdir -p "$STATE_DIR" "$CONFIG_DIR" "$LOG_DIR"
}

# Root check
require_root() {
    if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
        log_error "This script must be run as root (use sudo)"
        exit 1
    fi
}

# Check if command exists
require_command() {
    local cmd="$1"
    if ! command -v "$cmd" >/dev/null 2>&1; then
        log_error "Required command not found: $cmd"
        return 1
    fi
    return 0
}

# Check multiple commands
require_commands() {
    local missing=()
    for cmd in "$@"; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            missing+=("$cmd")
        fi
    done

    if (( ${#missing[@]} > 0 )); then
        log_error "Missing required commands: ${missing[*]}"
        return 1
    fi
    return 0
}

# Check if VM exists
vm_exists() {
    local vm_name="$1"
    virsh list --all | grep -qw "$vm_name"
}

# Assert VM exists or exit
assert_vm_exists() {
    local vm_name="${1:-${ROUTER_VM:-openwrt-isle-router}}"
    if ! vm_exists "$vm_name"; then
        log_error "VM '$vm_name' not found"
        log_info "Initialize with: sudo isle router init"
        exit 1
    fi
}

# Check if interface is reserved
is_port_reserved() {
    local port_id="$1"
    local reserved_file="${STATE_DIR}/reserved-ports.conf"
    [[ -f "$reserved_file" ]] && grep -q "^${port_id}$" "$reserved_file" 2>/dev/null
}

# Reserve a port
reserve_port() {
    local port_id="$1"
    local reserved_file="${STATE_DIR}/reserved-ports.conf"
    init_directories
    touch "$reserved_file"
    if ! is_port_reserved "$port_id"; then
        echo "$port_id" >> "$reserved_file"
        log_success "Reserved port: $port_id"
    fi
}

# Get ISP interface (the one with default route)
get_isp_interface() {
    local isp_file="${STATE_DIR}/isp-interface.conf"
    if [[ -f "$isp_file" ]]; then
        cat "$isp_file"
    else
        ip route | grep '^default' | awk '{print $5}' | head -n1
    fi
}

# Detect package manager
detect_package_manager() {
    command -v apt-get >/dev/null 2>&1 && { echo apt; return; }
    command -v dnf >/dev/null 2>&1 && { echo dnf; return; }
    command -v yum >/dev/null 2>&1 && { echo yum; return; }
    command -v pacman >/dev/null 2>&1 && { echo pacman; return; }
    command -v zypper >/dev/null 2>&1 && { echo zypper; return; }
    echo unknown
}

# Simple cleanup trap handler
cleanup_tmp() {
    if [[ -n "${TMP_FILES:-}" ]]; then
        rm -f $TMP_FILES 2>/dev/null || true
    fi
}

# Initialize common environment
init_common_env() {
    init_directories
    trap cleanup_tmp EXIT
}
