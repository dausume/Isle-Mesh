# Integration Guide: isle-agent-mdns vs Existing mDNS Setups

This document explains how the isle-agent-mdns integrates with your existing mDNS work and the differences between the three mDNS implementations in your IsleMesh project.

## Three mDNS Implementations

Your IsleMesh project now has **three separate mDNS implementations**, each serving a different purpose:

### 1. Host mDNS (`/mdns/`)

**Purpose**: Configure the host system to broadcast mDNS services

**Location**: `/home/dustin/Desktop/IsleMesh/mdns/`

**How it works**:
- Privileged container that modifies host system
- Installs Avahi on the host
- Configures systemd services on the host
- mDNS broadcasts from host's network interfaces

**Scope**: Host network (all interfaces)

**Use case**: Making the host machine's services discoverable

**File**: `docker-compose.yml` with `network_mode: host`

---

### 2. OpenWRT Router mDNS (`/openwrt-router/scripts/.../70-mdns.sh`)

**Purpose**: Enable mDNS reflection on the OpenWRT router

**Location**: `/home/dustin/Desktop/IsleMesh/openwrt-router/scripts/router-setup/isle-vlan-router-config-lib/70-mdns.sh`

**How it works**:
- Configures Avahi on the OpenWRT router
- Enables mDNS reflector mode
- Reflects mDNS packets between network segments

**Scope**: Router network (reflects between interfaces)

**Use case**: Allowing mDNS discovery across different network segments (e.g., between vLAN 10 and management network)

**Configuration**: Avahi config pushed to router via SSH

---

### 3. Isle Agent mDNS (`/isle-agent-mdns/`) - **NEW**

**Purpose**: Broadcast mesh app services over the vLAN only

**Location**: `/home/dustin/Desktop/IsleMesh/isle-agent-mdns/`

**How it works**:
- Custom Docker container with nginx + Avahi
- Connects to macvlan network (isle-br-0)
- Broadcasts mDNS only on vLAN interface
- Auto-registers services from Isle Agent registry

**Scope**: vLAN only (macvlan network)

**Use case**: Making mesh apps discoverable to other devices on the vLAN

**File**: `docker-compose.yml` with `macvlan` network

---

## Comparison Table

| Aspect | Host mDNS | Router mDNS | **Isle Agent mDNS (NEW)** |
|--------|-----------|-------------|--------------------------|
| **Purpose** | Host services | Cross-network reflection | Mesh app discovery |
| **Installation** | Modifies host | Configures router | Standalone container |
| **Network Scope** | Host interfaces | All router interfaces | vLAN only (macvlan) |
| **Service Source** | Host applications | Reflected from networks | Isle Agent registry |
| **Isolation** | No isolation | Bridges networks | **Fully isolated to vLAN** |
| **Dependencies** | systemd, host packages | OpenWRT, Avahi package | Docker only |
| **Configuration** | systemd services | Router UCI/Avahi config | Docker Compose |
| **Privileges** | Requires host access | Requires router SSH | Container capabilities |

---

## How They Work Together

```
┌─────────────────────────────────────────────────────────────────┐
│ Host System                                                     │
│                                                                  │
│  [Host mDNS (Avahi)]  ← Broadcasts host services               │
│         │                                                        │
│         │                                                        │
│  ┌──────▼───────────────────────────────────────────┐          │
│  │ Docker                                            │          │
│  │                                                   │          │
│  │  ┌─────────────────────────────────────────┐    │          │
│  │  │ isle-agent-mdns Container (NEW)         │    │          │
│  │  │                                          │    │          │
│  │  │  [Nginx] + [Avahi mDNS]                │    │          │
│  │  │                                          │    │          │
│  │  │  Broadcasts mesh app services           │    │          │
│  │  └─────────────┬────────────────────────────┘    │          │
│  │                │ macvlan (isle-br-0)            │          │
│  └────────────────┼────────────────────────────────┘          │
│                   │                                             │
│  [isle-br-0] ◄────┘                                            │
│       │                                                         │
└───────┼─────────────────────────────────────────────────────────┘
        │
        │ vLAN traffic only
        │
┌───────▼─────────────────────────────────────────────────────────┐
│ OpenWRT Router (vLAN 10)                                        │
│                                                                  │
│  [Router mDNS Reflector] ← Reflects mDNS between networks      │
│         │                                                        │
│         │ Forwards mDNS packets                                 │
│         │                                                        │
└─────────┼───────────────────────────────────────────────────────┘
          │
          │ vLAN traffic
          │
┌─────────▼───────────────────────────────────────────────────────┐
│ Other vLAN Devices                                              │
│                                                                  │
│  Can discover:                                                  │
│  - Mesh apps from isle-agent-mdns (direct)                     │
│  - Host services (if router reflects them)                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Isolation Benefits

### isle-agent-mdns (NEW) Provides:

1. **Network Isolation**:
   - mDNS traffic **only** on the vLAN (macvlan network)
   - Host network is completely isolated
   - No cross-contamination between host and vLAN mDNS

2. **Service Isolation**:
   - Only advertises mesh app services
   - Host services are not advertised on vLAN (unless router reflects them)
   - Clear separation of concerns

3. **Configuration Isolation**:
   - No changes to host system
   - No changes to router (besides existing setup)
   - Self-contained in Docker container

---

## When to Use Each Implementation

### Use Host mDNS (`/mdns/`) when:
- You want to advertise services running directly on the host
- You need discovery on the host's primary network
- You're setting up development/testing environments

### Use Router mDNS (`/openwrt-router/.../70-mdns.sh`) when:
- You need mDNS discovery across network segments
- You want devices on different VLANs to discover each other
- You're bridging management and mesh networks

### Use Isle Agent mDNS (`/isle-agent-mdns/`) when:
- You want to advertise mesh apps on the vLAN **only**
- You need isolated service discovery
- You want automatic service registration from the Isle Agent registry
- **This is the recommended approach for production mesh deployments**

---

## Coexistence

All three implementations can **coexist without conflicts**:

- **Host mDNS**: Operates on host interfaces
- **Router mDNS**: Operates on router interfaces
- **Isle Agent mDNS**: Operates on macvlan interface (vLAN)

They each have their own Avahi daemon running in isolation:
- Host: `/usr/sbin/avahi-daemon` (systemd service)
- Router: `/usr/sbin/avahi-daemon` (OpenWRT init script)
- Isle Agent: `/usr/sbin/avahi-daemon` (inside container)

---

## Migration Path

If you were using the host mDNS setup for mesh apps, you can migrate:

### Before (Host mDNS for mesh apps):
```
Host Avahi ──> Advertises mesh apps on all host interfaces
              └─> Not isolated, visible on host network
```

### After (Isle Agent mDNS):
```
Isle Agent Avahi ──> Advertises mesh apps ONLY on vLAN
                    └─> Fully isolated, not visible on host network
```

### Migration Steps:

1. **Stop advertising mesh apps from host**:
   ```bash
   sudo systemctl stop mesh-mdns  # or whatever your host service is called
   ```

2. **Start isle-agent-mdns**:
   ```bash
   cd /path/to/isle-agent-mdns
   docker-compose up -d
   ```

3. **Sync services from registry**:
   ```bash
   ./scripts/sync-services.sh
   ```

4. **Verify services are only on vLAN**:
   ```bash
   # From host (should NOT see mesh apps)
   avahi-browse -a

   # From vLAN device (should see mesh apps)
   avahi-browse -a
   ```

---

## Best Practices

### For Development:
- Use **Host mDNS** for quick testing
- Keep it simple with single-network discovery

### For Production:
- Use **Isle Agent mDNS** for mesh apps (vLAN isolation)
- Use **Router mDNS** for cross-network discovery (if needed)
- Avoid Host mDNS for security (reduces attack surface)

### Security Considerations:

1. **vLAN Isolation**: isle-agent-mdns keeps mesh app discovery isolated
2. **No Host Exposure**: Host services are not advertised on vLAN
3. **Minimal Attack Surface**: Only the container has mDNS capabilities
4. **Firewall**: Ensure mDNS (5353/udp) is only allowed on intended interfaces

---

## Troubleshooting Multiple mDNS Setups

### Services appearing on wrong network:

Check which Avahi daemon is advertising:
```bash
# Host
sudo systemctl status avahi-daemon

# Router
ssh root@192.168.1.1 '/etc/init.d/avahi-daemon status'

# Isle Agent
docker exec isle-agent-mdns pgrep avahi-daemon
```

### Duplicate service advertisements:

Ensure each service is registered in only ONE place:
- **Mesh apps**: isle-agent-mdns only
- **Host services**: host mDNS only
- **Router services**: router mDNS only

### Conflicts:

Each Avahi daemon has its own:
- Configuration file
- Service directory
- Network interface binding

They should not conflict if properly isolated.

---

## Summary

**isle-agent-mdns** is a **new, separate implementation** that:
- ✅ Does NOT overwrite your existing mDNS setups
- ✅ Provides vLAN-isolated service discovery
- ✅ Integrates with the Isle Agent registry
- ✅ Works alongside host and router mDNS
- ✅ Recommended for production mesh deployments

Choose the right tool for each job:
- **Host mDNS**: Host services
- **Router mDNS**: Cross-network reflection
- **Isle Agent mDNS**: Mesh app discovery on vLAN
