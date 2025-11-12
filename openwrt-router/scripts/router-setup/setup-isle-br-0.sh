#!/bin/bash

#############################################################################
# Setup isle-br-0 Bridge for Local Isle Agent
#
# Creates the isle-br-0 bridge on the host and attaches it to the OpenWRT
# VM as eth1. This allows the local isle-agent container to communicate
# with the router.
#############################################################################

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

VM_NAME="${VM_NAME:-openwrt-isle-router}"

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Check if running as root
if [[ $EUID -ne 0 ]]; then
    log_error "This script must be run as root (use sudo)"
    exit 1
fi

log_info "Setting up isle-br-0 bridge..."

# 1. Create isle-br-0 bridge
if ip link show isle-br-0 &> /dev/null; then
    log_info "Bridge isle-br-0 already exists"
else
    log_info "Creating bridge isle-br-0"
    ip link add isle-br-0 type bridge || {
        log_error "Failed to create bridge isle-br-0"
        exit 1
    }
    log_success "Created bridge isle-br-0"
fi

# Bring up the bridge
if ! ip link set isle-br-0 up; then
    log_error "Failed to bring up bridge isle-br-0"
    exit 1
fi

log_success "Bridge isle-br-0 is UP"

# 2. Check if VM exists
if ! virsh dominfo "$VM_NAME" &> /dev/null; then
    log_error "VM $VM_NAME does not exist"
    log_info "Please create the VM first with: sudo isle router init"
    exit 1
fi

# 3. Check if isle-br-0 is already attached to VM
if virsh dumpxml "$VM_NAME" | grep -q "isle-br-0"; then
    log_info "isle-br-0 already attached to VM $VM_NAME"
else
    log_info "Attaching isle-br-0 to VM as eth1..."

    # Create temporary XML for network interface
    INTERFACE_XML="/tmp/isle-br-0-interface.xml"
    cat > "$INTERFACE_XML" << 'EOF'
<interface type='bridge'>
  <source bridge='isle-br-0'/>
  <model type='virtio'/>
</interface>
EOF

    # Attach interface to VM (config only, requires reboot)
    if virsh attach-device "$VM_NAME" "$INTERFACE_XML" --config; then
        log_success "isle-br-0 attached to VM (persistent config)"

        # If VM is running, also attach to live config
        if virsh domstate "$VM_NAME" 2>/dev/null | grep -q "running"; then
            log_info "VM is running, attempting live attach..."
            if virsh attach-device "$VM_NAME" "$INTERFACE_XML" --live 2>/dev/null; then
                log_success "isle-br-0 attached to running VM"
            else
                log_warning "Live attach failed - VM reboot required"
                NEED_REBOOT=1
            fi
        fi
    else
        log_error "Failed to attach isle-br-0 to VM"
        rm -f "$INTERFACE_XML"
        exit 1
    fi

    rm -f "$INTERFACE_XML"
fi

# 4. Verify interface count
IFACE_COUNT=$(virsh dumpxml "$VM_NAME" | grep -c "interface type" || echo 0)
log_info "VM has $IFACE_COUNT network interface(s)"

if [[ $IFACE_COUNT -lt 2 ]]; then
    log_warning "VM should have 2 interfaces (br-mgmt + isle-br-0)"
    log_info "Current count: $IFACE_COUNT"
fi

# 5. Reboot VM if needed
if [[ -n "${NEED_REBOOT:-}" ]] || [[ $IFACE_COUNT -lt 2 ]]; then
    log_info "VM needs to be rebooted to detect new interface"

    if virsh domstate "$VM_NAME" 2>/dev/null | grep -q "running"; then
        read -p "Reboot VM now? (y/N) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            log_info "Rebooting VM..."
            virsh reboot "$VM_NAME" || {
                log_warning "Reboot failed, trying shutdown and start..."
                virsh shutdown "$VM_NAME" --mode acpi
                sleep 5
                virsh start "$VM_NAME"
            }

            log_info "Waiting for VM to boot (30 seconds)..."
            sleep 30

            # Test connectivity
            if ping -c 1 -W 2 192.168.1.1 &> /dev/null; then
                log_success "VM is reachable at 192.168.1.1"
            else
                log_warning "Cannot ping 192.168.1.1 yet"
            fi
        else
            log_info "Skipping reboot - please reboot VM manually later"
        fi
    fi
fi

# 6. Display summary
cat << EOF

${GREEN}╔═══════════════════════════════════════════════════════════════════════════╗
║                    isle-br-0 Setup Complete                               ║
╚═══════════════════════════════════════════════════════════════════════════╝${NC}

${BLUE}Bridge Status:${NC}
$(ip link show isle-br-0)

${BLUE}VM Network Interfaces:${NC}
$(virsh dumpxml "$VM_NAME" | grep -A 3 "interface type" | grep "source bridge" | awk -F"'" '{print "  - " $2}')

${BLUE}Next Steps:${NC}
  1. Verify eth1 exists in OpenWRT: ssh root@192.168.1.1 'ip link show'
  2. Configure OpenWRT network on eth1.10
  3. Update isle-agent docker-compose to use macvlan on isle-br-0

${BLUE}Verification:${NC}
  Check bridges:    ip link show type bridge
  Check VM config:  virsh dumpxml $VM_NAME

EOF

log_success "isle-br-0 setup complete!"
