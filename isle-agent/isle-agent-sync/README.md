# Isle Agent Sync

The **Isle Agent Sync** is a Python container service that receives mDNS announcements and synchronizes configuration with the Isle VLAN Agent (nginx).

## Purpose

- **Receives mDNS Data**: Listens for HTTP POST requests containing mDNS service information
- **Generates Nginx Configs**: Creates nginx configuration fragments for discovered services
- **Syncs with VLAN Agent**: Updates the isle-vlan-agent with new configurations
- **Provides API**: Exposes endpoints for querying discovered services

## Architecture

```
┌─────────────────────────────────────────────┐
│ Host Machine                                │
│                                             │
│  ┌───────────────────────────────────────┐ │
│  │ mDNS Detector (host service)          │ │
│  └─────────────┬─────────────────────────┘ │
│                │ HTTP POST                  │
│                ▼                            │
│  ┌───────────────────────────────────────┐ │
│  │ isle-agent-sync (Container)           │ │
│  │                                       │ │
│  │ • Receives mDNS data on port 8888     │ │
│  │ • Stores service registry             │ │
│  │ • Generates nginx configs             │ │
│  │ • Notifies isle-vlan-agent            │ │
│  └─────────────┬─────────────────────────┘ │
│                │                            │
│                ▼                            │
│  ┌───────────────────────────────────────┐ │
│  │ isle-vlan-agent (nginx container)     │ │
│  └───────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
```

## Components

### 1. Python API Server (app.py)

A lightweight Falcon-based HTTP API that:
- Receives mDNS service data via POST /mdns
- Stores services in memory
- Exposes query endpoints for service discovery
- (Future) Generates nginx configuration fragments

### 2. Docker Container

- Based on Python 3.11-slim
- Minimal dependencies (only Falcon framework)
- Exposes port 8888 for API access
- No Avahi/mDNS libraries needed (receives data via HTTP)

## API Endpoints

### POST /mdns
Receive mDNS service data from host

**Request:**
```json
{
  "name": "app1._http._tcp.local.",
  "type": "_http._tcp.local.",
  "addresses": ["10.0.10.5"],
  "port": 443,
  "server": "app1.local.",
  "properties": {
    "path": "/",
    "isle-mesh": "true"
  }
}
```

**Response:**
```json
{
  "status": "received",
  "service_name": "app1._http._tcp.local.",
  "timestamp": "2025-12-07T13:00:00.000000"
}
```

### GET /services
List all received services

**Response:**
```json
{
  "services": [
    {
      "name": "app1._http._tcp.local.",
      "type": "_http._tcp.local.",
      "addresses": ["10.0.10.5"],
      "port": 443,
      "received_at": "2025-12-07T13:00:00.000000",
      "last_updated": "2025-12-07T13:05:00.000000"
    }
  ],
  "count": 1,
  "timestamp": "2025-12-07T13:10:00.000000"
}
```

### GET /services/{service_name}
Get details for a specific service

### DELETE /services/{service_name}/remove
Remove a service (called when service disappears)

### GET /health
Health check endpoint

**Response:**
```json
{
  "status": "healthy",
  "service_count": 3,
  "timestamp": "2025-12-07T13:00:00.000000"
}
```

### GET /sync/status
Get sync status and statistics

**Response:**
```json
{
  "status": "running",
  "services_synced": 3,
  "services": ["app1._http._tcp.local.", "app2._http._tcp.local."],
  "timestamp": "2025-12-07T13:00:00.000000"
}
```

## Running the Container

### Build

```bash
cd /home/detts/Isle-Mesh/isle-agent/isle-agent-sync
docker build -t isle-agent-sync .
```

### Run Standalone

```bash
docker run -d \
  --name isle-agent-sync \
  -p 8888:8888 \
  --network isle-agent-net \
  isle-agent-sync
```

### Run with Docker Compose

See the main agent docker-compose.yml

## Integration

### With Host mDNS Detector

The host mDNS detector should POST discovered services:

```python
import requests

def forward_to_sync(service_data):
    try:
        response = requests.post(
            'http://localhost:8888/mdns',
            json=service_data,
            timeout=2
        )
        if response.status_code == 200:
            print(f"✅ Synced: {service_data['name']}")
    except Exception as e:
        print(f"❌ Sync error: {e}")
```

### With isle-vlan-agent

The sync container shares a volume with the nginx container:

```yaml
volumes:
  - /etc/isle-mesh/agent/configs:/app/configs
```

When a service is received, the sync container:
1. Generates an nginx config fragment
2. Writes it to `/app/configs/{service_name}.conf`
3. Signals nginx to reload

## Testing

### Manual Test

```bash
# Start the container
docker-compose up isle-agent-sync

# In another terminal, send test mDNS data
curl -X POST http://localhost:8888/mdns \
  -H "Content-Type: application/json" \
  -d '{
    "name": "test._http._tcp.local.",
    "addresses": ["10.0.10.5"],
    "port": 443
  }'

# Verify it was received
curl http://localhost:8888/services | jq

# Check sync status
curl http://localhost:8888/sync/status | jq

# Remove the service
curl -X DELETE http://localhost:8888/services/test._http._tcp.local./remove

# Verify it was removed
curl http://localhost:8888/services | jq
```

### Health Check

```bash
curl http://localhost:8888/health
```

## Configuration

The container can be configured via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `API_PORT` | `8888` | Port for the API server |
| `CONFIG_DIR` | `/app/configs` | Directory for generated nginx configs |
| `VERBOSE` | `false` | Enable verbose logging |

## Future Enhancements

- [ ] Generate nginx configuration fragments automatically
- [ ] Signal nginx to reload when configs change
- [ ] Persist services to disk (optional)
- [ ] Add authentication for API endpoints
- [ ] Implement config validation before writing
- [ ] Add metrics export (Prometheus)

## Troubleshooting

### Container won't start

```bash
# Check logs
docker logs isle-agent-sync

# Verify port 8888 is available
netstat -tuln | grep 8888

# Rebuild
docker-compose build isle-agent-sync
docker-compose up -d isle-agent-sync
```

### Services not appearing

```bash
# Check if POST requests are reaching the container
docker logs -f isle-agent-sync

# Test manually
curl -X POST http://localhost:8888/mdns \
  -H "Content-Type: application/json" \
  -d '{"name": "test", "addresses": ["10.0.0.1"]}'
```

### API not accessible

```bash
# Check container is running
docker ps | grep isle-agent-sync

# Check network
docker network inspect isle-agent-net

# Check port mapping
docker port isle-agent-sync
```

## See Also

- **isle-host-agent**: Host mDNS broadcaster
- **isle-vlan-agent**: Nginx proxy container
- **Main Agent README**: `/home/detts/Isle-Mesh/isle-agent/README.md`
