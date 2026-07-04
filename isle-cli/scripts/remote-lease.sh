#!/usr/bin/env bash
# remote-lease.sh — ensure the isle cable interface auto-leases from OpenWRT.
#
# On a remote node, set up a persistent DHCP auto-connect on the isle-candidate
# ethernet port so it pulls an isle address whenever the cable is live — on boot and
# on plug-in — with NO manual dhclient. Idempotent; safe to re-run.
#
# THREAT-MODEL GUARD: never touches the wifi / default-route interface (the ISP LAN /
# SSH path). Only acts on a wired, non-default-route, non-wireless NIC with carrier.
#
#   isle remote-lease [iface]   (auto-detects the isle cable NIC if omitted)
set -uo pipefail

log(){ echo -e "\033[0;36m[isle-remote-lease]\033[0m $*"; }
ok(){  echo -e "\033[0;32m[isle-remote-lease] ✓\033[0m $*"; }
err(){ echo -e "\033[0;31m[isle-remote-lease] ✗\033[0m $*" >&2; }

require_root(){ [[ ${EUID:-$(id -u)} -eq 0 ]] || { err "run as root (sudo isle remote-lease)"; exit 1; }; }

default_iface(){ ip route show default 2>/dev/null | awk '{print $5; exit}'; }

# Never touch: default-route iface, any wireless iface, loopback.
is_protected(){
  local i="$1" d; d="$(default_iface)"
  [[ -n "$d" && "$i" == "$d" ]] && return 0
  [[ "$i" == "lo" ]] && return 0
  [[ -d "/sys/class/net/$i/wireless" ]] && return 0
  case "$i" in wl*|wlan*|wlp*) return 0 ;; esac
  return 1
}

# Pick the isle-candidate wired NIC (ethernet, not protected, carrier up).
pick_iface(){
  local i
  for i in $(ls /sys/class/net 2>/dev/null); do
    [[ "$(cat "/sys/class/net/$i/type" 2>/dev/null)" == "1" ]] || continue   # ARPHRD_ETHER
    is_protected "$i" && continue
    ip link set "$i" up 2>/dev/null || true
    [[ "$(cat "/sys/class/net/$i/carrier" 2>/dev/null)" == "1" ]] || continue
    echo "$i"; return 0
  done
  return 1
}

require_root
IFACE="${1:-}"
[[ -z "$IFACE" ]] && IFACE="$(pick_iface)"
[[ -n "$IFACE" ]] || { log "no isle-candidate ethernet with carrier — nothing to do"; exit 0; }
is_protected "$IFACE" && { err "refusing protected interface $IFACE (wifi/default-route/SSH)"; exit 1; }
log "isle cable interface: $IFACE"

if command -v nmcli >/dev/null 2>&1 && nmcli -t -f RUNNING general 2>/dev/null | grep -q "running"; then
  # NetworkManager: a persistent autoconnect DHCP profile handles boot AND plug-in.
  con="isle-cable-$IFACE"
  if nmcli -t -f NAME connection show 2>/dev/null | grep -qx "$con"; then
    ok "NM autoconnect profile '$con' already present"
  else
    nmcli connection add type ethernet ifname "$IFACE" con-name "$con" \
      ipv4.method auto ipv6.method ignore connection.autoconnect yes >/dev/null \
      && ok "created NM autoconnect profile '$con' (DHCP, autoconnect)" \
      || { err "failed to create NM profile"; exit 1; }
  fi
  nmcli connection up "$con" >/dev/null 2>&1 || true
else
  # Non-NM fallback: one-shot DHCP now; boot persistence via isle-remote-lease.service.
  if   command -v dhclient >/dev/null 2>&1; then dhclient -1 "$IFACE" || true
  elif command -v udhcpc   >/dev/null 2>&1; then udhcpc -i "$IFACE" -q -n -t 5 || true
  else err "no DHCP client (dhclient/udhcpc) available"; exit 1; fi
fi

sleep 2
addr="$(ip -4 -o addr show "$IFACE" 2>/dev/null | awk '{print $4}' | head -1)"
if [[ -n "$addr" ]]; then ok "leased: $IFACE = $addr"; else log "no lease yet on $IFACE (is OpenWRT serving DHCP on the cable?)"; fi
