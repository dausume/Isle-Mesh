#!/usr/bin/env bash
# Read the installation mode rather than inferring it from connectivity.
router_offline() {
  [[ "$(cat /etc/polari/install-mode 2>/dev/null)" == offline ]]
}

router_offline_source() {
  local medium
  medium=$(cat /etc/polari/offline-source 2>/dev/null) || return 1
  [[ -f "$medium/ISLE_OFFLINE_BUNDLE" && -f "$medium/manifest.json" ]] || {
    log_error "offline router/: reconnect the complete installation medium"; return 1;
  }
  printf '%s\n' "$medium"
}

router_packages_dir() {
  if router_offline; then
    local medium
    medium=$(router_offline_source) || return 1
    printf '%s/router/packages\n' "$medium"
  else
    # The packed entrypoint lives at scripts/, the development libraries
    # live at scripts/router-setup/router-init-lib/.
    if [[ "${BUNDLED_MODE:-0}" == 1 ]]; then
      printf '%s/../packages\n' "$PROJECT_ROOT"
    else
      printf '%s/../packages\n' "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
    fi
  fi
}
