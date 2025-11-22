# Quick Start Guide

## TL;DR

```bash
# Build and run
cd /home/dustin/Desktop/IsleMesh/isle-agent-mdns
docker-compose up -d

# Test it works
curl http://localhost:8888/health

# Manually send test mDNS data (simulating localhost-mdns)
curl -X POST http://localhost:8888/mdns \
  -H "Content-Type: application/json" \
  -d '{"name": "test", "addresses": ["10.0.10.1"], "port": 80}'

# View received services
curl http://localhost:8888/services | jq
```

## What This Does

This service:
1. **Receives** mDNS data forwarded from `localhost-mdns` on the host (via HTTP POST)
2. Stores received services in memory
3. Exposes them as JSON at `http://localhost:8888/services`
4. Can trigger DHCP setup based on received mDNS (future)

## The Problem It Solves

- ✅ mDNS works on **localhost** (via localhost-mdns)
- ✅ mDNS works on **OpenWRT router**
- ❌ mDNS **does NOT work in containers**

**Solution:** localhost-mdns on host detects mDNS and forwards it to this container via HTTP POST.

## Architecture

```
OpenWRT Router → broadcasts mDNS
         ↓
localhost-mdns (on host) → detects mDNS
         ↓ HTTP POST to http://localhost:8888/mdns
isle-agent-mdns (container) → receives and stores
         ↓
Triggers DHCP setup (future)
```

## For localhost-mdns to Forward Data

localhost-mdns needs to POST discovered services here:

```bash
# When service is discovered
curl -X POST http://localhost:8888/mdns \
  -H "Content-Type: application/json" \
  -d '{
    "name": "router._http._tcp.local.",
    "addresses": ["10.0.10.1"],
    "port": 80
  }'

# When service is removed
curl -X DELETE http://localhost:8888/services/router._http._tcp.local./remove
```

## Common Commands

```bash
# Start
docker-compose up -d

# Stop
docker-compose down

# View logs
docker logs -f isle-agent-mdns

# Restart
docker-compose restart

# Rebuild
docker-compose build --no-cache
```

## Testing

```bash
# Send test data
curl -X POST http://localhost:8888/mdns \
  -H "Content-Type: application/json" \
  -d '{"name": "test", "addresses": ["10.0.10.1"]}'

# Check if received
curl http://localhost:8888/services | jq

# Remove test data
curl -X DELETE http://localhost:8888/services/test/remove
```

## Next Steps

1. Modify `localhost-mdns` to forward mDNS data to this container
2. Implement DHCP setup based on received mDNS
3. See [README.md](README.md) for full documentation
