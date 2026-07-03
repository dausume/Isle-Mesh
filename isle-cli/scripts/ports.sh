#!/bin/bash
#
# Isle Ports — see physical ethernet cables and switch them onto/off the isle.
#
# This is the ENGINE for the manager app's "Network Ports" screen. End users are
# not expected to run it; the app calls it (list = unprivileged, attach/detach via
# pkexec). Switching a cable "onto the isle" means enslaving that physical port to
# the isle bridge (isle-br-0) so a device plugged into it joins the isle L2 and
# gets an address from the router.
#
# Usage:
#   isle ports list            Machine-readable: one line per ethernet port
#   isle ports attach <iface>  Put the port on the isle (enslave to isle bridge)
#   isle ports detach <iface>  Take the port off the isle
#   isle ports help
#
set -uo pipefail

ISLE_BRIDGE_CANONICAL="isle-br-0"

# The isle bridge the router VM lives on (prefer the canonical name).
detect_isle_bridge() {
    if ip link show "$ISLE_BRIDGE_CANONICAL" &>/dev/null; then
        echo "$ISLE_BRIDGE_CANONICAL"; return 0
    fi
    ip -br link show type bridge 2>/dev/null | awk '{print $1}' \
        | grep -iE '^(isle-br|br-)' | grep -viE 'br-mgmt|docker|^br-[0-9a-f]{12}$' | head -1
}

# The internet/normal-network uplink — must NEVER be bridged into the isle.
uplink_iface() { ip route show default 2>/dev/null | awk '{print $5}' | head -1; }

# True for a real wired NIC (not virtual/wireless/bridge).
is_physical_eth() {
    local i="$1"
    [[ -e "/sys/class/net/$i" ]] || return 1
    case "$i" in lo|docker*|virbr*|veth*|br-*|isle-br-*|wl*|tap*|tun*) return 1 ;; esac
    [[ -d "/sys/class/net/$i/wireless" ]] && return 1
    return 0
}

list_ports() {
    local bridge uplink
    bridge="$(detect_isle_bridge)"
    uplink="$(uplink_iface)"
    local i
    for i in $(ls /sys/class/net 2>/dev/null | sort); do
        is_physical_eth "$i" || continue
        local carrier state ip master on_isle is_up
        carrier=$(cat "/sys/class/net/$i/carrier" 2>/dev/null || echo 0)
        state=$(cat "/sys/class/net/$i/operstate" 2>/dev/null || echo unknown)
        ip=$(ip -4 -br addr show "$i" 2>/dev/null | awk '{print $3}' | head -1)
        master=$(basename "$(readlink "/sys/class/net/$i/master" 2>/dev/null)" 2>/dev/null)
        [[ -n "$bridge" && "$master" == "$bridge" ]] && on_isle=true || on_isle=false
        [[ "$i" == "$uplink" ]] && is_up=true || is_up=false
        echo "port name=$i carrier=$carrier state=$state ip=${ip:-none} on_isle=$on_isle uplink=$is_up master=${master:-none}"
    done
    echo "summary isle_bridge=${bridge:-none} uplink=${uplink:-none}"
}

attach_port() {
    local iface="${1:-}"
    [[ -z "$iface" ]] && { echo "usage: isle ports attach <iface>" >&2; exit 1; }
    is_physical_eth "$iface" || { echo "Not a physical ethernet port: $iface" >&2; exit 1; }

    # SAFETY: never bridge the internet/normal-network uplink into the isle —
    # that would join the isle to your real LAN and destroy isolation.
    if [[ "$iface" == "$(uplink_iface)" ]]; then
        echo "REFUSED: $iface is your internet/normal-network uplink." >&2
        echo "Bridging it into the isle would break isolation, so it is protected." >&2
        exit 1
    fi

    local bridge; bridge="$(detect_isle_bridge)"
    if [[ -z "$bridge" ]]; then
        echo "No isle bridge found — start the isle first (Isle Create), then try again." >&2
        exit 1
    fi

    # Drop any address (the isle router hands out addressing) and enslave.
    ip addr flush dev "$iface" 2>/dev/null || true
    ip link set "$iface" up 2>/dev/null || true
    if ip link set "$iface" master "$bridge" 2>/dev/null; then
        echo "OK attached $iface to the isle ($bridge)"
    else
        echo "Failed to attach $iface (need root?)" >&2; exit 1
    fi
}

detach_port() {
    local iface="${1:-}"
    [[ -z "$iface" ]] && { echo "usage: isle ports detach <iface>" >&2; exit 1; }
    if ip link set "$iface" nomaster 2>/dev/null; then
        echo "OK detached $iface from the isle"
    else
        echo "Failed to detach $iface (need root?)" >&2; exit 1
    fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    CMD="${1:-list}"; shift || true
    case "$CMD" in
        list|check) list_ports ;;
        attach)     attach_port "${1:-}" ;;
        detach)     detach_port "${1:-}" ;;
        help|-h|--help) sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
        *) echo "Unknown command: $CMD (try 'isle ports help')" >&2; exit 1 ;;
    esac
fi
