#!/usr/bin/env bash
# 30-bridge.sh
if [[ -n "${_BR_SH:-}" ]]; then return; fi; _BR_SH=1
BR="br-${ISLE}"
create_bridge_and_enslave(){
  log "Creating/updating bridge: $BR"
  # A managed Ethernet connection can detach the port after ip-link enslaving.
  # Reserve only this explicitly selected cable, never Wi-Fi or all interfaces.
  [[ "$ETH_IFACE" =~ ^[a-zA-Z0-9_.:-]+$ ]] || return 1
  if command -v nmcli >/dev/null && systemctl is-active --quiet NetworkManager; then
    local nmfile="/etc/NetworkManager/conf.d/90-isle-ethernet-${ETH_IFACE}.conf"
    if [[ -f "$nmfile" ]] && ! grep -q '^# Managed by Isle Ethernet reservation$' "$nmfile"; then
      err "Refusing to replace unrelated $nmfile"; return 1
    fi
    mkdir -p /etc/NetworkManager/conf.d
    printf '# Managed by Isle Ethernet reservation\n[device-isle-%s]\nmatch-device=interface-name:%s\nmanaged=0\n' "$ETH_IFACE" "$ETH_IFACE" > "$nmfile"
    nmcli device set "$ETH_IFACE" managed no || return 1
  fi
  ip link show "$BR" >/dev/null 2>&1 || ip link add name "$BR" type bridge
  ip link set "$BR" up
  # remove IP from physical iface (safety)
  ip addr flush dev "$ETH_IFACE" || true
  ip link set "$ETH_IFACE" up
  # enslave
  ip link set "$ETH_IFACE" master "$BR"
  ok "Enslaved $ETH_IFACE -> $BR"
}
# End: 30-bridge.sh
