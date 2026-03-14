#!/bin/sh
#
# Isle Remote Agent - Discovery Listener
# One-shot script that listens on UDP 7878 for discovery beacons from the router.
# Parses ISLE_MESH_DISCOVERY|isle=NAME|vlan=ID|router=IP|dhcp=RANGE
# Writes JSON to /etc/isle-mesh/agent/remote/discovery.json
# Exits 0 on first valid beacon, exits 1 on timeout.
#

set -e

DISCOVERY_PORT="${DISCOVERY_PORT:-7878}"
TIMEOUT="${TIMEOUT:-60}"
OUTPUT_FILE="${OUTPUT_FILE:-/etc/isle-mesh/agent/remote/discovery.json}"

log() {
    echo "[discovery-listener] $*"
}

# Ensure output directory exists
mkdir -p "$(dirname "$OUTPUT_FILE")"

log "Listening for Isle discovery beacon on UDP port ${DISCOVERY_PORT}..."
log "Timeout: ${TIMEOUT}s"

# Determine which tool to use for listening
LISTEN_TOOL=""
if command -v socat >/dev/null 2>&1; then
    LISTEN_TOOL="socat"
elif command -v nc >/dev/null 2>&1; then
    LISTEN_TOOL="nc"
else
    log "ERROR: Neither socat nor nc is available"
    log "Install socat: sudo apt-get install socat"
    exit 1
fi

# Listen for a single beacon
BEACON=""
case "$LISTEN_TOOL" in
    socat)
        BEACON=$(timeout "$TIMEOUT" socat -u UDP4-RECV:${DISCOVERY_PORT},reuseaddr - 2>/dev/null | head -n1) || true
        ;;
    nc)
        BEACON=$(timeout "$TIMEOUT" nc -lu -p ${DISCOVERY_PORT} 2>/dev/null | head -n1) || true
        ;;
esac

if [ -z "$BEACON" ]; then
    log "ERROR: No discovery beacon received within ${TIMEOUT}s"
    log ""
    log "Possible causes:"
    log "  - No Isle router is broadcasting on this network"
    log "  - Firewall blocking UDP port ${DISCOVERY_PORT}"
    log "  - Router discovery service not running"
    log ""
    log "Verify on the router: /etc/init.d/isle-discovery status"
    exit 1
fi

log "Received beacon: $BEACON"

# Validate beacon format
if ! echo "$BEACON" | grep -q "^ISLE_MESH_DISCOVERY|"; then
    log "ERROR: Invalid beacon format"
    exit 1
fi

# Parse beacon fields
ISLE_NAME=$(echo "$BEACON" | sed -n 's/.*|isle=\([^|]*\).*/\1/p')
VLAN_ID=$(echo "$BEACON" | sed -n 's/.*|vlan=\([^|]*\).*/\1/p')
ROUTER_IP=$(echo "$BEACON" | sed -n 's/.*|router=\([^|]*\).*/\1/p')
DHCP_RANGE=$(echo "$BEACON" | sed -n 's/.*|dhcp=\([^|]*\).*/\1/p')

# Validate required fields
if [ -z "$ISLE_NAME" ] || [ -z "$VLAN_ID" ] || [ -z "$ROUTER_IP" ] || [ -z "$DHCP_RANGE" ]; then
    log "ERROR: Beacon missing required fields"
    log "  isle=${ISLE_NAME} vlan=${VLAN_ID} router=${ROUTER_IP} dhcp=${DHCP_RANGE}"
    exit 1
fi

# Write discovery data as JSON
cat > "$OUTPUT_FILE" <<EOF
{
  "isle_name": "${ISLE_NAME}",
  "vlan_id": "${VLAN_ID}",
  "router_ip": "${ROUTER_IP}",
  "dhcp_range": "${DHCP_RANGE}",
  "raw_beacon": "${BEACON}",
  "discovered_at": "$(date -Iseconds)"
}
EOF

log "Discovery data written to ${OUTPUT_FILE}"
log ""
log "  Isle:       ${ISLE_NAME}"
log "  VLAN ID:    ${VLAN_ID}"
log "  Router IP:  ${ROUTER_IP}"
log "  DHCP Range: ${DHCP_RANGE}"

exit 0
