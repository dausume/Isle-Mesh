#!/usr/bin/env bash
# configure-discovery.sh - Configure OpenWRT discovery beacon
# Deploys the discovery beacon to OpenWRT router

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common-log.sh"
source "$SCRIPT_DIR/../lib/common-utils.sh"
source "$SCRIPT_DIR/../lib/template-engine.sh"

# Configuration
OPENWRT_IP="${OPENWRT_IP:-192.168.1.1}"
OPENWRT_USER="${OPENWRT_USER:-root}"
SSH_OPTS="-o ConnectTimeout=10 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

# Isle configuration (should match router config)
ISLE_NAME="${ISLE_NAME:-my-isle}"
VLAN_ID="${VLAN_ID:-10}"
ROUTER_IP="${ROUTER_IP:-10.${VLAN_ID}.0.1}"
DHCP_RANGE="${DHCP_RANGE:-10.${VLAN_ID}.0.0/24}"
BROADCAST_INTERVAL="${BROADCAST_INTERVAL:-30}"

show_usage() {
    cat << EOF
Configure OpenWRT Discovery Beacon

Usage: $0 [options]

Options:
  --isle-name NAME       Isle name (default: my-isle)
  --vlan-id ID          VLAN ID (default: 10)
  --router-ip IP        Router IP (default: 10.VLAN.0.1)
  --dhcp-range RANGE    DHCP range (default: 10.VLAN.0.0/24)
  --interval SECONDS    Broadcast interval (default: 30)
  --openwrt-ip IP       OpenWRT management IP (default: 192.168.1.1)
  -h, --help            Show this help

Description:
  Deploys the Isle discovery beacon to OpenWRT router. The beacon
  broadcasts discovery packets that remote nginx proxy agents can
  listen for and auto-configure bridges to join the mesh.

Example:
  sudo $0 --isle-name my-isle --vlan-id 10

EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --isle-name)
                ISLE_NAME="$2"
                shift 2
                ;;
            --vlan-id)
                VLAN_ID="$2"
                shift 2
                ;;
            --router-ip)
                ROUTER_IP="$2"
                shift 2
                ;;
            --dhcp-range)
                DHCP_RANGE="$2"
                shift 2
                ;;
            --interval)
                BROADCAST_INTERVAL="$2"
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
    if ! ssh $SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" "echo SSHOK" >/dev/null 2>&1; then
        log_error "Cannot connect to OpenWRT via SSH"
        log_info "Ensure the router is running and accessible"
        log_info "You may need to set root password or configure SSH keys"
        return 1
    fi
    log_success "SSH connectivity OK"
}

deploy_discovery_beacon() {
    log_step "Deploying Discovery Beacon to OpenWRT"

    # Create temporary directory
    local tmp_dir
    tmp_dir=$(mktemp -d)
    TMP_FILES="$tmp_dir"

    # Generate beacon script from template
    local beacon_template
    beacon_template=$(get_template "openwrt/scripts/isle-discovery-beacon.sh")

    apply_template "$beacon_template" "$tmp_dir/isle-discovery-beacon.sh" \
        "ISLE_NAME=${ISLE_NAME}" \
        "VLAN_ID=${VLAN_ID}" \
        "ROUTER_IP=${ROUTER_IP}" \
        "DHCP_RANGE=${DHCP_RANGE}" \
        "BROADCAST_INTERVAL=${BROADCAST_INTERVAL}"

    # Generate init script from template
    local init_template
    init_template=$(get_template "openwrt/scripts/isle-discovery-init.sh")

    apply_template "$init_template" "$tmp_dir/isle-discovery-init.sh" \
        "ISLE_NAME=${ISLE_NAME}" \
        "VLAN_ID=${VLAN_ID}"

    # Copy beacon script to OpenWRT
    log_info "Copying beacon script to OpenWRT..."
    scp $SSH_OPTS "$tmp_dir/isle-discovery-beacon.sh" \
        "${OPENWRT_USER}@${OPENWRT_IP}:/tmp/" || {
        log_error "Failed to copy beacon script"
        return 1
    }

    # Install beacon script
    log_info "Installing beacon on OpenWRT..."
    ssh $SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" << 'INSTALL_EOF'
        mv /tmp/isle-discovery-beacon.sh /usr/bin/isle-discovery-beacon
        chmod +x /usr/bin/isle-discovery-beacon
        echo "Beacon installed: /usr/bin/isle-discovery-beacon"
INSTALL_EOF

    # Copy and run init script
    log_info "Setting up discovery service..."
    scp $SSH_OPTS "$tmp_dir/isle-discovery-init.sh" \
        "${OPENWRT_USER}@${OPENWRT_IP}:/tmp/" || {
        log_error "Failed to copy init script"
        return 1
    }

    ssh $SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" << 'SERVICE_EOF'
        sh /tmp/isle-discovery-init.sh
        rm /tmp/isle-discovery-init.sh
SERVICE_EOF

    log_success "Discovery beacon deployed and started"
}

show_completion() {
    log_step "Discovery Beacon Configuration Complete"

    cat << EOF

${GREEN}╔═══════════════════════════════════════════════════════════════╗
║     OpenWRT Discovery Beacon Successfully Configured          ║
╚═══════════════════════════════════════════════════════════════╝${NC}

${BLUE}Configuration:${NC}
  Isle Name:      ${ISLE_NAME}
  vLAN ID:        ${VLAN_ID}
  Router IP:      ${ROUTER_IP}
  DHCP Range:     ${DHCP_RANGE}
  Broadcast:      Every ${BROADCAST_INTERVAL} seconds

${BLUE}Service Management (on OpenWRT):${NC}
  # Check status
  /etc/init.d/isle-discovery status

  # Restart service
  /etc/init.d/isle-discovery restart

  # View logs
  logread | grep isle-discovery

${BLUE}What Happens Next:${NC}
  1. OpenWRT broadcasts discovery packets every ${BROADCAST_INTERVAL}s
  2. Remote machines with isle-agent daemon receive broadcasts
  3. Agents auto-create bridges and connect nginx containers
  4. nginx containers get DHCP IPs from OpenWRT (${DHCP_RANGE})
  5. mDNS services are reflected across the mesh

${BLUE}Testing Discovery:${NC}
  # On remote machine, listen for broadcasts:
  sudo nc -l -u ${DISCOVERY_PORT:-7878}

  # You should see packets like:
  # ISLE_MESH_DISCOVERY|isle=${ISLE_NAME}|vlan=${VLAN_ID}|...

${YELLOW}Note:${NC} Remote nginx proxy agents must be installed and running
      to automatically join the mesh when they receive discovery packets.

EOF
}

main() {
    log_banner "OpenWRT Discovery Beacon Configuration"

    parse_args "$@"
    require_root
    require_commands ssh scp
    init_common_env

    check_ssh_connectivity || exit 1
    deploy_discovery_beacon
    show_completion

    log_success "Configuration complete!"
}

main "$@"
