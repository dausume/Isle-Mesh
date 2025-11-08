# Isle Mesh OpenWRT Router

OpenWRT-based mesh router with automatic remote nginx proxy discovery and integration.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│ Host Machine A                                              │
│  ┌────────────────────────────────────────┐                │
│  │ OpenWRT KVM Router                     │                │
│  │ - vLAN: 10.10.0.1/24                   │                │
│  │ - DHCP server for virtual MACs         │                │
│  │ - Discovery beacon (broadcasts UDP)    │                │
│  │ - mDNS reflector                       │                │
│  └────────────────────────────────────────┘                │
│           │ Physical interfaces (eth/wifi)                  │
└───────────┼─────────────────────────────────────────────────┘
            │
            │ Network Connection
            │
┌───────────┼─────────────────────────────────────────────────┐
│ Host Machine B (Remote)                     │               │
│  ┌────────────────────────────────────────┐│               │
│  │ isle-agent (listens for discovery)     ││               │
│  │ - Receives beacon from OpenWRT         ││               │
│  │ - Auto-creates bridge: br-isle10       ││               │
│  │ - Enslaves physical interface          ││               │
│  └────────────────────────────────────────┘│               │
│           │                                 │               │
│           ▼                                 │               │
│  ┌────────────────────────────────────────┐│               │
│  │ nginx proxy container                  ││               │
│  │ - Virtual MAC: 02:00:00:00:0a:XX       ││               │
│  │ - DHCP IP: 10.10.0.50 (from OpenWRT)  ││               │
│  │ - mDNS broadcasts with this IP         ││               │
│  └────────────────────────────────────────┘│               │
│                                             │               │
│  OpenWRT never sees Machine B's real IP!   │               │
└─────────────────────────────────────────────────────────────┘
```

**Key Feature**: Remote nginx containers join the mesh with virtual MAC addresses, getting DHCP IPs directly from OpenWRT. The host machine's real IP is never exposed to the mesh network.

## Directory Structure

```
openwrt-router/
├── scripts/
│   ├── lib/                          # Shared libraries (DRY principle)
│   │   ├── common-log.sh             # Unified logging functions
│   │   ├── common-utils.sh           # Shared utility functions
│   │   └── template-engine.sh        # Template variable substitution
│   │
│   ├── router-setup/                 # VM initialization & configuration
│   │   ├── router-init.main.sh       # Initialize OpenWRT VM
│   │   ├── router-init-lib/          # Init script modules
│   │   ├── isle-vlan-router-config.main.sh  # Configure vLAN
│   │   ├── isle-vlan-router-config-lib/     # Config modules
│   │   ├── configure-dhcp-vlan.sh    # DHCP for virtual MACs
│   │   ├── configure-discovery.sh    # Discovery beacon setup
│   │   └── ...
│   │
│   ├── utilities/                    # Connection management
│   │   ├── add-ethernet-connection.main.sh
│   │   ├── add-usb-wifi.main.sh
│   │   └── ...
│   │
│   ├── hotplug-handlers/            # Event-driven port detection
│   │   ├── install-port-detection.sh
│   │   └── ...
│   │
│   └── setup-isle-mesh-router.sh    # Master setup script
│
└── templates/                        # Configuration templates
    ├── libvirt/                      # VM XML templates
    │   ├── base-vm.xml               # Base VM definition
    │   ├── interface-bridge.xml      # Bridge interface snippet
    │   ├── interface-direct.xml      # Direct interface snippet
    │   └── usb-passthrough.xml       # USB passthrough snippet
    │
    └── openwrt/                      # OpenWRT configuration templates
        ├── uci/                      # UCI config scripts
        │   ├── network-vlan.uci      # vLAN network config
        │   ├── firewall-zone.uci     # Firewall zone config
        │   ├── dhcp-server.uci       # DHCP server config
        │   └── mdns-reflector.uci    # mDNS reflector config
        │
        ├── configs/                  # Configuration files
        │   └── avahi-daemon.conf     # Avahi mDNS config
        │
        └── scripts/                  # OpenWRT runtime scripts
            ├── isle-discovery-beacon.sh    # Discovery broadcaster
            └── isle-discovery-init.sh      # Service installer
```

## Quick Start

### Complete Setup (One Command)

```bash
cd openwrt-router/scripts
sudo ./setup-isle-mesh-router.sh --isle-name my-isle --vlan-id 10
```

This will:
1. Initialize OpenWRT VM
2. Configure vLAN networking
3. Setup DHCP server
4. Deploy discovery beacon
5. Enable mDNS reflection

### Step-by-Step Setup

#### 1. Initialize Router VM

```bash
cd openwrt-router/scripts/router-setup
sudo ./router-init.main.sh --vm-name openwrt-isle-router
```

#### 2. Configure vLAN and Network

```bash
sudo ./isle-vlan-router-config.main.sh \
    --isle-name my-isle \
    --vlan-id 10
```

#### 3. Configure DHCP for Virtual MACs

```bash
sudo ./configure-dhcp-vlan.sh \
    --isle-name my-isle \
    --vlan-id 10 \
    --dhcp-start 50 \
    --dhcp-limit 200
```

This enables OpenWRT to assign IPs to remote nginx containers with virtual MAC addresses.

#### 4. Deploy Discovery Beacon

```bash
sudo ./configure-discovery.sh \
    --isle-name my-isle \
    --vlan-id 10 \
    --interval 30
```

OpenWRT will broadcast discovery packets every 30 seconds:
```
ISLE_MESH_DISCOVERY|isle=my-isle|vlan=10|router=10.10.0.1|dhcp=10.10.0.0/24
```

#### 5. Add Physical Interfaces (Optional)

```bash
# Ethernet
sudo ../utilities/add-ethernet-connection.main.sh

# USB WiFi
sudo ../utilities/add-usb-wifi.main.sh
```

## How Discovery Works

### 1. OpenWRT Side (Already Implemented)

**Discovery Beacon** (`templates/openwrt/scripts/isle-discovery-beacon.sh`):
- Runs as OpenWRT service
- Broadcasts UDP packets every 30s
- Contains: Isle name, vLAN ID, router IP, DHCP range

**DHCP Server** configured to accept virtual MACs:
- Pattern: `02:00:00:00:VLAN_ID:XX`
- Assigns IPs from pool: `10.VLAN.0.50-250`
- No authentication needed (virtual MACs are ephemeral)

### 2. Remote Machine Side (To Be Implemented)

**isle-agent daemon** (not yet built):
- Listens on UDP port 7878
- Receives discovery broadcasts
- Auto-creates bridge on physical interface
- Connects nginx container with virtual MAC
- Container requests DHCP from OpenWRT

### 3. nginx Container Side (To Be Implemented)

Container must have labels:
```yaml
labels:
  - "isle-mesh.isle=my-isle"
  - "isle-mesh.proxy=true"
  - "isle-mesh.vlan=10"
```

When agent detects matching isle, it:
1. Creates `br-isle10` bridge
2. Enslaves physical interface
3. Generates virtual MAC: `02:00:00:00:0a:XX`
4. Connects container to bridge
5. Container gets DHCP IP from OpenWRT

## Shared Libraries

### common-log.sh

Unified logging functions used by all scripts:

```bash
source "$(dirname $0)/../lib/common-log.sh"

log_info "Information message"
log_success "Success message"
log_warning "Warning message"
log_error "Error message"
log_step "Major step header"
log_banner "Script title banner"
```

### common-utils.sh

Shared utility functions:

```bash
source "$(dirname $0)/../lib/common-utils.sh"

require_root                    # Exit if not root
require_commands ssh scp virsh  # Check commands exist
init_common_env                 # Initialize directories
vm_exists "vm-name"             # Check if VM exists
assert_vm_exists "vm-name"      # Exit if VM doesn't exist
is_port_reserved "eth0"         # Check if port is reserved
reserve_port "eth0"             # Reserve a port
get_isp_interface               # Get ISP interface name
```

### template-engine.sh

Simple variable substitution for templates:

```bash
source "$(dirname $0)/../lib/template-engine.sh"

# Apply template to file
apply_template \
    "$(get_template 'libvirt/base-vm.xml')" \
    "/etc/isle-mesh/router/vm.xml" \
    "VM_NAME=my-router" \
    "MEMORY=512" \
    "VCPUS=2"

# Render template to stdout
render_template \
    "$(get_template 'openwrt/uci/network-vlan.uci')" \
    "VLAN_ID=10" \
    "ISLE_IP=10.10.0.1"
```

## Templates

All configuration files are stored as templates with variable placeholders: `{{VARIABLE_NAME}}`

### Example: network-vlan.uci

```sh
# Create tagged subinterface for isle
uci set network.{{ISLE_UCI}}=interface
uci set network.{{ISLE_UCI}}.proto='static'
uci set network.{{ISLE_UCI}}.device='{{ISLE_IF_DEV}}'
uci set network.{{ISLE_UCI}}.ipaddr='{{ISLE_IP}}'
uci set network.{{ISLE_UCI}}.netmask='{{ISLE_NETMASK}}'
```

Variables are substituted by `template-engine.sh` at runtime.

## Virtual MAC Address Scheme

Remote nginx containers use deterministic virtual MACs:

```
02:00:00:00:VLAN_ID:XX
│  │  │  │     │      └─ Random per container
│  │  │  │     └──────── VLAN ID (hex)
│  │  │  └────────────── Reserved
│  └──└────────────────── Locally administered
└──────────────────────── Unicast
```

Example:
- vLAN 10 (0x0a): `02:00:00:00:0a:42`
- vLAN 20 (0x14): `02:00:00:00:14:7f`

OpenWRT DHCP server recognizes this pattern and assigns IPs.

## Testing

### Test Discovery Broadcasts

On any machine on the network:

```bash
# Listen for discovery packets
sudo nc -l -u 7878

# You should see:
# ISLE_MESH_DISCOVERY|isle=my-isle|vlan=10|router=10.10.0.1|dhcp=10.10.0.0/24
```

### Test DHCP Server

On OpenWRT:

```bash
# View DHCP leases
cat /tmp/dhcp.leases

# View DHCP configuration
uci show dhcp

# Monitor DHCP requests
logread -f | grep dnsmasq
```

### Test VM Status

```bash
# List VMs
sudo virsh list --all

# Access OpenWRT console
sudo virsh console openwrt-isle-router

# Check VM XML
sudo virsh dumpxml openwrt-isle-router
```

## Router Management

### Access OpenWRT

```bash
# Console (no network needed)
sudo virsh console openwrt-isle-router

# SSH (if configured)
ssh root@192.168.1.1
```

### Service Management on OpenWRT

```bash
# Discovery beacon
/etc/init.d/isle-discovery {start|stop|restart|status}

# Network
/etc/init.d/network restart

# DHCP
/etc/init.d/dnsmasq restart

# mDNS
/etc/init.d/avahi-daemon restart
```

### Logs

```bash
# On OpenWRT
logread -f

# Filter discovery logs
logread | grep isle-discovery

# Filter DHCP logs
logread | grep dnsmasq
```

## Troubleshooting

### Discovery Not Working

```bash
# On OpenWRT, check if beacon is running
ps | grep isle-discovery

# Check logs
logread | grep isle-discovery

# Manually test broadcast
echo "TEST" | nc -u -b 255.255.255.255 7878
```

### DHCP Not Assigning IPs

```bash
# On OpenWRT, check DHCP config
uci show dhcp

# Check if dnsmasq is running
ps | grep dnsmasq

# View DHCP logs
logread -f | grep dnsmasq

# Check leases
cat /tmp/dhcp.leases
```

### VM Not Starting

```bash
# Check VM status
sudo virsh list --all

# View VM logs
sudo virsh console openwrt-isle-router

# Check libvirt logs
sudo journalctl -u libvirtd -f
```

### SSH to OpenWRT Fails

```bash
# Set root password via console
sudo virsh console openwrt-isle-router
# At OpenWRT prompt:
passwd

# Or use isle-vlan-router-config.main.sh which sets it
```

## Next Steps

1. **Build remote isle-agent** (not yet implemented)
   - Daemon that listens for discovery
   - Auto-configures bridges
   - Connects nginx containers

2. **nginx container labeling** (not yet implemented)
   - Add isle labels to nginx proxies
   - Automatic detection by agent

3. **mDNS integration** (partially implemented)
   - Ensure nginx broadcasts mDNS with vLAN IP
   - Test mDNS reflection across mesh

4. **Physical interface management**
   - Consolidate add-ethernet and add-usb-wifi
   - Integrate with hotplug system

## Contributing

When adding new scripts:

1. **Use shared libraries** - Source from `lib/`
2. **Extract configs to templates** - No embedded heredocs
3. **Follow naming conventions** - Clear, descriptive names
4. **Add documentation** - Update this README

## License

Part of the Isle Mesh project.
