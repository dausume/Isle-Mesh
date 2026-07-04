#!/usr/bin/env bash
# dns-register.sh — register/unregister/list .isle names on the router DNS.
#
# Deterministic, jail-safe .isle registration (the recipe proven 2026-07-04):
#   • ensure dnsmasq is authoritative for .isle (local=/isle/ in the jail-readable
#     /etc/dnsmasq.conf) — without it dnsmasq REFUSES .isle;
#   • one DEDUPED UCI 'domain' entry per name → device IP (raw address= in a conf-dir
#     does NOT work: the procd jail can't see it);
#   • reload dnsmasq. Any isle node then resolves <name>.isle → deviceIP via the router.
#
#   isle dns register <name> <ip>     (name: "app" or "app.isle")
#   isle dns unregister <name>
#   isle dns list
set -uo pipefail

OPENWRT_IP="${OPENWRT_IP:-192.168.1.1}"; OPENWRT_USER="${OPENWRT_USER:-root}"
ISLE_SSH_KEY="${ISLE_SSH_KEY:-/etc/isle-mesh/router/ssh/isle_router_key}"
_PF="/etc/isle-mesh/router/ssh/.cached_password"
_OPTS="-o ConnectTimeout=10 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
[[ -f "$ISLE_SSH_KEY" ]] && _OPTS="-i $ISLE_SSH_KEY $_OPTS"
_PASS=""; [[ -f "$_PF" ]] && _PASS="$(cat "$_PF" 2>/dev/null)"; _AUTH=""

log(){ echo -e "\033[0;36m[isle-dns]\033[0m $*"; }
ok(){  echo -e "\033[0;32m[isle-dns] ✓\033[0m $*"; }
err(){ echo -e "\033[0;31m[isle-dns] ✗\033[0m $*" >&2; }

_probe(){
  ssh -o BatchMode=yes $_OPTS "$OPENWRT_USER@$OPENWRT_IP" true 2>/dev/null && { _AUTH=key; return 0; }
  [[ -n "$_PASS" ]] && command -v sshpass >/dev/null 2>&1 \
    && sshpass -p "$_PASS" ssh $_OPTS "$OPENWRT_USER@$OPENWRT_IP" true 2>/dev/null && { _AUTH=pass; return 0; }
  return 1
}
rssh(){
  case "$_AUTH" in
    key)  ssh -o BatchMode=yes $_OPTS "$OPENWRT_USER@$OPENWRT_IP" "$@" ;;
    pass) sshpass -p "$_PASS" ssh $_OPTS "$OPENWRT_USER@$OPENWRT_IP" "$@" ;;
    *)    return 1 ;;
  esac
}
norm(){ local n="${1%.isle}"; printf '%s.isle' "$n"; }

require_root(){ [[ ${EUID:-$(id -u)} -eq 0 ]] || { err "run as root (sudo isle dns ...)"; exit 1; }; }

ACTION="${1:-list}"; shift || true
require_root
_probe || { err "cannot reach OpenWRT non-interactively (router SSH key/cached password)"; exit 1; }

case "$ACTION" in
  register)
    NAME="$(norm "${1:?usage: isle dns register <name> <ip>}")"; IP="${2:?usage: isle dns register <name> <ip>}"
    log "registering $NAME → $IP"
    out="$(rssh "sh" <<EOF
grep -qx 'local=/isle/' /etc/dnsmasq.conf || echo 'local=/isle/' >> /etc/dnsmasq.conf
i=0
while uci -q get dhcp.@domain[\$i] >/dev/null 2>&1; do
  if [ "\$(uci -q get dhcp.@domain[\$i].name)" = "$NAME" ]; then uci -q delete dhcp.@domain[\$i]; else i=\$((i+1)); fi
done
uci add dhcp domain >/dev/null
uci set dhcp.@domain[-1].name='$NAME'
uci set dhcp.@domain[-1].ip='$IP'
uci commit dhcp
/etc/init.d/dnsmasq restart >/dev/null 2>&1
echo OK
EOF
)"
    printf '%s' "$out" | grep -q OK && ok "$NAME → $IP (resolvable from any isle node via 10.10.0.1)" || { err "registration failed: ${out:-no output}"; exit 1; }
    ;;
  unregister)
    NAME="$(norm "${1:?usage: isle dns unregister <name>}")"
    log "unregistering $NAME"
    out="$(rssh "sh" <<EOF
i=0; removed=0
while uci -q get dhcp.@domain[\$i] >/dev/null 2>&1; do
  if [ "\$(uci -q get dhcp.@domain[\$i].name)" = "$NAME" ]; then uci -q delete dhcp.@domain[\$i]; removed=1; else i=\$((i+1)); fi
done
uci commit dhcp
/etc/init.d/dnsmasq restart >/dev/null 2>&1
echo removed=\$removed
EOF
)"
    printf '%s' "$out" | grep -q removed=1 && ok "$NAME unregistered" || log "$NAME was not registered"
    ;;
  list)
    log ".isle mappings on the router:"
    rssh "i=0; while uci -q get dhcp.@domain[\$i] >/dev/null 2>&1; do n=\$(uci -q get dhcp.@domain[\$i].name); ip=\$(uci -q get dhcp.@domain[\$i].ip); case \"\$n\" in *.isle) echo \"  \$n -> \$ip\";; esac; i=\$((i+1)); done"
    ;;
  *)
    err "unknown action: $ACTION"
    echo "Usage: isle dns register <name> <ip> | unregister <name> | list"
    exit 1
    ;;
esac
