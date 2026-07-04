#!/usr/bin/env bash
# boot-bringup.sh — idempotent full-isle bring-up for boot-time recovery + manual use.
#
# Wired to `isle recover` (alias `isle boot`) and run at every boot by
# isle-mesh-boot.service. It reconstructs, in dependency order, everything a
# reboot/power-loss tears down, and is safe to re-run at any time.
#
# Order matches the hard-won recovery playbook:
#   1. router VM + its ephemeral bridges (isle-br-0, br-mgmt)   [reuses `isle router up`]
#   2. replay reserved physical isle cables into the isle bridge (the covert path)
#   3. re-sync the isle macvlan docker network (parent bridge gets a fresh ifindex)
#   4. (re)create the agent so it binds the fresh macvlan
#
# THREAT-MODEL GUARD: this NEVER touches the wifi / default-route interface or the
# SSH control path. It only acts on isle bridges, reserved isle ports, the router
# VM, the isle macvlan, and the agent. See the Isle-Mesh threat model.

set -uo pipefail   # deliberately NOT `set -e`: a missing cable or a down peer must
                   # not abort the rest of the bring-up.

ROUTER_VM="${ROUTER_VM:-openwrt-isle-router}"
ISLE="${ISLE:-my-isle}"
ISLE_BRIDGE="br-${ISLE}"                       # reserved physical cables enslave here
MACVLAN="${MACVLAN:-isle-br-0}"
MACVLAN_PARENT="${MACVLAN_PARENT:-isle-br-0}"
# Isle subnet — must match what OpenWRT's dnsmasq serves (network.myisle), so the agent
# sits in the same subnet as remote nodes. The agent's static IP (10.10.0.2) is pinned
# in isle-agent/docker-compose.yml, below the DHCP pool (starts .50).
MACVLAN_SUBNET="${MACVLAN_SUBNET:-10.10.0.0/24}"
MACVLAN_GATEWAY="${MACVLAN_GATEWAY:-10.10.0.1}"
RESERVED="${RESERVED:-/var/lib/isle-mesh/reserved-ports.conf}"

BOOT=0
[[ "${1:-}" == "--boot" ]] && BOOT=1

# Resolve through symlinks (the CLI is npm-linked at /usr/local/lib/node_modules/
# isle-cli → the repo), so paths land in the real repo, not the symlink's parent.
SELF="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$SELF")" && pwd -P)"
AGENT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd -P)/isle-agent"

C='\033[0;36m'; G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; N='\033[0m'
log(){  echo -e "${C}[isle-recover]${N} $*"; }
ok(){   echo -e "${G}[isle-recover] ✓${N} $*"; }
warn(){ echo -e "${Y}[isle-recover] ⚠${N} $*"; }
err(){  echo -e "${R}[isle-recover] ✗${N} $*" >&2; }

require_root(){ [[ ${EUID:-$(id -u)} -eq 0 ]] || { err "run as root (sudo isle recover)"; exit 1; }; }

default_iface(){ ip route show default 2>/dev/null | awk '{print $5; exit}'; }

# Never touch the default-route iface, any wireless iface, or loopback.
is_protected_iface(){
  local ifc="$1" defc; defc="$(default_iface)"
  [[ -n "$defc" && "$ifc" == "$defc" ]] && return 0
  [[ "$ifc" == "lo" ]] && return 0
  [[ -d "/sys/class/net/$ifc/wireless" ]] && return 0
  case "$ifc" in wl*|wlan*|wlp*) return 0 ;; esac
  return 1
}

# 1) Router VM + XML bridges. `isle router up` recreates the ephemeral bridges from
#    the VM definition and waits for the router; it is a no-op if already running.
bring_up_router(){
  log "Step 1/4: router VM + bridges"
  if ! virsh list --all 2>/dev/null | grep -qw "$ROUTER_VM"; then
    warn "router VM '$ROUTER_VM' not defined — run 'isle create' first; skipping"
    return 0
  fi
  if virsh list --state-running 2>/dev/null | grep -qw "$ROUTER_VM"; then
    ok "router already running"
  else
    bash "$SCRIPT_DIR/router.sh" up "$ROUTER_VM" || warn "router up reported an error"
  fi
}

# 2) Replay reserved physical cables. Enslave each into the isle bridge so the covert
#    cable path is restored automatically (even before the cable links — membership
#    persists and traffic flows the moment carrier appears).
replay_reserved_ports(){
  log "Step 2/4: reserved isle cables"
  if [[ ! -s "$RESERVED" ]]; then
    log "no reserved ports (${RESERVED} empty) — nothing to enslave yet"
    return 0
  fi
  ip link show "$ISLE_BRIDGE" &>/dev/null || ip link add name "$ISLE_BRIDGE" type bridge
  ip link set "$ISLE_BRIDGE" up
  local entry ifc
  while IFS= read -r entry; do
    entry="${entry%%#*}"; entry="$(echo -n "$entry" | tr -d '[:space:]')"
    [[ -z "$entry" ]] && continue
    # add-ethernet records reservations as "ETH:enp1s0"; USB-wifi entries use a
    # different (passthrough) mechanism and are handled elsewhere. Bare names are
    # accepted for back-compat.
    case "$entry" in
      ETH:*) ifc="${entry#ETH:}" ;;
      *:*)   warn "skip non-ethernet reserved entry: $entry"; continue ;;
      *)     ifc="$entry" ;;
    esac
    if is_protected_iface "$ifc"; then
      err "refusing to touch protected interface '$ifc' (wifi/default-route/SSH) — skipped"
      continue
    fi
    if [[ ! -e "/sys/class/net/$ifc" ]]; then warn "reserved iface '$ifc' absent — skipped"; continue; fi
    ip link set "$ifc" up 2>/dev/null || true
    local carrier; carrier="$(cat "/sys/class/net/$ifc/carrier" 2>/dev/null || echo 0)"
    [[ "$carrier" != "1" ]] && warn "reserved iface '$ifc' has no carrier (cable unplugged?) — enslaving anyway; links when plugged"
    ip addr flush dev "$ifc" 2>/dev/null || true      # stealth: no host IP on the isle cable
    local cur; cur="$(basename "$(readlink -f "/sys/class/net/$ifc/master" 2>/dev/null)" 2>/dev/null || echo '')"
    if [[ "$cur" == "$ISLE_BRIDGE" ]]; then
      ok "'$ifc' already enslaved to $ISLE_BRIDGE"
    elif ip link set "$ifc" master "$ISLE_BRIDGE" 2>/dev/null; then
      ok "enslaved '$ifc' → $ISLE_BRIDGE"
    else
      err "failed to enslave '$ifc' → $ISLE_BRIDGE"
    fi
  done < "$RESERVED"
}

# 3) Re-sync the isle macvlan. Its parent bridge is recreated with a fresh ifindex
#    every boot, orphaning the old network object — so tear down and recreate.
resync_macvlan(){
  log "Step 3/4: isle macvlan docker network"
  if ! command -v docker &>/dev/null; then warn "docker absent — skipping macvlan"; return 0; fi
  if ! ip link show "$MACVLAN_PARENT" &>/dev/null; then
    warn "parent bridge '$MACVLAN_PARENT' missing — router step should create it; skipping macvlan"
    return 0
  fi
  if docker network inspect "$MACVLAN" &>/dev/null; then
    local c
    for c in $(docker network inspect "$MACVLAN" --format '{{range $k,$v := .Containers}}{{$v.Name}} {{end}}' 2>/dev/null); do
      docker network disconnect -f "$MACVLAN" "$c" 2>/dev/null || true
    done
    docker network rm "$MACVLAN" &>/dev/null || true
  fi
  if docker network create --driver macvlan --opt parent="$MACVLAN_PARENT" \
        --subnet "$MACVLAN_SUBNET" --gateway "$MACVLAN_GATEWAY" "$MACVLAN" &>/dev/null; then
    ok "macvlan '$MACVLAN' bound to $MACVLAN_PARENT"
  else
    err "failed to create macvlan '$MACVLAN'"
  fi
}

# 4) (Re)create the agent so it binds the fresh macvlan.
bring_up_agent(){
  log "Step 4/4: isle agent"
  if [[ ! -f "$AGENT_DIR/docker-compose.yml" ]]; then warn "agent compose not found at $AGENT_DIR — skipped"; return 0; fi
  if ( cd "$AGENT_DIR" && docker compose up -d --force-recreate ) >/dev/null 2>&1; then
    ok "agent (re)started"
  else
    err "agent failed to start (see: docker logs isle-vlan-agent)"
  fi
}

main(){
  require_root
  log "=== Isle bring-up$( [[ $BOOT == 1 ]] && echo ' (boot)' ) ==="
  bring_up_router
  replay_reserved_ports
  resync_macvlan
  bring_up_agent
  log "=== summary ==="
  docker ps --format '  {{.Names}}: {{.Status}}' 2>/dev/null | grep -iE 'isle|vlan|sample' || true
  log "=== done ==="
}
main "$@"
