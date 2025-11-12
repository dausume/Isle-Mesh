#!/bin/bash

#############################################################################
# Bridge Setup for OpenWRT Router
#
# Creates and configures two bridges:
#   - br-mgmt: Management bridge for SSH access to OpenWRT router
#   - isle-br-0: Isle bridge for local isle-agent connectivity
#
# This provides isolated management access and allows the local isle-agent
# container to connect to the router via DHCP
#############################################################################

# Create and configure management bridge
setup_management_bridge() {
    log_step "Management Bridge Setup"

    # Check for required commands
    if ! command -v ip &> /dev/null; then
        log_error "Missing required command: ip"
        log_info "Install with: apt-get install iproute2"
        return 1
    fi

    # Create br-mgmt bridge if it doesn't exist
    if ip link show br-mgmt &> /dev/null; then
        log_info "Bridge br-mgmt already exists"
    else
        log_info "Creating bridge: br-mgmt (Management network)"

        if ! ip link add br-mgmt type bridge; then
            log_error "Failed to create bridge br-mgmt"
            return 1
        fi

        if ! ip link set br-mgmt up; then
            log_error "Failed to bring up bridge br-mgmt"
            return 1
        fi

        log_success "Created bridge: br-mgmt"
    fi

    # Assign IP address to management bridge
    if ip addr show br-mgmt | grep -q "192.168.1.254"; then
        log_info "IP address already assigned to br-mgmt"
    else
        log_info "Assigning IP 192.168.1.254/24 to br-mgmt"

        if ! ip addr add 192.168.1.254/24 dev br-mgmt 2>/dev/null; then
            log_warning "IP address may already be assigned"
        fi
    fi

    # Enable IP forwarding
    sysctl -w net.ipv4.ip_forward=1 > /dev/null 2>&1

    log_success "Management bridge configured (192.168.1.254/24)"
}

# Attach management bridge to VM
attach_management_bridge_to_vm() {
    log_info "Attaching br-mgmt to VM..."

    if [[ -z "$VM_NAME" ]]; then
        log_error "VM_NAME not set"
        return 1
    fi

    # Check if VM exists
    if ! virsh dominfo "$VM_NAME" &> /dev/null; then
        log_error "VM $VM_NAME does not exist"
        return 1
    fi

    # Check if management interface already exists
    if virsh dumpxml "$VM_NAME" | grep -q "br-mgmt"; then
        log_info "Management interface already attached to VM"
        return 0
    fi

    # Create temporary XML for network interface
    local INTERFACE_XML="/tmp/br-mgmt-interface.xml"
    cat > "$INTERFACE_XML" << 'EOF'
<interface type='bridge'>
  <source bridge='br-mgmt'/>
  <model type='virtio'/>
</interface>
EOF

    # Attach interface to VM
    if virsh attach-device "$VM_NAME" "$INTERFACE_XML" --config; then
        log_success "Management interface attached to VM"

        # If VM is running, attach to live config too
        if virsh domstate "$VM_NAME" | grep -q "running"; then
            virsh attach-device "$VM_NAME" "$INTERFACE_XML" --live &> /dev/null || true
            log_info "VM is running - reboot may be required for changes to take effect"
        fi
    else
        log_error "Failed to attach management interface to VM"
        rm -f "$INTERFACE_XML"
        return 1
    fi

    rm -f "$INTERFACE_XML"

    log_success "Management bridge attached to $VM_NAME"
}

# Create and configure isle-br-0 bridge for local agent
setup_isle_br_0() {
    log_step "Isle Bridge Setup (isle-br-0)"

    # Check for required commands
    if ! command -v ip &> /dev/null; then
        log_error "Missing required command: ip"
        log_info "Install with: apt-get install iproute2"
        return 1
    fi

    # Create isle-br-0 bridge if it doesn't exist
    if ip link show isle-br-0 &> /dev/null; then
        log_info "Bridge isle-br-0 already exists"
    else
        log_info "Creating bridge: isle-br-0 (Local isle agent connectivity)"

        if ! ip link add isle-br-0 type bridge; then
            log_error "Failed to create bridge isle-br-0"
            return 1
        fi

        if ! ip link set isle-br-0 up; then
            log_error "Failed to bring up bridge isle-br-0"
            return 1
        fi

        log_success "Created bridge: isle-br-0"
    fi

    # No IP address needed - OpenWRT will be the gateway
    # Docker macvlan will attach to this bridge

    log_success "Isle bridge configured (no IP - router will handle DHCP)"
}

# Attach isle-br-0 to VM as eth1
attach_isle_br_0_to_vm() {
    log_info "Attaching isle-br-0 to VM as eth1..."

    if [[ -z "$VM_NAME" ]]; then
        log_error "VM_NAME not set"
        return 1
    fi

    # Check if VM exists
    if ! virsh dominfo "$VM_NAME" &> /dev/null; then
        log_error "VM $VM_NAME does not exist"
        return 1
    fi

    # Check if isle-br-0 is already attached to VM
    if virsh dumpxml "$VM_NAME" | grep -q "isle-br-0"; then
        log_info "isle-br-0 already attached to VM"
        return 0
    fi

    # Create temporary XML for network interface
    local INTERFACE_XML="/tmp/isle-br-0-interface.xml"
    cat > "$INTERFACE_XML" << 'EOF'
<interface type='bridge'>
  <source bridge='isle-br-0'/>
  <model type='virtio'/>
</interface>
EOF

    # Attach interface to VM
    if virsh attach-device "$VM_NAME" "$INTERFACE_XML" --config; then
        log_success "isle-br-0 attached to VM (will be eth1)"

        # If VM is running, attach to live config too
        if virsh domstate "$VM_NAME" | grep -q "running"; then
            virsh attach-device "$VM_NAME" "$INTERFACE_XML" --live &> /dev/null || true
            log_info "VM is running - reboot may be required for eth1 to appear"
        fi
    else
        log_error "Failed to attach isle-br-0 to VM"
        rm -f "$INTERFACE_XML"
        return 1
    fi

    rm -f "$INTERFACE_XML"

    log_success "Isle bridge attached to $VM_NAME"
}

# Create bridges before VM creation (called early in init process)
create_bridges() {
    setup_management_bridge || return 1
    setup_isle_br_0 || return 1
    log_success "Both bridges created (br-mgmt and isle-br-0)"
}

# Verify bridges after VM creation (interfaces are already in VM template)
run_bridge_setup() {
    log_step "Verifying Network Configuration"

    # Verify bridges exist
    if ! ip link show br-mgmt &> /dev/null; then
        log_error "br-mgmt bridge not found - should have been created earlier"
        return 1
    fi

    if ! ip link show isle-br-0 &> /dev/null; then
        log_error "isle-br-0 bridge not found - should have been created earlier"
        return 1
    fi

    # Verify VM has both interfaces (they're in the template)
    local IFACE_COUNT
    IFACE_COUNT=$(virsh dumpxml "$VM_NAME" | grep -c "interface type" || echo 0)

    if [[ $IFACE_COUNT -lt 2 ]]; then
        log_error "VM should have 2 network interfaces, found: $IFACE_COUNT"
        log_info "This indicates the VM template may not have been applied correctly"
        return 1
    fi

    log_success "VM has $IFACE_COUNT network interfaces (br-mgmt + isle-br-0)"

    # Reboot the VM so OpenWRT can detect and configure both interfaces
    if virsh domstate "$VM_NAME" | grep -q "running"; then
        log_info "Rebooting VM to apply network configuration..."
        virsh reboot "$VM_NAME" &> /dev/null || {
            log_warning "Failed to reboot, trying shutdown and start..."
            virsh shutdown "$VM_NAME" --mode acpi &> /dev/null
            sleep 5
            virsh start "$VM_NAME" &> /dev/null || {
                log_error "Failed to restart VM"
                return 1
            }
        }
    fi

    log_info "Waiting for OpenWRT to boot and configure network (30 seconds)..."
    sleep 30

    # Verify connectivity
    if ping -c 1 -W 2 192.168.1.1 &> /dev/null; then
        log_success "OpenWRT is reachable at 192.168.1.1"
    else
        log_warning "Cannot ping 192.168.1.1 yet. You may need to wait longer or check VM console."
        log_info "Try: sudo virsh console $VM_NAME"
    fi

    return 0
}
