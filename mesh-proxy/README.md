# Mesh Proxy - Dynamic Nginx Configuration Builder

A modular, template-based system for dynamically generating nginx mesh-proxy configurations from docker-compose files using Jinja2 templates.

## Overview

This system allows you to automatically generate nginx reverse proxy configurations by analyzing your docker-compose.yml file. It uses modular Jinja2 template segments that can be mixed and matched to create the exact proxy configuration you need.

Using this system we can dynamically construct our nginx proxies to fit whatever needs we may have for routing.

## Directory Structure

```
mesh-proxy/
├── segments/           # Modular Jinja2 template segments
│   ├── base.conf.j2                          # Base nginx structure
│   ├── upstream.conf.j2                      # Upstream block template
│   ├── security-headers.conf.j2              # CORS and CSP headers
│   ├── server-http-base.conf.j2              # Base domain HTTP server
│   ├── server-https-base.conf.j2             # Base domain HTTPS server
│   ├── server-http-subdomain.conf.j2         # Subdomain HTTP server
│   ├── server-https-subdomain-mtls.conf.j2   # Subdomain HTTPS with mTLS
│   └── server-https-subdomain-simple.conf.j2 # Subdomain HTTPS without mTLS
├── templates/          # Main templates that compose segments
│   └── nginx-mesh-proxy.conf.j2              # Main template
├── scripts/            # Build scripts
│   ├── parse-docker-compose.sh               # Extracts service info from docker-compose
│   └── build-proxy-config.py                 # Main builder script
├── output/             # Generated configurations
└── build-mesh-proxy.sh # Convenient wrapper script

```

## How It Works

1. **Parse docker-compose.yml**: The `parse-docker-compose.sh` script extracts service information (names, ports, labels) from your docker-compose configuration.

2. **Build configuration**: The `build-proxy-config.py` Python script uses Jinja2 to assemble modular template segments into a complete nginx configuration.

3. **Template segments**: Each aspect of the nginx config (upstreams, security headers, server blocks) is defined in its own reusable template segment.

## Quick Start

### Option 1: Using Docker (Recommended)

**No dependencies needed!** The Docker approach includes everything you need.

```bash
cd mesh-prototypes/mesh-proxy

# Build the Docker image
make docker-build

# Generate proxy config
make docker-run

# Or use docker-compose directly
docker-compose run --rm mesh-proxy-builder

# Watch mode (auto-rebuild on changes)
make docker-watch
```

See [DOCKER.md](DOCKER.md) for detailed Docker usage.

### Option 2: Local Installation

#### Prerequisites

- Python 3.6+
- Jinja2 (`pip install jinja2`)
- yq (YAML processor) - Install from https://github.com/mikefarah/yq

```bash
# Install dependencies automatically
make install-deps

# Or install manually:

# Install yq (choose your platform)
# Linux
sudo wget -qO /usr/local/bin/yq https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64
sudo chmod +x /usr/local/bin/yq

# macOS
brew install yq

# Install Jinja2
pip3 install --user jinja2
```

#### Basic Usage

The easiest way to build a proxy configuration is using the wrapper script:

```bash
cd mesh-prototypes/mesh-proxy

# Build with defaults (uses localhost-mdns docker-compose)
./build-mesh-proxy.sh

# Build with custom docker-compose file
./build-mesh-proxy.sh --compose /path/to/docker-compose.yml --domain your-domain.local

# Or use make
make build
make build DOMAIN=custom.local
```

### Advanced Usage

For more control, use the Python script directly:

```bash
python3 scripts/build-proxy-config.py \
    --compose ../localhost-mdns/docker-compose.lh-mdns.yml \
    --domain mesh-app.local \
    --base-cert mesh-app.crt \
    --base-key mesh-app.key \
    --service-mtls backend \
    --output output/nginx-mesh-proxy.conf
```

#### Options:

- `--compose`: Path to docker-compose.yml file (required)
- `--domain`: Base domain name (required)
- `--base-cert`: Base SSL certificate filename (default: mesh-app.crt)
- `--base-key`: Base SSL key filename (default: mesh-app.key)
- `--service-mtls`: Service name that requires mTLS (can be specified multiple times)
- `--output`: Output path for generated configuration (required)

## Template Segments

### Base Structure (`segments/base.conf.j2`)

Provides the fundamental nginx structure with events, http block, logging, and placeholders for upstreams, security headers, and server blocks.

### Upstream (`segments/upstream.conf.j2`)

Generates upstream blocks for backend services:

```jinja2
upstream {{ service_name }} {
    server {{ service_name }}:{{ service_port }};
}
```

### Security Headers (`segments/security-headers.conf.j2`)

Dynamically generates CORS and Content Security Policy headers for all services and subdomains.

### Server Blocks

#### Base Domain
- `server-http-base.conf.j2`: HTTP server for base domain
- `server-https-base.conf.j2`: HTTPS server for base domain with static file support

#### Subdomains
- `server-http-subdomain.conf.j2`: HTTP server for subdomain
- `server-https-subdomain-mtls.conf.j2`: HTTPS server with mutual TLS (for backend services)
- `server-https-subdomain-simple.conf.j2`: HTTPS server without mTLS (for frontend services)

## Configuring Services

### Using Docker Compose Labels

You can configure proxy behavior using labels in your docker-compose.yml:

```yaml
services:
  backend:
    build: ./backend
    expose:
      - "8443"
    labels:
      mesh.subdomain: "backend"  # Subdomain name (default: service name)
      mesh.mtls: "true"          # Enable mTLS for this service
    networks:
      - meshnet

  frontend:
    build: ./frontend
    expose:
      - "8443"
    labels:
      mesh.subdomain: "frontend"
      # mesh.mtls defaults to false
    networks:
      - meshnet
```

### Specifying mTLS via Command Line

Alternatively, specify mTLS services when building:

```bash
./build-mesh-proxy.sh --compose docker-compose.yml --service-mtls backend --service-mtls api
```

## Customizing Templates

### Adding a New Segment

1. Create a new template file in `segments/`:

```jinja2
{# segments/my-custom-feature.conf.j2 #}
# My custom nginx feature
location /custom {
    return 200 "Custom feature for {{ subdomain }}";
}
```

2. Include it in the main template (`templates/nginx-mesh-proxy.conf.j2`):

```jinja2
{% block servers %}
    server {
        # ... existing config ...
        {% include "segments/my-custom-feature.conf.j2" with context %}
    }
{% endblock %}
```

### Creating a New Main Template

You can create entirely new template compositions:

```jinja2
{# templates/minimal-proxy.conf.j2 #}
{% extends "segments/base.conf.j2" %}

{% block upstreams %}
{% for service in services %}
{% include "segments/upstream.conf.j2" with context %}
{% endfor %}
{% endblock %}

{% block servers %}
{# Only include what you need #}
{% endblock %}
```

## Example Output

The generated configuration will look similar to your existing `lh-mdns.proxy.conf`, but dynamically created based on your docker-compose services. It includes:

- Logging configuration
- Upstream definitions for each service
- CORS and CSP security headers
- HTTP and HTTPS server blocks for base domain
- HTTP and HTTPS server blocks for each subdomain
- Automatic mTLS configuration for specified services

## Integration with Existing Mesh Environments

To use the generated configuration in your mesh environment:

```bash
# Build the configuration
cd mesh-prototypes/mesh-proxy
./build-mesh-proxy.sh

# Copy to your proxy directory
cp output/nginx-mesh-proxy.conf ../localhost-mdns/proxy/lh-mdns.proxy.conf

# Rebuild your docker containers
cd ../localhost-mdns
docker-compose down
docker-compose up --build
```

## Future Enhancements

- Support for WebSocket-specific configurations
- Rate limiting templates
- Load balancing templates
- Custom location block templates
- Environment variable substitution
- Multiple domain support
- Automatic certificate path detection

## Troubleshooting

### yq not found

Install yq from https://github.com/mikefarah/yq

### Jinja2 not found

```bash
pip install jinja2
```

### Template not found errors

Ensure you're running the script from the `mesh-proxy` directory or providing absolute paths.

### Service not appearing in output

Check that your service has either `expose` or `ports` defined in docker-compose.yml.

## Contributing

When adding new template segments:

1. Keep segments focused on a single concern
2. Use descriptive variable names
3. Include comments explaining the purpose
4. Test with multiple service configurations
5. Update this README with usage examples
