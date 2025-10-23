# Docker-based Mesh Proxy Builder

This document explains how to use the containerized version of the mesh-proxy builder. The Docker setup allows you to generate nginx configurations without installing dependencies on your host system.

## Quick Start

### 1. Build the Docker Image

```bash
cd mesh-environments/mesh-proxy
docker build -t mesh-proxy-builder .
```

### 2. Run with Docker Compose

```bash
# Build with default configuration (localhost-mdns)
docker-compose run --rm mesh-proxy-builder

# Build with custom domain
docker-compose run --rm mesh-proxy-builder --domain custom.local

# Build with custom docker-compose file
docker-compose run --rm mesh-proxy-builder --compose /input/mesh-environments/isle/docker-compose.yml

# Build with specific mTLS services
docker-compose run --rm mesh-proxy-builder --service-mtls backend --service-mtls api
```

### 3. Watch Mode (Auto-rebuild on Changes)

```bash
# Start the watcher service
docker-compose up mesh-proxy-watcher

# It will automatically rebuild when the docker-compose file changes
# Press Ctrl+C to stop
```

## Architecture

### Container Structure

```
mesh-proxy-builder (container)
├── /mesh-proxy/              # Working directory
│   ├── segments/             # Template segments
│   ├── templates/            # Main templates
│   ├── scripts/              # Build scripts
│   └── output/               # Generated configs (mounted from host)
├── /input/                   # Mounted input files (read-only)
│   ├── localhost-mdns/       # Default environment
│   └── mesh-environments/    # All environments
└── /docker-entrypoint.sh     # Automation script
```

### Volume Mounts

The docker-compose.yml mounts:

1. **Input directories (read-only)**:
   - `../localhost-mdns` → `/input/localhost-mdns`
   - `../` → `/input/mesh-environments`

2. **Output directory (read-write)**:
   - `./output` → `/mesh-proxy/output`

## Usage Examples

### Example 1: Basic Build

Generate nginx config for the default localhost-mdns setup:

```bash
docker-compose run --rm mesh-proxy-builder
```

**Output**: `./output/nginx-mesh-proxy.conf`

### Example 2: Custom Domain

Build for a different domain:

```bash
docker-compose run --rm mesh-proxy-builder --domain isle-mesh.local
```

### Example 3: Different Environment

Build for a different mesh environment:

```bash
docker-compose run --rm mesh-proxy-builder \
    --compose /input/mesh-environments/isle/docker-compose.yml \
    --domain isle-mesh.local
```

### Example 4: Multiple mTLS Services

Specify multiple services that require mTLS:

```bash
docker-compose run --rm mesh-proxy-builder \
    --service-mtls backend \
    --service-mtls api \
    --service-mtls database
```

### Example 5: Custom Output Location

Change where the output is written:

```bash
docker-compose run --rm mesh-proxy-builder \
    --output /mesh-proxy/output/custom-proxy.conf
```

### Example 6: Watch Mode for Development

Automatically rebuild when docker-compose changes:

```bash
# Start the watcher
docker-compose up mesh-proxy-watcher

# In another terminal, modify your docker-compose.yml
# The watcher will detect changes and rebuild automatically

# Stop the watcher
# Press Ctrl+C or:
docker-compose down
```

## Environment Variables

You can customize behavior by modifying the `docker-compose.yml` environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `INPUT_COMPOSE` | `/input/localhost-mdns/docker-compose.lh-mdns.yml` | Input docker-compose file |
| `BASE_DOMAIN` | `mesh-app.local` | Base domain name |
| `BASE_CERT` | `mesh-app.crt` | SSL certificate filename |
| `BASE_KEY` | `mesh-app.key` | SSL key filename |
| `MTLS_SERVICES` | `backend` | Space-separated list of mTLS services |
| `OUTPUT_FILE` | `/mesh-proxy/output/nginx-mesh-proxy.conf` | Output path |
| `WATCH_MODE` | `false` | Enable watch mode |
| `WATCH_INTERVAL` | `10` | Watch interval in seconds |

### Custom Environment Variables

Create a `.env` file in the mesh-proxy directory:

```bash
# .env
INPUT_COMPOSE=/input/mesh-environments/isle/docker-compose.yml
BASE_DOMAIN=isle-mesh.local
MTLS_SERVICES=backend api
WATCH_INTERVAL=5
```

Then run:

```bash
docker-compose run --rm mesh-proxy-builder
```

## Integration with Existing Proxy

After generating the configuration, copy it to your proxy container:

```bash
# Generate the config
docker-compose run --rm mesh-proxy-builder

# Copy to proxy directory
cp output/nginx-mesh-proxy.conf ../localhost-mdns/proxy/lh-mdns.proxy.conf

# Rebuild proxy container
cd ../localhost-mdns
docker-compose up -d --build proxy
```

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Build Mesh Proxy Config

on:
  push:
    paths:
      - 'mesh-environments/*/docker-compose*.yml'

jobs:
  build-proxy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Build proxy config
        run: |
          cd mesh-environments/mesh-proxy
          docker build -t mesh-proxy-builder .
          docker run --rm \
            -v $(pwd)/output:/mesh-proxy/output \
            -v $(pwd)/../localhost-mdns:/input/localhost-mdns:ro \
            mesh-proxy-builder

      - name: Upload artifact
        uses: actions/upload-artifact@v3
        with:
          name: nginx-config
          path: mesh-environments/mesh-proxy/output/nginx-mesh-proxy.conf
```

### GitLab CI Example

```yaml
build-proxy:
  image: docker:latest
  services:
    - docker:dind
  script:
    - cd mesh-environments/mesh-proxy
    - docker build -t mesh-proxy-builder .
    - docker run --rm
        -v $(pwd)/output:/mesh-proxy/output
        -v $(pwd)/../localhost-mdns:/input/localhost-mdns:ro
        mesh-proxy-builder
  artifacts:
    paths:
      - mesh-environments/mesh-proxy/output/nginx-mesh-proxy.conf
```

## Troubleshooting

### Permission Denied on Output Directory

If you get permission errors writing to the output directory:

```bash
# Fix permissions
chmod 777 output/

# Or run container as your user
docker-compose run --rm --user $(id -u):$(id -g) mesh-proxy-builder
```

### Input File Not Found

Make sure the volume mounts are correct in `docker-compose.yml`:

```bash
# List available input files
docker-compose run --rm mesh-proxy-builder bash -c "find /input -name '*.yml'"
```

### Container Fails to Build

Check Docker logs:

```bash
docker-compose build --no-cache
```

### Watch Mode Not Detecting Changes

- Ensure the file is actually changing (check timestamp)
- Increase `WATCH_INTERVAL` if changes are too frequent
- Check container logs: `docker-compose logs mesh-proxy-watcher`

## Advanced Usage

### Running Without Docker Compose

```bash
docker run --rm \
  -v $(pwd)/output:/mesh-proxy/output \
  -v $(pwd)/../localhost-mdns:/input/localhost-mdns:ro \
  mesh-proxy-builder \
  --compose /input/localhost-mdns/docker-compose.lh-mdns.yml \
  --domain mesh-app.local
```

### Interactive Shell for Debugging

```bash
docker-compose run --rm mesh-proxy-builder bash

# Inside container:
ls -la /input/
cat /input/localhost-mdns/docker-compose.lh-mdns.yml
./build-mesh-proxy.sh --help
```

### Building Multiple Configs at Once

```bash
#!/bin/bash
# build-all-configs.sh

ENVIRONMENTS=("localhost-mdns" "isle" "archipelago")

for env in "${ENVIRONMENTS[@]}"; do
    echo "Building config for $env..."
    docker-compose run --rm mesh-proxy-builder \
        --compose "/input/mesh-environments/$env/docker-compose.yml" \
        --domain "${env}.local" \
        --output "/mesh-proxy/output/${env}-proxy.conf"
done
```

## Performance

- **Build time**: ~1-2 seconds for typical configurations
- **Image size**: ~150MB (Python slim base)
- **Watch mode overhead**: Minimal (~1MB RAM, <1% CPU)

## Security Considerations

1. **Read-only mounts**: Input directories are mounted read-only to prevent accidental modifications
2. **No network access needed**: The builder doesn't require internet connectivity
3. **Isolated environment**: Runs in isolated container, doesn't affect host system
4. **No secrets**: Certificate keys are only referenced by filename, not copied into image

## Updating the Builder

When you modify templates or scripts:

```bash
# Rebuild the image
docker-compose build

# Or force rebuild without cache
docker-compose build --no-cache

# Test the new version
docker-compose run --rm mesh-proxy-builder
```
