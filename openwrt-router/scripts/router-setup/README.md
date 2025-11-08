# Router Setup - Execution Flow

This directory contains the scripts to create and configure an OpenWRT virtual router that enables Isle vLAN connectivity for remote nginx containers without exposing real MAC or IP addresses.

## Execution Order

### 1. `router-init.main.sh`
Creates an OpenWRT VM with NO network interfaces attached. The VM starts in a completely isolated state with dynamically assigned ports for security and flexibility. Uses modular library components from `router-init-lib/` for validation, permissions, image handling, and VM creation.

**Flow:** This establishes the foundational virtual router environment before any network connectivity is configured, ensuring a secure baseline.

---

### 2. `isle-vlan-router-config.main.sh`
Configures the OpenWRT router's internal settings including network interfaces, firewall rules, and mDNS reflection. Sets up SSH access, installs required packages, and establishes the basic routing infrastructure for a single isle. Uses modular library components from `isle-vlan-router-config-lib/`.

**Flow:** Now that the VM exists, this step configures its internal OpenWRT software to understand isle networking concepts and prepare it to route traffic between containers.

---

### 3. `setup-router-bridges.sh`
Creates host-side network bridges: br-mgmt (management access to OpenWRT at 192.168.1.254), br-isles (vLAN trunk for isle interconnection), and optional individual isle bridges. Configures kernel parameters for IP forwarding and bridge netfilter, then persists configuration via netplan or systemd-networkd.

**Flow:** This connects the host machine to the isolated router, creating the network pathways that will allow containers on the host to communicate through the router's vLAN infrastructure.

---

### 4. `configure-dhcp-vlan.sh`
Configures OpenWRT's DHCP server to assign IPs to remote nginx containers that join with virtual MAC addresses (format: 02:00:00:00:VLAN_ID:XX). Enables containers to get DHCP-assigned IPs in the 10.VLAN_ID.0.0/24 range without exposing their host machine's real IP.

**Flow:** With bridges established, remote containers now need IP addresses to communicate. This enables automatic IP assignment when containers join the mesh using virtual MACs.

---

### 5. `configure-discovery.sh`
Deploys a discovery beacon script to OpenWRT that broadcasts isle information (name, vLAN ID, router IP, DHCP range) at regular intervals. Remote machines with isle-agent daemons listen for these broadcasts to auto-configure their bridges and join the mesh automatically.

**Flow:** The router now actively advertises its presence and configuration, enabling a zero-configuration experience for remote machines wanting to join the mesh.

---

### 6. `verify-network-isolation.sh`
Security verification tool that checks gateway isolation, MAC address isolation, routing tables, IP visibility, subnet segregation, DNS isolation, and firewall rules. Ensures isolated networks cannot access the real ISP network or expose sensitive host information.

**Flow:** This final validation ensures the entire setup maintains proper security boundaries between the virtual isle network and the host's real network connection.

---


## Manual Use of Shell Files to make a OpenWRT Isle.

```bash
# 1. Initialize router VM
sudo ./router-init.main.sh

# 2. Configure OpenWRT software
sudo ./isle-vlan-router-config.main.sh

# 3. Setup host bridges
sudo ./setup-router-bridges.sh

# 4. Configure DHCP for virtual MACs
sudo ./configure-dhcp-vlan.sh --isle-name my-isle --vlan-id 10

# 5. Enable discovery broadcasting
sudo ./configure-discovery.sh --isle-name my-isle --vlan-id 10

# 6. Verify isolation
sudo ./verify-network-isolation.sh
```

## Result

Remote nginx containers with virtual MAC addresses can discover and join the isle mesh, receive DHCP-assigned IPs, and communicate via mDNS reflection—all without exposing real network addresses.
