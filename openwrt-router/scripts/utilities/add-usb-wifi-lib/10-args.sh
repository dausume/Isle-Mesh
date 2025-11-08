#!/usr/bin/env bash
if [[ -n "${_ARGS2_SH:-}" ]]; then return; fi; _ARGS2_SH=1
parse_args(){
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --usb-path) USB_PATH="$2"; shift 2 ;;     # devpath like 1-2.4
      --ssid) SSID_DEFAULT="$2"; shift 2 ;;
      --psk)  WIFI_PSK_DEFAULT="$2"; shift 2 ;;
      --openwrt-host) OPENWRT_HOST="$2"; OPENWRT_SSH="root@$2"; shift 2 ;;
      --no-ssh) OPENWRT_HOST=""; shift ;;
      -h|--help)
        cat <<EOF
Usage: add-usb-wifi.sh [--usb-path 1-3.2] [--ssid myAP] [--psk secret] [--openwrt-host 192.168.50.1]
If --usb-path omitted, you'll select from detected Wi-Fi dongles. If --openwrt-host is provided and reachable,
we will check/create a wireless AP via UCI automatically. Otherwise we stage a config file for later.
EOF
        exit 0;;
      *) err "Unknown arg: $1"; exit 1 ;;
    esac
  done
}
