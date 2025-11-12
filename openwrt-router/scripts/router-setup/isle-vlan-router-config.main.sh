#!/usr/bin/env bash
# Dev entrypoint: sources ./mdns-config-lib/*.sh. Bundled version sets BUNDLED_MODE=1.
set -euo pipefail
BUNDLED_MODE="${BUNDLED_MODE:-0}"

if [[ "$BUNDLED_MODE" != "1" ]]; then
  SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  for f in "$SCRIPT_DIR"/isle-vlan-router-config-lib/[0-9][0-9]-*.sh; do source "$f"; done
fi

main() {
  parse_args "$@"
  banner "OpenWRT Router mDNS Passthrough Configuration (single isle: $ISLE_NAME)"
  check_prerequisites_or_prompt
  maybe_set_root_password
  update_packages
  install_required_packages
  configure_single_isle_network
  configure_single_isle_firewall
  configure_mdns_reflector
  setup_ssh_keys_if_any
  show_summary_single_isle
  ok "OpenWRT configuration complete!"
}

if [[ "${BASH_SOURCE[0]:-}" == "$0" ]]; then
  main "$@"
fi
