# Local Agent App - Test Case

**Test Type**: Isle-Agent integration with localhost-mdns
**Purpose**: Demonstrate unified isle-agent proxy with multi-service mesh app
**Pattern**: Full-stack app (backend + frontend) using isle-agent

## Overview

This test case demonstrates a mesh app that integrates with the **unified isle-agent** instead of using a per-app proxy. It showcases:

- **Backend Service**: Python/Falcon API with mTLS (HTTPS + client cert)
- **Frontend Service**: Python/Falcon HTML server (HTTP, proxied to HTTPS by isle-agent)
- **Unified Proxy**: Uses isle-agent instead of dedicated mesh-proxy
- **mDNS**: Localhost-only .local domain resolution
- **SSL**: Self-signed certificates for local development

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Host Machine (localhost only)                          │
│                                                          │
│  ┌────────────────────────────────────────────────┐    │
│  │ isle-agent (unified nginx proxy)                │    │
│  │ - Handles all mesh apps on this device          │    │
│  │ - https://api.local-app.local → backend:8443    │    │
│  │ - https://app.local-app.local → frontend:8080   │    │
│  └────────────────────────────────────────────────┘    │
│                   ↓                ↓                     │
│  ┌─────────────────┐   ┌──────────────────┐            │
│  │ Backend         │   │ Frontend         │            │
│  │ - Port 8443     │   │ - Port 8080      │            │
│  │ - mTLS enabled  │   │ - HTTP only      │            │
│  └─────────────────┘   └──────────────────┘            │
│                                                          │
│  mDNS broadcasts:                                        │
│  - local-app.local                                       │
│  - api.local-app.local                                   │
│  - app.local-app.local                                   │
└─────────────────────────────────────────────────────────┘
```

## Directory Structure

```
local-agent-app/
├── backend/
│   ├── app.py                    # Backend API (mTLS)
│   ├── Dockerfile               # Backend container
│   └── entrypoint.sh            # Startup script
├── frontend/
│   ├── app.py                    # Frontend HTML (HTTP)
│   └── Dockerfile               # Frontend container
├── ssl/
│   ├── certs/                    # SSL certificates
│   └── keys/                     # SSL keys
├── config/
│   └── .env                      # Environment variables
├── docker-compose.yml            # App services (no proxy!)
├── setup.yml                     # Environment config
├── isle-mesh.yml                 # Mesh configuration
└── README.md                     # This file
```

## Prerequisites

1. **isle-agent must be running**:
   ```bash
   sudo isle agent start
   isle agent status
   ```

2. **SSL certificates generated**:
   ```bash
   # Generate certs for local-app.local
   isle ssl generate local-app.local
   ```

3. **mDNS must be configured**:
   ```bash
   # Setup mDNS for .local domains
   isle mdns enable
   ```

## Quick Start

### 1. Start the isle-agent (if not already running)

```bash
sudo isle agent start
```

### 2. Deploy the app

```bash
cd test-mesh-apps/local-agent-app

# Option A: Using isle CLI
isle app up

# Option B: Manual deployment
docker compose up --build
```

### 3. Register with isle-agent

```bash
# Generate nginx fragment for this app
python3 ../../isle-agent/scripts/generate-app-fragment.py \
    --app-name local-app \
    --compose docker-compose.yml \
    --domain local-app.local \
    --mode local \
    --output /etc/isle-mesh/agent/configs/local-app.conf

# Reload isle-agent to pick up the new config
sudo isle agent reload
```

### 4. Setup mDNS broadcasting

```bash
# Broadcast .local domains
isle mdns publish local-app.local
isle mdns publish api.local-app.local
isle mdns publish app.local-app.local
```

### 5. Access the app

```bash
# Frontend
https://app.local-app.local

# Backend API
https://api.local-app.local
```

## Testing

### Verify Services

```bash
# Check app containers are running
docker ps | grep local-app

# Check isle-agent knows about the app
isle agent status

# Check mDNS is broadcasting
avahi-browse -a | grep local-app
```

### Test Endpoints

```bash
# Frontend (no mTLS)
curl -k https://app.local-app.local

# Backend (requires mTLS)
curl -k \
  --cert ssl/certs/local-app.local.crt \
  --key ssl/keys/local-app.local.key \
  https://api.local-app.local
```

## Key Differences from Standard Mesh Apps

| Aspect | Standard Mesh App | Local Agent App |
|--------|------------------|-----------------|
| Proxy | Dedicated mesh-proxy per app | Shared isle-agent |
| Network | App-specific meshnet | External isle-agent-net |
| Config | Complete nginx.conf | Fragment only |
| Reload | Restart proxy container | Reload isle-agent |
| Resources | N × proxy overhead | 1 × shared proxy |

## Cleanup

```bash
# Stop the app
docker compose down -v

# Remove from isle-agent
sudo rm /etc/isle-mesh/agent/configs/local-app.conf
sudo isle agent reload

# Stop broadcasting
isle mdns unpublish local-app.local
isle mdns unpublish api.local-app.local
isle mdns unpublish app.local-app.local
```

## Troubleshooting

### App not accessible

```bash
# 1. Check isle-agent is running
isle agent status

# 2. Check app is registered
cat /etc/isle-mesh/agent/registry.json | jq .

# 3. Check nginx config is valid
isle agent validate

# 4. Check mDNS is broadcasting
avahi-browse -a
```

### mTLS errors for backend

```bash
# Ensure SSL certs are correctly mounted
docker exec local-app_backend ls -la /ssl/certs/
docker exec local-app_backend ls -la /ssl/keys/

# Verify cert paths in app code match mounted locations
```

### Domain conflicts

```bash
# Check registry for conflicts
jq -r '.domains' /etc/isle-mesh/agent/registry.json
jq -r '.subdomains' /etc/isle-mesh/agent/registry.json
```

## Next Steps

This test case demonstrates:

- ✓ Multi-service app with isle-agent
- ✓ Mixed mTLS (backend) and non-mTLS (frontend) services
- ✓ Localhost-mdns integration
- ✓ Unified proxy management

Use this pattern for:
- Local development environments
- Self-hosted apps on a single machine
- Testing mesh app conversion
- Learning isle-agent integration
