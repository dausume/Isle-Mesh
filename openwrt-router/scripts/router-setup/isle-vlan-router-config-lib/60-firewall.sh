#!/usr/bin/env bash
if [[ -n "${_FW_SH:-}" ]]; then return; fi; _FW_SH=1

configure_single_isle_firewall(){
  info "Configuring firewall (single zone: ${MY_ISLE_UCI})…"
  local tmp="$STATE_DIR/firewall-config.sh"
  cat >"$tmp"<<SH
#!/bin/sh
set -e
cp /etc/config/firewall /etc/config/firewall.bak 2>/dev/null || true

# Create a zone for my-isle (isolated by default)
uci -q delete firewall.${MY_ISLE_UCI}
uci add firewall zone
uci set firewall.@zone[-1].name='${MY_ISLE_UCI}'
uci set firewall.@zone[-1].network='${MY_ISLE_UCI}'
uci set firewall.@zone[-1].input='ACCEPT'
uci set firewall.@zone[-1].output='ACCEPT'
uci set firewall.@zone[-1].forward='REJECT'

uci commit firewall
/etc/init.d/firewall restart || true
SH

  copy_to_openwrt "$tmp" "/tmp/firewall-config.sh"
  exec_ssh "chmod +x /tmp/firewall-config.sh && /tmp/firewall-config.sh" \
    || { err "Firewall configuration failed"; exit 1; }
  ok "Firewall zone '${MY_ISLE_UCI}' configured (forward=REJECT)"
}
