# Isle Discover Command

The `isle discover` command helps you find all `.local` domains available on your system through multiple detection methods.

## Why Use Discovery?

When working with mesh applications and local services, you often need to know:
- What `.local` domains are currently accessible
- Which services are running and their URLs
- Whether your mesh-proxy configurations are correct
- If mDNS services are being advertised properly

## Quick Start

```bash
# Discover all .local domains from all sources
isle discover

# Discover and test if URLs are accessible
isle discover test

# Only check specific source
isle discover docker
isle discover nginx
isle discover hosts
isle discover mdns

# Export discovered domains to JSON
isle discover export domains.json
```

## Detection Methods

### 1. Docker Container Labels

Scans running Docker containers for mesh-related labels:

**Labels checked:**
- `mesh.domain` - The primary domain for the service
- `mesh.subdomain` - Subdomain prefix
- `mesh.service.name` - Service name (checks for .local)
- `mesh.proxy.route` - Proxy routing information
- `mesh.component=proxy` - Identifies mesh-proxy containers

**Example output:**
```
▸ Discovering via Docker Container Labels
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ✓ backend-service
    → https://backend.mesh-app.local
    ℹ Routes: /api/backend
```

**Use case:** Detect running mesh-app services with proper labels

### 2. Nginx Configuration Files

Checks nginx configuration files for `server_name` directives containing `.local`:

**Sources checked:**
- Running nginx Docker containers: `/etc/nginx/conf.d/*.conf`
- Host system: `/etc/nginx/` directory

**Example output:**
```
▸ Discovering via Nginx Configuration Files
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ℹ Checking mesh-proxy for .local domains
  ✓ api.mesh-app.local (from mesh-proxy)
    → https://api.mesh-app.local
```

**Use case:** Detect domains configured in mesh-proxy or other nginx instances

### 3. /etc/hosts Entries

Scans `/etc/hosts` file for `.local` domain entries:

**Example output:**
```
▸ Discovering via /etc/hosts
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ✓ backend.mesh-app.local
    → http://backend.mesh-app.local
    → https://backend.mesh-app.local
```

**Use case:** Detect manually configured local domains

### 4. mDNS/Avahi Services

Discovers services advertised via mDNS (Multicast DNS):

**Requirements:**
- Linux: `avahi-utils` package
- macOS: `dns-sd` (built-in)

**Install on Linux:**
```bash
sudo apt-get install avahi-utils
```

**Example output:**
```
▸ Discovering via mDNS (Avahi)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ✓ myservice
    → http://myservice.local
    → https://myservice.local
```

**Use case:** Detect services advertising themselves via mDNS (real mesh networking)

## Commands

### Discover from All Sources

```bash
isle discover
# or
isle discover all
```

Runs all detection methods and provides a summary.

### Discover from Specific Source

```bash
# Only Docker containers
isle discover docker

# Only nginx configurations
isle discover nginx

# Only /etc/hosts
isle discover hosts

# Only mDNS
isle discover mdns
```

### Test URL Accessibility

```bash
isle discover test
# or
isle discover --test
```

After discovering URLs, tests each one to see if it's accessible via HTTP/HTTPS.

**Example output:**
```
▸ Testing Discovered URLs
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Testing https://backend.mesh-app.local       ✓ Accessible
  Testing https://frontend.mesh-app.local      ✗ Not accessible
```

### Export to JSON

```bash
# Export to default file (discovered-domains.json)
isle discover export

# Export to custom file
isle discover export my-domains.json
```

**JSON format:**
```json
{
  "timestamp": "2025-10-23T17:45:00Z",
  "sources": {
    "docker": [
      {"container": "backend", "domain": "backend.mesh-app.local"}
    ],
    "hosts": [
      {"ip": "127.0.0.1", "domain": "mesh-app.local"}
    ]
  }
}
```

## Use Cases

### 1. Development Setup Verification

After setting up a mesh-app, verify all services are discoverable:

```bash
# Start your services
isle up

# Verify they're discoverable
isle discover docker

# Test if they're accessible
isle discover test
```

### 2. Debugging Network Issues

When a service isn't accessible, check all discovery methods:

```bash
isle discover all

# Check if domain is in /etc/hosts
isle discover hosts

# Check if nginx is configured correctly
isle discover nginx

# Check if Docker labels are correct
isle discover docker
```

### 3. Documentation Generation

Export discovered services for documentation:

```bash
isle discover export services.json

# Use the JSON file to generate docs or service catalog
```

### 4. CI/CD Integration

In CI/CD pipelines, verify services are up:

```bash
#!/bin/bash
# Deploy services
isle up

# Wait a bit
sleep 5

# Discover and test
isle discover test

# Check exit code
if [ $? -eq 0 ]; then
    echo "All services accessible"
else
    echo "Some services not accessible"
    exit 1
fi
```

### 5. Service Discovery for Scripts

Use with other tools:

```bash
# Export to JSON
isle discover export domains.json

# Parse with jq
domains=$(jq -r '.sources.docker[].domain' domains.json)

# Use domains in script
for domain in $domains; do
    echo "Testing $domain"
    curl -k "https://$domain/health"
done
```

## Integration with Other Commands

### With `isle init`

After initializing a project, verify discovery:

```bash
isle init -d myapp.local
isle up
isle discover docker  # Should show your services
```

### With `isle mdns`

After installing mDNS, verify it's working:

```bash
isle mdns install
isle discover mdns  # Should show mDNS-advertised services
```

### With `isle proxy`

After setting up mesh-proxy, verify configuration:

```bash
isle proxy up
isle discover nginx  # Should show proxy domains
```

## Troubleshooting

### No domains discovered

**Problem:** `isle discover` finds nothing

**Solutions:**
1. Check if services are running: `isle ps` or `docker ps`
2. Verify domain labels on containers: `docker inspect <container> | grep domain`
3. Check /etc/hosts has entries: `cat /etc/hosts | grep .local`
4. Ensure mesh-proxy is configured correctly

### Docker discovery fails

**Problem:** `isle discover docker` shows no containers

**Solutions:**
1. Start your mesh-app: `isle up`
2. Check Docker is running: `docker ps`
3. Verify you're in the docker group: `groups | grep docker`

### mDNS discovery fails

**Problem:** `isle discover mdns` shows warning about missing tools

**Solutions:**
1. Install avahi-utils: `sudo apt-get install avahi-utils`
2. On macOS, dns-sd should be available by default
3. Check if Avahi daemon is running: `systemctl status avahi-daemon`

### URLs not accessible in test mode

**Problem:** Discovery finds domains but test shows "Not accessible"

**Solutions:**
1. Check if services are actually running: `isle ps`
2. Verify ports are mapped correctly: `docker ps`
3. Check nginx/proxy logs: `isle logs proxy`
4. Test manually: `curl -k https://domain.local`
5. Check SSL certificates: `isle ssl list`

## Advanced Usage

### Combine with grep

```bash
# Find only backend services
isle discover docker | grep backend

# Find all mesh-app.local domains
isle discover hosts | grep mesh-app.local
```

### Watch for changes

```bash
# Monitor discovered services
watch -n 5 'isle discover docker'
```

### Compare before and after deployment

```bash
# Before deployment
isle discover export before.json

# Deploy changes
isle up --build

# After deployment
isle discover export after.json

# Compare
diff <(jq '.sources.docker' before.json) <(jq '.sources.docker' after.json)
```

### Use in scripts

```bash
#!/bin/bash
# Script to check if all expected services are discoverable

expected_domains=("backend.mesh-app.local" "frontend.mesh-app.local" "api.mesh-app.local")

isle discover export discovered.json

for domain in "${expected_domains[@]}"; do
    if jq -e ".sources.docker[] | select(.domain == \"$domain\")" discovered.json > /dev/null; then
        echo "✓ $domain found"
    else
        echo "✗ $domain NOT found"
        exit 1
    fi
done

echo "All expected services discovered!"
```

## Output Format

### Terminal Output

Uses color-coded, pretty-printed output:
- ✓ (green) - Successfully discovered service
- ✗ (red) - Error or not found
- ⚠ (yellow) - Warning
- ℹ (yellow) - Information
- → (magenta) - URL

### JSON Output

Structured data for programmatic use:
```json
{
  "timestamp": "ISO-8601 timestamp",
  "sources": {
    "docker": [
      {
        "container": "container-name",
        "domain": "domain.local"
      }
    ],
    "hosts": [
      {
        "ip": "127.0.0.1",
        "domain": "domain.local"
      }
    ]
  }
}
```

## Best Practices

1. **Run discovery after deployment**
   ```bash
   isle up && isle discover test
   ```

2. **Regular health checks**
   ```bash
   # Add to cron or systemd timer
   */5 * * * * /usr/local/bin/isle discover test > /var/log/mesh-discover.log
   ```

3. **Document your services**
   ```bash
   # Keep a snapshot of your services
   isle discover export production-services.json
   git add production-services.json
   git commit -m "Update service discovery snapshot"
   ```

4. **Verify in CI/CD**
   ```yaml
   # In your CI/CD pipeline
   - name: Deploy services
     run: isle up
   - name: Verify discovery
     run: isle discover test
   ```

## Examples

### Example 1: New Project Setup

```bash
# Create and start project
mkdir myproject && cd myproject
isle init -d myproject.local
isle up

# Verify everything is discoverable
isle discover all

# Test accessibility
isle discover test
```

### Example 2: Debugging Missing Service

```bash
# Service not accessible at https://api.myapp.local

# Check all discovery methods
isle discover all

# Found in /etc/hosts but not in Docker?
# -> Service might not be running

# Found in Docker but not accessible?
# -> Check nginx/proxy configuration
isle discover nginx

# Not found anywhere?
# -> Check labels: docker inspect <container>
```

### Example 3: Multi-Environment Check

```bash
# Development environment
ENVIRONMENT=dev isle discover export dev-services.json

# Staging environment
ENVIRONMENT=staging isle discover export staging-services.json

# Compare
diff <(jq '.sources.docker' dev-services.json) \
     <(jq '.sources.docker' staging-services.json)
```

## See Also

- `isle init` - Initialize a mesh-app project
- `isle up` - Start mesh-app services
- `isle mdns` - Manage mDNS system
- `isle proxy` - Manage mesh-proxy
- `isle config` - CLI configuration

## Summary

The `isle discover` command provides comprehensive service discovery for `.local` domains through multiple methods:

- **Docker labels** - Find mesh-app services
- **Nginx configs** - Find proxy configurations
- **/etc/hosts** - Find manual entries
- **mDNS** - Find advertised services

Use it to verify deployments, debug issues, and integrate with automation tools!
