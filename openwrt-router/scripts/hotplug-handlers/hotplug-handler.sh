#!/bin/bash

#############################################################################
# Isle-Mesh Port Event Wrapper
#
# Called by udev rules when hardware events occur.
# Forwards events to the port event handler.
#
# This is just a thin wrapper that udev calls, which then calls
# the actual event handler script.
#
# Usage (called by udev):
#   isle-port-event usb <device_path>
#   isle-port-event ethernet <interface>
#
#############################################################################

EVENT_TYPE="$1"
DEVICE_INFO="$2"

LOG_FILE="/var/log/isle-mesh/port-events.log"
mkdir -p "$(dirname "$LOG_FILE")"

log_event() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
    logger -t isle-port-event "$1"
}

log_event "Port event: $EVENT_TYPE $DEVICE_INFO"

# Call the actual event handler
HANDLER="/usr/local/bin/isle-port-event-handler"

if [[ -x "$HANDLER" ]]; then
    "$HANDLER" "$EVENT_TYPE" "$DEVICE_INFO" &
else
    log_event "ERROR: Event handler not found: $HANDLER"
fi

exit 0
