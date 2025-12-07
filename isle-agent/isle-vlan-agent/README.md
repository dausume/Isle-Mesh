# Isle VLAN Agent

The **Isle VLAN Agent** is the nginx reverse proxy container that handles HTTP/HTTPS traffic for all mesh applications on the device.

## Purpose

- **Reverse Proxy**: Routes incoming requests to backend application containers
- **SSL Termination**: Handles HTTPS and certificate management
- **Virtual MAC**: Uses a unique MAC address for OpenWRT VLAN isolation
- **Config Fragments**: Dynamically loads app-specific nginx configurations

## Architecture

```
┌─────────────────────────────────────────────┐
│ OpenWRT Router                              │
│ Routes traffic to isle-vlan-agent           │
└────────────┬────────────────────────────────┘
             │ via VLAN / macvlan
             ▼
┌─────────────────────────────────────────────┐
│ Host Machine                                │
│                                             │
│  ┌───────────────────────────────────────┐ │
│  │ isle-vlan-agent (nginx)               │ │
│  │                                       │ │
│  │ MAC: 02:00:00:00:0a:01               │ │
│  │ Ports: 80, 443                        │ │
│  │                                       │ │
│  │ ┌─────────────────────────────────┐  │ │
│  │ │ nginx.conf (master config)      │  │ │
│  │ └─────────────────────────────────┘  │ │
│  │ ┌─────────────────────────────────┐  │ │
│  │ │ /etc/nginx/configs/             │  │ │
│  │ │ ├── app1.conf                   │  │ │
│  │ │ ├── app2.conf                   │  │ │
│  │ │ └── app3.conf                   │  │ │
│  │ └─────────────────────────────────┘  │ │
│  └───────────────┬───────────────────────┘ │
│                  │ proxy to backends        │
│                  ▼                          │
│  ┌───────────────────────────────────────┐ │
│  │ App Containers (backends)             │ │
│  │ - app1-backend:8443                   │ │
│  │ - app2-frontend:3000                  │ │
│  └───────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
```

## Key Features

### 1. Virtual MAC Address

The agent uses a locally administered MAC address for network isolation:

```yaml
mac_address: "02:00:00:00:0a:01"
```

**Benefits:**
- OpenWRT router sees agent as a separate device
- DHCP assigns unique IP based on MAC
- Host machine's real MAC/IP not exposed to mesh

### 2. Config Fragment System

Instead of one monolithic config, the agent uses **fragments**:

```
/etc/nginx/configs/
├── app1.conf    # Generated for app1
├── app2.conf    # Generated for app2
└── app3.conf    # Generated for app3
```

**Master config includes all fragments:**
```nginx
http {
    # ... base config ...
    include /etc/nginx/configs/*.conf;
}
```

**Benefits:**
- Apps can be added/removed independently
- Zero-downtime config reloads
- Clear separation of concerns
- Easy debugging per-app

### 3. SSL/TLS Support

- Certificates stored in `/etc/nginx/ssl/`
- Per-app certificate management
- Supports mTLS for backend services

### 4. Health Endpoint

Always available at:
```
GET http://localhost/health
```

Returns:
```
isle-vlan-agent healthy
```

## Running the Container

### Build

The container uses the official nginx:alpine image, no custom build needed.

### Run with Docker Compose

See the main docker-compose.yml in `/home/detts/Isle-Mesh/isle-agent/`

### Run Standalone

```bash
docker run -d \
  --name isle-vlan-agent \
  --mac-address "02:00:00:00:0a:01" \
  -p 80:80 \
  -p 443:443 \
  -v /etc/isle-mesh/agent/nginx.conf:/etc/nginx/nginx.conf:ro \
  -v /etc/isle-mesh/agent/configs:/etc/nginx/configs:ro \
  -v /etc/isle-mesh/agent/ssl:/etc/nginx/ssl:ro \
  --network isle-agent-net \
  nginx:alpine
```

## Configuration

### Master Config

Location: `/etc/isle-mesh/agent/nginx.conf`

This is copied into the container as the main nginx config. It:
- Sets up basic nginx parameters
- Defines the health check endpoint
- Includes all fragment configs with `include /etc/nginx/configs/*.conf;`

### App Fragments

Location: `/etc/isle-mesh/agent/configs/*.conf`

Each app gets its own fragment file. Example fragment for app1:

```nginx
# Upstream for app1
upstream app1_backend {
    server app1-backend:8443;
}

# HTTP redirect to HTTPS
server {
    listen 80;
    server_name app1.local api.app1.local;
    return 301 https://$host$request_uri;
}

# HTTPS server for app1.local
server {
    listen 443 ssl http2;
    server_name app1.local;

    ssl_certificate /etc/nginx/ssl/certs/app1.local.crt;
    ssl_certificate_key /etc/nginx/ssl/keys/app1.local.key;

    location / {
        proxy_pass https://app1_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### SSL Certificates

Location: `/etc/isle-mesh/agent/ssl/`

Structure:
```
ssl/
├── certs/
│   ├── app1.local.crt
│   ├── app2.local.crt
│   └── ...
└── keys/
    ├── app1.local.key
    ├── app2.local.key
    └── ...
```

## Operations

### Reload Configuration

After adding/modifying config fragments:

```bash
# Test config
docker exec isle-vlan-agent nginx -t

# Reload (zero-downtime)
docker exec isle-vlan-agent nginx -s reload
```

### View Logs

```bash
# Access logs
docker exec isle-vlan-agent cat /var/log/nginx/access.log

# Error logs
docker exec isle-vlan-agent cat /var/log/nginx/error.log

# Follow logs in real-time
docker logs -f isle-vlan-agent
```

### Test Config Syntax

```bash
docker exec isle-vlan-agent nginx -t
```

### Check Running Config

```bash
# View master config
docker exec isle-vlan-agent cat /etc/nginx/nginx.conf

# View all fragments
docker exec isle-vlan-agent ls -la /etc/nginx/configs/

# View specific fragment
docker exec isle-vlan-agent cat /etc/nginx/configs/app1.conf
```

## Integration with Other Components

### isle-agent-sync

The sync container writes config fragments to the shared volume:

```yaml
volumes:
  agent-configs:
    # Shared between isle-agent-sync and isle-vlan-agent

services:
  isle-agent-sync:
    volumes:
      - agent-configs:/app/configs

  isle-vlan-agent:
    volumes:
      - agent-configs:/etc/nginx/configs:ro
```

When sync receives mDNS data:
1. Generates nginx config fragment
2. Writes to `/app/configs/{app}.conf`
3. Signals vlan-agent to reload

### isle-host-agent

The host agent broadcasts mDNS for the agent's domains:
- `agent.local` -> Resolves to vlan-agent IP
- Enables mesh devices to discover the agent

### App Containers

Apps connect to the agent via Docker network:

```yaml
networks:
  - isle-agent-net

networks:
  isle-agent-net:
    external: true
```

The agent proxies requests to apps:
```
Client -> isle-vlan-agent -> app-backend:8443
```

## Networking

### Docker Networks

The agent connects to two networks:

1. **isle-agent-net** (bridge)
   - Internal Docker network
   - Apps connect here to reach agent
   - Subnet: 172.20.0.0/16

2. **isle-br-0** (macvlan)
   - Bridged to host's isle-br-0 interface
   - Connected to OpenWRT router
   - Uses virtual MAC for isolation

### Virtual MAC Address

Format: `02:00:00:00:VLAN:XX`

- `02`: Locally administered bit set
- `00:00:00`: Vendor ID (custom)
- `0a`: VLAN ID (10)
- `01`: Device ID

This MAC ensures:
- OpenWRT DHCP assigns unique IP
- Agent appears as separate L2 device
- Host MAC never exposed to mesh

## Security

### SSL/TLS

- TLSv1.2 and TLSv1.3 only
- Strong cipher suites
- Per-app certificates
- Optional mTLS for backends

### Network Isolation

- Agent runs in isolated container
- Only exposed ports: 80, 443
- No direct host network access
- Virtual MAC prevents MAC spoofing

### Read-Only Mounts

Configs are mounted read-only:
```yaml
volumes:
  - /etc/isle-mesh/agent/nginx.conf:/etc/nginx/nginx.conf:ro
  - /etc/isle-mesh/agent/configs:/etc/nginx/configs:ro
```

Container cannot modify its own config.

## Troubleshooting

### Agent won't start

```bash
# Check if ports are in use
sudo netstat -tulpn | grep :80
sudo netstat -tulpn | grep :443

# Check Docker is running
sudo systemctl status docker

# View container logs
docker logs isle-vlan-agent

# Check for config errors
docker exec isle-vlan-agent nginx -t
```

### Config syntax errors

```bash
# Test config
docker exec isle-vlan-agent nginx -t

# View error details
docker logs isle-vlan-agent
```

### App not accessible

```bash
# Check if fragment exists
docker exec isle-vlan-agent ls /etc/nginx/configs/

# View fragment
docker exec isle-vlan-agent cat /etc/nginx/configs/app1.conf

# Check nginx error log
docker exec isle-vlan-agent cat /var/log/nginx/error.log

# Verify app container is reachable
docker exec isle-vlan-agent wget -O- http://app1-backend:8443
```

### SSL certificate errors

```bash
# Check cert exists
docker exec isle-vlan-agent ls -la /etc/nginx/ssl/certs/

# View cert details
docker exec isle-vlan-agent openssl x509 -in /etc/nginx/ssl/certs/app1.local.crt -text -noout
```

### Reload fails

```bash
# Test config first
docker exec isle-vlan-agent nginx -t

# If valid, reload
docker exec isle-vlan-agent nginx -s reload

# If still fails, restart container
docker restart isle-vlan-agent
```

## Performance Tuning

### Worker Processes

```nginx
worker_processes auto;  # Uses all CPU cores
```

### Worker Connections

```nginx
events {
    worker_connections 1024;  # Increase for high traffic
}
```

### Caching

```nginx
proxy_cache_path /var/cache/nginx levels=1:2 keys_zone=app_cache:10m;

location / {
    proxy_cache app_cache;
    proxy_pass http://backend;
}
```

## Monitoring

### Health Checks

```bash
# Docker health check
docker inspect isle-vlan-agent | jq '.[0].State.Health'

# Manual health check
curl http://localhost/health
```

### Metrics

nginx stub_status (optional):

```nginx
location /nginx_status {
    stub_status;
    allow 127.0.0.1;
    deny all;
}
```

## See Also

- **isle-host-agent**: Host mDNS broadcaster
- **isle-agent-sync**: mDNS receiver and config generator
- **Main Agent README**: `/home/detts/Isle-Mesh/isle-agent/README.md`
- **Nginx Documentation**: https://nginx.org/en/docs/
