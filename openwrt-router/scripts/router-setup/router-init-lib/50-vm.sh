#!/usr/bin/env bash
# BEGIN: 50-vm.sh
if [[ -n "${_VM_SH_SOURCED:-}" ]]; then return 0; fi; _VM_SH_SOURCED=1

# Source template engine if not already loaded
if [[ -z "${_TEMPLATE_ENGINE_SH:-}" ]]; then
    LIB_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../lib" && pwd)"
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
  log_info "Configuring VM with NO network interfaces (dynamic assignment)..."

  local ROUTER_CONFIG_DIR="/etc/isle-mesh/router"
  mkdir -p "$ROUTER_CONFIG_DIR"

  local XML_FILE="$ROUTER_CONFIG_DIR/${VM_NAME}.xml"
  local IMAGE_PATH="$IMAGE_DIR/openwrt-isle-router.qcow2"

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
    virsh start "$VM_NAME" || { log_error "Failed to start VM"; exit 1; }
    log_success "VM started"
    log_info "Waiting for OpenWRT to boot (30 seconds)..."
    sleep 30
    log_success "OpenWRT should now be booted"
  else
    log_info "VM created but not started (--no-start specified)"
    log_info "Start with: sudo virsh start ${VM_NAME}"
  fi
}
# END: 50-vm.sh
