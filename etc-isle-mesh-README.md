# Isle-Mesh Local Configuration

This directory (`/etc/isle-mesh/`) contains only the essential configuration files needed to run Isle-Mesh on this host. The design philosophy is to keep this directory minimal so that Isle-Mesh can be:

- **Brought down to save space** - Stop all services and VMs when not in use
- **Brought back up anytime** - Restart with all configurations intact
- **Containerized/virtualized in the future** - Move most functionality to containers while keeping only essential configs local

## Directory Structure

```
/etc/isle-mesh/
├── README.md                    # This file
├── mesh-mdns.env               # mDNS service configuration (active config)
├── mesh-mdns-broadcast.sh      # mDNS broadcast script (runtime)
├── .installed_started          # Installation state marker
├── .install_complete           # Installation completion marker
└── router/                     # Router-specific configurations
    ├── openwrt-isle-router.xml        # VM domain XML configuration
    ├── openwrt-config-*.exp           # Network configuration expect scripts
    ├── openwrt-network-config.sh      # Network interface configuration
    ├── openwrt-firewall-config.sh     # Firewall rules configuration
    └── openwrt-mdns-config.sh         # mDNS relay configuration
```

## Configuration Files

### mDNS Configuration

**`mesh-mdns.env`**
- Active mDNS environment configuration
- Defines domains, ports, and service names
- Used by systemd service: `/etc/systemd/system/mesh-mdns.service`
- Source: Copy from `mesh-prototypes/localhost-mdns/lh-mdns.env.conf`

**`mesh-mdns-broadcast.sh`**
- Runtime script for broadcasting mDNS services
- Executed by mesh-mdns systemd service
- Source: `/usr/local/bin/isle-mesh/mesh-mdns-broadcast.sh`

### Router Configuration (`router/`)

This directory contains configurations for OpenWRT virtual routers running locally. Multiple routers can be configured, each with their own XML and configuration scripts.

**VM Configurations:**
- `openwrt-isle-router.xml` - Default secure router VM definition
- `openwrt-test.xml` - Test router VM definition (if using test mode)

**Network Configurations:**
- `openwrt-config-*.exp` - Expect scripts for automated network setup via console
- `openwrt-network-config.sh` - Network interface and vLAN configuration
- `openwrt-firewall-config.sh` - Firewall zones and isle isolation rules
- `openwrt-mdns-config.sh` - Avahi mDNS relay configuration

## Installation State

**`.installed_started`**
- Marker file indicating installation has begun
- Created during initial setup

**`.install_complete`**
- Marker file indicating installation completed successfully
- Used to verify complete installation

## Related Directories

Configuration files stored here reference other system locations:

- **Runtime Scripts:** `/usr/local/bin/isle-mesh/`
  - Executable scripts for mDNS, router management

- **Systemd Services:** `/etc/systemd/system/`
  - `mesh-mdns.service` - mDNS broadcasting service
  - `isle-port-detection.service` - USB/Ethernet port detection (future)

- **Router Images:** Project-specific (not in /etc)
  - Stored in `<project>/openwrt-router/images/`
  - Large files that can be shared across installations

- **Project Source:** `/path/to/IsleMesh/`
  - Development source code
  - Templates and examples
  - Not needed for runtime

## Space-Saving Design

This directory is kept minimal because:

1. **VM images are stored elsewhere** - Router disk images are in the project directory
2. **Scripts are versioned in source** - Runtime scripts link to project source
3. **Only active configs here** - No templates, examples, or documentation
4. **Containerization ready** - Future: Move to Docker/containers with minimal host config

## Bringing Isle-Mesh Down

To save space when not using Isle-Mesh:

```bash
# Stop all services
sudo systemctl stop mesh-mdns
sudo isle router test cleanup  # or cleanup production router

# VM images can be deleted (will re-download if needed)
# Config files in /etc/isle-mesh remain for easy restart
```

## Bringing Isle-Mesh Back Up

To restart Isle-Mesh with existing configuration:

```bash
# Restart mDNS service
sudo systemctl start mesh-mdns

# Reinitialize router (will use existing XML configs)
sudo isle router init

# Or restart test router
sudo isle router test basic
```

## Future: Containerization

Future versions will move most functionality to containers:

- **Keep in /etc/isle-mesh:**
  - Active configuration only (`mesh-mdns.env`, router XMLs)

- **Move to containers:**
  - Runtime scripts
  - mDNS services
  - Router VMs (nested virtualization or alternative)

- **Benefits:**
  - Smaller host footprint
  - Easier updates (pull new container images)
  - Better isolation
  - Portable configurations

## Maintenance

**Backup this directory:**
```bash
sudo tar -czf isle-mesh-config-backup.tar.gz /etc/isle-mesh
```

**Restore configuration:**
```bash
sudo tar -xzf isle-mesh-config-backup.tar.gz -C /
```

**Clean old configs:**
```bash
# Remove old expect scripts
sudo rm -f /etc/isle-mesh/router/openwrt-config-*.exp

# Keep only essential configs
```

## See Also

- **CLI Documentation:** `isle help`
- **Router Management:** `isle router help`
- **mDNS Setup:** `mdns/manual-mdns-setup.md`
- **Router Architecture:** `openwrt-router/README.md`
