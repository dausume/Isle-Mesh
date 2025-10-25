# Test Case: From-Scratch Mesh-App

**Test Type**: New project initialization without existing docker-compose
**CLI Commands Used**: `isle init`
**Purpose**: Validate creating a brand new mesh-app from scratch

## Test Description

This test case demonstrates creating a completely new Isle-Mesh project without an existing docker-compose.yml file. The CLI should generate all necessary scaffolding.

## CLI Commands Executed

### 1. Initialize Project
```bash
cd test-mesh-apps/from-scratch-mesh-app
isle init -d scratch-app.local
```

**Expected Output**:
```
No docker-compose files found in current directory.
Creating a new mesh-app project from scratch...

Creating new mesh-app project...
✓ Created mesh-app project: /path/to/test-mesh-apps/from-scratch-mesh-app
✓ Created setup.yml
✓ Created docker-compose.mesh-app.yml
✓ Created isle-mesh.yml
✓ Set as current project
```

## Generated Files

```
from-scratch-mesh-app/
├── docker-compose.mesh-app.yml    # Template compose file
├── setup.yml                       # Environment configuration
├── isle-mesh.yml                   # Mesh network configuration
├── config/                         # Configuration directory
├── ssl/                            # SSL certificates (empty initially)
├── proxy/                          # Nginx proxy configs (empty initially)
└── README.md                       # This file
```

## File Contents

### docker-compose.mesh-app.yml
```yaml
version: '3.8'

networks:
  from-scratch-mesh-app_meshnet:
    driver: bridge

services:
  # Add your services here
  # Example:
  # myservice:
  #   image: nginx:alpine
  #   networks:
  #     - from-scratch-mesh-app_meshnet
  #   expose:
  #     - "80"
```

### setup.yml
```yaml
# setup.yml - Environment configuration for from-scratch-mesh-app
current-setup:
  env: dev

environments:
  dev:
    domain: localhost
    expose_ports_on_localhost: true
    projects:
      from-scratch-mesh-app: { path: . }

  production:
    domain: scratch-app.local
    projects:
      from-scratch-mesh-app: { path: . }
```

### isle-mesh.yml
```yaml
# isle-mesh.yml - Mesh network configuration
mesh:
  name: from-scratch-mesh-app
  domain: scratch-app.local
  version: "1.0.0"

network:
  name: from-scratch-mesh-app_meshnet
  driver: bridge

services: {}
```

## Next Steps (Manual Customization)

### 1. Add Services to docker-compose.mesh-app.yml

Example: Adding a web service
```bash
nano docker-compose.mesh-app.yml
```

Add:
```yaml
services:
  web:
    image: nginx:alpine
    container_name: scratch-web
    networks:
      - from-scratch-mesh-app_meshnet
    expose:
      - "80"
    labels:
      mesh.subdomain: "www"
      mesh.mtls: "false"
```

### 2. Update isle-mesh.yml

```bash
nano isle-mesh.yml
```

Add service configuration:
```yaml
services:
  web:
    subdomain: www
    port: 80
    mtls: false
    url: https://www.scratch-app.local
```

### 3. Generate SSL Certificates

```bash
isle ssl generate-mesh config/ssl.env.conf
```

### 4. Generate Proxy Configuration

```bash
isle proxy build
```

### 5. Start Services

```bash
isle up --build
```

### 6. Verify Services

```bash
# Check running services
isle ps

# View logs
isle logs

# Access the service
curl https://www.scratch-app.local
```

## Service URLs (After Configuration)

Once services are added and configured:
- **Web Service**: `https://www.scratch-app.local`

## Test Validation

### ✅ Success Criteria
- [x] Project initialized without errors
- [x] All template files generated
- [x] Directory structure created
- [x] Project set as current in config
- [ ] Services can be added manually
- [ ] Services start with `isle up`
- [ ] Services accessible via configured URLs

### 📋 Test Results

**Date Tested**: 2025-10-23
**CLI Version**: Latest
**Status**: ✅ PASSED (initialization)

**Notes**:
- Template files generated correctly
- Directory structure matches expectations
- Ready for service addition
- Requires manual service configuration before `isle up`

## Common Issues

### Issue: "No services defined"
**Solution**: Add services to docker-compose.mesh-app.yml before running `isle up`

### Issue: SSL certificate errors
**Solution**: Generate SSL certificates with `isle ssl generate-mesh`

### Issue: Proxy not routing
**Solution**: Ensure proxy configuration is generated and services are in mesh network

## Related Test Cases

- `python-app-only/` - Converting existing app to mesh
- Other test cases in `/test-mesh-apps`

## CLI Commands Reference

```bash
# Initialize project
isle init -d scratch-app.local

# Manage services
isle up              # Start services
isle down            # Stop services
isle ps              # List services
isle logs            # View logs

# Configuration
isle config get-project    # Show current project
isle config show           # Show all config

# Cleanup
isle prune -f        # Remove all mesh resources
```
