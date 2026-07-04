#!/usr/bin/env bash
# hotplug.sh — cable-plug handler (invoked by udev when an ethernet NIC gains carrier).
#
# Role-aware plug-and-play: when a cable becomes live on an isle-candidate port,
#   • CORE  (has the OpenWRT router VM):  isle router add-connection   (reserve + config)
#   • REMOTE (no router VM):              isle remote-lease            (pull a lease)
#
# GATED ON DISCOVERY MODE (the operator's consent window): only auto-acts while
# discovery mode is ON. If it is OFF, the event is logged for the admission prompt
# (a valid isle-remote wants in) instead of auto-connecting.
#
# THREAT-MODEL GUARD: ignores the wifi / default-route interface entirely.
#
#   isle hotplug <iface>     (udev passes the interface via $INTERFACE/$1)
set -uo pipefail

IFACE="${1:-${INTERFACE:-}}"
LOG=/var/log/isle-hotplug.log
SELF="$(readlink -f "${BASH_SOURCE[0]}")"
CLI_DIR="$(cd "$(dirname "$SELF")" && pwd)"          # isle-cli/scripts
log(){ ( echo "[$(date '+%F %T')] $*" >> "$LOG" ) 2>/dev/null || true; }

[[ -n "$IFACE" ]] || { log "no interface given"; exit 0; }

# --- guards ---------------------------------------------------------------
# Never touch the WIFI (ISP/SSH path) or loopback. We do NOT skip on "holds a default
# route" — an isle-hijacked default lands on the ethernet cable, which is exactly what
# we want to act on (and remote-lease's never-default keeps it from recurring).
[[ "$IFACE" == "lo" ]] && exit 0
[[ -d "/sys/class/net/$IFACE/wireless" ]] && { log "skip $IFACE (wireless)"; exit 0; }
case "$IFACE" in wl*|wlan*|wlp*) log "skip $IFACE (wifi)"; exit 0 ;; esac
[[ "$(cat "/sys/class/net/$IFACE/type" 2>/dev/null)" == "1" ]] || exit 0   # ethernet only
[[ "$(cat "/sys/class/net/$IFACE/carrier" 2>/dev/null)" == "1" ]] || { log "skip $IFACE (no carrier)"; exit 0; }

# --- discovery-mode gate --------------------------------------------------
DISC_ACTIVE=0
if command -v jq >/dev/null 2>&1 && [[ -f /etc/isle-mesh/agent/discovery-mode.json ]]; then
  # reuse the canonical logic if available; else read the file directly
  if [[ -f "$CLI_DIR/lib/discovery-mode.sh" ]]; then
    # shellcheck source=/dev/null
    source "$CLI_DIR/lib/discovery-mode.sh"; dm_active && DISC_ACTIVE=1
  else
    [[ "$(jq -r '.active // false' /etc/isle-mesh/agent/discovery-mode.json)" == "true" ]] && DISC_ACTIVE=1
  fi
fi

# --- role detection -------------------------------------------------------
is_core(){ virsh list --all 2>/dev/null | grep -qw "openwrt-isle-router"; }

if [[ "$DISC_ACTIVE" != "1" ]]; then
  log "$IFACE went live but discovery mode is OFF — NOT auto-connecting. A valid isle node may want in (see: isle discovery start / admission prompt)."
  # Best-effort marker for the app/CLI to surface an admission suggestion.
  mkdir -p /var/lib/isle-mesh 2>/dev/null
  echo "$(date '+%F %T') pending-cable $IFACE" >> /var/lib/isle-mesh/pending-connections 2>/dev/null || true
  exit 0
fi

if is_core; then
  log "discovery ON + core → isle router add-connection --iface $IFACE --role lan"
  bash "$CLI_DIR/router.sh" add-connection --iface "$IFACE" --role lan >> "$LOG" 2>&1 || log "add-connection failed for $IFACE"
else
  log "discovery ON + remote → isle remote-lease $IFACE"
  bash "$CLI_DIR/remote-lease.sh" "$IFACE" >> "$LOG" 2>&1 || log "remote-lease failed for $IFACE"
fi
