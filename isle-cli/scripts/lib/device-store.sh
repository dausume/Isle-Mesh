#!/bin/bash
#
# Isle device store — shared read/write helpers for the known-devices file.
#
# This file tracks every device this node has seen on its isle interface(s),
# whether it is already part of the mesh (isle-mesh agent present) or not, and
# what decision (if any) is pending/made about onboarding it.
#
# It lives alongside the other isle-mesh config files so every node (core OR
# remote) keeps the same shaped record:
#
#   /etc/isle-mesh/agent/known-devices.json
#
# Schema:
#   {
#     "version": 1,
#     "node":    { "id": "<node-id>", "role": "core|remote" },
#     "updated_at": "<iso8601>",
#     "devices": {
#       "<mac>": {
#         "mac": "...", "ip": "...", "hostname": "...",
#         "is_agent": true|false,            # already running isle-mesh?
#         "status":   "onboarded|pending|onboarding|ignored",
#         "decision": "none|await|onboard|ignore",
#         "services": ["22","445"],          # reachable remote-access ports
#         "discovered_by": "<node-id>",      # which node first saw it
#         "discovered_on": "<interface>",
#         "relayed_to_core": true|false,     # remote→core relay done?
#         "first_seen": "<iso8601>", "last_seen": "<iso8601>"
#       }
#     }
#   }
#
# Sourced by scan.sh and devices.sh. Requires jq.

DEVICE_STORE_FILE="${DEVICE_STORE_FILE:-/etc/isle-mesh/agent/known-devices.json}"

ds_now() { date -Iseconds 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ; }

ds_node_role() {
    cat /etc/isle-mesh/agent/agent.mode 2>/dev/null | tr -d '[:space:]' || echo "unknown"
}

ds_node_id() {
    # Remotes save a sanitized hostname during 'isle join'; prefer it for stable id.
    if [[ -s /etc/isle-mesh/agent/remote/hostname ]]; then
        tr -d '[:space:]' < /etc/isle-mesh/agent/remote/hostname
    else
        hostname -s 2>/dev/null || hostname
    fi
}

ds_init() {
    [[ -f "$DEVICE_STORE_FILE" ]] && return 0
    mkdir -p "$(dirname "$DEVICE_STORE_FILE")" 2>/dev/null
    jq -n --arg id "$(ds_node_id)" --arg role "$(ds_node_role)" --arg t "$(ds_now)" \
        '{version:1, node:{id:$id, role:$role}, updated_at:$t, devices:{}}' \
        > "$DEVICE_STORE_FILE"
}

# Atomic write that preserves the inode (Docker bind-mount safe — never mv).
_ds_write() {
    local tmp="$1"
    cp "$tmp" "$DEVICE_STORE_FILE" && rm -f "$tmp"
}

# Derived "situation" drives which guided walkthrough the app shows:
#   onboarded          — already on the mesh (is_agent)
#   installable_remote — not on mesh, reachable, SSH(22) open  → guided remote install
#   manual_install     — not on mesh, reachable, no SSH        → guided manual install
#   firewalled         — could NOT be auto-detected (manually added by the user)
#
# ds_upsert <mac> <ip> <hostname> <is_agent> <services-space-sep> <discovered_by> <iface> [detected:true|false]
# Honors $DS_SESSION_ID (discovery session) when set. Preserves first_seen + user decision.
ds_upsert() {
    local mac="${1,,}" ip="${2:-}" host="${3:-}" is_agent="${4:-false}"
    local services="${5:-}" by="${6:-$(ds_node_id)}" iface="${7:-}" detected="${8:-true}"
    [[ -z "$mac" ]] && return 1
    ds_init
    local now; now="$(ds_now)"
    local session="${DS_SESSION_ID:-}"
    local tmp; tmp="$(mktemp)"
    jq \
        --arg mac "$mac" --arg ip "$ip" --arg host "$host" \
        --argjson agent "$is_agent" --arg services "$services" \
        --arg by "$by" --arg iface "$iface" --argjson detected "$detected" \
        --arg now "$now" --arg session "$session" '
        ($services | if . == "" then [] else (. / " ") end) as $svc
        | (if $agent then "onboarded"
           elif ($detected | not) then "firewalled"
           elif ($svc | index("22")) then "installable_remote"
           else "manual_install" end) as $situation
        | .updated_at = $now
        | .devices[$mac] = (
            (.devices[$mac] // {first_seen:$now, decision:"await", status:"pending", relayed_to_core:false})
            + {
                mac:$mac, ip:$ip, hostname:$host, is_agent:$agent,
                detected:$detected, services:$svc, situation:$situation,
                discovered_by: (.devices[$mac].discovered_by // $by),
                discovered_on:$iface, last_seen:$now
              }
            | .first_seen = (.first_seen // $now)
            | (if ($session != "") then .session_id = $session else . end)
            | if $agent then (.status = "onboarded" | .decision = "none")
              else (.status = (if (.decision == "ignore") then "ignored"
                               elif (.decision == "onboard") then "onboarding"
                               else "pending" end))
              end
          )
    ' "$DEVICE_STORE_FILE" > "$tmp" && _ds_write "$tmp"
}

# ds_add_manual <ip> [mac] [hostname] — user-selected device we could NOT detect
# (Situation C, firewalled). Synthesizes a stable mac key if none given.
ds_add_manual() {
    local ip="${1:-}" mac="${2:-}" host="${3:-}"
    [[ -z "$ip" && -z "$mac" ]] && return 1
    [[ -z "$mac" ]] && mac="manual:${ip}"
    ds_upsert "$mac" "$ip" "$host" "false" "" "$(ds_node_id)" "manual" "false"
}

# ds_set_decision <mac> <none|await|onboard|ignore>
ds_set_decision() {
    local mac="${1,,}" dec="${2:-await}"
    ds_init
    local tmp; tmp="$(mktemp)"
    jq --arg mac "$mac" --arg dec "$dec" --arg now "$(ds_now)" '
        .updated_at = $now
        | if .devices[$mac] then
            .devices[$mac].decision = $dec
            | .devices[$mac].status = (
                if $dec == "ignore" then "ignored"
                elif $dec == "onboard" then "onboarding"
                elif $dec == "await" then "pending"
                else .devices[$mac].status end)
          else . end
    ' "$DEVICE_STORE_FILE" > "$tmp" && _ds_write "$tmp"
}

# ds_mark_relayed <mac>
ds_mark_relayed() {
    local mac="${1,,}"
    ds_init
    local tmp; tmp="$(mktemp)"
    jq --arg mac "$mac" '
        if .devices[$mac] then .devices[$mac].relayed_to_core = true else . end
    ' "$DEVICE_STORE_FILE" > "$tmp" && _ds_write "$tmp"
}

# Merge a single device object (from a relaying remote) into this node's store.
# Used on the core to absorb devices discovered by remotes. Reads JSON on stdin.
ds_merge_device() {
    ds_init
    local incoming; incoming="$(cat)"
    [[ -z "$incoming" ]] && return 1
    local mac; mac="$(jq -r '.mac // empty' <<<"$incoming" | tr 'A-Z' 'a-z')"
    [[ -z "$mac" ]] && return 1
    local tmp; tmp="$(mktemp)"
    jq --arg mac "$mac" --argjson inc "$incoming" --arg now "$(ds_now)" '
        .updated_at = $now
        | .devices[$mac] = ((.devices[$mac] // {}) + $inc
            | .first_seen = (.first_seen // $now)
            | .last_seen = $now)
    ' "$DEVICE_STORE_FILE" > "$tmp" && _ds_write "$tmp"
}

# Emit devices as compact JSON lines, optionally filtered by a jq select expr.
ds_each() {
    local filter="${1:-true}"
    [[ -f "$DEVICE_STORE_FILE" ]] || return 0
    jq -c ".devices[] | select(${filter})" "$DEVICE_STORE_FILE" 2>/dev/null
}
