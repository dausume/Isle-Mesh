#!/usr/bin/env bash
# 30-bridge.sh
if [[ -n "${_BR_SH:-}" ]]; then return; fi; _BR_SH=1
BR="br-${ISLE}"
create_bridge_and_enslave(){
  log "Creating/updating bridge: $BR"
  ip link show "$BR" >/dev/null 2>&1 || ip link add name "$BR" type bridge
  ip link set "$BR" up
  # remove IP from physical iface (safety)
  ip addr flush dev "$ETH_IFACE" || true
  # enslave
  ip link set "$ETH_IFACE" master "$BR"
  ok "Enslaved $ETH_IFACE -> $BR"
}
# End: 30-bridge.sh
