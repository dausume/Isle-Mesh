#!/usr/bin/env bash
if [[ -n "${_PKG_SH:-}" ]]; then return; fi; _PKG_SH=1

update_packages(){
  # Skip opkg update on isolated VMs - packages will be installed offline
  info "Skipping opkg update (offline installation mode)"
}

install_required_packages(){
  info "Installing required packages (offline mode)…"

  # Note: kmod-8021q (VLAN support) is built into the kernel on x86/64

  # Define packages directory relative to script
  local SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
  local PACKAGES_DIR="${PACKAGES_DIR:-$SCRIPT_DIR/packages}"

  # Check if packages exist
  if [[ ! -d "$PACKAGES_DIR" ]] || [[ -z "$(ls -A "$PACKAGES_DIR"/*.ipk 2>/dev/null)" ]]; then
    warn "No packages found in $PACKAGES_DIR"
    warn "Run: $SCRIPT_DIR/download-packages.sh to download packages first"
    echo -n "Continue without installing packages? (y/N) "
    read -r ans
    [[ "$ans" =~ ^[Yy]$ ]] || exit 1
    return 0
  fi

  # Create temporary directory on router
  local REMOTE_TMP="/tmp/isle-packages"
  exec_ssh "mkdir -p $REMOTE_TMP"

  # Transfer packages to router
  info "Transferring packages to router…"
  local pkg_count=$(ls -1 "$PACKAGES_DIR"/*.ipk 2>/dev/null | wc -l)
  info "Found $pkg_count package(s) to transfer"

  scp $SSH_OPTS "$PACKAGES_DIR"/*.ipk "${OPENWRT_USER}@${OPENWRT_IP}:${REMOTE_TMP}/" || {
    err "Failed to transfer packages"
    exit 1
  }
  ok "Packages transferred"

  # Install packages on router
  info "Installing packages on router…"
  exec_ssh "cd $REMOTE_TMP && opkg install *.ipk || true"

  # Cleanup remote temporary directory on router
  exec_ssh "rm -rf $REMOTE_TMP"

  # Cleanup local packages directory to save disk space
  info "Cleaning up local package cache…"
  rm -rf "$PACKAGES_DIR"
  ok "Package installation complete (local cache cleaned)"
}
