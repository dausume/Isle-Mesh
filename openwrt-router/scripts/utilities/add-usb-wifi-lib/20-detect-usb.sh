#!/usr/bin/env bash
if [[ -n "${_DUSB_SH:-}" ]]; then return; fi; _DUSB_SH=1
list_wifi_usb(){
  lsusb | grep -iE '(wifi|wlan|802\.11|ralink|mediatek|realtek|atheros)' || true
}
devpath_for_busdev(){ # $1=bus $2=dev -> prints like 1-3.2
  udevadm info --query=property --name="/dev/bus/usb/$1/$2" 2>/dev/null \
    | awk -F= '/^DEVPATH=/{print $2}' | grep -oE '[0-9]+-[0-9]+(\.[0-9]+)*'
}
vidpid_for_busdev(){ # $1=bus $2=dev -> VID PID
  local id; id=$(lsusb -s "$1:$2" | awk '{print $(NF-1)}') # format VID:PID
  echo "${id%:*} ${id#*:}"
}
pick_or_validate_usb(){
  if [[ -n "$USB_PATH" ]]; then
    reserved "USB:$USB_PATH" && warn "USB path already reserved" || reserve "USB:$USB_PATH"
  else
    mapfile -t L < <(list_wifi_usb)
    [[ ${#L[@]} -gt 0 ]] || { err "No USB Wi-Fi adapters detected"; exit 1; }
    banner "Select USB Wi-Fi Adapter"
    local i=1; for line in "${L[@]}"; do echo "[$i] $line"; ((i++)); done
    read -rp "Choose [1-${#L[@]}]: " n
    [[ "$n" =~ ^[0-9]+$ ]] && (( n>=1 && n<=${#L[@]} )) || { err "Invalid choice"; exit 1; }
    local bus dev; bus=$(echo "${L[$((n-1))]}" | awk '{print $2}'); dev=$(echo "${L[$((n-1))]}" | awk '{print $4}' | tr -d :)
    USB_PATH=$(devpath_for_busdev "$bus" "$dev")
    [[ -n "$USB_PATH" ]] || { err "Could not resolve USB path"; exit 1; }
    reserved "USB:$USB_PATH" && warn "USB path already reserved" || reserve "USB:$USB_PATH"
  fi

  # derive VID/PID from bus:dev that matches this path
  # We'll scan all bus/dev to find matching DEVPATH
  while read -r b d; do
    local p; p=$(devpath_for_busdev "$b" "$d")
    if [[ "$p" == "$USB_PATH" ]]; then read -r USB_VID USB_PID < <(vidpid_for_busdev "$b" "$d"); break; fi
  done < <(lsusb | awk '{print $2, $4}' | sed 's/://')
  [[ -n "$USB_VID" && -n "$USB_PID" ]] || { err "Failed to map $USB_PATH to VID:PID"; exit 1; }

  ok "Selected USB path $USB_PATH (VID:PID=${USB_VID}:${USB_PID})"
}
