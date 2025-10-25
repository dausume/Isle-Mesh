# Test Mesh Apps - Summary

This directory contains **CLI-generated test cases** that validate Isle-Mesh automation capabilities.

## Test Cases Overview

### 1. from-scratch-mesh-app
**Purpose**: Validate creating a new mesh-app without existing docker-compose

**CLI Command**:
```bash
cd from-scratch-mesh-app
isle init -d scratch-app.local
```

**What it tests**:
- Creating mesh-app from nothing
- Template file generation
- Directory structure creation
- Ready for manual service addition

**Status**: ✅ Complete
**Documentation**: `from-scratch-mesh-app/README.md`

---

### 2. python-app-only
**Purpose**: Demonstrate converting existing Python Flask app to mesh

**Structure**:
- `original/` - Standard Docker Compose app (NO CLI)
- `mesh-converted/` - Mesh-enabled version (WITH CLI)

**CLI Commands**:
```bash
# In mesh-converted directory
cp ../original/* .
isle init -d python-api.local
isle up --build
```

**What it tests**:
- Auto-detection of docker-compose.yml
- Environment file extraction and tracking
- SSL certificate generation
- Nginx proxy configuration
- Single-service conversion
- Before/after comparison

**Service URLs**:
- Original: `http://localhost:5000`
- Mesh: `https://api.python-api.local`

**Status**: ✅ Complete
**Documentation**:
- `python-app-only/README.md` - Overview
- `python-app-only/original/README.md` - Original app
- `python-app-only/mesh-converted/CONVERSION-GUIDE.md` - Detailed conversion steps

---

## Quick Reference

### Test Case 1: New Project
```bash
cd test-mesh-apps
mkdir my-new-project
cd my-new-project
isle init -d myproject.local
# Edit docker-compose.mesh-app.yml to add services
isle up --build
```

### Test Case 2: Convert Existing App
```bash
cd test-mesh-apps
mkdir my-existing-app/mesh-version
cd my-existing-app/mesh-version
# Copy your docker-compose.yml and app files here
isle init -d myapp.local
isle up --build
```

## CLI Commands Reference

### Project Initialization
```bash
isle init                      # Auto-detect or create new
isle init -d domain.local      # Specify domain
isle init -f compose.yml       # Specify compose file
isle init -f compose.yml -d domain.local -o output/
```

### Service Management
```bash
isle up                # Start services (background)
isle up --build        # Build and start
isle down              # Stop services
isle down -v           # Stop and remove volumes
isle logs              # View all logs
isle logs service-name # View specific service
isle ps                # List services
```

### Project Configuration
```bash
isle config get-project        # Show current project
isle config set-project path   # Set current project
isle config show               # Show all config
```

### Cleanup
```bash
isle prune             # Clean with confirmation
isle prune -f          # Force clean
```

## Test Coverage

| Feature | from-scratch | python-app-only | Status |
|---------|--------------|-----------------|--------|
| Project init | ✅ | ✅ | Complete |
| Auto-detect compose | N/A | ✅ | Complete |
| Env file handling | ⚠️ Manual | ✅ | Complete |
| SSL generation | ⚠️ Manual | ✅ | Complete |
| Proxy config | ⚠️ Manual | ✅ | Complete |
| Service start | ⚠️ Needs config | ✅ | Complete |
| Multiple services | ❌ | ❌ | Future |
| mTLS | ❌ | ❌ | Future |
| Custom domain | ✅ | ✅ | Complete |

Legend:
- ✅ Complete and tested
- ⚠️ Partial (needs manual steps)
- ❌ Not yet implemented

## Comparison Matrix

| Aspect | from-scratch | python-app-only/original | python-app-only/mesh-converted |
|--------|--------------|--------------------------|-------------------------------|
| **CLI Used** | Yes | No | Yes |
| **Starting Point** | Empty directory | Complete app | Complete app |
| **Services** | None (template) | 1 (Python API) | 1 (Python API) |
| **Access** | N/A | HTTP localhost:5000 | HTTPS subdomain |
| **SSL** | Config only | No | Yes (auto-generated) |
| **Proxy** | Config only | No | Yes (nginx) |
| **Network** | Template | Default bridge | Mesh network |
| **Env Files** | None | .env in root | config/.env (tracked) |

## Future Test Cases

### Planned
- [ ] `multi-service-web-app/` - Frontend + Backend + Database
- [ ] `microservices-demo/` - Multiple services with mTLS
- [ ] `env-files-complex/` - Multiple env files per service
- [ ] `custom-domains-multi/` - Multiple subdomains
- [ ] `existing-proxy/` - Converting app with existing nginx
- [ ] `compose-multiple-files/` - Testing file selection workflow

### Ideas
- [ ] Performance comparison tests
- [ ] Resource usage metrics
- [ ] Startup time comparison
- [ ] Network latency tests
- [ ] Rollback procedures
- [ ] Migration guides

## Running All Tests

```bash
# Run through each test case
cd test-mesh-apps

# Test 1: from-scratch
cd from-scratch-mesh-app
# (Manual service addition required before testing)

# Test 2: python-app-only original
cd ../python-app-only/original
docker compose up -d
curl http://localhost:5000/health
docker compose down

# Test 2: python-app-only mesh
cd ../mesh-converted
isle up -d
curl -k https://api.python-api.local/health
isle down
```

## Success Criteria

### General
- [ ] CLI commands execute without errors
- [ ] All expected files generated
- [ ] Directory structure matches expectations
- [ ] Documentation is clear and accurate

### from-scratch-mesh-app
- [x] Project initialized
- [x] Template files created
- [ ] Services can be added manually
- [ ] Services start successfully

### python-app-only
- [x] Original app runs
- [x] Conversion completed
- [x] All endpoints accessible
- [x] Env variables loaded
- [ ] Performance equivalent
- [ ] No functionality lost

## Common Issues & Solutions

### Issue: "No docker-compose files found"
**Test Case**: from-scratch-mesh-app
**Expected**: This is correct behavior - creates template

### Issue: Connection refused
**Solution**:
```bash
isle ps  # Check if services running
isle logs  # Check for errors
isle down && isle up --build  # Restart
```

### Issue: DNS not resolving
**Solution**:
```bash
# Add to /etc/hosts
sudo bash -c 'echo "127.0.0.1 yourdomain.local" >> /etc/hosts'
```

### Issue: SSL certificate errors
**Solution**:
```bash
# Use -k flag for curl
curl -k https://yourdomain.local

# Or trust the certificate
sudo cp ssl/certs/*.crt /usr/local/share/ca-certificates/
sudo update-ca-certificates
```

## Documentation Index

- `README.md` (this file) - Test cases summary
- `from-scratch-mesh-app/README.md` - New project test
- `python-app-only/README.md` - Conversion test overview
- `python-app-only/original/README.md` - Original app docs
- `python-app-only/mesh-converted/CONVERSION-GUIDE.md` - Step-by-step conversion
- `/GETTING-STARTED.md` - CLI quick start guide
- `/PROJECT-STRUCTURE.md` - Project organization

## Contributing

To add a new test case:

1. Create directory in `test-mesh-apps/`
2. Use CLI to generate or copy source files
3. Document CLI commands used
4. Add README explaining test purpose
5. Update this summary document
6. Test thoroughly
7. Document results

## Related

- `/mesh-prototypes` - Hand-crafted reference implementations
- `/isle-cli` - CLI source code
- `/mesh-proxy` - Proxy automation
- `/embed-jinja` - Template system
