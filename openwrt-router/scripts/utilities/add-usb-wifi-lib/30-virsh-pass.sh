#!/usr/bin/env bash
if [[ -n "${_VPASS_SH:-}" ]]; then return; fi; _VPASS_SH=1
attach_usb_to_vm(){
  log "Attaching USB ${USB_VID}:${USB_PID} to VM $ROUTER_VM"
  TMPXML=$(mktemp)
  cat >"$TMPXML"<<EOF
<hostdev mode='subsystem' type='usb' managed='yes'>
  <source>
    <vendor id='0x${USB_VID}'/>
    <product id='0x${USB_PID}'/>
  </source>
</hostdev>
EOF
  virsh attach-device "$ROUTER_VM" "$TMPXML" --live --config >/dev/null
  rm -f "$TMPXML"
  ok "USB Wi-Fi passed through to VM"
}
