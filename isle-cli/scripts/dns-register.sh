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
#   isle dns managed-names push       (core only) copy the managed-name list, derived from placement.json
#                                     (every entry's domain, any state), to the router's /etc/isle-managed-names
#                                     so the router's mDNS join scan skips those names
#   isle dns managed-names list       the list the router holds now, one name per line (reads only)
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
rscp(){ # rscp <local> <remote path>: same auth as rssh; -O = legacy scp protocol (OpenSSH 9 defaults to SFTP;
  # the OpenWrt router has no sftp-server). Same flag as router-setup/*.sh.
  case "$_AUTH" in
    key)  scp -O -q -o BatchMode=yes $_OPTS "$1" "$OPENWRT_USER@$OPENWRT_IP:$2" ;;
    pass) sshpass -p "$_PASS" scp -O -q $_OPTS "$1" "$OPENWRT_USER@$OPENWRT_IP:$2" ;;
    *)    return 1 ;;
  esac
}
norm(){ local n="${1%.isle}"; printf '%s.isle' "$n"; }

# Ownership fence (storage contract section 5). A name with an entry in the placement record is "managed": it
# may be written only by a storage operation holding a matching pending intent (ISLE_STORAGE_INTENT = the
# intent JSON, ISLE_OPERATION_ID = its operation). A managed write holds placement.lock from the check through
# uci set/commit/dnsmasq restart and re-reads the intent under it. Unmanaged names behave exactly as before.
PLACEMENT="${ISLE_PLACEMENT:-/etc/isle-mesh/storage/placement.json}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_fence_check(){ # $1 name $2 address ("-" never matches an intent)
  python3 "$SCRIPT_DIR/storage_placement.py" fence-check --domain "$1" --address "$2" \
      --intent "${ISLE_STORAGE_INTENT:-}" --operation "${ISLE_OPERATION_ID:-}" --placement "$PLACEMENT"
}
# NOTE: `unregister` passes "-" as the address, which never matches an intent, so a managed name can never be
# unregistered through this door.  Intended: cutover/rollback/recover only ever re-register; unmanaged names are unchanged.
managed_fence(){
  [[ -e "$PLACEMENT" ]] || return 0
  local words
  words="$(_fence_check "$1" "$2" 2>&1)" || { err "managed name: refused outside a storage intent (${words:-placement check failed})"; exit 1; }
  [[ "$words" == unmanaged ]] && return 0
  exec 8>>"$PLACEMENT.lock"; flock 8 || { err "cannot take placement.lock"; exit 1; }
  words="$(_fence_check "$1" "$2" 2>&1)" || { err "managed name: refused outside a storage intent (${words:-intent changed})"; exit 1; }
  return 0 # fd 8 stays open: the lock is held until this script exits, across the router mutation
}

require_root(){ [[ ${EUID:-$(id -u)} -eq 0 || "${ISLE_DNS_ALLOW_NONROOT:-}" == 1 ]] || { err "run as root (sudo isle dns ...)"; exit 1; }; }

ACTION="${1:-list}"; shift || true
require_root
_probe || { err "cannot reach OpenWRT non-interactively (router SSH key/cached password)"; exit 1; }

case "$ACTION" in
  register)
    NAME="$(norm "${1:?usage: isle dns register <name> <ip>}")"; IP="${2:?usage: isle dns register <name> <ip>}"
    managed_fence "$NAME" "$IP"
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
    managed_fence "$NAME" "-"
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
  managed-names)
    if [[ "${1:-}" == list ]]; then   # names only on stdout: storage_core.py reads this before a cutover
      rssh "cat /etc/isle-managed-names 2>/dev/null || true" || { err "the router did not answer; its managed-names list is not known"; exit 1; }
      exit 0
    fi
    [[ "${1:-}" == push ]] || { err "usage: isle dns managed-names push | list"; exit 1; }
    [[ -e "$PLACEMENT" ]] || { err "no placement record at $PLACEMENT (managed-names push runs on the core)"; exit 1; }
    tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
    python3 - "$PLACEMENT" > "$tmp" <<'PY' || { err "cannot read $PLACEMENT"; exit 1; }
import json, sys
doc = json.load(open(sys.argv[1]))
names = {e['domain'] for e in (doc.get('datasets') or {}).values() if isinstance(e, dict) and e.get('domain')}
for n in sorted(names):
    print(n)
PY
    dest=/etc/isle-managed-names; stage="$dest.new.$$"
    rscp "$tmp" "$stage" && rssh "mv -f '$stage' '$dest'" \
      || { rssh "rm -f '$stage'" 2>/dev/null; err "managed-names push failed; the router list is unchanged"; exit 1; }
    ok "pushed $(wc -l < "$tmp") managed name(s) to $dest"
    ;;
  *)
    err "unknown action: $ACTION"
    echo "Usage: isle dns register <name> <ip> | unregister <name> | list | managed-names push|list"
    exit 1
    ;;
esac
