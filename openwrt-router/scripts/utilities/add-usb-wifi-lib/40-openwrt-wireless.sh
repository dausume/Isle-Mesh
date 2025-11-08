#!/usr/bin/env bash
if [[ -n "${_OWRTW_SH:-}" ]]; then return; fi; _OWRTW_SH=1
have_ssh(){ [[ -n "$OPENWRT_HOST" ]] && ping -c1 -W1 "$OPENWRT_HOST" >/dev/null 2>&1 && ssh $SSH_OPTS "$OPENWRT_SSH" 'echo ok' 2>/dev/null | grep -q ok; }

create_or_bind_ap_via_ssh(){
  log "Checking wireless in OpenWRT via SSH…"
  # Find first radio (radio0) and existing iface count
  ssh $SSH_OPTS "$OPENWRT_SSH" '
set -e
RAD=$(uci -q show wireless | awk -F= "/config wifi-device/ {print \$1}" | head -n1 | awk -F. "{print \$2}")
[ -z "$RAD" ] && { echo "no-radio"; exit 0; }
IFCNT=$(uci -q show wireless | grep -c "config wifi-iface")
echo "radio:$RAD"
echo "ifcnt:$IFCNT"
' > /tmp/owrt-wchk.$$ || { warn "SSH check failed"; return 1; }

RADIO="$(awk -F: "/^radio:/{print \$2}" /tmp/owrt-wchk.$$)"
IFCNT="$(awk -F: "/^ifcnt:/{print \$2}" /tmp/owrt-wchk.$$)"
rm -f /tmp/owrt-wchk.$$
[[ "$RADIO" == "no-radio" || -z "$RADIO" ]] && { warn "No wireless radio detected yet; the driver may need a moment."; return 1; }

if [[ "$IFCNT" -gt 0 ]]; then
  log "Found existing wifi-iface(s). Will add/overwrite a dedicated AP section."
fi

ssh $SSH_OPTS "$OPENWRT_SSH" "sh -s" <<EOF
set -e
RAD="$RADIO"
NEW="\$(uci add wireless wifi-iface)"
uci set wireless.\$NEW.device="\$RAD"
uci set wireless.\$NEW.mode='ap'
uci set wireless.\$NEW.network='lan'
uci set wireless.\$NEW.ssid='${SSID_DEFAULT}'
uci set wireless.\$NEW.encryption='psk2'
uci set wireless.\$NEW.key='${WIFI_PSK_DEFAULT}'
uci commit wireless
wifi reload || /etc/init.d/network restart
echo "configured"
EOF
}

stage_local_wireless_script(){
  local f="$STATE_DIR/openwrt-wireless-${SSID_DEFAULT}.sh"
  cat >"$f"<<'EOS'
#!/bin/sh
# Run this inside OpenWRT (ssh root@<router> sh ./thisfile.sh)
set -e
RAD=$(uci -q show wireless | awk -F= '/config wifi-device/ {print $1}' | head -n1 | awk -F. '{print $2}')
[ -z "$RAD" ] && { echo "No radio found"; exit 1; }
NEW="$(uci add wireless wifi-iface)"
uci set wireless.$NEW.device="$RAD"
uci set wireless.$NEW.mode='ap'
uci set wireless.$NEW.network='lan'
uci set wireless.$NEW.ssid='__SSID__'
uci set wireless.$NEW.encryption='psk2'
uci set wireless.$NEW.key='__PSK__'
uci commit wireless
wifi reload || /etc/init.d/network restart
echo "AP configured."
EOS
  sed -i "s/__SSID__/${SSID_DEFAULT}/" "$f"
  sed -i "s/__PSK__/${WIFI_PSK_DEFAULT}/" "$f"
  chmod +x "$f"
  ok "Staged wireless config: $f"
  echo "Copy & run in OpenWRT:"
  echo "  scp $f root@<router>:/root/ && ssh root@<router> 'sh /root/$(basename "$f")'"
}

maybe_configure_wireless(){
  if have_ssh; then
    if create_or_bind_ap_via_ssh; then
      ok "Wireless AP configured on OpenWRT (SSID=${SSID_DEFAULT})"
    else
      warn "Could not configure via SSH; staging script for later."
      stage_local_wireless_script
    fi
  else
    warn "OpenWRT host not reachable or SSH disabled; staging script."
    stage_local_wireless_script
  fi
}
