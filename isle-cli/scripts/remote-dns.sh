#!/usr/bin/env bash
# remote-dns.sh — on a remote node, route *.isle to the isle router, ISP DNS untouched.
#
# Split-DNS: `.isle` names resolve via the isle router (over the cable) while all other
# names keep using the ISP resolver. Uses a routing-only `~isle` domain so nothing else
# is affected. NetworkManager-native where present (persistent), else systemd-resolved.
# NEVER routes all DNS to the isle (that would break internet / leak).
#
#   isle dns use-router [iface] [router-ip]
set -uo pipefail
log(){ echo -e "\033[0;36m[isle-dns]\033[0m $*"; }
ok(){  echo -e "\033[0;32m[isle-dns] ✓\033[0m $*"; }
err(){ echo -e "\033[0;31m[isle-dns] ✗\033[0m $*" >&2; }
require_root(){ [[ ${EUID:-$(id -u)} -eq 0 ]] || { err "run as root (sudo isle dns use-router)"; exit 1; }; }

is_wifi(){ [[ -d "/sys/class/net/$1/wireless" ]] || { case "$1" in wl*) return 0;; esac; return 1; }; }

# The isle interface = a wired NIC that holds an isle (10.x) lease.
pick_isle_iface(){
  local i
  for i in $(ls /sys/class/net 2>/dev/null); do
    [[ "$(cat "/sys/class/net/$i/type" 2>/dev/null)" == "1" ]] || continue
    is_wifi "$i" && continue
    ip -4 addr show "$i" 2>/dev/null | grep -q "inet 10\." && { echo "$i"; return 0; }
  done
  return 1
}

require_root
IFACE="${1:-}"; [[ -z "$IFACE" ]] && IFACE="$(pick_isle_iface)"
[[ -n "$IFACE" ]] || { err "no isle interface found (a wired NIC with a 10.x isle lease)"; exit 1; }
is_wifi "$IFACE" && { err "refusing wifi interface $IFACE (ISP/SSH path)"; exit 1; }

ROUTER="${2:-}"
if [[ -z "$ROUTER" ]]; then
  base="$(ip -4 addr show "$IFACE" 2>/dev/null | grep -oE 'inet 10\.[0-9]+\.[0-9]+\.' | head -1 | sed 's/inet //')"
  [[ -n "$base" ]] && ROUTER="${base}1"
fi
[[ -n "$ROUTER" ]] || ROUTER="10.10.0.1"
log "routing *.isle → $ROUTER via $IFACE (ISP DNS untouched)"

if command -v nmcli >/dev/null 2>&1 && nmcli -t -f RUNNING general 2>/dev/null | grep -q "running"; then
  con="$(nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null | awk -F: -v d="$IFACE" '$2==d{print $1; exit}')"
  [[ -z "$con" ]] && con="isle-cable-$IFACE"
  if nmcli connection modify "$con" ipv4.dns "$ROUTER" ipv4.dns-search "~isle" ipv4.dns-priority 10 2>/dev/null; then
    nmcli connection up "$con" >/dev/null 2>&1 || true
    ok "NetworkManager: *.isle → $ROUTER on '$con' (routing-only ~isle; ISP DNS intact)"
  else
    err "nmcli configuration failed on '$con'"; exit 1
  fi
elif command -v resolvectl >/dev/null 2>&1; then
  resolvectl dns "$IFACE" "$ROUTER" && resolvectl domain "$IFACE" "~isle" \
    && ok "systemd-resolved: *.isle → $ROUTER via $IFACE (routing-only)" \
    || { err "resolvectl configuration failed"; exit 1; }
else
  err "no NetworkManager or systemd-resolved found — refusing to edit /etc/resolv.conf globally"
  err "(a global change would route ALL DNS to the isle and break internet)."
  exit 1
fi

sleep 1
log "verify: nslookup <app>.isle   (should resolve via $ROUTER)"
