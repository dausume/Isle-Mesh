#!/bin/bash

#############################################################################
# Isle-Mesh Port Event Handler (Event-Driven)
#
# This script is called by udev rules when USB or Ethernet events occur.
# It shows YAD dialogs to prompt the user for configuration.
#
# NO CONTINUOUS POLLING - Only runs when hardware events occur!
#
# Usage (called by udev):
#   port-event-handler usb <device_path>
#   port-event-handler ethernet <interface>
#
#############################################################################

set -e

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Configuration
STATE_DIR="/var/lib/isle-mesh"
RESERVED_PORTS_FILE="$STATE_DIR/reserved-ports.conf"
ISP_INTERFACE_FILE="$STATE_DIR/isp-interface.conf"
LOG_FILE="/var/log/isle-mesh/port-events.log"

# Ensure directories exist
mkdir -p "$STATE_DIR"
mkdir -p "$(dirname "$LOG_FILE")"
touch "$RESERVED_PORTS_FILE"

# Log functions
log_event() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    echo "$msg" >> "$LOG_FILE"
    logger -t isle-port-event "$1"
    echo -e "${BLUE}[EVENT]${NC} $1" >&2
}

log_success() {
    echo "$1" >> "$LOG_FILE"
    echo -e "${GREEN}[✓]${NC} $1" >&2
}

log_error() {
    echo "$1" >> "$LOG_FILE"
    echo -e "${RED}[✗]${NC} $1" >&2
}

# Check if YAD is available
check_yad() {
    if ! command -v yad &> /dev/null; then
        log_error "YAD not installed, cannot show dialog"
        return 1
    fi
    return 0
}

# Get ISP interface
get_isp_interface() {
    if [[ -f "$ISP_INTERFACE_FILE" ]]; then
        cat "$ISP_INTERFACE_FILE"
    else
        ip route | grep '^default' | awk '{print $5}' | head -n1
    fi
}

# Resolve the installed isle CLI (global, or fall back to /usr/local/bin).
isle_cli() {
    if command -v isle &>/dev/null; then
        isle "$@"
    elif [[ -x /usr/local/bin/isle ]]; then
        /usr/local/bin/isle "$@"
    else
        log_error "isle CLI not found; cannot run: isle $*"
        return 127
    fi
}

# This node's role (core or remote), per agent.mode.
node_role() { cat /etc/isle-mesh/agent/agent.mode 2>/dev/null | tr -d '[:space:]'; }

# Best-effort desktop notification; always logs (the ledger is the real surface).
notify_user() {
    local title="$1" body="$2"
    if command -v notify-send &>/dev/null; then
        DISPLAY="${DISPLAY:-:0}" notify-send -i network-wired "$title" "$body" 2>/dev/null || true
    fi
    log_event "$title — $body"
}

# Check if port is already reserved
is_port_reserved() {
    local port_id="$1"
    grep -q "^$port_id$" "$RESERVED_PORTS_FILE" 2>/dev/null
}

# Check if port is ignored
is_port_ignored() {
    local port_id="$1"
    grep -q "^IGNORED:$port_id$" "$RESERVED_PORTS_FILE" 2>/dev/null
}

# Reserve port
reserve_port() {
    local port_id="$1"
    echo "$port_id" >> "$RESERVED_PORTS_FILE"
    log_success "Reserved port: $port_id"
}

# Ignore port forever
ignore_port_forever() {
    local port_id="$1"
    echo "IGNORED:$port_id" >> "$RESERVED_PORTS_FILE"
    log_success "Ignored port forever: $port_id"
}

# Handle USB WiFi adapter
handle_usb_wifi() {
    local device_path="$1"

    log_event "USB WiFi adapter detected: $device_path"

    # Get device info
    local description=$(lsusb | grep -i "$(basename $device_path)" | cut -d':' -f3- || echo "Unknown USB WiFi Adapter")
    local full_id="USB:$device_path"

    # Check if already handled
    if is_port_reserved "$full_id"; then
        log_event "Port already reserved: $full_id"
        return 0
    fi

    if is_port_ignored "$full_id"; then
        log_event "Port is ignored: $full_id"
        return 0
    fi

    # Check YAD
    if ! check_yad; then
        return 1
    fi

    # Show YAD dialog
    local response=$(DISPLAY=:0 yad --form \
        --title="Isle-Mesh: New USB WiFi Adapter Detected" \
        --image=network-wireless \
        --width=600 \
        --height=400 \
        --text="<b>New USB WiFi Adapter Detected</b>\n\n<tt>Device: $device_path\nDescription: $description</tt>\n\nWould you like to use this adapter for Isle-Mesh?" \
        --field="Use for Isle-Mesh:CHK" TRUE \
        --field="Isle Name:" "isle1" \
        --field="vLAN ID:" "10" \
        --field="WiFi SSID:" "Isle1-WiFi" \
        --field="WiFi Password::H" "" \
        --field="Purpose (optional):" "" \
        --button="Reserve for Isle-Mesh:0" \
        --button="Ignore:1" \
        --button="Ignore Forever:2" \
        2>/dev/null)

    local exit_code=$?

    case $exit_code in
        0)  # Reserve
            local use_for_isle=$(echo "$response" | cut -d'|' -f1)
            if [[ "$use_for_isle" == "TRUE" ]]; then
                local isle_name=$(echo "$response" | cut -d'|' -f2)
                local vlan_id=$(echo "$response" | cut -d'|' -f3)
                local ssid=$(echo "$response" | cut -d'|' -f4)
                local password=$(echo "$response" | cut -d'|' -f5)

                log_event "User reserved USB WiFi: $full_id for $isle_name"
                reserve_port "$full_id"

                # TODO: Call isle-cli to configure
                log_event "Would configure: isle=$isle_name vlan=$vlan_id ssid=$ssid"

                DISPLAY=:0 yad --info --title="Success" \
                    --text="USB WiFi adapter reserved!\n\nPort: $device_path\nIsle: $isle_name\nSSID: $ssid" \
                    --button=gtk-ok:0 \
                    --timeout=5 \
                    2>/dev/null &
            fi
            ;;
        1)  # Ignore this time
            log_event "User ignored USB WiFi: $full_id"
            ;;
        2)  # Ignore forever
            log_event "User chose to ignore USB WiFi forever: $full_id"
            ignore_port_forever "$full_id"
            ;;
    esac
}

# Handle Ethernet interface: a cable was plugged in. Scan that segment for
# devices, record them in the known-devices ledger, relay finds to the core (if
# we are a remote), and prompt the user to onboard any device that isn't yet on
# the mesh. Works identically on the core host and on remote nodes.
handle_ethernet() {
    local iface="$1"

    log_event "Ethernet interface event: $iface"

    local isp_iface=$(get_isp_interface)

    # Never touch the ISP uplink.
    if [[ "$iface" == "$isp_iface" ]]; then
        log_event "Skipping ISP interface: $iface"
        return 0
    fi

    # Skip virtual / isle-internal interfaces.
    if [[ "$iface" =~ ^(br-|veth|docker|virbr|isle-) ]]; then
        log_event "Skipping virtual interface: $iface"
        return 0
    fi

    local full_id="ETH:$iface"
    if is_port_ignored "$full_id"; then
        log_event "Port is ignored: $full_id"
        return 0
    fi

    # Require link.
    local state=$(cat /sys/class/net/$iface/operstate 2>/dev/null || echo "unknown")
    if [[ "$state" != "up" && "$state" != "unknown" ]]; then
        log_event "Interface $iface not up (state: $state), skipping"
        return 0
    fi

    # Only act while the operator has discovery mode ON. Otherwise stay quiet —
    # this is the gate that keeps detection from running on every cable event.
    if ! isle_cli discovery active >/dev/null 2>&1; then
        log_event "Discovery mode is OFF — ignoring $iface (run 'isle discovery start' to add nodes)"
        return 0
    fi

    # Give DHCP / ARP a moment to settle so the scan can see the other end.
    sleep 3

    local role; role=$(node_role); [[ -z "$role" ]] && role="unknown"
    log_event "Scanning $iface for devices (this node role: $role)"
    isle_cli scan --interface "$iface" --save >/dev/null 2>&1 \
        || log_error "device scan failed on $iface"

    # Devices on this interface that are NOT on the mesh and await a decision.
    local pending_macs; pending_macs=$(isle_cli devices pending-macs "$iface" 2>/dev/null)
    local count; count=$(printf '%s\n' "$pending_macs" | grep -c . || true)

    if [[ "${count:-0}" -eq 0 ]]; then
        log_event "No new un-onboarded devices found on $iface"
        return 0
    fi
    log_success "Found $count un-onboarded device(s) on $iface"

    # Remote nodes relay the find back to the core so its user is prompted too
    # ("a remote found another device we can add"). Idempotent; queues on failure.
    if [[ "$role" == "remote" ]]; then
        if isle_cli devices relay >/dev/null 2>&1; then
            log_event "Relayed pending device(s) to core"
        else
            log_event "Relay to core deferred (queued in outbox for retry)"
        fi
    fi

    # Prompt the user. GUI when available; otherwise the ledger itself is the
    # notification surface ('isle devices pending' / manager app), so headless
    # nodes never block — the decision simply stays awaiting.
    if check_yad; then
        local response exit_code
        response=$(DISPLAY=:0 yad --form \
            --title="Isle-Mesh: New device(s) on $iface" \
            --image=network-wired --width=560 --height=240 \
            --text="<b>$count device(s) detected on $iface that are not on the mesh.</b>\n\nAdd them to isle-mesh?\n\n<tt>$(printf '%s\n' "$pending_macs" | head -8)</tt>" \
            --button="Onboard:0" --button="Ignore:1" --button="Decide later:2" \
            2>/dev/null)
        exit_code=$?
        case $exit_code in
            0)  # Onboard
                while read -r m; do
                    [[ -z "$m" ]] && continue
                    isle_cli devices onboard "$m" >/dev/null 2>&1 || true
                done <<<"$pending_macs"
                log_success "User chose to onboard $count device(s) on $iface"
                notify_user "Isle-Mesh" "Marked $count device(s) to onboard. Run 'sudo isle join' on each to finish."
                ;;
            1)  # Ignore
                while read -r m; do
                    [[ -z "$m" ]] && continue
                    isle_cli devices ignore "$m" >/dev/null 2>&1 || true
                done <<<"$pending_macs"
                log_event "User ignored device(s) on $iface"
                ;;
            *)  # Decide later — leave awaiting in the ledger
                log_event "User deferred decision on $iface ($count device(s) left awaiting)"
                ;;
        esac
    else
        notify_user "Isle-Mesh: $count new device(s) on $iface" \
            "Not on the mesh. Review with 'isle devices pending'. Saved in known-devices.json."
    fi
}

# Main execution
EVENT_TYPE="$1"
DEVICE_INFO="$2"

log_event "Event triggered: $EVENT_TYPE $DEVICE_INFO"

case "$EVENT_TYPE" in
    usb)
        handle_usb_wifi "$DEVICE_INFO"
        ;;
    ethernet)
        handle_ethernet "$DEVICE_INFO"
        ;;
    *)
        log_error "Unknown event type: $EVENT_TYPE"
        exit 1
        ;;
esac

exit 0
