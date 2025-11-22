# Isle Agent mDNS Receiver

A lightweight Python service that receives mDNS announcements forwarded from `localhost-mdns` (running on host) and exposes them to the container. This solves the problem that **mDNS detection doesn't work reliably in Docker containers**.

## The Problem

- ✅ mDNS works on **localhost** (via localhost-mdns)
- ✅ mDNS works on **OpenWRT router**
- ❌ mDNS **does NOT work in containers** (Avahi/multicast issues)

## The Solution

Instead of trying to make mDNS work in containers, we:
1. Use `localhost-mdns` on the **host** to detect mDNS from the OpenWRT router
2. `localhost-mdns` **forwards/POSTs** the mDNS data to this container service
3. Container receives the data and can set up DHCP or other services

## Architecture

```
┌─────────────────────────────────────────────────────┐
│ OpenWRT Router                                      │
│ Broadcasts mDNS                                     │
└────────────────┬────────────────────────────────────┘
                 │ mDNS multicast
                 ▼
┌─────────────────────────────────────────────────────┐
│ Host Machine                                        │
│                                                     │
│  ┌──────────────────────────────────────┐          │
│  │ localhost-mdns                       │          │
│  │ (detects mDNS from router)           │          │
│  └──────────────┬───────────────────────┘          │
│                 │ HTTP POST                         │
│                 │ /mdns endpoint                    │
│                 ▼                                   │
│  ┌──────────────────────────────────────┐          │
│  │ isle-agent-mdns Container            │          │
│  │                                      │          │
│  │  ┌────────────────────────────────┐ │          │
│  │  │ Falcon API                     │ │          │
│  │  │ Port 8888                      │ │          │
│  │  │                                │ │          │
│  │  │ Endpoints:                     │ │          │
│  │  │ - POST /mdns                   │ │          │
│  │  │ - GET /services                │ │          │
│  │  │ - DELETE /services/:name/remove│ │          │
│  │  │                                │ │          │
│  │  │ Can trigger DHCP setup         │ │          │
│  │  └────────────────────────────────┘ │          │
│  └──────────────────────────────────────┘          │
│                                                     │
└─────────────────────────────────────────────────────┘
```

## Why This Approach?

| Approach | Works? | Why/Why not? |
|----------|--------|--------------|
| mDNS in container | ❌ | Multicast doesn't work reliably in Docker |
| Avahi in container | ❌ | Requires D-Bus, capabilities, still unreliable |
| **localhost-mdns → container** | ✅ | **Host detects mDNS, forwards via HTTP** |

## Design Philosophy

Built from scratch based on the `localhost-mdns` reference implementation:

1. **Simple Python service** using Falcon framework (like localhost-mdns/backend)
2. **Containerized** with Docker (like localhost-mdns)
3. **Receives HTTP POSTs** - localhost-mdns forwards mDNS data to it
4. **Clean entrypoint** pattern from localhost-mdns/backend/entrypoint.sh
5. **No Avahi/zeroconf** - just receives HTTP requests
6. **Container networking** - exposed port allows host to communicate

## Key Features

- **📥 Receives mDNS data** forwarded from localhost-mdns on host
- **🌐 HTTP API** on port 8888 for receiving and querying services
- **💾 In-Memory Storage** of received mDNS services
- **🔄 Real-Time Updates** as localhost-mdns sends new data
- **🚀 DHCP Setup** capability based on received mDNS info (future)
- **💚 Health Checks** for monitoring

## Installation

### Prerequisites

1. Docker and docker-compose installed
2. `localhost-mdns` running on host and detecting mDNS from OpenWRT router
3. OpenWRT router broadcasting mDNS services

### Build and Run

```bash
cd /home/dustin/Desktop/IsleMesh/isle-agent-mdns

# Build the image
docker-compose build

# Start the service
docker-compose up -d

# Check logs
docker logs isle-agent-mdns

# Check health
curl http://localhost:8888/health
```

## API Endpoints

### 1. Receive mDNS Data (for localhost-mdns to call)

**Endpoint:** `POST /mdns`

localhost-mdns should POST discovered services here:

```bash
curl -X POST http://localhost:8888/mdns \
  -H "Content-Type: application/json" \
  -d '{
    "name": "router._http._tcp.local.",
    "type": "_http._tcp.local.",
    "addresses": ["10.0.10.1"],
    "port": 80,
    "server": "openwrt.local.",
    "properties": {
      "path": "/",
      "isle-mesh": "true"
    }
  }'
```

**Response:**
```json
{
  "status": "received",
  "service_name": "router._http._tcp.local.",
  "timestamp": "2025-11-13T10:30:45.123456"
}
```

### 2. List All Received Services

**Endpoint:** `GET /services`

```bash
curl http://localhost:8888/services
```

**Response:**
```json
{
  "services": [
    {
      "name": "router._http._tcp.local.",
      "type": "_http._tcp.local.",
      "addresses": ["10.0.10.1"],
      "port": 80,
      "server": "openwrt.local.",
      "properties": {
        "path": "/",
        "isle-mesh": "true"
      },
      "received_at": "2025-11-13T10:30:45.123456",
      "last_updated": "2025-11-13T10:35:12.654321"
    }
  ],
  "count": 1,
  "timestamp": "2025-11-13T10:35:30.000000"
}
```

### 3. Get Specific Service Details

**Endpoint:** `GET /services/{service_name}`

```bash
curl http://localhost:8888/services/router._http._tcp.local.
```

### 4. Remove Service (for localhost-mdns to call)

**Endpoint:** `DELETE /services/{service_name}/remove`

localhost-mdns should call this when a service disappears:

```bash
curl -X DELETE http://localhost:8888/services/router._http._tcp.local./remove
```

**Response:**
```json
{
  "status": "removed",
  "service_name": "router._http._tcp.local."
}
```

### 5. Health Check

**Endpoint:** `GET /health`

```bash
curl http://localhost:8888/health
```

**Response:**
```json
{
  "status": "healthy",
  "service_count": 1,
  "timestamp": "2025-11-13T10:35:30.000000"
}
```

## Integration with localhost-mdns

You need to modify `localhost-mdns` to forward mDNS discoveries to this container.

### Example: localhost-mdns forwarding script

Add this to your localhost-mdns setup:

```python
# In localhost-mdns, when a service is discovered:
import requests

def forward_mdns_to_container(service_data):
    """Forward discovered mDNS to isle-agent-mdns container"""
    try:
        response = requests.post(
            'http://localhost:8888/mdns',
            json=service_data,
            timeout=2
        )
        if response.status_code == 200:
            print(f"✅ Forwarded {service_data['name']} to container")
        else:
            print(f"⚠️ Failed to forward: {response.status_code}")
    except Exception as e:
        print(f"❌ Error forwarding to container: {e}")

# When service is removed:
def notify_service_removed(service_name):
    """Notify container that service was removed"""
    try:
        response = requests.delete(
            f'http://localhost:8888/services/{service_name}/remove',
            timeout=2
        )
        print(f"✅ Notified container of removal: {service_name}")
    except Exception as e:
        print(f"❌ Error notifying removal: {e}")
```

## Container to Container Communication

If you need other containers to access this service, add them to the same network:

```yaml
# In another service's docker-compose.yml
networks:
  - isle-mdns-net

networks:
  isle-mdns-net:
    external: true
```

Then from that container, access via:
```bash
curl http://isle-agent-mdns:8888/services
```

## DHCP Setup (Future Enhancement)

The service is designed to eventually trigger DHCP configuration based on received mDNS:

```python
# Future implementation in app.py
def setup_dhcp_for_service(service_data):
    """Configure DHCP based on mDNS service info"""
    if 'addresses' in service_data:
        ip = service_data['addresses'][0]
        # Configure DHCP reservation, routing, etc.
        print(f"Setting up DHCP for {ip}")
```

## Troubleshooting

### Container Won't Start

```bash
# Check logs
docker logs isle-agent-mdns

# Verify port 8888 is available
netstat -tuln | grep 8888

# Rebuild
docker-compose build --no-cache
docker-compose up -d
```

### localhost-mdns Can't Reach Container

```bash
# Test from host
curl http://localhost:8888/health

# Check port mapping
docker port isle-agent-mdns

# Check network
docker network inspect isle-mdns-net
```

### No Services Appearing

```bash
# Verify localhost-mdns is forwarding data
# Check isle-agent-mdns logs for incoming POSTs
docker logs -f isle-agent-mdns

# Manually test POST
curl -X POST http://localhost:8888/mdns \
  -H "Content-Type: application/json" \
  -d '{"name": "test", "addresses": ["10.0.10.1"]}'

# Check if it was received
curl http://localhost:8888/services
```

### Port 8888 Already in Use

Edit `app.py` and `docker-compose.yml` to use a different port:

```python
# app.py
PORT = 8889  # Change this
```

```yaml
# docker-compose.yml
ports:
  - "8889:8889"  # Change both sides
```

## Comparison with Old Implementation

| Aspect | Old Implementation | This Implementation |
|--------|-------------------|---------------------|
| **Approach** | Try to run Avahi in container | Receive data from host |
| **Complexity** | High (nginx, D-Bus, Avahi, supervisor) | Low (single Python service) |
| **Reliability** | Unreliable (mDNS in containers) | Reliable (HTTP from host) |
| **Dependencies** | Avahi, D-Bus, zeroconf, nginx | Only Falcon |
| **Pattern** | Custom | Based on localhost-mdns |
| **Works?** | ❌ No | ✅ Yes |

## Testing

### Manual Test

```bash
# 1. Start container
docker-compose up

# 2. In another terminal, simulate localhost-mdns sending data
curl -X POST http://localhost:8888/mdns \
  -H "Content-Type: application/json" \
  -d '{
    "name": "test-service._http._tcp.local.",
    "type": "_http._tcp.local.",
    "addresses": ["10.0.10.5"],
    "port": 8080,
    "properties": {"test": "true"}
  }'

# 3. Verify it was received
curl http://localhost:8888/services | jq

# 4. Remove the service
curl -X DELETE http://localhost:8888/services/test-service._http._tcp.local./remove

# 5. Verify it was removed
curl http://localhost:8888/services | jq
```

### Integration Test with localhost-mdns

```bash
# 1. Ensure localhost-mdns is running
cd /home/dustin/Desktop/IsleMesh/mesh-prototypes/localhost-mdns
docker-compose up -d

# 2. Start isle-agent-mdns
cd /home/dustin/Desktop/IsleMesh/isle-agent-mdns
docker-compose up -d

# 3. Add forwarding to localhost-mdns (modify its code to POST here)
# 4. Wait for mDNS from OpenWRT router
# 5. Check if data appears
curl http://localhost:8888/services | jq
```

## Security Considerations

1. **Port Exposure**: Port 8888 is exposed to host (localhost)
2. **No Authentication**: Trusts all POSTs (fine for localhost)
3. **Data Validation**: Validates JSON structure but trusts content
4. **Network Isolation**: Container on separate bridge network

## Development

### Watch logs in real-time

```bash
docker logs -f isle-agent-mdns
```

### Execute commands in container

```bash
docker exec -it isle-agent-mdns /bin/bash
```

### Modify and rebuild

```bash
# Edit app.py
nano app.py

# Rebuild and restart
docker-compose build
docker-compose up -d

# Check logs
docker logs -f isle-agent-mdns
```

## Related Documentation

- **localhost-mdns Reference**: `/mesh-prototypes/localhost-mdns/README.md`
- **OpenWRT Router**: `/openwrt-router/README.md`
- **Falcon Framework**: https://falcon.readthedocs.io/

## Next Steps

1. **Modify localhost-mdns** to forward mDNS data to this container
2. **Implement DHCP setup** based on received mDNS
3. **Add service filtering** (only relevant services)
4. **Add persistence** (optional - save services to disk)

## License

Same as the IsleMesh project.
