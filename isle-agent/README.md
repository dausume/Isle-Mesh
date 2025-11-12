# Isle Agent - Unified Nginx Proxy Container

The Isle Agent is a **single nginx proxy container** that serves all mesh applications on a device, with a virtual MAC address for isolated OpenWRT router connectivity.

## Architecture Overview

### Key Concept: Unified Proxy

Instead of running a separate nginx proxy container for each mesh app, the Isle Agent provides:

1. **Single Container**: One `isle-agent` container per device
2. **Virtual MAC**: Uses MAC address `02:00:00:00:0a:01` for OpenWRT DHCP isolation
3. **Config Fragments**: Each mesh app generates its own nginx config fragment
4. **Merged Configuration**: All fragments are included in the master `nginx.conf`
5. **Zero-Downtime Reload**: Apps can spin up/down without affecting other apps

### Benefits

- **Resource Efficient**: One nginx container instead of N containers
- **Simplified Network**: Single container with virtual MAC for router integration
- **Independent Apps**: Each app maintains its own config without affecting others
- **Conflict Detection**: Registry prevents domain/subdomain collisions
- **Easy Debugging**: All proxy logs in one place

## Directory Structure

```
/etc/isle-mesh/agent/                    # Agent configuration directory
├── docker-compose.yml                   # Agent container definition
├── nginx.conf                           # Master nginx config (auto-generated)
├── registry.json                        # Domain/subdomain registry
├── configs/                             # Per-app config fragments
│   ├── app1.conf                        # App1's nginx config fragment
│   ├── app2.conf                        # App2's nginx config fragment
│   └── ...
├── ssl/                                 # Shared SSL certificates
│   ├── certs/
│   │   ├── app1.local.crt
│   │   ├── app2.local.crt
│   │   └── ...
│   └── keys/
│       ├── app1.local.key
│       ├── app2.local.key
│       └── ...
└── logs/                                # Nginx logs
    ├── access.log
    └── error.log
```

## How It Works

### 1. Agent Container

The isle-agent runs as a standalone docker container:

```yaml
services:
  isle-agent:
    image: nginx:alpine
    container_name: isle-agent
    mac_address: "02:00:00:00:0a:01"  # Virtual MAC for OpenWRT
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /etc/isle-mesh/agent/nginx.conf:/etc/nginx/nginx.conf:ro
      - /etc/isle-mesh/agent/configs:/etc/nginx/configs:ro
      - /etc/isle-mesh/agent/ssl:/etc/nginx/ssl:ro
```

### 2. Config Fragment Generation

When a mesh app is deployed, it generates a config fragment:

```bash
# Generate fragment for myapp
python3 generate-app-fragment.py \
    --app-name myapp \
    --compose docker-compose.yml \
    --domain myapp.local \
    --output /etc/isle-mesh/agent/configs/myapp.conf
```

**Fragment structure:**
```nginx
# Upstream for myapp services
upstream myapp_backend {
    server backend:8443;
}

# HTTP redirect to HTTPS
server {
    listen 80;
    server_name myapp.local api.myapp.local;
    return 301 https://$host$request_uri;
}

# HTTPS server for myapp.local
server {
    listen 443 ssl http2;
    server_name myapp.local;
    ssl_certificate /etc/nginx/ssl/certs/myapp.local.crt;
    ssl_certificate_key /etc/nginx/ssl/keys/myapp.local.key;

    location / {
        proxy_pass https://myapp_backend;
        # ... proxy settings ...
    }
}

# HTTPS server for api.myapp.local
server {
    listen 443 ssl http2;
    server_name api.myapp.local;
    # ... similar config ...
}
```

### 3. Master Config Merging

The master `nginx.conf` includes all fragments:

```nginx
http {
    # ... base nginx config ...

    # Include all mesh-app config fragments
    include /etc/nginx/configs/*.conf;
}
```

This allows:
- **Independent updates**: Modify one app's config without touching others
- **Automatic inclusion**: New apps are automatically included via wildcard
- **Validation**: Each fragment can be validated independently

### 4. Domain Registry

The registry tracks claimed domains and subdomains:

```json
{
  "domains": {
    "app1.local": "app1",
    "app2.local": "app2"
  },
  "subdomains": {
    "api.app1.local": "app1",
    "db.app1.local": "app1",
    "api.app2.local": "app2"
  },
  "apps": {
    "app1": {
      "domain": "app1.local",
      "services": 2,
      "subdomains": ["api.app1.local", "db.app1.local"],
      "updated_at": "2025-11-08T09:45:00"
    },
    "app2": {
      "domain": "app2.local",
      "services": 1,
      "subdomains": ["api.app2.local"],
      "updated_at": "2025-11-08T09:50:00"
    }
  }
}
```

**Conflict prevention:**
- Before generating a fragment, check if domain/subdomains are already claimed
- If conflict exists, error and block the app from registering
- Provides clear error messages with suggestions

### 5. Hot Reload

When app configs change:

```bash
# Merge and validate all configs
isle agent merge

# Reload nginx (zero-downtime)
isle agent reload
```

The reload process:
1. Tests new configuration for syntax errors
2. If valid, signals nginx to gracefully reload
3. Active connections continue uninterrupted
4. New connections use updated config

## CLI Commands

### Agent Lifecycle

```bash
# Start the isle-agent container
isle agent start

# Stop the isle-agent container
isle agent stop

# Restart the isle-agent container
isle agent restart

# Show agent status and registered apps
isle agent status

# Reload nginx config (zero-downtime)
isle agent reload
```

### Configuration Management

```bash
# Merge all app configs and validate
isle agent merge

# Validate all config fragments
isle agent validate

# Test nginx configuration
isle agent test

# Show summary of registered apps
isle agent summary
```

### Logging & Debug

```bash
# Show recent agent logs
isle agent logs

# Tail agent logs in real-time
isle agent logs follow
```

## Typical Workflow

### Initial Setup

```bash
# 1. Start the isle-agent (once per device)
sudo isle agent start

# 2. Verify agent is running
isle agent status
```

### Deploying Mesh Apps

```bash
# 3. Scaffold a mesh app (auto-generates fragment)
isle app scaffold python-app docker-compose.yml

# 4. Deploy the app (auto-registers with agent)
cd python-app
isle app up

# 5. Verify app is registered
isle agent status
```

### Making Changes

```bash
# 6. Modify app's docker-compose.yml or services
vim docker-compose.yml

# 7. Regenerate fragment
isle app config-rebuild

# 8. Reload agent
isle agent reload
```

### Removing Apps

```bash
# 9. Stop and remove app
isle app down -v

# 10. Clean up registry
isle agent merge  # Automatically removes deleted apps
```

## Technical Details

### Virtual MAC Address

The agent uses a locally administered MAC address:
- **Format**: `02:00:00:00:VLAN:XX`
- **Agent MAC**: `02:00:00:00:0a:01`
- **Purpose**: Allows OpenWRT router to assign an isolated IP via DHCP
- **Benefit**: Host machine's real MAC/IP is never exposed to mesh network

### Network Isolation

```
Mesh App Containers ──┐
                      ├─> Docker Bridge Network ──> isle-agent (virtual MAC) ──> OpenWRT Router ──> Other Mesh Devices
Mesh App Containers ──┘
```

- **App containers**: Connect to shared docker network
- **isle-agent**: Single point of contact with virtual MAC
- **OpenWRT router**: Sees only the agent's virtual MAC, not host MAC
- **Mesh traffic**: Routed through OpenWRT for isolation and security

### Config Fragment Template

Fragments are generated using Jinja2 templates:
- **Location**: `isle-agent/templates/app-fragment.conf.j2`
- **Segments**: Modular includes for upstreams, servers, security headers
- **Customization**: Per-app mTLS, CORS, rate limiting, etc.

### Registry Conflict Detection

Before allowing an app to register:

```python
def check_conflicts(app_name, domain, services, registry):
    conflicts = []

    # Check domain claim
    if domain in registry["domains"]:
        existing_app = registry["domains"][domain]
        if existing_app != app_name:
            conflicts.append(f"Domain '{domain}' claimed by '{existing_app}'")

    # Check subdomain claims
    for service in services:
        subdomain_fqdn = f"{service['subdomain']}.{domain}"
        if subdomain_fqdn in registry["subdomains"]:
            existing_app = registry["subdomains"][subdomain_fqdn]
            if existing_app != app_name:
                conflicts.append(f"Subdomain '{subdomain_fqdn}' claimed by '{existing_app}'")

    return conflicts
```

Conflicts result in clear errors:
```
CONFLICT ERRORS:
  ✗ Domain 'myapp.local' is already claimed by app 'existing-app'
  ✗ Subdomain 'api.myapp.local' is already claimed by app 'existing-app'

Use --force to override conflict checking
```

## Comparison: Old vs New Architecture

### Old: Per-App Proxies

```
mesh-app-1/
  ├── mesh-proxy (nginx container) ──> OpenWRT
  └── services

mesh-app-2/
  ├── mesh-proxy (nginx container) ──> OpenWRT
  └── services

mesh-app-3/
  ├── mesh-proxy (nginx container) ──> OpenWRT
  └── services
```

**Problems:**
- N nginx containers = N * resource usage
- N virtual MACs needed for OpenWRT
- Complex network management
- Config regeneration for each app

### New: Unified Isle Agent

```
mesh-app-1/
  └── services ──┐
                 ├──> Docker Network ──> isle-agent (single nginx, one virtual MAC) ──> OpenWRT
mesh-app-2/      │
  └── services ──┤
                 │
mesh-app-3/      │
  └── services ──┘
```

**Benefits:**
- One nginx container = minimal resource usage
- One virtual MAC for all apps
- Simple network topology
- Independent config fragments per app
- Zero-downtime updates

## Migration from mesh-proxy

If you have existing mesh apps using the old mesh-proxy pattern:

```bash
# 1. Start the unified isle-agent
sudo isle agent start

# 2. For each existing app:
cd /path/to/existing-app

# 3. Generate fragment from existing docker-compose
python3 ${PROJECT_ROOT}/isle-agent/scripts/generate-app-fragment.py \
    --app-name myapp \
    --compose docker-compose.yml \
    --domain myapp.local \
    --output /etc/isle-mesh/agent/configs/myapp.conf

# 4. Stop old mesh-proxy container
docker stop myapp_mesh_proxy
docker rm myapp_mesh_proxy

# 5. Update app to connect to isle-agent network
# (Modify docker-compose to use external network: isle-agent-net)

# 6. Restart app
isle app up

# 7. Verify app is registered
isle agent status
```

## Future Enhancements

### Planned Features

1. **Automatic app detection**: Auto-generate fragments when apps start
2. **Dynamic routing**: Update routes without full reload for simple changes
3. **Load balancing**: Support multiple backend instances per service
4. **Advanced mTLS**: Per-subdomain client certificate requirements
5. **Rate limiting**: Per-app and per-subdomain rate limits
6. **WAF integration**: Web Application Firewall rules
7. **Metrics export**: Prometheus metrics for monitoring
8. **Multi-router**: Support for multiple OpenWRT routers with routing logic

### Experimental

- **Bridge automation**: Automatically create/manage network bridges
- **DHCP integration**: Dynamic IP assignment from OpenWRT
- **Service mesh**: Full service mesh with mTLS between all services
- **Canary deployments**: A/B testing and gradual rollouts

## Troubleshooting

### Agent won't start

```bash
# Check docker is running
sudo systemctl status docker

# Check if port 80/443 are in use
sudo netstat -tulpn | grep :80
sudo netstat -tulpn | grep :443

# View agent initialization logs
isle agent logs
```

### Config fragment invalid

```bash
# Validate all fragments
isle agent validate

# Test specific fragment
docker run --rm \
    -v /etc/isle-mesh/agent/configs/myapp.conf:/etc/nginx/conf.d/myapp.conf:ro \
    nginx:alpine nginx -t
```

### App not accessible

```bash
# Check agent status
isle agent status

# Verify app is registered
cat /etc/isle-mesh/agent/registry.json | jq .

# Check nginx error logs
isle agent logs | grep error

# Test DNS resolution
ping myapp.local

# Check mDNS is broadcasting
avahi-browse -a
```

### Domain conflict

```bash
# View all claimed domains
jq -r '.domains' /etc/isle-mesh/agent/registry.json

# View all claimed subdomains
jq -r '.subdomains' /etc/isle-mesh/agent/registry.json

# Manually remove app from registry (if deleted improperly)
jq 'del(.apps.myapp)' /etc/isle-mesh/agent/registry.json > /tmp/registry.json
sudo mv /tmp/registry.json /etc/isle-mesh/agent/registry.json
```

## Contributing

The isle-agent is designed to be extensible. To add features:

1. **New templates**: Add Jinja2 templates in `templates/` and `segments/`
2. **Custom generators**: Extend `generate-app-fragment.py` for special use cases
3. **Registry plugins**: Add custom validators or conflict resolution logic
4. **Agent extensions**: Add new commands to `agent-manager.sh`

## See Also

- **Mesh Proxy (Legacy)**: `mesh-proxy/README.md` - Original per-app proxy design
- **OpenWRT Router**: `openwrt-router/README.md` - Router setup and networking
- **mDNS Broadcasting**: `mdns/manual-mdns-setup.md` - Service discovery
- **CLI Documentation**: `isle help` - Full CLI reference
