#!/usr/bin/env bash
if [[ -n "${_PKG_SH:-}" ]]; then return; fi; _PKG_SH=1

update_packages(){
  info "Updating OpenWRT package lists…"
  exec_ssh "opkg update" || { err "opkg update failed"; exit 1; }
  ok "Packages updated"
}

install_required_packages(){
  info "Installing required packages…"
  local PKGS="kmod-8021q avahi-daemon avahi-utils ip-full tcpdump"
  exec_ssh "opkg install $PKGS || true"
  ok "Package installation attempted (non-fatal failures tolerated)"
}
