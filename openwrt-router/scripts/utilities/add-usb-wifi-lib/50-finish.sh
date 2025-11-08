#!/usr/bin/env bash
if [[ -n "${_FIN2_SH:-}" ]]; then return; fi; _FIN2_SH=1
finish_msg_wifi(){
  ok "USB Wi-Fi attached to VM."
  echo "If AP was not auto-configured, use the staged script under $STATE_DIR to configure SSID/PSK later."
}
