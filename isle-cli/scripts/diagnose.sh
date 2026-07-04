#!/usr/bin/env bash
# diagnose.sh — mesh-expansion hardware capacity diagnostic.
#
# Answers "how many USB-wifi radios can this machine take, and which chipsets?" so
# VLAN/mesh automation (and the manager app) can self-assess instead of a human
# enumerating lsusb by hand. Read-only. No root needed.
#
#   isle diagnose            human-readable capacity report
#   isle diagnose --json     machine-readable (for the manager-app button)
#   isle diagnose usb-wifi   same as default (focused alias)

set -uo pipefail

JSON=0
for a in "$@"; do case "$a" in --json) JSON=1 ;; usb-wifi|usb|wifi|"") : ;; esac; done

C='\033[0;36m'; G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; B='\033[1m'; N='\033[0m'

# ---- USB root hubs ---------------------------------------------------------
usb_total_ports=0; usb_used=0; usb3_ports=0; usb2_ports=0
declare -a HUB_LINES
for b in /sys/bus/usb/devices/usb*; do
  [[ -e "$b/speed" ]] || continue
  spd="$(cat "$b/speed" 2>/dev/null)"; ports="$(cat "$b/maxchild" 2>/dev/null || echo 0)"
  prod="$(cat "$b/product" 2>/dev/null)"
  usb_total_ports=$(( usb_total_ports + ports ))
  if [[ "${spd:-0}" -ge 5000 ]]; then usb3_ports=$(( usb3_ports + ports )); else usb2_ports=$(( usb2_ports + ports )); fi
  HUB_LINES+=("$(basename "$b")|${spd}|${ports}|${prod}")
done
# rough "in use" = attached devices that are not root hubs / not pure hubs
for d in /sys/bus/usb/devices/*-*; do
  [[ -e "$d/bDeviceClass" ]] || continue
  cls="$(cat "$d/bDeviceClass" 2>/dev/null)"
  [[ "$cls" == "09" ]] && continue   # 09 = hub, skip
  usb_used=$(( usb_used + 1 ))
done
n_controllers="$(lspci 2>/dev/null | grep -ic usb || echo '?')"

# ---- Wireless phys + chipset ----------------------------------------------
declare -a WIFI_LINES
wifi_usb=0; wifi_pci=0; good_ap=0
for phy in /sys/class/ieee80211/phy*; do
  [[ -e "$phy" ]] || continue
  name="$(basename "$phy")"
  drv="$(basename "$(readlink -f "$phy/device/driver" 2>/dev/null)" 2>/dev/null || echo '?')"
  devlink="$(readlink -f "$phy/device" 2>/dev/null || echo '')"
  if [[ "$devlink" == *usb* ]]; then bus="USB"; wifi_usb=$(( wifi_usb + 1 ));
  elif [[ "$devlink" == *pci* ]]; then bus="PCIe"; wifi_pci=$(( wifi_pci + 1 ));
  else bus="?"; fi
  # AP/mesh suitability by driver family
  case "$drv" in
    ath9k*|ath10k*|mt76*|mt7601*|carl9170) suit="strong (AP/mesh/ad-hoc)"; good_ap=$(( good_ap + 1 )) ;;
    rtw*|rtl8*|8188*|8812*|8821*|r8188eu)   suit="limited (Realtek — AP/mesh flaky)" ;;
    iwlwifi)                                suit="ok STA; AP limited" ;;
    brcm*)                                  suit="varies" ;;
    *)                                      suit="unknown" ;;
  esac
  WIFI_LINES+=("${name}|${bus}|${drv}|${suit}")
done

# ---- Capacity estimate -----------------------------------------------------
# Kernel is not the limiter; estimate practical headroom from free root-hub ports.
free_ports=$(( usb_total_ports - usb_used )); (( free_ports < 0 )) && free_ports=0
# conservative practical count for TX-heavy radios without a powered hub
if   (( free_ports >= 6 )); then est="4-6+ (with a powered hub); ~2-3 safely on bare ports"
elif (( free_ports >= 3 )); then est="2-3 on bare ports; more via a powered hub"
else est="1-2 on bare ports; add a powered hub to scale"; fi

if [[ "$JSON" == 1 ]]; then
  printf '{'
  printf '"host":"%s",' "$(hostname)"
  printf '"usb":{"controllers":"%s","root_hub_ports_total":%s,"usb3_ports":%s,"usb2_ports":%s,"in_use":%s,"free":%s},' \
    "$n_controllers" "$usb_total_ports" "$usb3_ports" "$usb2_ports" "$usb_used" "$free_ports"
  printf '"wifi":{"phys":%s,"pcie":%s,"usb":%s,"ap_capable":%s},' \
    "$(( wifi_pci + wifi_usb ))" "$wifi_pci" "$wifi_usb" "$good_ap"
  printf '"usb_wifi_headroom":"%s",' "$est"
  printf '"limiter":"power+shared-bus-bandwidth (not kernel); powered hub is the enabler",'
  printf '"iw_installed":%s' "$(command -v iw >/dev/null && echo true || echo false)"
  printf '}\n'
  exit 0
fi

echo -e "${B}Mesh-expansion capacity — $(hostname)${N}"
echo -e "${C}USB${N}  (controllers: ${n_controllers})"
for l in "${HUB_LINES[@]}"; do IFS='|' read -r h s p pr <<<"$l"
  echo -e "   • ${h}: ${s}Mbps, ${p} downstream ports  ${pr:+(${pr})}"; done
echo -e "   root-hub ports: ${B}${usb_total_ports}${N} total (${usb3_ports} USB3 / ${usb2_ports} USB2), ~${usb_used} in use, ~${free_ports} free"
echo -e "   ${Y}note:${N} chassis exposes fewer than root-hub totals; expand with a (powered) hub"
echo -e "${C}Wi-Fi radios${N}"
for l in "${WIFI_LINES[@]}"; do IFS='|' read -r nm bs dv su <<<"$l"
  col="$G"; [[ "$su" == limited* || "$su" == unknown ]] && col="$Y"
  echo -e "   • ${nm}: ${bs}, driver=${dv} — ${col}${su}${N}"; done
[[ ${#WIFI_LINES[@]} -eq 0 ]] && echo -e "   ${Y}(no wireless phys detected)${N}"
echo -e "${C}USB-Wi-Fi headroom${N}"
echo -e "   estimate: ${B}${est}${N}"
echo -e "   ${Y}real limiters:${N} power (~500mA/USB2, 900mA/USB3 per port) & shared bus bandwidth —"
echo -e "                 NOT the kernel (127 devs/bus, mac80211 handles many). A powered"
echo -e "                 USB hub is the key enabler; spread high-throughput radios across USB3 buses."
echo -e "   ${Y}chipset:${N} prefer ath9k_htc / MediaTek mt76 USB (strong AP/mesh); avoid Realtek USB for AP."
command -v iw >/dev/null || echo -e "   ${Y}tip:${N} 'sudo apt install iw', then 'iw list' shows a chosen adapter's AP/STA combos."
