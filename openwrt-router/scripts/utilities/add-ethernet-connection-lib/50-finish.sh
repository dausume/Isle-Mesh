#!/usr/bin/env bash
# 50-finish.sh
if [[ -n "${_FIN_SH:-}" ]]; then return; fi; _FIN_SH=1
finish_msg_eth(){
  ok "Ethernet connection prepared."
  echo "Next in OpenWRT:"
  echo "  uci set network.${ROLE}=interface"
  echo "  uci set network.${ROLE}.proto='dhcp'   # for WAN (or static for LAN)"
  echo "  uci set network.${ROLE}.ifname='@?'"   # match the virtio NIC (e.g., eth1)"
  echo "  uci commit network && /etc/init.d/network restart"
}
# End: 50-finish.sh