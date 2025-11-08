#!/bin/bash

#############################################################################
# Isle-Mesh Port Detection Initialization
#
# This script runs ONCE at boot to initialize the port detection system:
# - Detects and stores ISP interface
# - Creates state directories
# - No continuous monitoring (that's done by udev!)
#
# Usage: Called by systemd service
#
#############################################################################

set -e

# Configuration
STATE_DIR="/var/lib/isle-mesh"
ISP_INTERFACE_FILE="$STATE_DIR/isp-interface.conf"
RESERVED_PORTS_FILE="$STATE_DIR/reserved-ports.conf"
LOG_FILE="/var/log/isle-mesh/port-init.log"

# Ensure directories exist
mkdir -p "$STATE_DIR"
mkdir -p "$(dirname "$LOG_FILE")"
touch "$RESERVED_PORTS_FILE"

# Log function
log_event() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    echo "$msg" >> "$LOG_FILE"
    echo "$msg"
    logger -t isle-port-init "$1"
}

log_event "Isle-Mesh port detection initialization starting..."

# Detect ISP interface (the one with default route)
ISP_IFACE=$(ip route | grep '^default' | awk '{print $5}' | head -n1)

if [[ -n "$ISP_IFACE" ]]; then
    echo "$ISP_IFACE" > "$ISP_INTERFACE_FILE"
    log_event "Detected ISP interface: $ISP_IFACE (will be excluded from Isle-Mesh)"
else
    log_event "WARNING: No ISP interface detected (no default route)"
fi

log_event "Port detection initialization complete"
log_event "System is now EVENT-DRIVEN (no continuous polling)"
log_event "Plug in USB/Ethernet to trigger detection dialogs"

exit 0
