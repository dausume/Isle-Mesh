#!/usr/bin/env bash
# router-init.main.sh — dev entrypoint (sources from router-init-lib/)
# The bundled version produced by pack.sh will NOT need to source anything.

set -euo pipefail

# Colors & logging may be used by main banner before libs load in bundle mode,
# so keep banner minimal here and leave colors to 00-log.sh
BUNDLED_MODE="${BUNDLED_MODE:-0}"

if [[ "$BUNDLED_MODE" != "1" ]]; then
  # Dev mode: source all libs in deterministic order
  SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  LIB_DIR="${SCRIPT_DIR}/router-init-lib"
  if [[ ! -d "$LIB_DIR" ]]; then
    echo "[ERR ] router-init-lib/ not found next to router-init.main.sh" >&2
    exit 1
  fi
  for f in "$LIB_DIR"/[0-9][0-9]-*.sh; do
    # shellcheck disable=SC1090
    source "$f"
  done
fi

# Runtime defaults (same as your current script)
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR" && pwd)"
VM_NAME="${VM_NAME:-openwrt-isle-router}"
MEMORY="${MEMORY:-512}"
VCPUS="${VCPUS:-2}"
IMAGE_DIR="${IMAGE_DIR:-$PROJECT_ROOT/images}"
NO_START=false
CUSTOM_IMAGE=""

main() {
  cat << EOF >&2
$(printf '%b' "${CYAN:-}")╔═══════════════════════════════════════════════════════════════╗
║       OpenWRT Router VM Creation (Step 1 of Isle Setup)      ║
║                                                                ║
║  • Creates OpenWRT VM                                         ║
║  • Configures br-mgmt (management) for SSH access             ║
║  • Configures isle-br-0 for local isle-agent connectivity     ║
║  • Isolated network: No external IP exposure                  ║
╚═══════════════════════════════════════════════════════════════╝$(printf '%b' "${NC:-}")
EOF

  parse_args "$@"
  check_root
  check_prerequisites
  check_existing_vm
  setup_libvirt_permissions
  download_image

  # Create bridges BEFORE VM creation (VM template references them)
  create_bridges

  local XML_FILE
  XML_FILE="$(create_vm_xml)"
  create_vm "$XML_FILE"

  # Verify network setup (interfaces already in VM template)
  run_bridge_setup

  download_openwrt_packages
  copy_packages_to_router
  install_and_configure_packages
  show_next_steps
  log_success "Initialization complete!"
}

if [[ "${BASH_SOURCE[0]:-}" == "$0" ]]; then
  main "$@"
fi
