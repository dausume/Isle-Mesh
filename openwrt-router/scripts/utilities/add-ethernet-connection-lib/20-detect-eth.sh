#!/usr/bin/env bash
# 20-detect-eth.sh
if [[ -n "${_DETETH_SH:-}" ]]; then return; fi; _DETETH_SH=1
isp_iface(){ ip route | awk '/^default/ {print $5; exit}'; }
list_eth(){
  ip -o link show | awk -F': ' '/: (eth|enp|eno)/{print $2}' \
    | grep -vE '^(br-|virbr|docker|veth)' \
    | grep -v "^$(isp_iface)$"
}
pick_or_validate_eth(){
  if [[ -n "${ETH_IFACE:-}" ]]; then
    list_eth | grep -qx "$ETH_IFACE" || { err "Iface $ETH_IFACE not eligible"; exit 1; }
  else
    mapfile -t CANDIDATES < <(list_eth)
    [[ ${#CANDIDATES[@]} -gt 0 ]] || { err "No eligible Ethernet interfaces found"; exit 1; }
    banner "Select Ethernet Interface"
    local i=1; for c in "${CANDIDATES[@]}"; do echo "[$i] $c"; ((i++)); done
    read -rp "Choose [1-${#CANDIDATES[@]}]: " n
    [[ "$n" =~ ^[0-9]+$ ]] && (( n>=1 && n<=${#CANDIDATES[@]} )) || { err "Invalid choice"; exit 1; }
    ETH_IFACE="${CANDIDATES[$((n-1))]}"
  fi
  reserved "ETH:$ETH_IFACE" && { warn "Interface already reserved"; } || reserve "ETH:$ETH_IFACE"
  ok "Selected: $ETH_IFACE (role=$ROLE, isle=$ISLE, vlan=$VLAN_ID)"
}
# End: 20-detect-eth.sh
