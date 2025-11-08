# Isle Mesh OpenWRT Router - Quick Start

## One-Command Setup

```bash
cd openwrt-router/scripts
sudo ./setup-isle-mesh-router.sh --isle-name my-isle --vlan-id 10
```

## What Gets Installed

✅ OpenWRT KVM router
✅ vLAN network (10.10.0.1/24)
✅ DHCP server for virtual MACs
✅ Discovery beacon (broadcasts every 30s)
✅ mDNS reflector

## Architecture

```
OpenWRT Router (Host A)
    ↓ Discovery Broadcast (UDP 7878)
    ↓ "ISLE_MESH_DISCOVERY|isle=my-isle|vlan=10|..."
    ↓
Remote Machine (Host B)
    ↓ isle-agent hears broadcast
    ↓ Creates br-isle10 bridge
    ↓ Enslaves physical interface
    ↓
nginx Container
    ↓ Joins br-isle10 with virtual MAC: 02:00:00:00:0a:XX
    ↓ Requests DHCP from OpenWRT
    ↓ Gets IP: 10.10.0.50
    ↓ Broadcasts mDNS with this IP
    ↓
OpenWRT never sees Host B's real IP!
```

## Key Files Created

### Shared Libraries (scripts/lib/)
- `common-log.sh` - Unified logging
- `common-utils.sh` - Shared utilities
- `template-engine.sh` - Variable substitution

### Templates (templates/)
- `libvirt/base-vm.xml` - VM configuration
- `openwrt/uci/network-vlan.uci` - Network config
- `openwrt/uci/dhcp-server.uci` - DHCP config
- `openwrt/scripts/isle-discovery-beacon.sh` - Discovery broadcaster

### Scripts (scripts/router-setup/)
- `router-init.main.sh` - Initialize VM
- `isle-vlan-router-config.main.sh` - Configure vLAN
- `configure-dhcp-vlan.sh` - Setup DHCP
- `configure-discovery.sh` - Deploy discovery beacon
- `setup-isle-mesh-router.sh` - Master setup script

## Testing Discovery

### Listen for Broadcasts

```bash
sudo nc -l -u 7878
```

Expected output:
```
ISLE_MESH_DISCOVERY|isle=my-isle|vlan=10|router=10.10.0.1|dhcp=10.10.0.0/24
```

### Check OpenWRT Discovery Service

```bash
# Access OpenWRT
sudo virsh console openwrt-isle-router

# Check service
/etc/init.d/isle-discovery status

# View logs
logread | grep isle-discovery
```

## Virtual MAC Pattern

Remote nginx containers use virtual MACs:

```
02:00:00:00:VLAN_ID:XX

Example for vLAN 10:
02:00:00:00:0a:42
02:00:00:00:0a:7f
02:00:00:00:0a:b3
```

OpenWRT DHCP recognizes this pattern and assigns IPs.

## Common Commands

### Router Management
```bash
# List VMs
sudo virsh list --all

# Start router
sudo virsh start openwrt-isle-router

# Stop router
sudo virsh shutdown openwrt-isle-router

# Console access
sudo virsh console openwrt-isle-router
```

### Configuration
```bash
# On OpenWRT:

# View network config
uci show network

# View DHCP config
uci show dhcp

# View DHCP leases
cat /tmp/dhcp.leases

# View logs
logread -f
```

## Next Steps

1. **Remote machines** need `isle-agent` daemon (to be built)
2. **nginx containers** need labels:
   ```yaml
   labels:
     - "isle-mesh.isle=my-isle"
     - "isle-mesh.proxy=true"
     - "isle-mesh.vlan=10"
   ```
3. **Physical interfaces** can be added:
   ```bash
   sudo ./utilities/add-ethernet-connection.main.sh
   sudo ./utilities/add-usb-wifi.main.sh
   ```

## Troubleshooting

### Discovery not broadcasting
```bash
# On OpenWRT
/etc/init.d/isle-discovery restart
logread | grep isle-discovery
```

### DHCP not working
```bash
# On OpenWRT
uci show dhcp
/etc/init.d/dnsmasq restart
logread | grep dnsmasq
```

### Can't access OpenWRT
```bash
# Use console (no network needed)
sudo virsh console openwrt-isle-router

# Set root password
passwd
```

## File Structure

```
openwrt-router/
├── scripts/
│   ├── lib/                    # Shared libraries (NEW)
│   ├── router-setup/           # Router initialization
│   ├── utilities/              # Connection management
│   ├── hotplug-handlers/       # Port detection
│   └── setup-isle-mesh-router.sh  # Master script (NEW)
├── templates/                  # Config templates (NEW)
│   ├── libvirt/               # VM XML templates
│   └── openwrt/               # OpenWRT configs
├── README.md                   # Full documentation
└── QUICK-START.md             # This file
```

## Philosophy

**DRY (Don't Repeat Yourself)**
- Shared libraries eliminate duplicate code
- Templates separate config from logic
- Reusable components across all scripts

**Zero IP Exposure**
- Remote nginx containers use virtual MACs
- OpenWRT only sees virtual MAC addresses
- Real host IPs never exposed to mesh

**Auto-Discovery**
- OpenWRT broadcasts discovery packets
- Remote agents listen and auto-configure
- No manual configuration needed

---

For detailed documentation, see [README.md](README.md)
