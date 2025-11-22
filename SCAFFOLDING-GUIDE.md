# Isle CLI Scaffolding Guide

Quick reference for using the Isle CLI scaffolding system to create localhost-mdns and isle applications.

## Two App Types

### localhost-mdns
- **Purpose:** Localhost-only applications
- **Network:** Docker bridge (no router)
- **mDNS:** Via `/etc/hosts` or system resolver
- **DHCP:** No
- **Use case:** Development, personal tools

### isle
- **Purpose:** vLAN networked applications
- **Network:** macvlan on OpenWRT router
- **mDNS:** Via isle-agent-mdns (receives from router)
- **DHCP:** Yes (configured on router)
- **Use case:** Production-like multi-device apps

---

## Quick Commands

### List Apps by Type

```bash
# List localhost-mdns apps
isle localhost list

# List isle apps
isle localhost list-isle

# List all apps
isle localhost list-all
```

### Manage localhost-mdns Apps

```bash
# Start
isle localhost up <app-name>

# Stop
isle localhost down <app-name>

# Status
isle localhost status <app-name>

# Logs
isle localhost logs <app-name>
```

### Manage isle Apps

```bash
# Start (future)
isle app up <app-name>

# Stop
isle app down <app-name>
```

---

## Scaffolding Structure

```
isle-cli/scaffolding/
├── localhost-mdns/           # Base templates
│   ├── nginx/
│   │   └── proxy.conf.j2     # Nginx config
│   ├── docker/
│   │   └── docker-compose.yml.j2
│   └── app/
│       └── metadata.json.j2  # App metadata
│
└── isle/                     # Extends localhost-mdns
    ├── nginx/
    │   └── proxy.conf.j2     # Adds router support
    ├── docker/
    │   └── docker-compose.yml.j2  # Adds DHCP/vLAN
    └── app/
        └── metadata.json.j2  # Isle-specific metadata
```

**Key principle:** Isle builds on top of localhost-mdns

---

## App Metadata

Every app has `metadata.json` that tracks its type:

### localhost-mdns metadata

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

### isle metadata

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

**Location:** `$HOME/.isle/apps/<app-name>/metadata.json`

---

## Template Variables

### Common (both types)

| Variable | Description | Example |
|----------|-------------|---------|
| `app_name` | Application name | `my-app` |
| `base_domain` | Base domain | `mesh-app.local` |
| `base_domain_cert` | Certificate name | `mesh-app` |
| `services` | List of services | `[{name: "backend", ...}]` |

### Service Variables

| Variable | Description |
|----------|-------------|
| `service.name` | Service name |
| `service.internal_port` | Container port |
| `service.external_port` | Host port |
| `service.use_mtls` | Use mTLS? |

### isle-Specific

| Variable | Description | Default |
|----------|-------------|---------|
| `vlan_id` | VLAN ID | `10` |
| `router_ip` | Router IP | `10.10.0.1` |
| `router_mgmt_ip` | Management IP | `192.168.1.1` |
| `dhcp_range_start` | DHCP start | `10.10.0.100` |
| `dhcp_range_end` | DHCP end | `10.10.0.200` |
| `isle_agent_mdns_enabled` | Enable mDNS receiver | `true` |

---

## Examples

### Example 1: Create localhost-mdns App

```bash
# Scaffold app
isle scaffold docker-compose.yml \
  --type localhost-mdns \
  -d my-app.local \
  -n my-app

# List apps
isle localhost list

# Start app
isle localhost up my-app

# Test
curl https://my-app.local -k
```

**Generated structure:**
```
$HOME/.isle/apps/my-app/
├── docker-compose.yml
├── metadata.json          # type: "localhost-mdns"
├── proxy/
│   └── nginx.conf         # From localhost-mdns template
└── ssl/
    ├── certs/
    └── keys/
```

### Example 2: Create isle App

```bash
# Scaffold app
isle scaffold docker-compose.yml \
  --type isle \
  --vlan 10 \
  -d my-isle-app.local \
  -n my-isle-app

# List apps
isle localhost list-isle

# Start app (with DHCP setup)
isle app up my-isle-app

# Access from network
curl https://my-isle-app.local -k
```

**Generated structure:**
```
$HOME/.isle/apps/my-isle-app/
├── docker-compose.yml     # Includes macvlan network
├── metadata.json          # type: "isle", DHCP config
├── proxy/
│   └── nginx.conf         # From isle template (extends localhost-mdns)
└── ssl/
    ├── certs/
    └── keys/
```

---

## Workflow

### Development → Production

1. **Start with localhost-mdns**
   ```bash
   isle scaffold app.yml --type localhost-mdns
   isle localhost up my-app
   # Develop and test locally
   ```

2. **Migrate to isle** (future feature)
   ```bash
   isle migrate my-app --to isle --vlan 10
   # App now uses router and DHCP
   ```

---

## How isle Extends localhost-mdns

### Template Inheritance

isle templates extend localhost-mdns using Jinja2:

```jinja2
{% extends "../localhost-mdns/nginx/proxy.conf.j2" %}

{% block extra_upstreams %}
    upstream router {
        server {{ router_ip }}:80;
    }
{% endblock %}

{% block extra_servers %}
    server {
        listen 443 ssl;
        server_name router.{{ base_domain }};
        # Router proxy config
    }
{% endblock %}
```

**Benefits:**
- Reuses localhost-mdns base config
- Adds router-specific features
- Maintains consistency
- No duplication

---

## Checking App Type

### Via CLI

```bash
# List by type
isle localhost list          # Only localhost-mdns apps
isle localhost list-isle     # Only isle apps
```

### Via Metadata

```bash
# Check app type
cat ~/.isle/apps/my-app/metadata.json | jq '.type'

# Output: "localhost-mdns" or "isle"
```

### In Scripts

The `localhost.sh` script checks app type:

```bash
is_localhost_app() {
    local app_name=$1
    local metadata=$(get_app_metadata "$app_name")
    local app_type=$(echo "$metadata" | jq -r '.type')

    [ "$app_type" = "localhost-mdns" ]
}
```

---

## File Locations

| Path | Description |
|------|-------------|
| `isle-cli/scaffolding/localhost-mdns/` | localhost-mdns templates |
| `isle-cli/scaffolding/isle/` | isle templates |
| `isle-cli/scripts/localhost.sh` | localhost-mdns commands |
| `$HOME/.isle/apps/` | Generated apps |
| `$HOME/.isle/apps/<name>/metadata.json` | App metadata |

---

## Troubleshooting

### App Type Mismatch

**Error:** `App 'my-app' is not a localhost-mdns app`

**Solution:**
```bash
# Check app type
cat ~/.isle/apps/my-app/metadata.json | jq '.type'

# Use correct command:
# - localhost-mdns → isle localhost up <app>
# - isle → isle app up <app>
```

### App Not Listed

**Problem:** App doesn't appear in list

**Solution:**
```bash
# Verify metadata exists
ls ~/.isle/apps/my-app/metadata.json

# Verify type is set
cat ~/.isle/apps/my-app/metadata.json | jq '.type'

# Should be "localhost-mdns" or "isle"
```

---

## Integration with Components

### localhost-mdns Type

**Components used:**
- Docker bridge network
- Nginx proxy
- SSL certificates
- `/etc/hosts` or system mDNS

**Does NOT use:**
- OpenWRT router
- isle-agent-mdns
- DHCP

### isle Type

**Components used:**
- Docker macvlan network
- Nginx proxy (extends localhost-mdns)
- SSL certificates
- OpenWRT router
- isle-agent-mdns (receives mDNS from router)
- DHCP on router

**Builds on:**
- localhost-mdns templates (extended with router features)

---

## Next Steps

1. ✅ Scaffolding templates created
2. ✅ localhost-mdns commands implemented
3. ✅ App type tracking via metadata
4. ⚠️ Enhanced app commands to support isle type
5. ⚠️ DHCP setup automation for isle apps
6. ⚠️ Migration command (localhost-mdns → isle)

---

## Related Documentation

- [Scaffolding README](isle-cli/scaffolding/README.md) - Detailed template documentation
- [localhost-mdns Reference](mesh-prototypes/localhost-mdns/README.md)
- [isle-agent-mdns](isle-agent-mdns/README.md)
- [Conflict Analysis](CONFLICT-ANALYSIS.md)

---

**Last Updated:** 2025-11-13
