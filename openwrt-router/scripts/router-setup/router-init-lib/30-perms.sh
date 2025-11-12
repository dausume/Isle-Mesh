#!/usr/bin/env bash
# BEGIN: 30-perms.sh
if [[ -n "${_PERMS_SH_SOURCED:-}" ]]; then return 0; fi; _PERMS_SH_SOURCED=1

setup_libvirt_permissions() {
  log_step "Step 2: Setting Up Libvirt Permissions"

  # Ensure IMAGE_DIR exists before setting ACLs
  mkdir -p "$IMAGE_DIR"

  local IMAGE_PATH="$IMAGE_DIR/openwrt-isle-router.qcow2"

  local LIBVIRT_USER="libvirt-qemu"
  if ! getent passwd "$LIBVIRT_USER" &> /dev/null; then
      if getent passwd "qemu" &> /dev/null; then
          LIBVIRT_USER="qemu"
      else
          log_error "Cannot find libvirt user (tried libvirt-qemu, qemu)"
          exit 1
      fi
  fi
  log_info "Using libvirt user: $LIBVIRT_USER"

  local CURRENT_PATH="$IMAGE_DIR"
  while [[ "$CURRENT_PATH" != "/" ]]; do
      if ! getfacl "$CURRENT_PATH" 2>/dev/null | grep -q "user:$LIBVIRT_USER"; then
          log_info "Granting traverse permission on $CURRENT_PATH"
          setfacl -m "u:$LIBVIRT_USER:x" "$CURRENT_PATH"
      fi
      CURRENT_PATH=$(dirname "$CURRENT_PATH")
  done

  if [[ -f "$IMAGE_PATH" ]]; then
      log_info "Granting read permission on $IMAGE_PATH"
      setfacl -m "u:$LIBVIRT_USER:r" "$IMAGE_PATH"
  fi

  log_success "Libvirt permissions configured using ACLs"
}
# END: 30-perms.sh
