#!/usr/bin/env bash
if [[ -n "${_SUM_SH:-}" ]]; then return; fi; _SUM_SH=1

show_summary_single_isle(){
  cat <<EOF

$(printf "\033[0;32m")╔═══════════════════════════════════════════════════════════════════╗
║                   OpenWRT Configuration Complete                   ║
╚═══════════════════════════════════════════════════════════════════╝$(printf "\033[0m")

Management:
  IP Address:     ${OPENWRT_IP}
  Web Interface:  http://${OPENWRT_IP}
  SSH:            ssh ${OPENWRT_USER}@${OPENWRT_IP}

Single Isle:
  Name:           ${MY_ISLE_NAME}  (UCI: ${MY_ISLE_UCI})
  VLAN:           ${VLAN_ID}
  Interface:      ${MY_ISLE_IF_DEV}
  Address:        ${MY_ISLE_IP}/${MY_ISLE_NETMASK}

Notes:
  - The isle zone '${MY_ISLE_UCI}' is isolated by default (forward=REJECT).
  - Adjust inter-zone forwarding later if you want LAN/Internet access.
  - mDNS reflector is enabled via Avahi across router interfaces.

Useful Commands:
  uci show network
  uci show firewall
  ip addr; ip route
  avahi-browse -a
EOF
}
