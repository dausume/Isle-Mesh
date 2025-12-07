# Isle Agent - Three Component Architecture Setup

This guide explains how to set up and test the new three-component Isle Agent architecture.

## Architecture Overview

The Isle Agent now consists of three independent components:

```
┌─────────────────────────────────────────────────────────┐
│ Host Machine                                            │
│                                                         │
│  ┌───────────────────────────────────────────────────┐ │
│  │ 1. isle-host-agent (systemd service)              │ │
│  │    - Broadcasts mDNS (via mesh-mdns system)       │ │
│  │    - Can relay to sync container                  │ │
│  │    - Three modes: broadcast | relay | both        │ │
│  └───────────────────────────────────────────────────┘ │
│                         │                               │
│                         ▼ (relay mode)                  │
│  ┌───────────────────────────────────────────────────┐ │
│  │ 2. isle-agent-sync (Python container)             │ │
│  │    - Receives mDNS data via HTTP                  │ │
│  │    - Generates nginx config fragments             │ │
│  │    - API on port 8888                             │ │
│  └───────────────┬───────────────────────────────────┘ │
│                  │ (shared volume)                     │
│                  ▼                                      │
│  ┌───────────────────────────────────────────────────┐ │
│  │ 3. isle-vlan-agent (nginx container)              │ │
│  │    - Reverse proxy for all apps                   │ │
│  │    - Virtual MAC: 02:00:00:00:0a:01               │ │
│  │    - Ports: 80, 443                               │ │
│  └───────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

## Quick Start

### 1. Build and Start Container Components

```bash
cd /home/detts/Isle-Mesh/isle-agent

# Build the sync container
docker-compose build isle-agent-sync

# Start both containers
docker-compose up -d

# Check status
docker-compose ps
```

### 2. Verify Containers Are Running

```bash
# Check isle-agent-sync health
curl http://localhost:8888/health
# Expected: {"status": "healthy", "service_count": 0, ...}

# Check isle-vlan-agent health
curl http://localhost/health
# Expected: isle-vlan-agent healthy
```

### 3. (Optional) Install and Start Host Agent

The host agent is optional. By default, you can use the existing `mesh-mdns` system.

```bash
cd /home/detts/Isle-Mesh/isle-agent/isle-host-agent

# Copy files to system locations
sudo mkdir -p /usr/local/bin/isle-mesh
sudo cp isle-host-agent-relay.sh /usr/local/bin/isle-mesh/
sudo chmod +x /usr/local/bin/isle-mesh/isle-host-agent-relay.sh

# Copy config
sudo mkdir -p /etc/isle-mesh/agent
sudo cp host-agent.conf /etc/isle-mesh/agent/

# Copy systemd service
sudo cp isle-host-agent.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# (Optional) Enable and start
sudo systemctl enable isle-host-agent
sudo systemctl start isle-host-agent

# Check status
sudo systemctl status isle-host-agent
```

## Component Details

### Component 1: isle-host-agent

**Location**: `isle-agent/isle-host-agent/`

**Purpose**: Host systemd service for mDNS broadcasting and/or relaying

**Files**:
- `isle-host-agent-relay.sh` - Main relay script
- `isle-host-agent.service` - Systemd service definition
- `host-agent.conf` - Configuration file

**Configuration** (`/etc/isle-mesh/agent/host-agent.conf`):

```bash
# Mode: broadcast | relay | both
ISLE_AGENT_MODE=broadcast

# Relay endpoint (if using relay or both modes)
SYNC_ENDPOINT=http://localhost:8888/mdns

# Relay interval in seconds
RELAY_INTERVAL=30
```

**Modes**:

1. **broadcast** (default): Uses existing `mesh-mdns-broadcast.sh` system
2. **relay**: Only relays to sync container, no mDNS broadcasting
3. **both**: Broadcasts mDNS AND relays to sync container

**Integration with mesh-mdns**:
- Uses same domain list: `/usr/local/etc/mesh-mdns-domains.list`
- Leverages existing broadcast script: `/usr/local/bin/isle-mesh/mesh-mdns-broadcast.sh`
- No duplication of functionality

### Component 2: isle-agent-sync

**Location**: `isle-agent/isle-agent-sync/`

**Purpose**: Python container that receives mDNS data and generates nginx configs

**Files**:
- `app.py` - Falcon API server
- `Dockerfile` - Container image definition
- `entrypoint.sh` - Container entrypoint script

**API Endpoints**:

```
POST   /mdns                           - Receive mDNS service data
GET    /services                       - List all received services
GET    /services/{service_name}        - Get specific service
DELETE /services/{service_name}/remove - Remove a service
GET    /health                         - Health check
GET    /sync/status                    - Sync status and statistics
```

**Testing**:

```bash
# Send test mDNS data
curl -X POST http://localhost:8888/mdns \
  -H "Content-Type: application/json" \
  -d '{
    "name": "test-app._http._tcp.local.",
    "addresses": ["10.0.10.5"],
    "port": 443
  }'

# List services
curl http://localhost:8888/services | jq

# Check sync status
curl http://localhost:8888/sync/status | jq
```

**Volumes**:
- `agent-configs:/app/configs` - Shared with vlan-agent for nginx configs
- `/etc/isle-mesh/agent/sync-data:/app/data` - Optional persistence

### Component 3: isle-vlan-agent

**Location**: `isle-agent/isle-vlan-agent/`

**Purpose**: Nginx reverse proxy container with virtual MAC

**Files**:
- `nginx.conf` - Master nginx configuration

**Key Features**:
- Virtual MAC: `02:00:00:00:0a:01`
- Ports: 80 (HTTP), 443 (HTTPS)
- Health endpoint: `GET /health`
- Dynamically loads configs from `/etc/nginx/configs/*.conf`

**Testing**:

```bash
# Health check
curl http://localhost/health

# Test nginx config
docker exec isle-vlan-agent nginx -t

# Reload nginx
docker exec isle-vlan-agent nginx -s reload

# View logs
docker logs isle-vlan-agent
```

**Volumes**:
- `./isle-vlan-agent/nginx.conf:/etc/nginx/nginx.conf:ro` - Master config
- `agent-configs:/etc/nginx/configs:ro` - App fragments (generated by sync)
- `/etc/isle-mesh/agent/ssl:/etc/nginx/ssl:ro` - SSL certificates
- `/etc/isle-mesh/agent/logs:/var/log/nginx` - Logs

## Testing the Full Stack

### 1. Start Everything

```bash
# Start containers
cd /home/detts/Isle-Mesh/isle-agent
docker-compose up -d

# (Optional) Start host agent
sudo systemctl start isle-host-agent
```

### 2. Verify Health

```bash
# Check all containers
docker-compose ps

# Check sync health
curl http://localhost:8888/health | jq

# Check vlan-agent health
curl http://localhost/health

# Check host agent (if running)
sudo systemctl status isle-host-agent
```

### 3. Test mDNS Relay

If using relay or both modes:

```bash
# Edit host agent config
sudo nano /etc/isle-mesh/agent/host-agent.conf
# Set: ISLE_AGENT_MODE=relay

# Restart host agent
sudo systemctl restart isle-host-agent

# Check if domains are being relayed
curl http://localhost:8888/services | jq
```

### 4. Test nginx Config Generation

```bash
# Manually send a service to sync
curl -X POST http://localhost:8888/mdns \
  -H "Content-Type: application/json" \
  -d '{
    "name": "app1._http._tcp.local.",
    "type": "_http._tcp.local.",
    "addresses": ["172.20.0.10"],
    "port": 443,
    "server": "app1.local.",
    "properties": {
      "isle-mesh": "true"
    }
  }'

# Verify it was received
curl http://localhost:8888/services | jq

# TODO: Check if nginx config was generated
# (Config generation is placeholder in current implementation)
```

## Troubleshooting

### Containers Won't Start

```bash
# Check logs
docker-compose logs isle-agent-sync
docker-compose logs isle-vlan-agent

# Rebuild
docker-compose build --no-cache
docker-compose up -d
```

### Port Conflicts

```bash
# Check what's using port 80/443/8888
sudo netstat -tulpn | grep -E ':80|:443|:8888'

# Stop conflicting services
sudo systemctl stop apache2  # if Apache is running
sudo systemctl stop nginx    # if nginx is running
```

### Host Agent Not Broadcasting

```bash
# Check if mesh-mdns is installed
sudo systemctl status mesh-mdns

# Check domain list exists
cat /usr/local/etc/mesh-mdns-domains.list

# Check avahi is running
sudo systemctl status avahi-daemon

# View host agent logs
sudo journalctl -u isle-host-agent -f
```

### Sync Not Receiving Data

```bash
# Test sync endpoint
curl -X POST http://localhost:8888/mdns \
  -H "Content-Type: application/json" \
  -d '{"name": "test", "addresses": ["10.0.0.1"]}'

# Check if host agent is configured to relay
cat /etc/isle-mesh/agent/host-agent.conf | grep ISLE_AGENT_MODE

# Check sync logs
docker logs -f isle-agent-sync
```

## Integration with agent-manager.sh

The agent-manager.sh can be updated to support the new architecture:

### Option 1: Manual Management (Current)

Manage components separately:
- Containers: `docker-compose up/down`
- Host agent: `systemctl start/stop isle-host-agent`

### Option 2: Integrated Management (Future)

Update agent-manager.sh to:
```bash
isle agent start   # Starts all three components
isle agent stop    # Stops all three components
isle agent status  # Shows status of all components
```

This integration is pending and will be added in a future update.

## Cleanup

### Stop Everything

```bash
# Stop containers
cd /home/detts/Isle-Mesh/isle-agent
docker-compose down

# Stop host agent
sudo systemctl stop isle-host-agent
sudo systemctl disable isle-host-agent
```

### Remove Everything

```bash
# Remove containers and volumes
docker-compose down -v

# Remove host agent
sudo systemctl stop isle-host-agent
sudo systemctl disable isle-host-agent
sudo rm /etc/systemd/system/isle-host-agent.service
sudo rm /usr/local/bin/isle-mesh/isle-host-agent-relay.sh
sudo rm /etc/isle-mesh/agent/host-agent.conf
sudo systemctl daemon-reload
```

## Next Steps

1. **Test Basic Functionality**: Start containers and verify health endpoints
2. **Test mDNS Relay**: Configure host agent in relay mode and verify data flow
3. **Implement Config Generation**: Update isle-agent-sync to generate nginx configs
4. **Integrate with agent-manager**: Update agent-manager.sh for unified management
5. **Test with Real Apps**: Deploy mesh apps and verify end-to-end functionality

## See Also

- **isle-host-agent README**: `isle-agent/isle-host-agent/README.md`
- **isle-agent-sync README**: `isle-agent/isle-agent-sync/README.md`
- **isle-vlan-agent README**: `isle-agent/isle-vlan-agent/README.md`
- **Main Agent README**: `isle-agent/README.md`
