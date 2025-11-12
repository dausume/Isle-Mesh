# Isle Mesh Join Protocol

The Isle Join Protocol enables automatic discovery and dual-domain access for devices in the mesh network. Devices advertise themselves via mDNS (`.local` domains) and are automatically mapped to `.vlan` domains for mesh-wide DNS resolution.

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                     Join Protocol Flow                        │
└──────────────────────────────────────────────────────────────┘

1. Agent advertises mDNS
   └─> myserver.local (via Avahi)

2. Router discovers via avahi-browse
   └─> Detects: myserver.local @ 10.10.0.50

3. Join Protocol creates DNS mapping
   └─> myserver.local  -> 10.10.0.50
   └─> myserver.vlan   -> 10.10.0.50

4. dnsmasq serves both domains
   └─> DNS queries resolve both .local and .vlan

5. Agent responds to both domains
   └─> nginx server_name: myserver.local myserver.vlan
```

## Components

### Router-Side: Join Protocol Service

**Location:** `/home/dustin/Desktop/IsleMesh/openwrt-router/scripts/router-setup/configure-join-protocol.sh`

**What it does:**
- Runs on the OpenWRT router as a procd service
- Scans for mDNS `.local` domains every 30 seconds using `avahi-browse`
- Extracts hostname and IP address from mDNS advertisements
- Automatically creates DNS entries in `/etc/dnsmasq.d/isle-vlan-domains.conf`
- Maps `hostname.local` → `hostname.vlan` (same IP)
- Reloads dnsmasq when mappings change

**Installation:**
```bash
cd /home/dustin/Desktop/IsleMesh/openwrt-router/scripts/router-setup
sudo ./configure-join-protocol.sh --isle-name my-isle --vlan-id 10
```

**Service Management:**
```bash
# Check status
ssh root@192.168.1.1 '/etc/init.d/isle-join-protocol status'

# View logs
ssh root@192.168.1.1 'logread | grep isle-join-protocol'

# Restart service
ssh root@192.168.1.1 '/etc/init.d/isle-join-protocol restart'

# View DNS mappings
ssh root@192.168.1.1 'cat /etc/dnsmasq.d/isle-vlan-domains.conf'
```

### Agent-Side: Dual-Domain Configuration

**Location:** `/home/dustin/Desktop/IsleMesh/isle-agent-mdns/scripts/configure-dual-domain.sh`

**What it does:**
- Configures nginx to respond to both `.local` and `.vlan` domains
- Sets up container hostname
- Updates Avahi hostname configuration
- Creates example nginx vhost configurations
- Configures SSH for DNS resolution

**Usage:**
```bash
# Inside the isle-agent container
/usr/local/bin/configure-dual-domain myserver

# Or run from host
docker exec isle-agent-mdns /usr/local/bin/configure-dual-domain myserver
```

**Nginx Configuration:**
```nginx
server {
    listen 80;
    server_name myserver.local myserver.vlan;

    location / {
        # Your application
    }
}
```

## Setup Process

### 1. Deploy Join Protocol to Router

```bash
cd /home/dustin/Desktop/IsleMesh/openwrt-router/scripts/router-setup

# Deploy the service
sudo ./configure-join-protocol.sh \
    --isle-name production \
    --vlan-id 10
```

### 2. Configure Isle Agent for Dual-Domain

```bash
cd /home/dustin/Desktop/IsleMesh/isle-agent-mdns

# Start the agent (if not already running)
docker-compose up -d

# Configure dual-domain support
docker exec isle-agent-mdns /usr/local/bin/configure-dual-domain myserver

# Restart nginx to apply changes
docker exec isle-agent-mdns nginx -s reload
```

### 3. Verify the Join Protocol

```bash
cd /home/dustin/Desktop/IsleMesh/openwrt-router/scripts/router-setup

# Run verification tests
./verify-join-protocol.sh --vlan-id 10
```

## How It Works

### mDNS Discovery (Agent → Router)

1. **Agent advertises** via Avahi:
   - Broadcasts mDNS packets on the vLAN
   - Advertises hostname as `myserver.local`
   - Includes IP address (from DHCP)

2. **Router listens** via `avahi-browse`:
   ```bash
   avahi-browse -a -t -r
   ```
   - Discovers all mDNS services
   - Parses hostname and IP pairs
   - Filters for VLAN subnet (10.X.0.0/24)

### DNS Mapping Creation

3. **Join Protocol daemon** processes discoveries:
   - Runs every 30 seconds
   - Compares new discoveries with existing mappings
   - Updates `/etc/dnsmasq.d/isle-vlan-domains.conf`:
   ```
   address=/myserver.local/10.10.0.50
   address=/myserver.vlan/10.10.0.50
   ```

4. **dnsmasq reloads** configuration:
   - Both `.local` and `.vlan` now resolve
   - DNS queries return the same IP
   - Available to all mesh members

### Agent Response

5. **nginx accepts both domains**:
   ```nginx
   server_name myserver.local myserver.vlan;
   ```
   - HTTP Host header matches either domain
   - Same backend serves both
   - Transparent to clients

## Testing

### Test mDNS Discovery

```bash
# From router
ssh root@192.168.1.1 'avahi-browse -a -t'

# Should show:
# = eth1 IPv4 myserver                        Web Site  local
```

### Test DNS Resolution

```bash
# Test .local domain
nslookup myserver.local 192.168.1.1

# Test .vlan domain
nslookup myserver.vlan 192.168.1.1

# Both should return same IP
```

### Test HTTP Access

```bash
# Via .local domain
curl http://myserver.local

# Via .vlan domain
curl http://myserver.vlan

# Both should work identically
```

### Run Full Verification

```bash
./verify-join-protocol.sh --vlan-id 10 --test-hostname myserver
```

This runs:
- ✓ Router connectivity check
- ✓ Join protocol service status
- ✓ Avahi/mDNS availability
- ✓ mDNS discovery test
- ✓ DNS mapping verification
- ✓ DNS resolution from router
- ✓ DHCP lease verification
- ✓ HTTP connectivity test
- ✓ Join protocol log review

## Troubleshooting

### No .local domains discovered

**Problem:** `avahi-browse` returns empty results

**Solutions:**
1. Check agent is advertising:
   ```bash
   docker exec isle-agent-mdns avahi-browse -a -t
   ```

2. Verify avahi-daemon is running in agent:
   ```bash
   docker exec isle-agent-mdns pgrep avahi-daemon
   ```

3. Check network connectivity:
   ```bash
   docker exec isle-agent-mdns ip addr
   # Should have IP in 10.X.0.0/24 range
   ```

### .vlan domains not created

**Problem:** No entries in `/etc/dnsmasq.d/isle-vlan-domains.conf`

**Solutions:**
1. Check join protocol is running:
   ```bash
   ssh root@192.168.1.1 '/etc/init.d/isle-join-protocol status'
   ```

2. View daemon logs:
   ```bash
   ssh root@192.168.1.1 'logread | grep isle-join-protocol'
   ```

3. Wait 30 seconds for next discovery cycle

4. Manually trigger discovery:
   ```bash
   ssh root@192.168.1.1 '/etc/init.d/isle-join-protocol restart'
   ```

### DNS doesn't resolve .vlan

**Problem:** `nslookup myserver.vlan` fails

**Solutions:**
1. Check dnsmasq is reading config:
   ```bash
   ssh root@192.168.1.1 'cat /etc/dnsmasq.conf | grep conf-dir'
   # Should show: conf-dir=/etc/dnsmasq.d
   ```

2. Restart dnsmasq:
   ```bash
   ssh root@192.168.1.1 '/etc/init.d/dnsmasq restart'
   ```

3. Test from router itself:
   ```bash
   ssh root@192.168.1.1 'nslookup myserver.vlan localhost'
   ```

### nginx not responding to .vlan domain

**Problem:** `.local` works but `.vlan` gives 404

**Solutions:**
1. Check nginx server_name includes both:
   ```bash
   docker exec isle-agent-mdns cat /etc/nginx/conf.d/*.conf | grep server_name
   ```

2. Reload nginx configuration:
   ```bash
   docker exec isle-agent-mdns nginx -s reload
   ```

3. Re-run dual-domain configuration:
   ```bash
   docker exec isle-agent-mdns /usr/local/bin/configure-dual-domain myserver
   ```

## Advanced Configuration

### Custom Discovery Interval

Edit `/usr/bin/isle-join-protocol` on router:
```bash
DISCOVERY_INTERVAL=15  # Default is 30 seconds
```

### Filter by Service Type

Modify discovery to only find HTTP services:
```bash
avahi-browse _http._tcp -t -r
```

### Add Custom Domain Suffixes

Instead of just `.vlan`, support multiple:
```bash
# In dnsmasq config
address=/myserver.vlan/10.10.0.50
address=/myserver.mesh/10.10.0.50
address=/myserver.isle/10.10.0.50
```

## Integration with Existing Systems

### Update Agent Docker Compose

```yaml
services:
  isle-agent-mdns:
    # ... existing config ...

    environment:
      - HOSTNAME=myserver

    volumes:
      # Add dual-domain script
      - ./scripts/configure-dual-domain.sh:/usr/local/bin/configure-dual-domain:ro
```

### Automated Configuration on Startup

Add to agent `entrypoint.sh`:
```bash
# Configure dual-domain on container start
if [ -n "$HOSTNAME" ]; then
    /usr/local/bin/configure-dual-domain "$HOSTNAME"
fi
```

## Benefits

1. **Automatic Discovery**: No manual DNS configuration needed
2. **Dual-Domain Access**: Same service accessible via `.local` and `.vlan`
3. **mDNS + DNS**: Combines benefits of both protocols
4. **Self-Healing**: Automatically updates when IPs change
5. **Scalable**: Handles multiple agents without manual intervention

## Next Steps

- [ ] Test with multiple agents
- [ ] Configure HTTPS certificates for `.vlan` domains
- [ ] Set up nginx reverse proxies to backend services
- [ ] Test mDNS reflection across physical network segments
- [ ] Implement health checks and auto-removal of stale entries
- [ ] Add metrics and monitoring for join protocol
