#!/bin/bash
#
# Isle Discovery Mode — turn node-discovery on/off at the host.
#
# Detection and onboarding only happen while discovery mode is ON. Open a session
# when you are adding devices ("I'm plugging in a new node now"), and the cable-
# plug handler + relay ingest will act and track everything found in that session.
# Closing the session (or its timeout) returns the host to quiet/ignore.
#
# Usage:
#   isle discovery start [--timeout <minutes>]   Open a session (default 30m; 0 = no expiry)
#   isle discovery stop                          Close the session
#   isle discovery status                        Human-readable state + session devices
#   isle discovery active                        Exit 0 if active, 1 if not (for scripts)
#   isle discovery check                         Machine-readable state (manager app)
#   isle discovery help

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/discovery-mode.sh
source "$SCRIPT_DIR/lib/discovery-mode.sh"
# device store is optional here (used to list session devices)
[[ -f "$SCRIPT_DIR/lib/device-store.sh" ]] && source "$SCRIPT_DIR/lib/device-store.sh"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
CHECK="${GREEN}✓${NC}"; WARN="${YELLOW}⚠${NC}"; INFO="${BLUE}ℹ${NC}"

DEFAULT_TIMEOUT_MIN=30

require_jq() { command -v jq &>/dev/null || { echo "jq is required" >&2; exit 1; }; }

cmd_start() {
    require_jq
    local minutes="$DEFAULT_TIMEOUT_MIN"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --timeout) minutes="${2:-$DEFAULT_TIMEOUT_MIN}"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done
    local secs=$(( minutes * 60 ))
    local sid; sid="$(dm_start "$secs")"
    echo -e "  ${CHECK} Discovery mode ${BOLD}ON${NC}  (session ${sid})"
    if [[ "$minutes" -gt 0 ]]; then
        echo -e "  ${INFO} Auto-stops in ${minutes} min. New nodes detected on plug-in will be tracked."
    else
        echo -e "  ${INFO} No timeout — remember to run ${CYAN}isle discovery stop${NC} when done."
    fi
    echo ""
    echo -e "  Plug in a device, or run: ${CYAN}isle scan --save${NC}"
}

cmd_stop() {
    require_jq
    dm_stop "user"
    echo -e "  ${CHECK} Discovery mode ${BOLD}OFF${NC}"
}

cmd_status() {
    require_jq
    if dm_active; then
        local sid rem
        sid=$(jq -r '.session_id' "$DISCOVERY_FILE")
        rem=$(dm_remaining)
        echo -e "  ${CHECK} Discovery mode: ${GREEN}${BOLD}ON${NC}   session ${sid}"
        if [[ "$rem" -lt 0 ]]; then echo -e "  ${INFO} No timeout set"
        else echo -e "  ${INFO} Auto-stops in $(( rem / 60 ))m $(( rem % 60 ))s"; fi
    else
        echo -e "  ${WARN} Discovery mode: ${YELLOW}${BOLD}OFF${NC}"
        echo -e "  ${INFO} Start with: ${CYAN}isle discovery start${NC}"
        return 0
    fi
    # Devices recorded during this session
    if [[ -n "${DEVICE_STORE_FILE:-}" && -f "${DEVICE_STORE_FILE:-/nonexistent}" ]] && type ds_each &>/dev/null; then
        local sid; sid=$(jq -r '.session_id' "$DISCOVERY_FILE")
        echo ""
        echo -e "${CYAN}═══ Devices in this session ═══${NC}"
        local any=false line
        while IFS= read -r line; do
            any=true
            echo -e "    • $(jq -r '"\(.ip // "?")  \(.mac)  [\(.situation // "?")]"' <<<"$line")"
        done < <(ds_each ".session_id == \"$sid\"")
        $any || echo "    (none yet)"
    fi
}

cmd_check() {
    require_jq
    if dm_active; then
        jq -r '"discovery active=true session=\(.session_id) expires_epoch=\(.expires_epoch // 0)"' "$DISCOVERY_FILE"
    else
        echo "discovery active=false"
    fi
}

show_help() {
    cat <<EOF

$(echo -e "${BOLD}Isle Discovery Mode${NC}")

Detection/onboarding only runs while discovery mode is ON. Open a session when
adding nodes; everything found during it is tracked together.

Usage:
  isle discovery start [--timeout <minutes>]   Open a session (default ${DEFAULT_TIMEOUT_MIN}m; 0 = no expiry)
  isle discovery stop                          Close the session
  isle discovery status                        State + devices found this session
  isle discovery active                        Exit 0 if active (for scripts)
  isle discovery check                         Machine-readable state
  isle discovery help

State file: ${DISCOVERY_FILE}
EOF
}

CMD="${1:-status}"; shift || true
case "$CMD" in
    start)  cmd_start "$@" ;;
    stop)   cmd_stop ;;
    status) cmd_status ;;
    active) dm_active ;;       # exit code only
    check)  cmd_check ;;
    help|-h|--help) show_help ;;
    *) echo "Unknown command: $CMD (try 'isle discovery help')" >&2; exit 1 ;;
esac
