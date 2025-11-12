# Dual-Domain Support in Isle CLI

The Isle CLI (`isle app init` and `isle app scaffold`) now automatically generates nginx configurations that respond to both `.local` and `.vlan` domains for seamless integration with the Isle Mesh join protocol.

## What Changed

### Automatic Dual-Domain nginx Configs

When you use `isle app init` or `isle app scaffold` with a `.local` domain, the generated nginx configurations will automatically include both the `.local` and `.vlan` variants in `server_name` directives.

**Example:**
```bash
isle app init -d myapp.local
```

**Generated nginx configuration:**
```nginx
server {
    listen 80;
    # Dual-domain support: mDNS (.local) and mesh DNS (.vlan)
    server_name myapp.local myapp.vlan;

    location / {
        # Your configuration
    }
}

server {
    listen 443 ssl;
    # Dual-domain support: mDNS (.local) and mesh DNS (.vlan)
    server_name frontend.myapp.local frontend.myapp.vlan;

    ssl_certificate /ssl/certs/myapp.crt;
    ssl_certificate_key /ssl/keys/myapp.key;

    location / {
        proxy_pass http://frontend;
    }
}
```

## Usage

### Initialize a New Mesh App

```bash
# Basic initialization with dual-domain support
isle app init -d myapp.local

# Convert existing docker-compose with dual-domain
isle app init -f docker-compose.yml -d myapp.local

# Full customization
isle app init -f app.yml -d myapp.local -n myproject -o ./output
```

### Scaffold Existing Docker Compose

```bash
# Scaffold with default domain
isle app scaffold docker-compose.yml

# Scaffold with custom domain
isle app scaffold docker-compose.yml -d myapp.local -o ./mesh-output

# Scaffold with specific project name
isle app scaffold ./app/docker-compose.yml -n myapp -d myapp.local
```

## How It Works

### Template System

The CLI uses Jinja2 templates located in `/mesh-proxy/segments/` to generate nginx configurations. Each template now includes conditional logic to add `.vlan` domains when the base domain ends with `.local`:

**HTTP Subdomain Template** (`server-http-subdomain.conf.j2`):
```jinja
server {
    listen 80;
    # Dual-domain support: mDNS (.local) and mesh DNS (.vlan)
    server_name {{ subdomain }}.{{ base_domain }} {% if base_domain.endswith('.local') %}{{ subdomain }}.{{ base_domain.replace('.local', '.vlan') }}{% endif %};

    location / {
        proxy_pass http://{{ upstream_name }};
    }
}
```

### Generated Configuration Example

For a service `backend` with domain `mesh-app.local`, the CLI generates:

```nginx
server {
    listen 80;
    server_name backend.mesh-app.local backend.mesh-app.vlan;
    # ...
}

server {
    listen 443 ssl;
    server_name backend.mesh-app.local backend.mesh-app.vlan;
    # ...
}
```

## Integration with Join Protocol

The dual-domain nginx configs work seamlessly with the Isle Mesh join protocol:

1. **Agent advertises** `myserver.local` via mDNS
2. **Join protocol discovers** and creates DNS mapping for `myserver.vlan`
3. **nginx responds** to both domains with the same configuration
4. **Users can access** via either:
   - `http://myserver.local` (mDNS)
   - `http://myserver.vlan` (Mesh DNS)

## Updated Templates

The following nginx template segments now include dual-domain support:

- `server-http-base.conf.j2` - Base domain HTTP server
- `server-https-base.conf.j2` - Base domain HTTPS server
- `server-http-subdomain.conf.j2` - Subdomain HTTP servers
- `server-https-subdomain-simple.conf.j2` - Subdomain HTTPS (no mTLS)
- `server-https-subdomain-mtls.conf.j2` - Subdomain HTTPS with mTLS

## Examples

### Example 1: Simple Web App

```bash
# Create project
mkdir my-web-app && cd my-web-app
isle app init -d webapp.local

# Edit docker-compose.mesh-app.yml to add services
# Then start the mesh app
isle app up --build
```

**Result:**
- Services accessible via both `frontend.webapp.local` and `frontend.webapp.vlan`
- Automatic SSL support for both domains
- Join protocol auto-discovers and maps domains

### Example 2: Convert Existing App

```bash
# You have docker-compose.yml with services: frontend, backend, db
isle app scaffold docker-compose.yml -d myapp.local -o ./mesh-app

# Generated nginx config includes:
# - myapp.local / myapp.vlan
# - frontend.myapp.local / frontend.myapp.vlan
# - backend.myapp.local / backend.myapp.vlan
```

### Example 3: Multi-Service Mesh

```bash
# Initialize with custom name
isle app init -d services.local -n my-microservices

# Add services to docker-compose.mesh-app.yml:
# - api-gateway
# - user-service
# - auth-service
# - database

# All services will be accessible via both .local and .vlan
```

## Testing Dual-Domain Support

After running `isle app init` or `isle app scaffold`:

1. **Check generated nginx config:**
   ```bash
   cat proxy/nginx-mesh-proxy.conf
   # Look for server_name directives with both .local and .vlan
   ```

2. **Start the mesh app:**
   ```bash
   isle app up --build
   ```

3. **Test .local domain:**
   ```bash
   curl http://frontend.myapp.local
   ```

4. **Wait for join protocol** (30 seconds max)

5. **Test .vlan domain:**
   ```bash
   curl http://frontend.myapp.vlan
   ```

Both should return the same content!

## Non-.local Domains

If you use a domain that doesn't end in `.local`, the CLI will only generate single-domain configs:

```bash
# Using .com domain
isle app init -d myapp.com

# Generated nginx config:
# server_name myapp.com (no .vlan variant)
```

This is intentional - the `.vlan` suffix is only added for mDNS-compatible `.local` domains that integrate with the join protocol.

## Troubleshooting

### nginx Shows Only .local Domain

If generated configs only show `.local` domains:

1. Verify you used a `.local` domain:
   ```bash
   grep "domain:" setup.yml
   # Should show: domain: myapp.local
   ```

2. Regenerate configs:
   ```bash
   # Re-run scaffold
   isle app scaffold docker-compose.yml -d myapp.local
   ```

3. Check template files in `/mesh-proxy/segments/` for dual-domain logic

### .vlan Domain Not Resolving

If `.local` works but `.vlan` doesn't:

1. Ensure join protocol is running on router:
   ```bash
   ssh root@192.168.1.1 '/etc/init.d/isle-join-protocol status'
   ```

2. Check DNS mappings:
   ```bash
   ssh root@192.168.1.1 'cat /etc/dnsmasq.d/isle-vlan-domains.conf'
   ```

3. Verify agent is advertising mDNS:
   ```bash
   docker exec isle-agent-mdns avahi-browse -a -t
   ```

## Advanced Usage

### Custom Domain Suffix

To use a different suffix than `.vlan`, modify the template files:

1. Edit `/mesh-proxy/segments/server-http-subdomain.conf.j2`
2. Change `.replace('.local', '.vlan')` to `.replace('.local', '.mesh')`
3. Update join protocol to use `.mesh` instead of `.vlan`

### SSL Certificates for .vlan Domains

The same SSL certificates work for both domains:

```nginx
ssl_certificate /ssl/certs/myapp.crt;
ssl_certificate_key /ssl/keys/myapp.key;
```

To generate wildcard certificates:

```bash
isle app ssl generate-mesh config/ssl.env.conf
```

This creates a certificate valid for:
- `*.myapp.local`
- `*.myapp.vlan` (if SAN is configured)

## Related Documentation

- [Join Protocol](../openwrt-router/JOIN-PROTOCOL.md) - How domain mapping works
- [Isle Agent Dual-Domain](../isle-agent-mdns/scripts/configure-dual-domain.sh) - Agent configuration
- [Verification Script](../openwrt-router/scripts/router-setup/verify-join-protocol.sh) - Testing tools

## Summary

The Isle CLI now automatically configures nginx to respond to both `.local` and `.vlan` domains when you use a `.local` base domain. This enables:

- **Seamless mDNS integration** - Services advertise via `.local`
- **Mesh DNS fallback** - Same services accessible via `.vlan`
- **Automatic discovery** - Join protocol handles domain mapping
- **Zero configuration** - Works out of the box with `isle app init`

Just use `.local` domains and the CLI handles the rest!
