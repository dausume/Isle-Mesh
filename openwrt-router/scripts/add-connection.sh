#!/usr/bin/env bash
# add-connection.sh — dispatcher for `isle router add-connection`.
#
# Adds a physical connection to the isle router. Wires the CLI verb (router.sh
# cmd_add_connection) to the concrete flows under utilities/. Defaults to Ethernet
# (the isle cable); `--usb-wifi` routes to the USB-wifi flow instead.
#
#   isle router add-connection [--iface ethX] [--role lan|wan] [--isle my-isle] [--vlan 10]
#   isle router add-connection --usb-wifi [ ...usb-wifi args... ]
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
UTIL="$SCRIPT_DIR/utilities"

TYPE="ethernet"
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --type)              TYPE="$2"; shift 2 ;;
    --ethernet|--eth)    TYPE="ethernet"; shift ;;
    --usb-wifi|--wifi)   TYPE="usb-wifi"; shift ;;
    *)                   ARGS+=("$1"); shift ;;
  esac
done

case "$TYPE" in
  ethernet|eth)
    exec bash "$UTIL/add-ethernet-connection.main.sh" ${ARGS[@]+"${ARGS[@]}"} ;;
  usb-wifi|wifi)
    exec bash "$UTIL/add-usb-wifi.main.sh" ${ARGS[@]+"${ARGS[@]}"} ;;
  *)
    echo "Unknown connection type: $TYPE (use: ethernet | usb-wifi)" >&2; exit 1 ;;
esac
