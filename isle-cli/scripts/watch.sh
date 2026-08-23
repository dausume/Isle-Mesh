#!/bin/bash
# watch.sh — `isle watch`: the member-side ISLE-ENDING service (unin-7).
#
# When an isle's CORE is uninstalled, everything members depend on dies
# with it (router DNS, the CA, apt-on-mesh, the core polari) and there
# is NO way to uninstall software on other devices remotely. What the
# core CAN do is send a last-gasp KILL SIGNAL (a fingerprint-tagged UDP
# broadcast on the isle subnet — remotes hold host-level isle IPs, so a
# tiny host-side listener hears it). This service:
#
#   1. LISTENS for ISLE-ENDING broadcasts; a matching CA fingerprint
#      triggers the SAFE stage automatically: stop isle app containers
#      + the agent (nothing is deleted), and record the event in
#      /etc/isle-mesh/isle-ended.
#   2. Also POLLS core reachability as the fallback (a member that was
#      offline during the broadcast still finds out): after
#      ISLE_WATCH_MISSES consecutive failed checks it records a SOFTER
#      event (core-unreachable, not core-deleted).
#   3. The store shell's first-open (and `isle status`) sees the flag
#      and PROMPTS the human: remove all polari-isle apps from this
#      device? Removal is NEVER automatic — the kill signal only stops
#      things (reversible); deletion needs a person and comes with the
#      no-going-back warning.
#
#   isle watch listen        the daemon loop (systemd runs this)
#   isle watch enable        install+start the systemd service (root)
#   isle watch disable       stop+remove it
#   isle watch status        service + flag state
#   isle watch clear         clear the isle-ended flag (false alarm /
#                            the isle came back)
#
# Honesty note: the broadcast is fingerprint-TAGGED, not signed — a
# malicious device already inside the isle could spoof it. The blast
# radius is bounded on purpose: a spoof can only make members STOP
# containers (recoverable, isle watch clear + restart); it can never
# delete anything.
set -u
PORT="${ISLE_WATCH_PORT:-7879}"
MISSES="${ISLE_WATCH_MISSES:-30}"       # polls before core-unreachable event
POLL_S="${ISLE_WATCH_POLL_S:-120}"
CA=/etc/isle-mesh/ca/isle-root.crt
FLAG=/etc/isle-mesh/isle-ended
UNIT=/etc/systemd/system/isle-watch.service
G="\033[0;32m"; Y="\033[1;33m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }
warn(){ echo -e "${Y}[WARN]${N} $*"; }

ca_fp() { openssl x509 -in "$CA" -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2; }

stop_isle_apps() {
    # THE SAFE STAGE: stop (not remove) every isle-family container +
    # the agent. Reversible by design.
    local c
    for c in $(docker ps --format '{{.Names}}' 2>/dev/null \
        | grep -E '^(isle-|prf-isle-|prf-polari-)'); do
        docker stop "$c" >/dev/null 2>&1
    done
}

record_event() {  # KIND DETAIL
    mkdir -p /etc/isle-mesh
    printf 'kind=%s\nwhen=%s\ndetail=%s\n' "$1" "$(date -Iseconds)" "$2" > "$FLAG"
}

core_alive() {
    # the core serves apt.isle + polari.isle; either answering = alive
    curl -skf --max-time 6 -o /dev/null https://apt.isle/ 2>/dev/null && return 0
    curl -skf --max-time 6 -o /dev/null https://polari.isle/ 2>/dev/null && return 0
    getent hosts polari.isle >/dev/null 2>&1 && return 0
    return 1
}

listen_loop() {
    # only meaningful on a member (a core doesn't watch itself)
    local mode; mode=$(cat /etc/isle-mesh/agent/agent.mode 2>/dev/null || echo "")
    local myfp; myfp=$(ca_fp)
    local misses=0 line
    echo "isle-watch: listening on udp/$PORT + polling every ${POLL_S}s (mode=${mode:-none})"
    while true; do
        # window 1: listen for the kill signal
        line=$(timeout "$POLL_S" socat -u "UDP4-RECVFROM:$PORT,reuseaddr" STDOUT 2>/dev/null || true)
        if printf '%s' "$line" | grep -q '^ISLE-ENDING '; then
            local fp; fp=$(printf '%s' "$line" | awk '{print $2}')
            if [ -n "$myfp" ] && [ "$fp" = "$myfp" ]; then
                echo "isle-watch: ISLE-ENDING received (fingerprint matches) — safe stop"
                stop_isle_apps
                record_event core-deleted "kill signal received from the core's uninstall"
            else
                echo "isle-watch: ISLE-ENDING ignored (fingerprint mismatch — not our isle, or spoofed)"
            fi
            continue
        fi
        # window 2: the poll fallback
        if core_alive; then
            misses=0
        else
            misses=$((misses + 1))
            if [ "$misses" -ge "$MISSES" ] && [ ! -f "$FLAG" ]; then
                echo "isle-watch: core unreachable for $misses checks — recording (softer) event"
                record_event core-unreachable "no core response for $((MISSES * POLL_S / 60)) minutes"
            fi
        fi
    done
}

case "${1:-status}" in
    listen) listen_loop ;;
    broadcast-ending)
        # CORE side, last gasp before teardown: tell every member the
        # isle is ending so their watchers stop apps + prompt humans.
        # Members offline right now still catch up via the poll fallback.
        FP=$(ca_fp)
        [ -n "$FP" ] || { warn "no isle CA — no fingerprint to tag; skipping broadcast"; exit 0; }
        SENT=0
        for i in 1 2 3 4 5; do
            printf 'ISLE-ENDING %s %s\n' "$FP" "$(date -Iseconds)" \
                | socat -u STDIN "UDP4-DATAGRAM:10.10.0.255:$PORT,broadcast" 2>/dev/null \
                && SENT=$((SENT + 1))
            sleep 1
        done
        [ "$SENT" -gt 0 ] && ok "ISLE-ENDING broadcast sent (${SENT}×, 10.10.0.255:$PORT)" \
            || warn "broadcast could not be sent (no isle-facing route?) — members will rely on the poll fallback" ;;
    enable)
        [ "$(id -u)" = 0 ] || { warn "needs root: sudo isle watch enable"; exit 1; }
        cat > "$UNIT" <<EOF
[Unit]
Description=isle-watch — member-side ISLE-ENDING listener + core-health poll
After=network-online.target docker.service
[Service]
ExecStart=/bin/bash /usr/share/isle-mesh/isle-cli/scripts/watch.sh listen
Restart=always
RestartSec=30
[Install]
WantedBy=multi-user.target
EOF
        systemctl daemon-reload && systemctl enable --now isle-watch >/dev/null 2>&1
        ok "isle-watch enabled (udp/$PORT + ${POLL_S}s core poll)" ;;
    disable)
        [ "$(id -u)" = 0 ] || { warn "needs root: sudo isle watch disable"; exit 1; }
        systemctl disable --now isle-watch >/dev/null 2>&1; rm -f "$UNIT"; systemctl daemon-reload
        ok "isle-watch disabled" ;;
    clear)
        [ "$(id -u)" = 0 ] || { warn "needs root: sudo isle watch clear"; exit 1; }
        rm -f "$FLAG" && ok "isle-ended flag cleared (containers can be started again)" ;;
    status|*)
        systemctl is-active isle-watch >/dev/null 2>&1 && ok "isle-watch service active" \
            || echo "isle-watch service not active (enable: sudo isle watch enable)"
        if [ -f "$FLAG" ]; then
            warn "ISLE-ENDED flag present:"
            sed 's/^/    /' "$FLAG"
            echo "    remove everything: sudo isle uninstall --everything"
            echo "    false alarm:       sudo isle watch clear"
        else
            echo "no isle-ended event recorded"
        fi ;;
esac
