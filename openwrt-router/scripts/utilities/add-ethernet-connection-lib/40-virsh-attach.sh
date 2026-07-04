#!/usr/bin/env bash
# 40-virsh-attach.sh
if [[ -n "${_VIRATT_SH:-}" ]]; then return; fi; _VIRATT_SH=1
attach_bridge_to_vm(){
  # Idempotent: if the VM already has an interface on this bridge, reuse it (capture
  # its MAC for the OpenWRT-config step) instead of attaching a duplicate NIC.
  local existing
  existing="$(virsh domiflist "$ROUTER_VM" 2>/dev/null | awk -v b="$BR" '$3==b{print $5}' | tail -1)"
  if [[ -n "$existing" ]]; then
    ATTACHED_MAC="$existing"
    ok "Bridge $BR already attached to $ROUTER_VM (mac $existing) — reusing"
    return 0
  fi
  log "Attaching $BR to VM $ROUTER_VM (virtio)"
  virsh attach-interface --domain "$ROUTER_VM" --type bridge --source "$BR" \
     --model virtio --config --live >/dev/null
  # Capture the MAC libvirt assigned, so 45-openwrt-network.sh can find the NIC inside
  # OpenWRT and bridge it into the isle network.
  ATTACHED_MAC="$(virsh domiflist "$ROUTER_VM" 2>/dev/null | awk -v b="$BR" '$3==b{print $5}' | tail -1)"
  ok "Attached bridge to VM (mac ${ATTACHED_MAC:-unknown})"
}
# End: 40-virsh-attach.sh
