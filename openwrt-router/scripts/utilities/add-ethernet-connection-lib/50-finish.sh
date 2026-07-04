#!/usr/bin/env bash
# 50-finish.sh
if [[ -n "${_FIN_SH:-}" ]]; then return; fi; _FIN_SH=1
finish_msg_eth(){
  ok "Ethernet connection added to the isle."
  echo "  Interface : ${ETH_IFACE}  →  bridge ${BR}  →  OpenWRT (${ISLE_UCI:-isle}, role=${ROLE})"
  if [[ "${ROLE:-lan}" == "lan" ]]; then
    echo "  A remote node connected to this cable will now lease an isle address"
    echo "  automatically from OpenWRT (no further steps)."
  fi
}
# End: 50-finish.sh
