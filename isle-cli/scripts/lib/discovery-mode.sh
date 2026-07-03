#!/bin/bash
#
# Isle discovery mode — a deliberate, session-scoped "I am adding nodes now" state.
#
# Detection/onboarding only acts while discovery mode is ON. This gates the
# always-listening behaviour: the cable-plug handler and relay ingest do nothing
# unless the host operator has explicitly opened a discovery session. Each
# session has an id so devices found during it can be tracked together.
#
# State: /etc/isle-mesh/agent/discovery-mode.json
#   { "active": bool, "session_id": "disc-<epoch>", "started_at": "<iso>",
#     "expires_epoch": <int 0=never>, "started_by": "<node>", "added_macs": [] }
#
# Sourced by discovery.sh, scan.sh, devices.sh. Requires jq.

DISCOVERY_FILE="${DISCOVERY_FILE:-/etc/isle-mesh/agent/discovery-mode.json}"

dm_now_iso()   { date -Iseconds 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ; }
dm_now_epoch() { date +%s; }
dm_node_id()   {
    if [[ -s /etc/isle-mesh/agent/remote/hostname ]]; then
        tr -d '[:space:]' < /etc/isle-mesh/agent/remote/hostname
    else hostname -s 2>/dev/null || hostname; fi
}

_dm_write() { local tmp="$1"; cp "$tmp" "$DISCOVERY_FILE" && rm -f "$tmp"; }

# True (0) iff discovery mode is active and not expired. Auto-stops on expiry.
dm_active() {
    [[ -f "$DISCOVERY_FILE" ]] || return 1
    [[ "$(jq -r '.active // false' "$DISCOVERY_FILE" 2>/dev/null)" == "true" ]] || return 1
    local exp now
    exp=$(jq -r '.expires_epoch // 0' "$DISCOVERY_FILE" 2>/dev/null)
    now=$(dm_now_epoch)
    if [[ "${exp:-0}" -gt 0 && "$now" -ge "$exp" ]]; then
        dm_stop "expired" >/dev/null 2>&1
        return 1
    fi
    return 0
}

# Echo the active session id (empty if inactive).
dm_session_id() { dm_active && jq -r '.session_id // empty' "$DISCOVERY_FILE" 2>/dev/null; }

# dm_start [timeout_seconds]   (0 = no expiry)
dm_start() {
    local timeout="${1:-0}"
    mkdir -p "$(dirname "$DISCOVERY_FILE")" 2>/dev/null
    local now sid exp
    now=$(dm_now_epoch); sid="disc-${now}"; exp=0
    [[ "${timeout:-0}" -gt 0 ]] && exp=$((now + timeout))
    local tmp; tmp="$(mktemp)"
    jq -n --arg sid "$sid" --arg started "$(dm_now_iso)" \
          --argjson exp "$exp" --arg by "$(dm_node_id)" \
        '{active:true, session_id:$sid, started_at:$started, expires_epoch:$exp,
          started_by:$by, added_macs:[]}' > "$tmp" && _dm_write "$tmp"
    echo "$sid"
}

# dm_stop [reason]
dm_stop() {
    [[ -f "$DISCOVERY_FILE" ]] || { echo '{"active":false}' > "$DISCOVERY_FILE" 2>/dev/null; return 0; }
    local tmp; tmp="$(mktemp)"
    jq --arg reason "${1:-stopped}" --arg at "$(dm_now_iso)" \
        '.active=false | .stopped_at=$at | .stop_reason=$reason' \
        "$DISCOVERY_FILE" > "$tmp" && _dm_write "$tmp"
}

# Record a MAC as added/seen during the current session (dedup).
dm_record() {
    local mac="${1,,}"
    [[ -z "$mac" ]] && return 0
    [[ -f "$DISCOVERY_FILE" ]] || return 0
    local tmp; tmp="$(mktemp)"
    jq --arg m "$mac" '.added_macs = ((.added_macs // []) + [$m] | unique)' \
        "$DISCOVERY_FILE" > "$tmp" && _dm_write "$tmp"
}

# Human/seconds-remaining helper.
dm_remaining() {
    [[ -f "$DISCOVERY_FILE" ]] || { echo 0; return; }
    local exp now; exp=$(jq -r '.expires_epoch // 0' "$DISCOVERY_FILE" 2>/dev/null); now=$(dm_now_epoch)
    [[ "${exp:-0}" -le 0 ]] && { echo -1; return; }   # -1 = no expiry
    local rem=$((exp - now)); [[ $rem -lt 0 ]] && rem=0
    echo "$rem"
}
