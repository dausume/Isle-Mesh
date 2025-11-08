#!/usr/bin/env bash
# openwrt-router/scripts/add-ethernet-connection.main.sh
# add-ethernet-connection.main.sh — main script for adding Ethernet connection to OpenWRT VM
# -----------------------------------------------------------------------------
set -euo pipefail
BUNDLED_MODE="${BUNDLED_MODE:-0}"

if [[ "$BUNDLED_MODE" != "1" ]]; then
  SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  for f in "$SCRIPT_DIR"/add-ethernet-lib/[0-9][0-9]-*.sh; do source "$f"; done
fi

main() {
  banner "Add Physical Ethernet to OpenWRT"
  require_root
  parse_args "$@"
  assert_router_exists

  pick_or_validate_eth
  create_bridge_and_enslave
  attach_bridge_to_vm
  finish_msg_eth
}
trap 'cleanup_tmp' EXIT
if [[ "${BASH_SOURCE[0]:-}" == "$0" ]]; then main "$@"; fi
# End: openwrt-router/scripts/add-ethernet-connection.main.sh
