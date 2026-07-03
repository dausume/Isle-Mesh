#!/bin/bash
#
# Isle Devices — the known-devices ledger.
#
# Every device this node sees on its isle interface(s) is recorded in
# /etc/isle-mesh/agent/known-devices.json (alongside the other isle-mesh config),
# tagged as already-on-the-mesh or not, with any pending onboarding decision.
#
# Populated by 'isle scan --save' and by the cable-plug event handler. On a
# remote node, newly-found devices are also relayed to the core so the core
# user gets prompted ("a remote found another device we can add").
#
# Usage:
#   isle devices                 List all known devices
#   isle devices pending         List devices awaiting an onboarding decision
#   isle devices onboard <mac>   Mark a device to be onboarded (decision=onboard)
#   isle devices ignore <mac>    Dismiss a device (decision=ignore)
#   isle devices forget <mac>    Remove a device from the ledger entirely
#   isle devices relay           Relay this node's pending devices to the core
#   isle devices accept <json>   (core) Absorb a device relayed by a remote
#   isle devices check           Machine-readable output (for the manager app)
#   isle devices help            Show help

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/device-store.sh
source "$SCRIPT_DIR/lib/device-store.sh"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

CHECK_MARK="${GREEN}✓${NC}"
WARNING_MARK="${YELLOW}⚠${NC}"
INFO_MARK="${BLUE}ℹ${NC}"

require_jq() { command -v jq &>/dev/null || { echo "jq is required" >&2; exit 1; }; }

# ───────────────────────────────────────────────
# Listing
# ───────────────────────────────────────────────
print_row() {
    # reads one compact device JSON object on stdin
    local d; d="$(cat)"
    local mac ip host agent status dec by services situation
    mac=$(jq -r '.mac // "?"' <<<"$d")
    ip=$(jq -r '.ip // ""' <<<"$d")
    host=$(jq -r '.hostname // ""' <<<"$d")
    agent=$(jq -r '.is_agent // false' <<<"$d")
    status=$(jq -r '.status // "?"' <<<"$d")
    dec=$(jq -r '.decision // "none"' <<<"$d")
    by=$(jq -r '.discovered_by // "?"' <<<"$d")
    services=$(jq -r '(.services // []) | join(",")' <<<"$d")
    situation=$(jq -r '.situation // "?"' <<<"$d")

    local mark="$WARNING_MARK"
    [[ "$agent" == "true" ]] && mark="$CHECK_MARK"
    echo -e "    ${mark} ${BOLD}${ip:-<no-ip>}${NC}  ${host:-<unknown>}  ${CYAN}${mac}${NC}"
    echo -e "        situation: ${situation}   status: ${status}   decision: ${dec}   found-by: ${by}"
    [[ -n "$services" && "$agent" != "true" ]] && echo -e "        remote-access ports: ${services}"
    [[ "$situation" == "installable_remote" ]] && echo -e "        → ${CYAN}isle onboard ${mac}${NC}  (guided remote install)"
    [[ "$situation" == "manual_install" || "$situation" == "firewalled" ]] && echo -e "        → ${CYAN}isle onboard ${mac}${NC}  (guided manual steps)"
}

cmd_list() {
    local filter="${1:-true}" title="${2:-All known devices}"
    require_jq
    if [[ ! -f "$DEVICE_STORE_FILE" ]]; then
        echo -e "  ${INFO_MARK} No devices recorded yet. Run: ${CYAN}isle scan --save${NC}"
        return 0
    fi
    echo ""
    echo -e "${BOLD}${BLUE}═══ ${title} ═══${NC}"
    echo -e "  ${INFO_MARK} ledger: ${DEVICE_STORE_FILE}  (this node: $(ds_node_id)/$(ds_node_role))"
    echo ""
    local any=false line
    while IFS= read -r line; do
        any=true
        print_row <<<"$line"
    done < <(ds_each "$filter")
    $any || echo -e "    ${INFO_MARK} none"
    echo ""
}

# ───────────────────────────────────────────────
# Decisions
# ───────────────────────────────────────────────
cmd_decide() {
    local mac="${1:-}" dec="${2:-}"
    require_jq
    [[ -z "$mac" ]] && { echo "usage: isle devices ${dec} <mac>" >&2; exit 1; }
    if [[ ! -f "$DEVICE_STORE_FILE" ]] || ! jq -e --arg m "${mac,,}" '.devices[$m]' "$DEVICE_STORE_FILE" >/dev/null 2>&1; then
        echo -e "  ${WARNING_MARK} No such device: ${mac}" >&2
        exit 1
    fi
    ds_set_decision "$mac" "$dec"
    echo -e "  ${CHECK_MARK} ${mac} → decision=${dec}"
    if [[ "$dec" == "onboard" ]]; then
        echo ""
        echo "  To bring it onto the mesh, run ON THAT DEVICE:"
        echo -e "      ${CYAN}sudo isle join${NC}"
        echo "  (detect-and-advise only — isle never logs into it for you)"
    fi
}

# Manually add a device the scan could NOT detect (Situation C: firewalled).
# usage: isle devices add --ip <ip> [--mac <mac>] [--hostname <name>]
cmd_add() {
    require_jq
    local ip="" mac="" host=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --ip) ip="${2:-}"; shift 2 ;;
            --mac) mac="${2:-}"; shift 2 ;;
            --hostname|--name) host="${2:-}"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done
    if [[ -z "$ip" && -z "$mac" ]]; then
        echo "usage: isle devices add --ip <ip> [--mac <mac>] [--hostname <name>]" >&2
        exit 1
    fi
    # Tag with the active discovery session if one is open.
    if [[ -f "$SCRIPT_DIR/lib/discovery-mode.sh" ]]; then
        source "$SCRIPT_DIR/lib/discovery-mode.sh"
        export DS_SESSION_ID="$(dm_session_id 2>/dev/null)"
    fi
    ds_add_manual "$ip" "$mac" "$host"
    echo -e "  ${CHECK_MARK} Added ${ip:-$mac} as a manual (firewalled) device — could not auto-detect it."
    echo -e "  ${INFO_MARK} It will need a manual install. See: ${CYAN}isle onboard ${mac:-manual:$ip}${NC}"
}

cmd_forget() {
    local mac="${1:-}"
    require_jq
    [[ -z "$mac" ]] && { echo "usage: isle devices forget <mac>" >&2; exit 1; }
    ds_init
    local tmp; tmp="$(mktemp)"
    jq --arg m "${mac,,}" --arg now "$(ds_now)" \
        '.updated_at=$now | del(.devices[$m])' "$DEVICE_STORE_FILE" > "$tmp" \
        && cp "$tmp" "$DEVICE_STORE_FILE" && rm -f "$tmp"
    echo -e "  ${CHECK_MARK} Forgot ${mac}"
}

# ───────────────────────────────────────────────
# Relay (remote → core)
# ───────────────────────────────────────────────
# Each unrelayed, non-agent, awaiting device is pushed to the core. Transport is
# delegated to relay_device_to_core() in the relay lib (chosen to match the
# existing node↔core sync path); if unavailable we just mark intent and leave the
# entry for the core to pull. Safe to run repeatedly (idempotent via relayed flag).
cmd_relay() {
    require_jq
    if [[ "$(ds_node_role)" == "core" ]]; then
        echo -e "  ${INFO_MARK} This is the core node — nothing to relay (it is the destination)."
        return 0
    fi
    local relay_lib="$SCRIPT_DIR/lib/device-relay.sh"
    [[ -f "$relay_lib" ]] && source "$relay_lib"
    local line mac sent=0
    while IFS= read -r line; do
        mac=$(jq -r '.mac' <<<"$line")
        if type relay_device_to_core &>/dev/null; then
            if relay_device_to_core "$line"; then
                ds_mark_relayed "$mac"; sent=$((sent+1))
            else
                echo -e "  ${WARNING_MARK} relay failed for ${mac} (will retry next run)" >&2
            fi
        else
            echo -e "  ${WARNING_MARK} no relay transport available; ${mac} left for core to pull" >&2
        fi
    done < <(ds_each '.is_agent == false and .relayed_to_core != true and .decision == "await"')
    echo -e "  ${CHECK_MARK} relayed ${sent} device(s) to core"
}

# Core-side: absorb a device object emitted by a relaying remote (stdin or arg).
cmd_accept() {
    require_jq
    local json="${1:-}"
    if [[ -z "$json" ]]; then json="$(cat)"; fi
    [[ -z "$json" ]] && { echo "usage: isle devices accept '<device-json>'" >&2; exit 1; }
    ds_merge_device <<<"$json"
    local mac; mac=$(jq -r '.mac // "?"' <<<"$json")
    # Reply begins with OK so a relaying remote can confirm delivery over the wire.
    echo "OK accepted ${mac}"
}

# Core-side receiver: a tiny TCP listener that pipes each relayed JSON line into
# 'accept'. Run on the core (optionally as a service). Requires socat.
cmd_serve_relay() {
    require_jq
    local port="${1:-${ISLE_RELAY_PORT:-7879}}"
    if ! command -v socat &>/dev/null; then
        echo "socat is required for 'serve-relay' (sudo apt-get install socat)" >&2
        exit 1
    fi
    echo -e "  ${INFO_MARK} Listening for device relays on tcp/${port} (Ctrl-C to stop)"
    echo -e "  ${INFO_MARK} ledger: ${DEVICE_STORE_FILE}"
    exec socat "TCP-LISTEN:${port},reuseaddr,fork" "EXEC:bash ${SCRIPT_DIR}/devices.sh accept"
}

# ───────────────────────────────────────────────
# Machine-readable (manager app)
# ───────────────────────────────────────────────
cmd_check() {
    [[ -f "$DEVICE_STORE_FILE" ]] || { echo "devices=0 pending=0"; return 0; }
    jq -r '
        .devices | to_entries[] |
        "device mac=\(.value.mac) ip=\(.value.ip // "") agent=\(.value.is_agent) " +
        "situation=\(.value.situation // "?") status=\(.value.status) decision=\(.value.decision) " +
        "found_by=\(.value.discovered_by // "") session=\(.value.session_id // "") " +
        "services=\((.value.services // []) | join(","))"
    ' "$DEVICE_STORE_FILE" 2>/dev/null
    local total pending
    total=$(jq -r '.devices | length' "$DEVICE_STORE_FILE" 2>/dev/null || echo 0)
    pending=$(jq -r '[.devices[] | select(.is_agent==false and .decision=="await")] | length' "$DEVICE_STORE_FILE" 2>/dev/null || echo 0)
    echo "summary devices=${total} pending=${pending}"
}

show_help() {
    cat <<EOF

$(echo -e "${BOLD}Isle Devices — known-devices ledger${NC}")

Tracks every device seen on your isle interface(s): which are already on the
mesh (isle-mesh agent present) and which are candidates to onboard, plus the
decision pending/made for each. Stored alongside your isle-mesh config at:
  ${DEVICE_STORE_FILE}

Usage:
  isle devices                 List all known devices
  isle devices pending         List devices awaiting an onboarding decision
  isle devices onboard <mac>   Mark a device to be onboarded
  isle devices ignore <mac>    Dismiss a device
  isle devices add --ip <ip>   Manually add a firewalled/undetectable device
  isle devices forget <mac>    Remove a device from the ledger
  isle devices relay           (remote) Relay pending devices to the core node
  isle devices serve-relay     (core)   Listen for device relays from remotes
  isle devices accept <json>   (core)   Absorb a device relayed by a remote
  isle devices check           Machine-readable output
  isle devices help            This help

Populate it with:  isle scan --save
EOF
}

# ───────────────────────────────────────────────
COMMAND="${1:-list}"
shift || true

case "$COMMAND" in
    list|"")     cmd_list "true" "All known devices" ;;
    pending)     cmd_list '.is_agent == false and .decision == "await"' "Devices awaiting a decision" ;;
    onboarded)   cmd_list '.is_agent == true' "Onboarded devices (on the mesh)" ;;
    pending-macs)
        # Bare MAC list of devices awaiting a decision, optionally for one iface.
        require_jq
        if [[ -n "${1:-}" ]]; then
            ds_each ".is_agent==false and .decision==\"await\" and .discovered_on==\"$1\"" | jq -r '.mac'
        else
            ds_each '.is_agent==false and .decision=="await"' | jq -r '.mac'
        fi
        ;;
    onboard)     cmd_decide "${1:-}" "onboard" ;;
    ignore)      cmd_decide "${1:-}" "ignore" ;;
    add)         cmd_add "$@" ;;
    forget)      cmd_forget "${1:-}" ;;
    relay)       cmd_relay ;;
    accept)      cmd_accept "${1:-}" ;;
    serve-relay) cmd_serve_relay "${1:-}" ;;
    check)       cmd_check ;;
    help|-h|--help) show_help ;;
    *)           echo "Unknown command: $COMMAND (try 'isle devices help')" >&2; exit 1 ;;
esac
