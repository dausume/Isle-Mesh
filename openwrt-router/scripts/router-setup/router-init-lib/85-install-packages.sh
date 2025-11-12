#!/usr/bin/env bash
# BEGIN: 85-install-packages.sh
if [[ -n "${_INSTALL_PACKAGES_SH_SOURCED:-}" ]]; then return 0; fi; _INSTALL_PACKAGES_SH_SOURCED=1

install_and_configure_packages() {
  log_step "Step 9: Installing and Configuring Packages on Router"

  # Router connection details
  local router_ip="${ROUTER_IP:-192.168.1.1}"
  local router_user="${ROUTER_USER:-root}"
  local router_dest="${ROUTER_PACKAGE_DIR:-/tmp/packages}"

  log_info "Router IP: $router_ip"

  # Check SSH connection
  if ! ssh -o ConnectTimeout=2 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
          "${router_user}@${router_ip}" "exit" 2>/dev/null; then
    log_error "Cannot connect to router via SSH"
    exit 1
  fi

  # Install packages
  log_info "Installing packages from ${router_dest}..."
  if ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
         "${router_user}@${router_ip}" \
         "opkg install ${router_dest}/*.ipk" 2>/dev/null; then
    log_success "Packages installed successfully"
  else
    log_warning "Some packages may have failed to install (this is often OK if already installed)"
  fi

  # Configure and start avahi-daemon
  log_info "Configuring avahi-daemon..."

  # Enable and start avahi-daemon service
  ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      "${router_user}@${router_ip}" \
      "uci set avahi.@avahi[0].enable_reflector='1' && \
       uci set avahi.@avahi[0].enable_dbus='yes' && \
       uci commit avahi && \
       /etc/init.d/avahi-daemon enable && \
       /etc/init.d/avahi-daemon start" 2>/dev/null

  if [[ $? -eq 0 ]]; then
    log_success "avahi-daemon configured and started"
  else
    log_warning "Failed to configure avahi-daemon (may need manual configuration)"
  fi

  # Enable dbus (required for avahi)
  log_info "Enabling dbus service..."
  ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      "${router_user}@${router_ip}" \
      "/etc/init.d/dbus enable && /etc/init.d/dbus start" 2>/dev/null

  if [[ $? -eq 0 ]]; then
    log_success "dbus service enabled and started"
  else
    log_warning "Failed to start dbus service"
  fi

  # Verify services are running
  log_info "Verifying services..."
  local avahi_status=$(ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                          "${router_user}@${router_ip}" \
                          "/etc/init.d/avahi-daemon status" 2>/dev/null)

  if echo "$avahi_status" | grep -q "running"; then
    log_success "avahi-daemon is running"
  else
    log_warning "avahi-daemon may not be running properly"
  fi

  # Verify installed packages
  log_info "Verifying installed packages..."
  local installed_pkgs=$(ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                            "${router_user}@${router_ip}" \
                            "opkg list-installed | grep -E '(avahi|ip-full|tcpdump)' | wc -l" 2>/dev/null)

  log_info "Verified $installed_pkgs package(s) installed"

  log_success "Package installation and configuration complete"
}
# END: 85-install-packages.sh
