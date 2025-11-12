# Isle Agent with mDNS

Unified nginx proxy container with integrated Avahi mDNS daemon for automatic service discovery over the vLAN.

## Overview

This is an enhanced version of the isle-agent that includes mDNS (multicast DNS) service advertisement. The key feature is that **mDNS traffic only propagates over the vLAN** (via the macvlan network on `isle-br-0`), keeping service discovery isolated from your host network.

### Key Features

- **Nginx + Avahi**: Single container running both nginx proxy and Avahi mDNS daemon
- **vLAN-Only Broadcasting**: mDNS advertisements only propagate over the macvlan network (vLAN)
- **Automatic Service Discovery**: Mesh apps are discoverable at `<app-name>.local` on the vLAN
- **Service Registration**: Scripts to register/unregister services based on the Isle Agent registry
- **Zero Host Impact**: No changes to host system's mDNS/Avahi configuration

### Architecture

```
┌─────────────────────────────────────────────────────────┐
│ isle-agent-mdns Container                               │
│                                                          │
│  ┌──────────────┐      ┌──────────────┐                │
│  │    Nginx     │      │    Avahi     │                │
│  │   (Port 80)  │      │  mDNS Daemon │                │
│  │  (Port 443)  │      │  (Port 5353) │                │
│  └──────────────┘      └──────────────┘                │
│         │                      │                         │
│         └──────────┬───────────┘                         │
│                    │                                     │
└────────────────────┼─────────────────────────────────────┘
                     │
                     │ (macvlan on isle-br-0)
                     │
                     ▼
        ┌────────────────────────┐
        │   OpenWRT Router       │
        │   (vLAN 10)            │
        │   10.0.10.1/24         │
        └────────────────────────┘
                     │
                     │ mDNS propagates only here
                     │
                     ▼
        ┌────────────────────────┐
        │  Other vLAN Devices    │
        │  Can discover services │
        └────────────────────────┘
```

## Differences from Original Isle Agent

| Feature | Original isle-agent | isle-agent-mdns |
|---------|-------------------|-----------------|
| Base Image | `nginx:alpine` | Custom (`nginx:alpine` + `avahi`) |
| Service Discovery | Manual DNS/hosts | Automatic via mDNS |
| Discovery Scope | N/A | vLAN only |
| Container Capabilities | None | `NET_ADMIN`, `NET_RAW` |
| Additional Services | Nginx only | Nginx + D-Bus + Avahi |
| Ports | 80, 443 | 80, 443, 5353/udp |

## Directory Structure

```
isle-agent-mdns/
├── Dockerfile                    # Custom image with nginx + avahi
├── docker-compose.yml            # Container definition
├── README.md                     # This file
├── avahi/
│   ├── avahi-daemon.conf         # Avahi mDNS configuration
│   └── supervisord.conf          # Supervisor config (manages all daemons)
└── scripts/
    ├── entrypoint.sh             # Container entrypoint
    ├── register-service.sh       # Register an mDNS service
    ├── unregister-service.sh     # Unregister an mDNS service
    ├── sync-services.sh          # Sync services from registry
    └── test-mdns.sh              # Test mDNS functionality
```

### Host Directories

The container expects these host directories (same as original isle-agent):

```
/etc/isle-mesh/agent/
├── nginx.conf                    # Master nginx config
├── configs/                      # Per-app config fragments
├── ssl/                          # SSL certificates
├── logs/                         # Nginx logs
├── registry.json                 # App registry
└── mdns/
    └── services/                 # mDNS service definitions (persistent)
```

## Installation

### Prerequisites

1. **Docker** and **docker-compose** installed
2. **isle-br-0** bridge interface created (for macvlan network)
3. **OpenWRT router** configured and running
4. **Original isle-agent setup** completed (directory structure, registry, etc.)

### Build the Image

```bash
cd /path/to/IsleMesh/isle-agent-mdns

# Build the custom image
docker-compose build

# Verify the image was created
docker images | grep isle-agent-mdns
```

### Create Required Directories

```bash
# Create mDNS service directory
sudo mkdir -p /etc/isle-mesh/agent/mdns/services

# Set permissions
sudo chmod 755 /etc/isle-mesh/agent/mdns
sudo chmod 755 /etc/isle-mesh/agent/mdns/services
```

### Start the Container

```bash
# Start the isle-agent-mdns container
docker-compose up -d

# Verify it's running
docker ps | grep isle-agent-mdns

# Check logs
docker logs isle-agent-mdns

# You should see:
#   - D-Bus daemon starting
#   - Avahi daemon starting
#   - Nginx starting
```

## Usage

### 1. Register a Service Manually

```bash
# Register a service for an app
docker exec isle-agent-mdns register-service myapp myapp.local 443 https

# This creates /etc/avahi/services/myapp.service inside the container
```

### 2. Sync Services from Registry

```bash
# Auto-register all apps from the Isle Agent registry
./scripts/sync-services.sh

# This reads /etc/isle-mesh/agent/registry.json and registers each app
```

### 3. Test mDNS Discovery

From inside the container:

```bash
# Run the test script
./scripts/test-mdns.sh

# Or manually:
docker exec isle-agent-mdns avahi-browse -a -t
```

From another device on the vLAN:

```bash
# Linux/macOS with avahi-utils installed
avahi-browse -a

# macOS with dns-sd
dns-sd -B _https._tcp

# You should see services advertised by the isle-agent-mdns
```

### 4. Unregister a Service

```bash
# Remove a service from mDNS advertising
docker exec isle-agent-mdns unregister-service myapp
```

### 5. View Active Services

```bash
# List registered service files
docker exec isle-agent-mdns ls -la /etc/avahi/services/

# View a specific service file
docker exec isle-agent-mdns cat /etc/avahi/services/myapp.service
```

## How It Works

### 1. Container Startup

When the container starts:

1. **D-Bus** daemon starts first (required by Avahi)
2. **Avahi** daemon starts and begins advertising services
3. **Nginx** starts and serves as the reverse proxy
4. **Entrypoint** checks for existing registry and can auto-register services

### 2. mDNS Service Advertisement

Each registered app gets an Avahi service file like this:

```xml
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">myapp</name>
  <service>
    <type>_https._tcp</type>
    <port>443</port>
    <txt-record>path=/</txt-record>
    <txt-record>isle-mesh=true</txt-record>
    <txt-record>version=1.0</txt-record>
  </service>
  <service>
    <type>_http._tcp</type>
    <port>80</port>
    <txt-record>path=/</txt-record>
    <txt-record>isle-mesh=true</txt-record>
  </service>
</service-group>
```

Avahi broadcasts this over multicast DNS on the macvlan interface.

### 3. Network Isolation

The container has two network interfaces:

1. **eth0** (internal docker bridge): Connects to app containers
2. **eth1** (macvlan on isle-br-0): Connects to OpenWRT router

**Avahi only advertises on the macvlan interface**, so mDNS traffic only propagates over the vLAN.

### 4. Service Discovery Flow

```
1. App container starts
2. Isle Agent adds app to registry.json
3. sync-services.sh runs
4. Service registered with Avahi
5. Avahi broadcasts mDNS over macvlan (vLAN)
6. Other vLAN devices see the service at <app>.local
```

## Integration with Isle CLI

### Auto-Registration (Future Enhancement)

You can integrate service registration into the `isle app up` workflow:

```bash
# In isle-cli/scripts/app.sh, after app starts:
if [ -f /etc/isle-mesh/agent/registry.json ]; then
    # Sync mDNS services
    /path/to/isle-agent-mdns/scripts/sync-services.sh
fi
```

### Auto-Unregistration

```bash
# In isle-cli/scripts/app.sh, when app is stopped:
docker exec isle-agent-mdns unregister-service "$APP_NAME"
```

## Configuration

### Avahi Configuration

Edit `avahi/avahi-daemon.conf` to customize:

- **IPv6 support**: Set `use-ipv6=yes`
- **Interface binding**: Uncomment `allow-interfaces=eth1` to bind to specific interface
- **Domain name**: Change `domain-name=local` to use a different domain

### Network Configuration

Edit `docker-compose.yml` to customize:

- **MAC address**: Change `mac_address` if needed
- **Ports**: Adjust port mappings
- **Networks**: Modify network configuration

## Comparison with Router mDNS Setup

| Aspect | This Setup (Container mDNS) | OpenWRT Router mDNS |
|--------|----------------------------|-------------------|
| Scope | Advertises services FROM this agent | Reflects mDNS BETWEEN networks |
| Purpose | Service discovery | mDNS reflection/forwarding |
| Location | Inside isle-agent container | On OpenWRT router |
| Configuration | `avahi-daemon.conf` in container | Avahi config on router |
| Use Case | Apps on THIS device | Apps across MULTIPLE devices |

Both setups can coexist:
- **Container mDNS**: Advertises this device's services on the vLAN
- **Router mDNS**: Reflects mDNS between vLAN and other networks (if configured)

## Troubleshooting

### Avahi Daemon Not Starting

```bash
# Check D-Bus is running first
docker exec isle-agent-mdns pgrep dbus-daemon

# Check Avahi logs
docker logs isle-agent-mdns | grep avahi

# Manually restart services
docker exec isle-agent-mdns supervisorctl restart dbus avahi
```

### Services Not Being Advertised

```bash
# Verify service files exist
docker exec isle-agent-mdns ls /etc/avahi/services/

# Check Avahi can see them
docker exec isle-agent-mdns avahi-browse -a -t

# Verify network interface
docker exec isle-agent-mdns ip addr show
```

### mDNS Not Visible on vLAN

```bash
# Ensure macvlan network is configured correctly
docker network inspect isle-br-0

# Check if isle-br-0 bridge exists on host
ip link show isle-br-0

# Verify multicast is enabled on the bridge
ip link set isle-br-0 multicast on

# Check firewall rules aren't blocking mDNS (port 5353/udp)
sudo iptables -L | grep 5353
```

### Container Capabilities

If Avahi fails to start, ensure the container has the required capabilities:

```yaml
cap_add:
  - NET_ADMIN
  - NET_RAW
```

These are needed for multicast networking.

## Performance

### Resource Usage

Expected resource usage per container:

- **Memory**: ~50-100 MB (nginx + avahi + dbus)
- **CPU**: Minimal (< 1% idle)
- **Network**: Multicast traffic on port 5353/udp (low bandwidth)

### Scalability

- **Services**: Can advertise hundreds of services without issues
- **Devices**: mDNS scales to ~50-100 devices on a local network
- **Updates**: Service changes are reflected within seconds

## Security Considerations

### Network Isolation

- mDNS traffic is **isolated to the vLAN** via macvlan network
- Host system's mDNS is **not affected**
- No cross-talk between host and vLAN mDNS

### Service Exposure

- Only advertises services that are already exposed via nginx
- Does not create new attack surface
- TXT records can include security metadata

### Recommended Practices

1. **Firewall**: Ensure mDNS (port 5353/udp) is only allowed on vLAN
2. **mTLS**: Use mutual TLS for sensitive services
3. **Monitoring**: Monitor mDNS traffic for unusual activity

## Migration from Original isle-agent

If you're already using the original `isle-agent`:

1. **Stop the original agent**:
   ```bash
   cd /path/to/isle-agent
   docker-compose down
   ```

2. **Start the mDNS-enabled agent**:
   ```bash
   cd /path/to/isle-agent-mdns
   docker-compose up -d
   ```

3. **Sync existing services**:
   ```bash
   ./scripts/sync-services.sh
   ```

4. **Verify services are advertised**:
   ```bash
   ./scripts/test-mdns.sh
   ```

The new agent is a drop-in replacement - all volume mounts and configurations are compatible.

## Future Enhancements

### Planned

- [ ] Auto-registration on app start
- [ ] Auto-unregistration on app stop
- [ ] Service health monitoring
- [ ] mDNS metrics export (Prometheus)
- [ ] Support for service subtypes
- [ ] DNS-SD service browsing API

### Experimental

- [ ] mDNS-SD for service metadata
- [ ] Wide-area mDNS (DNS-SD over DNS)
- [ ] Service dependency tracking

## Related Documentation

- **Isle Agent (original)**: `/isle-agent/README.md`
- **OpenWRT mDNS Setup**: `/openwrt-router/scripts/router-setup/isle-vlan-router-config-lib/70-mdns.sh`
- **Host mDNS Setup**: `/mdns/docker-compose.yml`
- **Avahi Documentation**: https://www.avahi.org/

## License

Same as the IsleMesh project.

## Support

For issues or questions:
1. Check the troubleshooting section above
2. Review container logs: `docker logs isle-agent-mdns`
3. Test mDNS: `./scripts/test-mdns.sh`
4. Check the OpenWRT router mDNS configuration
