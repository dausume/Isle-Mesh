#!/usr/bin/env bash
# BEGIN: 85-install-packages.sh
if [[ -n "${_INSTALL_PACKAGES_SH_SOURCED:-}" ]]; then return 0; fi; _INSTALL_PACKAGES_SH_SOURCED=1

install_and_configure_packages() {
  log_step "Step 9: Installing and Configuring Packages on Router"

  local router_dest="${ROUTER_PACKAGE_DIR:-/tmp/packages}"

  # Check SSH connection
  if ! router_ssh_test; then
    log_error "Cannot connect to router via SSH"
    exit 1
  fi

  # Install packages
  log_info "Installing packages from ${router_dest}..."
  if router_ssh "opkg install ${router_dest}/*.ipk" 2>/dev/null; then
    log_success "Packages installed successfully"
  else
    log_warning "Some packages may have failed to install (this is often OK if already installed)"
  fi

  # Enable dbus FIRST (avahi requires it), then avahi. Both steps are
  # deliberately non-fatal — and must be WRITTEN that way: the packed
  # router-init.sh runs under `set -e`, so the old bare-command-then-
  # check-$? shape died silently on the first failure (the 2026-08-19
  # fresh-box run: this OpenWrt image's avahi has NO UCI section, so
  # `uci set avahi.@avahi[0]` was an instant fatal). Every remote step
  # sits in an if-condition, and the UCI path falls back to the conf
  # file avahi actually ships with.
  log_info "Enabling dbus service..."
  if router_ssh "/etc/init.d/dbus enable && /etc/init.d/dbus start" 2>/dev/null; then
    log_success "dbus service enabled and started"
  else
    log_warning "Failed to start dbus service"
  fi

  log_info "Configuring avahi-daemon..."
  if router_ssh \
      "uci -q set avahi.@avahi[0].enable_reflector='1' && \
       uci -q set avahi.@avahi[0].enable_dbus='yes' && \
       uci -q commit avahi" 2>/dev/null; then
    log_success "avahi configured via UCI"
  elif router_ssh \
      "[ -f /etc/avahi/avahi-daemon.conf ] && \
       sed -i 's/^#*enable-reflector=.*/enable-reflector=yes/' /etc/avahi/avahi-daemon.conf" 2>/dev/null; then
    log_success "avahi configured via /etc/avahi/avahi-daemon.conf (no UCI section on this image)"
  else
    log_warning "Failed to configure avahi-daemon (may need manual configuration)"
  fi
  if router_ssh "/etc/init.d/avahi-daemon enable && /etc/init.d/avahi-daemon restart" 2>/dev/null; then
    log_success "avahi-daemon enabled and started"
  else
    log_warning "avahi-daemon did not start (mDNS reflection degraded, not fatal)"
  fi

  # Verify services are running
  log_info "Verifying services..."
  local avahi_status
  avahi_status=$(router_ssh "/etc/init.d/avahi-daemon status" 2>/dev/null) || true

  if echo "$avahi_status" | grep -q "running"; then
    log_success "avahi-daemon is running"
  else
    log_warning "avahi-daemon may not be running properly"
  fi

  # Verify installed packages
  log_info "Verifying installed packages..."
  local installed_pkgs
  installed_pkgs=$(router_ssh "opkg list-installed | grep -cE '(avahi|ip-full|tcpdump)'" 2>/dev/null) || installed_pkgs=0

  log_info "Verified $installed_pkgs package(s) installed"

  log_success "Package installation and configuration complete"
}
# END: 85-install-packages.sh
