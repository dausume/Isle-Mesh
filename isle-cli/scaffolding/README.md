# Isle CLI Scaffolding System

Jinja2 templates for generating Isle Mesh applications.

## Overview

The scaffolding system provides two types of application templates:

1. **localhost-mdns** - Localhost-only applications (no router, no DHCP)
2. **isle** - vLAN applications with DHCP and router integration

**Isle scaffolding builds on top of localhost-mdns**, extending it with router integration and DHCP support.

## Directory Structure

```
scaffolding/
├── localhost-mdns/          # Localhost-only templates
│   ├── nginx/
│   │   └── proxy.conf.j2    # Nginx proxy configuration
│   ├── docker/
│   │   └── docker-compose.yml.j2  # Docker compose template
│   └── app/
│       └── metadata.json.j2 # App metadata tracking
│
└── isle/                    # Isle vLAN templates (extends localhost-mdns)
    ├── nginx/
    │   └── proxy.conf.j2    # Extends localhost-mdns proxy
    ├── docker/
    │   └── docker-compose.yml.j2  # Adds DHCP/vLAN support
    └── app/
        └── metadata.json.j2 # Isle-specific metadata

```

## App Types

### localhost-mdns

**Use case:** Local development, single-machine hosting

**Features:**
- Localhost-only access (127.0.0.1)
- mDNS via `/etc/hosts` or system resolver
- Docker bridge networking
- No router required
- No DHCP setup

**Example apps:**
- Development environments
- Personal tools
- Localhost-only services

**Metadata:**
```json
{
  "type": "localhost-mdns",
  "networking": {
    "mode": "localhost-only",
    "uses_mdns": true,
    "uses_dhcp": false,
    "requires_router": false
  }
}
```

### isle

**Use case:** Multi-device networking, vLAN isolation

**Features:**
- vLAN networking via OpenWRT router
- DHCP reservations for services
- mDNS detection via isle-agent-mdns
- Router integration
- Network isolation

**Example apps:**
- Multi-device applications
- Networked services
- Production-like environments

**Metadata:**
```json
{
  "type": "isle",
  "networking": {
    "mode": "vlan",
    "uses_mdns": true,
    "uses_dhcp": true,
    "requires_router": true,
    "vlan_id": 10,
    "dhcp": {
      "enabled": true,
      "reservations": [...]
    }
  }
}
```

## Template Variables

### Common Variables (both types)

```jinja2
{{ app_name }}           - Application name
{{ base_domain }}        - Base domain (e.g., mesh-app.local)
{{ base_domain_cert }}   - Base certificate name
{{ created_at }}         - Timestamp of creation
{{ description }}        - App description
{{ services }}           - List of services
```

### Service Variables

```jinja2
{{ service.name }}           - Service name
{{ service.internal_port }}  - Container internal port
{{ service.external_port }}  - Host exposed port
{{ service.use_mtls }}       - Whether to use mTLS
```

### Isle-Specific Variables

```jinja2
{{ vlan_id }}                - VLAN ID (default: 10)
{{ router_ip }}              - Router IP on VLAN
{{ router_mgmt_ip }}         - Router management IP
{{ dhcp_range_start }}       - DHCP range start
{{ dhcp_range_end }}         - DHCP range end
{{ isle_agent_mdns_enabled }}- Enable mDNS receiver
{{ service.mac_address }}    - MAC address for DHCP reservation
{{ service.dhcp_ip }}        - Reserved DHCP IP
```

## Usage

### List Apps by Type

```bash
# List localhost-mdns apps only
isle localhost list

# List isle apps only
isle localhost list-isle

# List all apps
isle localhost list-all
```

**Example output:**
```
APP NAME             TYPE            BASE DOMAIN                    STATUS
--------             ----            -----------                    ------
my-local-app         localhost-mdns  mesh-app.local                 running
dev-tools            localhost-mdns  dev.local                      stopped

APP NAME             TYPE            BASE DOMAIN                    VLAN       STATUS
--------             ----            -----------                    ----       ------
production-app       isle            prod.mesh-app.local            10         running
staging-app          isle            staging.mesh-app.local         11         running
```

### Manage localhost-mdns Apps

```bash
# Start a localhost-mdns app
isle localhost up my-app

# Stop a localhost-mdns app
isle localhost down my-app

# View status
isle localhost status my-app

# View logs
isle localhost logs my-app
```

### Manage isle Apps

```bash
# Use standard app commands for isle apps
isle app up my-isle-app
isle app down my-isle-app
```

## Template Extension

### How isle Extends localhost-mdns

The isle nginx template extends localhost-mdns using Jinja2 inheritance:

```jinja2
{% extends "../localhost-mdns/nginx/proxy.conf.j2" %}

{% block extra_upstreams %}
    # Router management upstream
    upstream router {
        server {{ router_ip }}:{{ router_port }};
    }
{% endblock %}

{% block extra_servers %}
    # Router management proxy
    server {
        listen 443 ssl;
        server_name router.{{ base_domain }};
        ...
    }
{% endblock %}
```

This approach:
- ✅ Reuses localhost-mdns base configuration
- ✅ Adds router-specific features
- ✅ Maintains consistency
- ✅ Reduces duplication

## Metadata Tracking

Each app has a `metadata.json` file that tracks its type and configuration:

**Purpose:**
- Determine app type (localhost-mdns vs isle)
- Track networking requirements
- Store DHCP reservations
- Document service configuration

**Location:** `$HOME/.isle/apps/<app-name>/metadata.json`

**Generated from:** `scaffolding/{type}/app/metadata.json.j2`

## Creating New Scaffolding Templates

### 1. localhost-mdns Template

Create your template in `scaffolding/localhost-mdns/`:

```bash
# Add new template
vim scaffolding/localhost-mdns/nginx/custom-feature.conf.j2
```

Use standard Jinja2 syntax:
```jinja2
{% for service in services %}
location /{{ service.name }} {
    proxy_pass http://{{ service.name }};
}
{% endfor %}
```

### 2. isle Template (Extends localhost-mdns)

Create your template in `scaffolding/isle/` that extends the localhost-mdns version:

```jinja2
{% extends "../localhost-mdns/nginx/custom-feature.conf.j2" %}

{% block extra_config %}
    # Isle-specific additions
    upstream router {
        server {{ router_ip }};
    }
{% endblock %}
```

## Best Practices

### 1. Start with localhost-mdns

Always create localhost-mdns templates first, then extend them for isle:

```
1. Create scaffolding/localhost-mdns/<feature>.j2
2. Test with localhost-mdns apps
3. Extend in scaffolding/isle/<feature>.j2
4. Test with isle apps
```

### 2. Use Metadata

Always include app type in metadata:

```json
{
  "type": "localhost-mdns",  // or "isle"
  "scaffolding_type": "localhost-mdns"
}
```

### 3. Document Variables

Document all template variables in comments:

```jinja2
{#
  Variables:
  - app_name: Application name
  - base_domain: Base domain (e.g., mesh-app.local)
#}
```

### 4. Test Both Types

Always test templates with both app types:

```bash
# Test localhost-mdns
isle scaffold my-compose.yml --type localhost-mdns

# Test isle
isle scaffold my-compose.yml --type isle
```

## Integration with Commands

### localhost.sh

Manages localhost-mdns apps:
```bash
isle localhost list       # List localhost-mdns apps
isle localhost up <app>   # Start localhost-mdns app
```

**Checks app type via metadata:**
```bash
# Verifies app.type == "localhost-mdns"
if ! is_localhost_app "$app_name"; then
    log_error "App is not a localhost-mdns app"
fi
```

### app.sh (Future Enhancement)

Will manage isle apps:
```bash
isle app list            # List isle apps
isle app up <app>        # Start isle app (with DHCP)
```

## File Locations

| Path | Description |
|------|-------------|
| `scaffolding/localhost-mdns/` | localhost-mdns templates |
| `scaffolding/isle/` | Isle templates (extends localhost-mdns) |
| `scripts/localhost.sh` | localhost-mdns management commands |
| `$HOME/.isle/apps/<name>/metadata.json` | App metadata |

## Examples

### Example 1: localhost-mdns App

```bash
# Create app
isle scaffold my-compose.yml --type localhost-mdns -d my-app.local

# List apps
isle localhost list

# Start app
isle localhost up my-app

# Access
curl https://my-app.local -k
```

### Example 2: isle App

```bash
# Create app
isle scaffold my-compose.yml --type isle --vlan 10

# List apps
isle localhost list-isle

# Start app (includes DHCP setup)
isle app up my-isle-app

# Access from network
curl https://my-isle-app.local -k
```

## Troubleshooting

### App Not Listed

**Problem:** App doesn't appear in `isle localhost list`

**Solution:**
```bash
# Check metadata
cat ~/.isle/apps/my-app/metadata.json | jq '.type'

# Should return "localhost-mdns" or "isle"
```

### Wrong Command for App Type

**Problem:** `isle localhost up` fails with "not a localhost-mdns app"

**Solution:**
```bash
# Check app type
cat ~/.isle/apps/my-app/metadata.json | jq '.type'

# Use correct command:
# - localhost-mdns → isle localhost up <app>
# - isle → isle app up <app>
```

## Future Enhancements

- [ ] Automatic type detection during scaffold
- [ ] Migration command (localhost-mdns → isle)
- [ ] Template validation
- [ ] Custom scaffolding templates
- [ ] Template versioning

## Related Documentation

- [localhost-mdns Reference](/mesh-prototypes/localhost-mdns/README.md)
- [isle-agent-mdns](/isle-agent-mdns/README.md)
- [OpenWRT Router](/openwrt-router/README.md)

---

**Last Updated:** 2025-11-13
