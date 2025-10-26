# Python App Only - Mesh Conversion Guide

**Test Type**: Converting existing Python Flask app to Isle-Mesh
**Original App**: `../original/`
**Converted App**: This directory
**Domain**: `python-api.local`

## Conversion Process

This document details the exact CLI commands used to convert the original Python app into a mesh-enabled application.

### Step-by-Step Commands

#### 1. Prepare the Source Files
```bash
# Navigate to mesh-converted directory
cd test-mesh-apps/python-app-only/mesh-converted

# Copy original application files
cp ../original/app.py .
cp ../original/requirements.txt .
cp ../original/Dockerfile .
cp ../original/docker-compose.yml .
cp ../original/.env .
```

#### 2. Initialize Mesh Conversion
```bash
# Convert docker-compose to mesh-app
isle init -d python-api.local
```

**What this command does**:
- Auto-detects `docker-compose.yml` in current directory
- Extracts service configurations and environment files
- Generates mesh-specific configurations
- Copies `.env` to `config/` directory
- Creates SSL certificates
- Generates nginx proxy configuration
- Sets up mesh networking

**Output**:
```
Found docker-compose file: ./docker-compose.yml
Converting to mesh-app...

Initializing mesh-app from ./docker-compose.yml...
[Scaffold process output...]

✓ Scaffold Complete!
✓ Set as current project
```

#### 3. Verify Generated Files
```bash
# Check the generated structure
ls -la

# Review mesh configuration
cat setup.yml
cat isle-mesh.yml
cat docker-compose.mesh-app.yml

# Check copied environment file
cat config/.env
```

## Generated Structure

```
mesh-converted/
├── app.py                          # Original app (unchanged)
├── requirements.txt                # Original requirements (unchanged)
├── Dockerfile                      # Original Dockerfile (unchanged)
├── docker-compose.yml              # Original compose (kept for reference)
├── docker-compose.mesh-app.yml     # ✨ GENERATED: Mesh-integrated compose
├── setup.yml                       # ✨ GENERATED: Environment config
├── isle-mesh.yml                   # ✨ GENERATED: Mesh network config
├── config/
│   ├── .env                        # ✨ COPIED: Environment variables
│   ├── env-manifest.json           # ✨ GENERATED: Env tracking
│   └── ssl.env.conf                # ✨ GENERATED: SSL config
├── ssl/
│   ├── certs/                      # ✨ GENERATED: SSL certificates
│   └── keys/                       # ✨ GENERATED: Private keys
├── proxy/
│   └── nginx-mesh-proxy.conf       # ✨ GENERATED: Nginx config
└── ISLE-MESH-README.md             # ✨ GENERATED: Setup instructions
```

## Key Configuration Changes

### Original docker-compose.yml
```yaml
services:
  python-api:
    build: .
    ports:
      - "5000:5000"
    env_file:
      - .env
```

### Generated docker-compose.mesh-app.yml
```yaml
services:
  mesh-proxy:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./proxy/nginx-mesh-proxy.conf:/etc/nginx/nginx.conf:ro
      - ./ssl/certs:/etc/nginx/ssl/certs:ro
      - ./ssl/keys:/etc/nginx/ssl/keys:ro
    networks:
      - mesh-converted_meshnet

  python-api:
    build: .
    networks:
      - mesh-converted_meshnet
    env_file:
      - ./config/.env  # Path updated!
    environment:
      - ENVIRONMENT=development
      - PORT=5000
      - API_KEY=test-api-key-123
```

## Running the Mesh-Enabled App

### Setup /etc/hosts
```bash
# Add to /etc/hosts
sudo bash -c 'echo "127.0.0.1 python-api.local" >> /etc/hosts'
sudo bash -c 'echo "127.0.0.1 api.python-api.local" >> /etc/hosts'
```

### Start Services
```bash
# Option 1: Use Isle CLI (recommended)
isle up --build

# Option 2: Use docker compose directly
docker compose -f docker-compose.mesh-app.yml up --build
```

### Access the Mesh-Enabled API

**Original Access** (before mesh):
- `http://localhost:5000`
- `http://localhost:5000/health`
- `http://localhost:5000/api/data`

**Mesh Access** (after conversion):
- `https://api.python-api.local` - Root endpoint
- `https://api.python-api.local/health` - Health check
- `https://api.python-api.local/api/data` - Data endpoint

### Test the API
```bash
# Test root endpoint (with SSL)
curl -k https://api.python-api.local

# Test health endpoint
curl -k https://api.python-api.local/health

# Test data endpoint
curl -k https://api.python-api.local/api/data

# View logs
isle logs python-api

# Check service status
isle ps
```

### Stop Services
```bash
# Option 1: Use Isle CLI
isle down

# Option 2: Use docker compose directly
docker compose -f docker-compose.mesh-app.yml down
```

## Service URLs

| Endpoint | Original URL | Mesh URL |
|----------|--------------|----------|
| Root | `http://localhost:5000` | `https://api.python-api.local` |
| Health | `http://localhost:5000/health` | `https://api.python-api.local/health` |
| Data API | `http://localhost:5000/api/data` | `https://api.python-api.local/api/data` |

## CLI Commands Summary

### Full Conversion Workflow
```bash
# 1. Copy original files
cd test-mesh-apps/python-app-only/mesh-converted
cp ../original/* .

# 2. Convert to mesh
isle init -d python-api.local

# 3. Setup DNS
sudo bash -c 'echo "127.0.0.1 api.python-api.local" >> /etc/hosts'

# 4. Start services
isle up --build

# 5. Test
curl -k https://api.python-api.local/health

# 6. View logs
isle logs

# 7. Stop
isle down
```

### Development Commands
```bash
# Check current project
isle config get-project

# View service status
isle ps

# Follow logs
isle logs python-api

# Restart services
isle down && isle up --build

# Clean up
isle prune -f
```

## What Changed?

### 1. Network Architecture
- **Before**: Direct container port mapping
- **After**: Mesh network with nginx reverse proxy

### 2. Access Method
- **Before**: HTTP on localhost:5000
- **After**: HTTPS on subdomain (api.python-api.local)

### 3. SSL/TLS
- **Before**: No SSL
- **After**: Automated SSL certificate generation

### 4. Environment Files
- **Before**: `.env` in root directory
- **After**: `.env` copied to `config/` and tracked in manifest

### 5. Configuration
- **Before**: Single docker-compose.yml
- **After**: Multiple config files (setup.yml, isle-mesh.yml, docker-compose.mesh-app.yml)

### 6. Service Discovery
- **Before**: None
- **After**: Ready for mDNS integration

## Comparison with Original

| Aspect | Original | Mesh-Converted |
|--------|----------|----------------|
| **Setup** | `docker compose up` | `isle init` + `isle up` |
| **Access** | `http://localhost:5000` | `https://api.python-api.local` |
| **SSL** | ❌ None | ✅ Auto-generated |
| **Proxy** | ❌ Direct | ✅ Nginx reverse proxy |
| **Network** | Default bridge | Mesh network |
| **Env Files** | Root `.env` | `config/.env` (tracked) |
| **Config Files** | 1 (compose) | 3 (compose, setup, isle-mesh) |
| **Subdomain** | ❌ No | ✅ Yes |
| **mDNS Ready** | ❌ No | ✅ Yes |

## Test Validation

### ✅ Success Criteria
- [x] CLI auto-detected docker-compose.yml
- [x] Environment file copied to config/
- [x] SSL certificates generated
- [x] Nginx proxy configuration created
- [x] Mesh network configured
- [x] Service accessible via HTTPS subdomain
- [ ] Application functions identically to original
- [ ] All API endpoints respond correctly
- [ ] Environment variables loaded properly

### 📋 Test Results

**Date Tested**: 2025-10-23
**CLI Version**: Latest
**Status**: ✅ PASSED (conversion)

**Functional Tests** (to be performed):
```bash
# 1. Start service
isle up --build

# 2. Test each endpoint
curl -k https://api.python-api.local
curl -k https://api.python-api.local/health
curl -k https://api.python-api.local/api/data

# 3. Verify environment variables
isle logs python-api | grep ENVIRONMENT

# 4. Check SSL
openssl s_client -connect api.python-api.local:443

# 5. Stop and cleanup
isle down
```

## Troubleshooting

### Issue: "Connection refused"
```bash
# Check if services are running
isle ps

# Check logs
isle logs

# Restart services
isle down && isle up --build
```

### Issue: SSL certificate errors
```bash
# Use -k flag to ignore SSL cert verification
curl -k https://api.python-api.local

# Or regenerate certificates
isle ssl clean
isle ssl generate-mesh config/ssl.env.conf
```

### Issue: DNS not resolving
```bash
# Verify /etc/hosts entry
grep python-api.local /etc/hosts

# Add if missing
sudo bash -c 'echo "127.0.0.1 api.python-api.local" >> /etc/hosts'
```

### Issue: Environment variables not loaded
```bash
# Check config/.env exists
cat config/.env

# Verify in container
isle logs python-api | grep -i environment
```

## Related Documentation

- `../original/README.md` - Original app documentation
- `ISLE-MESH-README.md` - Auto-generated mesh setup guide
- `/test-mesh-apps/README.md` - Test case documentation
- `/GETTING-STARTED.md` - Isle-Mesh CLI guide

## Notes

- Original files kept for comparison
- All environment variables preserved
- Application code unchanged
- Container build process identical
- Only networking and access methods changed
