#!/bin/bash
#
# Isle device relay (sender side) — remote node → core node.
#
# When a remote node discovers a device that isn't on the mesh, it relays the
# device record to the core so the core user gets prompted too. We deliberately
# reuse what already exists instead of standing up a new HTTP service:
#
#   • Transport: a newline-delimited-JSON line sent over TCP with nc/socat to a
#     small receiver the core runs ('isle devices serve-relay'), which pipes the
#     line straight into 'isle devices accept'. nc and socat are already required
#     elsewhere in the CLI, and this needs no nginx changes.
#   • Durability: every relayed record is also written to a local outbox so an
#     undelivered record is retried on the next 'isle devices relay' run.
#   • Core address: the remote learns the network from the discovery beacon
#     (router IP in discovery.json). The core advertises itself over mDNS; we try
#     core.isle / core.local, an explicit override, then the router as a relay.
#
# Sourced by devices.sh (cmd_relay). Requires jq and one of nc/socat.

RELAY_PORT="${ISLE_RELAY_PORT:-7879}"          # 7878 is the discovery beacon
RELAY_OUTBOX="${RELAY_OUTBOX:-/etc/isle-mesh/agent/relay-outbox}"
RELAY_CORE_CONF="${RELAY_CORE_CONF:-/etc/isle-mesh/agent/core-address.conf}"

# Resolve a reachable address for the core node.
relay_resolve_core() {
    [[ -n "${ISLE_CORE_ADDR:-}" ]] && { echo "$ISLE_CORE_ADDR"; return 0; }
    [[ -s "$RELAY_CORE_CONF" ]] && { tr -d '[:space:]' < "$RELAY_CORE_CONF"; return 0; }
    local n
    for n in core.isle core.local; do
        if getent hosts "$n" &>/dev/null; then echo "$n"; return 0; fi
        if command -v avahi-resolve &>/dev/null && avahi-resolve -n "$n" &>/dev/null; then
            echo "$n"; return 0
        fi
    done
    # Last resort: the router from the discovery beacon (it can forward / co-host).
    if [[ -s /etc/isle-mesh/agent/remote/discovery.json ]]; then
        jq -r '.router // empty' /etc/isle-mesh/agent/remote/discovery.json 2>/dev/null
    fi
}

# Low-level: send one JSON line to host:port, echo the reply.
# socat is preferred: -t5 keeps the read side open for ~5s after our stdin EOF so
# we actually receive the server's reply (plain nc doesn't half-close, so the
# receiver's read would block and never answer). nc is a fallback using -N/-q to
# shut the write side down on EOF.
_relay_send() {
    local host="$1" port="$2" payload="$3"
    if command -v socat &>/dev/null; then
        printf '%s\n' "$payload" | socat -t5 - "TCP:${host}:${port}" 2>/dev/null
    elif command -v nc &>/dev/null; then
        printf '%s\n' "$payload" | nc -N -w5 "$host" "$port" 2>/dev/null \
            || printf '%s\n' "$payload" | nc -q1 -w5 "$host" "$port" 2>/dev/null
    else
        return 127
    fi
}

# relay_device_to_core <device-json> -> 0 only on confirmed delivery (reply contains OK)
relay_device_to_core() {
    local json="$1"
    [[ -z "$json" ]] && return 1
    local mac; mac="$(jq -r '.mac // "unknown"' <<<"$json" | tr 'A-Z' 'a-z')"

    # Durable queue copy first — survives until delivery is confirmed.
    mkdir -p "$RELAY_OUTBOX" 2>/dev/null
    printf '%s\n' "$json" > "$RELAY_OUTBOX/${mac}.json" 2>/dev/null

    local core; core="$(relay_resolve_core)"
    [[ -z "$core" ]] && return 1

    local reply; reply="$(_relay_send "$core" "$RELAY_PORT" "$json")"
    if [[ "$reply" == *OK* ]]; then
        rm -f "$RELAY_OUTBOX/${mac}.json" 2>/dev/null
        return 0
    fi
    return 1
}
