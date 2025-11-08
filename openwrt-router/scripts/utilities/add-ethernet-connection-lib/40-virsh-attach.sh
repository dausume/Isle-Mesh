#!/usr/bin/env bash
# 40-virsh-attach.sh
if [[ -n "${_VIRATT_SH:-}" ]]; then return; fi; _VIRATT_SH=1
attach_bridge_to_vm(){
  log "Attaching $BR to VM $ROUTER_VM (virtio)"
  virsh attach-interface --domain "$ROUTER_VM" --type bridge --source "$BR" \
     --model virtio --config --live >/dev/null
  ok "Attached bridge to VM"
  echo "Suggested mapping inside OpenWRT:"
  echo "  ROLE=$ROLE → put this interface on $( [[ "$ROLE" == "lan" ]] && echo "LAN" || echo "WAN") network."
}
# End: 40-virsh-attach.sh
