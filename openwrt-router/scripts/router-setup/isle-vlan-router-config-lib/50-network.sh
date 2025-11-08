#!/usr/bin/env bash
if [[ -n "${_NET_SH:-}" ]]; then return; fi; _NET_SH=1

configure_single_isle_network(){
  info "Configuring network (single isle: ${MY_ISLE_NAME}, VLAN ${VLAN_ID}, dev ${MY_ISLE_IF_DEV})…"
  local tmp="$STATE_DIR/network-config.sh"
  cat >"$tmp"<<SH
#!/bin/sh
set -e
cp /etc/config/network /etc/config/network.bak 2>/dev/null || true

# Ensure 802.1q available
modprobe 8021q 2>/dev/null || true

# Create tagged subinterface for my-isle
# Using device syntax for DSA-less/virtio models: ${MY_ISLE_IF_DEV}
uci -q delete network.${MY_ISLE_UCI}
uci set network.${MY_ISLE_UCI}=interface
uci set network.${MY_ISLE_UCI}.proto='static'
uci set network.${MY_ISLE_UCI}.device='${MY_ISLE_IF_DEV}'
uci set network.${MY_ISLE_UCI}.ipaddr='${MY_ISLE_IP}'
uci set network.${MY_ISLE_UCI}.netmask='${MY_ISLE_NETMASK}'

uci commit network
/etc/init.d/network restart || true
SH

  copy_to_openwrt "$tmp" "/tmp/network-config.sh"
  exec_ssh "chmod +x /tmp/network-config.sh && /tmp/network-config.sh" \
    || { err "Network configuration failed"; exit 1; }
  ok "Network configured for ${MY_ISLE_NAME} on ${MY_ISLE_IF_DEV}"
}
