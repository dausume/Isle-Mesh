#!/usr/bin/env bash
# BEGIN: 50-vm.sh
if [[ -n "${_VM_SH_SOURCED:-}" ]]; then return 0; fi; _VM_SH_SOURCED=1

# Source template engine if not already loaded.
# This part runs from TWO homes: standalone at
# router-setup/router-init-lib/ (lib = ../../lib) and INLINED into the
# packed scripts/router-init.sh (lib = ./lib, a sibling subdir) — the
# 2026-08-19 fresh-box install died on the second shape. Try both.
if [[ -z "${_TEMPLATE_ENGINE_SH:-}" ]]; then
    _VM_HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
    if [[ -f "$_VM_HERE/lib/template-engine.sh" ]]; then
        LIB_DIR="$_VM_HERE/lib"
    else
        LIB_DIR="$(cd -- "$_VM_HERE/../../lib" && pwd)"
    fi
    source "$LIB_DIR/template-engine.sh"
fi

check_existing_vm() {
  if virsh list --all | grep -q "^.*\\b${VM_NAME}\\b"; then
    log_error "VM '${VM_NAME}' already exists!"
    log_info "Options:"
    log_info "  1. Use a different name: --vm-name other-name"
    log_info "  2. Destroy existing VM: virsh destroy ${VM_NAME} && virsh undefine ${VM_NAME}"
    exit 1
  fi
}

create_vm_xml() {
  log_step "Step 4: Creating VM Configuration"
  log_info "Configuring VM with br-mgmt (eth0) and isle-br-0 (eth1)..."

  local ROUTER_CONFIG_DIR="/etc/isle-mesh/router"
  mkdir -p "$ROUTER_CONFIG_DIR"

  local XML_FILE="$ROUTER_CONFIG_DIR/${VM_NAME}.xml"

  # Stage the router disk into the STANDARD libvirt images dir and run the VM from there.
  # The VM must NOT run from the install tree: virt-aa-helper (AppArmor) cannot read a disk
  # under /usr/share (the .deb bundle) or an arbitrary checkout path, so the per-VM AppArmor
  # profile fails to load and the VM won't start. Staging here makes the app (.deb) and cli
  # (checkout) installs behave IDENTICALLY — both run from /var/lib/libvirt/images.
  local TEMPLATE_IMAGE="$IMAGE_DIR/openwrt-isle-router.qcow2"
  local RUNTIME_DIR="/var/lib/libvirt/images"
  local IMAGE_PATH="$RUNTIME_DIR/${VM_NAME}.qcow2"
  mkdir -p "$RUNTIME_DIR"
  if [[ ! -f "$IMAGE_PATH" ]]; then
    log_info "Staging router image -> $IMAGE_PATH (writable, AppArmor-readable)"
    cp "$TEMPLATE_IMAGE" "$IMAGE_PATH" || { log_error "Failed to stage router image from $TEMPLATE_IMAGE"; exit 1; }
  fi
  chown libvirt-qemu:kvm "$IMAGE_PATH" 2>/dev/null || true
  chmod 660 "$IMAGE_PATH" 2>/dev/null || true

  # Use template engine to generate VM XML
  local vm_template
  vm_template=$(get_template "libvirt/base-vm.xml")

  apply_template "$vm_template" "$XML_FILE" \
    "VM_NAME=${VM_NAME}" \
    "MEMORY=${MEMORY}" \
    "VCPUS=${VCPUS}" \
    "IMAGE_PATH=${IMAGE_PATH}"

  log_success "VM configuration created: $XML_FILE"
  echo "$XML_FILE"
}

create_vm() {
  local XML_FILE="$1"
  log_step "Step 5: Creating VM"
  log_info "Defining VM '${VM_NAME}'..."
  virsh define "$XML_FILE" || { log_error "Failed to define VM"; exit 1; }
  log_success "VM '${VM_NAME}' created successfully"

  if [[ "${NO_START}" == "false" ]]; then
    log_info "Starting VM..."
    # Self-correcting start: on failure, remediate the two things that actually break a
    # router VM boot in normal use — a disk libvirt/AppArmor can't read (re-stage it into
    # the standard images dir) and a stale per-VM AppArmor profile (drop it, restart
    # libvirtd) — then retry once. Goal: the user never sees a raw libvirt error.
    if ! virsh start "$VM_NAME" 2>/dev/null; then
      log_warning "VM start failed — self-correcting (stage disk + clear stale AppArmor profile)..."
      local _rt="/var/lib/libvirt/images/${VM_NAME}.qcow2" _uuid
      [[ -f "$_rt" ]] || cp "$IMAGE_DIR/openwrt-isle-router.qcow2" "$_rt" 2>/dev/null || true
      chown libvirt-qemu:kvm "$_rt" 2>/dev/null || true; chmod 660 "$_rt" 2>/dev/null || true
      _uuid="$(virsh domuuid "$VM_NAME" 2>/dev/null)"
      [[ -n "$_uuid" ]] && rm -f "/etc/apparmor.d/libvirt/libvirt-${_uuid}"* 2>/dev/null || true
      systemctl restart libvirtd 2>/dev/null || true; sleep 2
      if virsh start "$VM_NAME" 2>/dev/null; then
        log_success "VM started (self-corrected)"
      else
        log_error "Failed to start VM after self-correction (see: journalctl -u libvirtd)"; exit 1
      fi
    else
      log_success "VM started"
    fi
    # always-available: bring the router up on libvirtd/boot, independent of boot-bringup
    virsh autostart "$VM_NAME" >/dev/null 2>&1 \
      && log_success "VM autostart enabled (always-available)" \
      || log_info "Could not set autostart (later: sudo virsh autostart ${VM_NAME})"
    log_info "Waiting for OpenWRT to boot (30 seconds)..."
    sleep 30
    log_success "OpenWRT should now be booted"
  else
    log_info "VM created but not started (--no-start specified)"
    log_info "Start with: sudo virsh start ${VM_NAME}"
  fi
}
# END: 50-vm.sh
