#!/usr/bin/env bash
# configure-dhcp-vlan.sh - Configure DHCP server for Isle vLAN with virtual MAC support
# This enables OpenWRT to assign IPs to remote nginx containers with virtual MACs

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common-log.sh"
source "$SCRIPT_DIR/../lib/common-utils.sh"
source "$SCRIPT_DIR/../lib/template-engine.sh"

# Configuration
OPENWRT_IP="${OPENWRT_IP:-192.168.1.1}"
OPENWRT_USER="${OPENWRT_USER:-root}"

# Source SSH key library for cached password and key-based auth
if [[ -f "$SCRIPT_DIR/router-init-lib/15-ssh-key.sh" ]]; then
  # Provide stub log functions if not already defined (15-ssh-key.sh may use them)
  source "$SCRIPT_DIR/router-init-lib/00-log.sh" 2>/dev/null || true
  source "$SCRIPT_DIR/router-init-lib/15-ssh-key.sh"
fi

# Build SSH_OPTS with key if available
ISLE_SSH_KEY_PATH="${ISLE_SSH_KEY:-/etc/isle-mesh/router/ssh/isle_router_key}"
if [[ -f "$ISLE_SSH_KEY_PATH" ]]; then
  SSH_OPTS="-i $ISLE_SSH_KEY_PATH -o ConnectTimeout=10 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
else
  SSH_OPTS="-o ConnectTimeout=10 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
fi

# Load cached password for sshpass fallback
_CACHED_PASS=""
_PASS_FILE="/etc/isle-mesh/router/ssh/.cached_password"
if [[ -f "$_PASS_FILE" ]]; then
  _CACHED_PASS="$(cat "$_PASS_FILE" 2>/dev/null)"
fi

# Wrapper: ssh with key-first, sshpass fallback
_ssh_router() {
  if ssh -o BatchMode=yes $SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" "$@" 2>/dev/null; then
    return 0
  fi
  if [[ -n "$_CACHED_PASS" ]] && command -v sshpass >/dev/null 2>&1; then
    sshpass -p "$_CACHED_PASS" ssh $SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" "$@"
    return $?
  fi
  ssh $SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" "$@"
}

# Wrapper: scp with -O flag (Dropbear lacks sftp-server) and sshpass fallback
_scp_router() {
  local src="$1" dst="$2"
  if scp -O -o BatchMode=yes $SSH_OPTS "$src" "${OPENWRT_USER}@${OPENWRT_IP}:${dst}" 2>/dev/null; then
    return 0
  fi
  if [[ -n "$_CACHED_PASS" ]] && command -v sshpass >/dev/null 2>&1; then
    sshpass -p "$_CACHED_PASS" scp -O $SSH_OPTS "$src" "${OPENWRT_USER}@${OPENWRT_IP}:${dst}"
    return $?
  fi
  scp -O $SSH_OPTS "$src" "${OPENWRT_USER}@${OPENWRT_IP}:${dst}"
}

# Isle configuration
ISLE_NAME="${ISLE_NAME:-my-isle}"
ISLE_UCI="myisle"
VLAN_ID="${VLAN_ID:-10}"
DHCP_START="${DHCP_START:-50}"
DHCP_LIMIT="${DHCP_LIMIT:-200}"
DHCP_LEASETIME="${DHCP_LEASETIME:-12h}"

show_usage() {
    cat << EOF
Configure DHCP Server for Isle vLAN

Usage: $0 [options]

Options:
  --isle-name NAME       Isle name (default: my-isle)
  --vlan-id ID          VLAN ID (default: 10)
  --dhcp-start N        DHCP pool start (default: 50)
  --dhcp-limit N        DHCP pool size (default: 200)
  --lease-time TIME     DHCP lease time (default: 12h)
  --openwrt-ip IP       OpenWRT management IP (default: 192.168.1.1)
  -h, --help            Show this help

Description:
  Configures the OpenWRT DHCP server to handle the Isle vLAN and
  assign IPs to remote nginx containers with virtual MAC addresses.

  Virtual MAC Pattern: 02:00:00:00:VLAN_ID:XX

  This allows remote nginx proxies to join the mesh by getting
  DHCP-assigned IPs without exposing their host machine's real IP.

Example:
  sudo $0 --isle-name my-isle --vlan-id 10 --dhcp-start 50

EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --isle-name)
                ISLE_NAME="$2"
                ISLE_UCI=$(echo "$ISLE_NAME" | tr -d '-')
                shift 2
                ;;
            --vlan-id)
                VLAN_ID="$2"
                shift 2
                ;;
            --dhcp-start)
                DHCP_START="$2"
                shift 2
                ;;
            --dhcp-limit)
                DHCP_LIMIT="$2"
                shift 2
                ;;
            --lease-time)
                DHCP_LEASETIME="$2"
                shift 2
                ;;
            --openwrt-ip)
                OPENWRT_IP="$2"
                shift 2
                ;;
            -h|--help)
                show_usage
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                show_usage
                exit 1
                ;;
        esac
    done
}

check_ssh_connectivity() {
    log_info "Testing SSH connectivity to ${OPENWRT_USER}@${OPENWRT_IP}..."
    if ! _ssh_router "echo SSHOK" >/dev/null 2>&1; then
        log_error "Cannot connect to OpenWRT via SSH"
        return 1
    fi
    log_success "SSH connectivity OK"
}

configure_dhcp() {
    log_step "Configuring DHCP for Isle vLAN"

    # Create temporary directory
    local tmp_dir
    tmp_dir=$(mktemp -d)
    TMP_FILES="$tmp_dir"

    # Generate DHCP config script from template
    local dhcp_template
    dhcp_template=$(get_template "openwrt/uci/dhcp-server.uci")

    apply_template "$dhcp_template" "$tmp_dir/dhcp-config.sh" \
        "ISLE_UCI=${ISLE_UCI}" \
        "VLAN_ID=${VLAN_ID}" \
        "DHCP_START=${DHCP_START}" \
        "DHCP_LIMIT=${DHCP_LIMIT}" \
        "DHCP_LEASETIME=${DHCP_LEASETIME}"

    # Copy to OpenWRT
    log_info "Copying DHCP configuration to OpenWRT..."
    _scp_router "$tmp_dir/dhcp-config.sh" "/tmp/" || {
        log_error "Failed to copy DHCP config"
        return 1
    }

    # Execute on OpenWRT
    log_info "Applying DHCP configuration..."
    _ssh_router "chmod +x /tmp/dhcp-config.sh && /tmp/dhcp-config.sh && rm /tmp/dhcp-config.sh"

    log_success "DHCP configuration applied"
}

show_completion() {
    log_step "DHCP Configuration Complete"

    cat << EOF

${GREEN}╔═══════════════════════════════════════════════════════════════╗
║      DHCP Server Configured for Isle vLAN                     ║
╚═══════════════════════════════════════════════════════════════╝${NC}

${BLUE}Configuration:${NC}
  Isle Name:      ${ISLE_NAME}
  vLAN ID:        ${VLAN_ID}
  DHCP Range:     10.${VLAN_ID}.0.${DHCP_START} - 10.${VLAN_ID}.0.$((DHCP_START + DHCP_LIMIT - 1))
  Lease Time:     ${DHCP_LEASETIME}
  Virtual MACs:   02:00:00:00:$(printf '%02x' ${VLAN_ID}):XX

${BLUE}What This Enables:${NC}
  ✓ Remote nginx containers can request DHCP IPs
  ✓ Containers use virtual MAC addresses (02:00:00:00:${VLAN_ID}:XX)
  ✓ OpenWRT assigns IPs without knowing real host IPs
  ✓ Complete IP isolation for remote mesh members

${BLUE}Testing DHCP:${NC}
  # On OpenWRT, check DHCP leases:
  cat /tmp/dhcp.leases

  # On OpenWRT, check DHCP config:
  uci show dhcp | grep ${ISLE_UCI}

  # Monitor DHCP requests:
  logread -f | grep dnsmasq

${BLUE}Expected Behavior:${NC}
  1. Remote nginx container joins bridge with virtual MAC
  2. Container sends DHCP DISCOVER
  3. OpenWRT DHCP server responds with OFFER
  4. Container gets IP: 10.${VLAN_ID}.0.XX
  5. Container broadcasts mDNS with this IP
  6. OpenWRT reflects mDNS across isle members

${YELLOW}Next Steps:${NC}
  1. Configure discovery beacon:
     sudo ./configure-discovery.sh --vlan-id ${VLAN_ID}

  2. On remote machines, install isle-agent daemon
     (This will be built next)

EOF
}

main() {
    log_banner "OpenWRT DHCP vLAN Configuration"

    parse_args "$@"
    require_root
    require_commands ssh scp
    init_common_env

    check_ssh_connectivity || exit 1
    configure_dhcp
    show_completion

    log_success "Configuration complete!"
}

main "$@"
