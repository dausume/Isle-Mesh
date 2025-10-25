# Quick Start Guide

## What is this?

This test case demonstrates two deployment patterns:

1. **Isolation Mode**: Services run independently (can be on different machines)
2. **Suite Mode**: Services run together on one machine and communicate

## Test Both Modes Automatically

```bash
cd test-mesh-apps/isolation-suite-pattern
./test-deployment.sh both
```

This will test both deployment modes and verify they work correctly.

## Manual Testing

### Test Isolation Mode

```bash
# Run the test script
./test-deployment.sh isolation

# Or manually:
cd service-a && docker-compose up -d
cd service-b && docker-compose up -d

# Test endpoints
curl http://localhost:5001/health  # Service A
curl http://localhost:6001/health  # Service B
curl http://localhost:6001/api/process  # No integration
```

### Test Suite Mode

```bash
# Run the test script
./test-deployment.sh suite

# Or manually:
cd suite && docker-compose up -d

# Test endpoints
curl http://localhost:5001/health  # Service A
curl http://localhost:6001/health  # Service B
curl http://localhost:6001/api/process  # WITH integration!
```

### Cleanup

```bash
./test-deployment.sh cleanup
```

## Key Differences

### Isolation Mode
- Services don't know about each other
- Can run on different machines
- `mesh.isolation=true` label
- Service B returns: `"integration": "not-configured"`

### Suite Mode
- Services share a network
- Service B can call Service A
- `mesh.suite=true` label
- Service B returns: `"integration": "success"`

## Verification

Check labels to see which mode is active:

```bash
# Isolation
docker inspect service-a-isolation | jq '.[0].Config.Labels["mesh.isolation"]'
# Returns: "true"

# Suite
docker inspect service-a-suite | jq '.[0].Config.Labels["mesh.suite"]'
# Returns: "true"
```

## What's Next?

- See `README.md` for detailed architecture explanation
- See `DEPLOYMENT-GUIDE.md` for production deployment strategies
- See `FUTURE-ENHANCEMENTS.md` for advanced features (service discovery, load balancing, etc.)

## Real-World Application

This pattern enables:

**Development**: Run suite mode on your laptop
```bash
cd suite && docker-compose up
```

**Production (Small)**: Run suite mode on one server
```bash
# All services on one machine
cd suite && docker-compose up -d
```

**Production (Large)**: Run isolation mode on multiple servers
```bash
# Machine 1
cd service-a && docker-compose up -d

# Machine 2
cd service-b && docker-compose up -d
```

The `mesh.isolation` and `mesh.suite` labels let your deployment automation decide which mode to use based on available infrastructure!
