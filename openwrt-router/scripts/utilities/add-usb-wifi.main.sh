#!/usr/bin/env bash
# add-usb-wifi.main.sh — main script for adding USB Wi-Fi to OpenWRT VM
# -----------------------------------------------------------------------------
set -euo pipefail
BUNDLED_MODE="${BUNDLED_MODE:-0}"
if [[ "$BUNDLED_MODE" != "1" ]]; then
  SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  for f in "$SCRIPT_DIR"/add-usb-wifi-lib/[0-9][0-9]-*.sh; do source "$f"; done
fi

main(){
  banner "Add USB Wi-Fi to OpenWRT"
  require_root
  parse_args "$@"
  assert_router_exists
  pick_or_validate_usb
  attach_usb_to_vm
  maybe_configure_wireless
  finish_msg_wifi
}
trap 'cleanup_tmp' EXIT
if [[ "${BASH_SOURCE[0]:-}" == "$0" ]]; then main "$@"; fi
# END: add-usb-wifi.main.sh