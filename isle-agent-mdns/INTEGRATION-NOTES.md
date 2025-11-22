# Integration Notes: isle-agent-mdns

## Running with OpenWRT Router and localhost-mdns

### Conflict Check

✅ **No conflicts detected** when running all three components together on the same host.

See `/home/dustin/Desktop/IsleMesh/CONFLICT-ANALYSIS.md` for full analysis.

### Quick Verification

```bash
# Run automatic conflict checker
/home/dustin/Desktop/IsleMesh/check-conflicts.sh
```

---

## Component Summary

| Component | Port(s) | Network | mDNS Role |
|-----------|---------|---------|-----------|
| **OpenWRT Router** | None (VM) | `br-mgmt`, `isle-br-0` | Broadcasts mDNS |
| **localhost-mdns** | 80, 443, 8080, 8100 | Docker `meshnet` | None (future: detect) |
| **isle-agent-mdns** | 8888 | Docker `isle-mdns-net` | Receives via HTTP |

---

## Architecture

```
OpenWRT Router (KVM)
  ├─ Broadcasts mDNS on host network (port 5353/udp multicast)
  └─ Network: 192.168.1.1, 10.10.0.1

         ↓ mDNS multicast

localhost-mdns (host)
  ├─ Future: Will detect mDNS from OpenWRT
  ├─ Sample app on ports 80/443
  └─ Network: Docker bridge 'meshnet'

         ↓ HTTP POST to :8888

isle-agent-mdns (container)
  ├─ Receives mDNS data via HTTP
  ├─ Exposes on port 8888
  └─ Network: Docker bridge 'isle-mdns-net'
```

---

## Missing Piece

**localhost-mdns currently does NOT detect mDNS.**

It's just a sample app using `.local` domains via `/etc/hosts`.

### To Complete the Integration:

You need to add mDNS detection to localhost-mdns that forwards to isle-agent-mdns.

**Option 1:** Add Python script to localhost-mdns

```python
# Add to localhost-mdns
import requests
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

class MDNSForwarder(ServiceListener):
    def add_service(self, zc, type_, name):
        info = zc.get_service_info(type_, name)
        if info:
            # Forward to isle-agent-mdns
            requests.post('http://localhost:8888/mdns', json={
                'name': name,
                'type': type_,
                'addresses': [str(addr) for addr in info.parsed_addresses()],
                'port': info.port,
                'properties': dict(info.properties)
            })
```

**Option 2:** Create separate forwarder container

**Option 3:** Modify localhost-mdns backend to include mDNS detection

---

## Startup Order

1. **OpenWRT Router** (first)
   ```bash
   sudo virsh start openwrt-isle-router
   ```

2. **localhost-mdns** (second)
   ```bash
   cd /home/dustin/Desktop/IsleMesh/mesh-prototypes/localhost-mdns
   docker-compose up -d
   ```

3. **isle-agent-mdns** (third)
   ```bash
   cd /home/dustin/Desktop/IsleMesh/isle-agent-mdns
   docker-compose up -d
   ```

---

## Testing the Integration

### 1. Start all components

```bash
# Start OpenWRT
sudo virsh start openwrt-isle-router

# Start localhost-mdns
cd /home/dustin/Desktop/IsleMesh/mesh-prototypes/localhost-mdns
docker-compose up -d

# Start isle-agent-mdns
cd /home/dustin/Desktop/IsleMesh/isle-agent-mdns
docker-compose up -d
```

### 2. Verify OpenWRT is broadcasting mDNS

```bash
# From host, check for mDNS announcements
avahi-browse -a -t
```

### 3. Manually test isle-agent-mdns

```bash
# Simulate localhost-mdns forwarding mDNS data
curl -X POST http://localhost:8888/mdns \
  -H "Content-Type: application/json" \
  -d '{
    "name": "router._http._tcp.local.",
    "type": "_http._tcp.local.",
    "addresses": ["10.10.0.1"],
    "port": 80
  }'

# Verify it was received
curl http://localhost:8888/services | jq
```

### 4. Check all services are running

```bash
# OpenWRT
virsh domstate openwrt-isle-router

# localhost-mdns
docker ps | grep -E "(mesh-proxy|frontend|backend)"

# isle-agent-mdns
docker ps | grep isle-agent-mdns

# Health check
curl http://localhost:8888/health
```

---

## Port Summary

All ports are non-conflicting:

- **80, 443** → localhost-mdns (HTTPS proxy)
- **8080** → localhost-mdns frontend (optional)
- **8100** → localhost-mdns backend (optional)
- **8888** → isle-agent-mdns (API)
- **5353/udp** → OpenWRT mDNS (multicast, not bound to host port)

---

## Network Summary

All networks are isolated:

- **br-mgmt** → OpenWRT management (192.168.1.0/24)
- **isle-br-0** → OpenWRT to containers (no IP)
- **meshnet** → localhost-mdns Docker network
- **isle-mdns-net** → isle-agent-mdns Docker network

---

## Resources Required

- **RAM:** ~500-800MB total
- **Disk:** ~650MB total
- **CPU:** Minimal

---

## Next Steps

1. ✅ All components can run together safely
2. ⚠️ Add mDNS detection to localhost-mdns
3. 📝 Test end-to-end integration
4. 🚀 Implement DHCP setup in isle-agent-mdns based on received mDNS

---

## See Also

- [CONFLICT-ANALYSIS.md](/home/dustin/Desktop/IsleMesh/CONFLICT-ANALYSIS.md) - Full conflict analysis
- [check-conflicts.sh](/home/dustin/Desktop/IsleMesh/check-conflicts.sh) - Automated conflict checker
- [README.md](README.md) - isle-agent-mdns documentation
- [QUICK-START.md](QUICK-START.md) - Quick start guide
