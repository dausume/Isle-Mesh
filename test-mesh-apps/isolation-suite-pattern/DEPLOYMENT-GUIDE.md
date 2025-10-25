# Deployment Guide: Isolation vs Suite Pattern

## Quick Start

### Option 1: Isolation Mode (Run Services Independently)

```bash
# Terminal 1 - Service A
cd test-mesh-apps/isolation-suite-pattern/service-a
docker-compose up

# Terminal 2 - Service B
cd test-mesh-apps/isolation-suite-pattern/service-b
docker-compose up
```

### Option 2: Suite Mode (Run Services Together)

```bash
cd test-mesh-apps/isolation-suite-pattern/suite
docker-compose up
```

## Architecture Comparison

### Isolation Architecture
```
┌─────────────────────────────────────────┐
│              Host Machine                │
│                                          │
│  ┌────────────────┐  ┌────────────────┐ │
│  │  Service A     │  │  Service B     │ │
│  │  Container     │  │  Container     │ │
│  │                │  │                │ │
│  │ Bridge Network │  │ Bridge Network │ │
│  │  (isolated)    │  │  (isolated)    │ │
│  └────────────────┘  └────────────────┘ │
│         │                    │           │
│         │                    │           │
│    Port 5001             Port 6001       │
└─────────┼────────────────────┼───────────┘
          │                    │
          └────────────────────┘
         External Access Only
```

### Suite Architecture
```
┌─────────────────────────────────────────┐
│              Host Machine                │
│                                          │
│  ┌─────────────────────────────────────┐│
│  │      Shared Suite Network           ││
│  │                                      ││
│  │  ┌────────────┐   ┌────────────┐   ││
│  │  │ Service A  │   │ Service B  │   ││
│  │  │ Container  │◄──│ Container  │   ││
│  │  │            │   │            │   ││
│  │  └────────────┘   └────────────┘   ││
│  │         │                │          ││
│  └─────────┼────────────────┼──────────┘│
│            │                │            │
│       Port 5001        Port 6001         │
└────────────┼────────────────┼────────────┘
             │                │
             └────────────────┘
      External + Internal Communication
```

## Label-Based Deployment Selection

### Identifying Deployment Mode

```bash
# Check if a service is in isolation mode
docker inspect <container> | jq '.[0].Config.Labels["mesh.isolation"]'
# Returns: "true" for isolation mode

# Check if a service is in suite mode
docker inspect <container> | jq '.[0].Config.Labels["mesh.suite"]'
# Returns: "true" for suite mode
```

### Programmatic Deployment Selection

```bash
#!/bin/bash
# Example: Dynamic deployment based on machine count

MACHINE_COUNT=1  # Change based on available machines

if [ $MACHINE_COUNT -eq 1 ]; then
    echo "Single machine detected: deploying in suite mode"
    cd suite
    docker-compose up -d
else
    echo "Multiple machines detected: deploying in isolation mode"
    # Deploy service-a on machine 1
    # Deploy service-b on machine 2
fi
```

## Environment Variables

### Isolation Mode

**Service A:**
- `SERVICE_NAME`: service-a
- `PORT`: 5000
- `DEPLOYMENT_MODE`: isolation

**Service B:**
- `SERVICE_NAME`: service-b
- `PORT`: 6000
- `DEPLOYMENT_MODE`: isolation
- `SERVICE_A_URL`: Not set (no integration)

### Suite Mode

**Service A:**
- `SERVICE_NAME`: service-a
- `PORT`: 5000
- `DEPLOYMENT_MODE`: suite

**Service B:**
- `SERVICE_NAME`: service-b
- `PORT`: 6000
- `DEPLOYMENT_MODE`: suite
- `SERVICE_A_URL`: http://service-a:5000 (enables integration)

## Testing Checklist

### Isolation Mode Testing

- [ ] Service A starts independently
- [ ] Service A responds to health checks
- [ ] Service A provides data via `/api/data`
- [ ] Service B starts independently
- [ ] Service B responds to health checks
- [ ] Service B processes without Service A
- [ ] No network connectivity between services

```bash
# Test isolation
curl http://localhost:5001/health
curl http://localhost:5001/api/data
curl http://localhost:6001/health
curl http://localhost:6001/api/process
# Should see: "integration": "not-configured"
```

### Suite Mode Testing

- [ ] Service A starts first (healthcheck)
- [ ] Service B waits for Service A
- [ ] Both services share network
- [ ] Service B can reach Service A
- [ ] Integration endpoint works
- [ ] Data flows from A to B

```bash
# Test suite integration
curl http://localhost:5001/health
curl http://localhost:6001/api/status
# Should see: "service_a_configured": true

curl http://localhost:6001/api/process
# Should see: "integration": "success"
# Should include data from Service A
```

## Multi-Machine Deployment Scenario

### Machine 1 (Running Service A)
```bash
# On machine-1 (IP: 192.168.1.10)
cd service-a
docker-compose up -d

# Verify
curl http://localhost:5001/health
```

### Machine 2 (Running Service B)
```bash
# On machine-2 (IP: 192.168.1.20)
cd service-b

# Modify docker-compose.yml to add external Service A URL
docker-compose up -d

# Or use environment override
SERVICE_A_URL=http://192.168.1.10:5001 docker-compose up -d

# Verify integration
curl http://localhost:6001/api/status
```

## Migration Between Modes

### From Isolation to Suite

```bash
# Stop isolation deployments
cd service-a && docker-compose down
cd service-b && docker-compose down

# Start suite
cd suite && docker-compose up -d
```

### From Suite to Isolation

```bash
# Stop suite
cd suite && docker-compose down

# Start isolation
cd service-a && docker-compose up -d
cd service-b && docker-compose up -d
```

## Monitoring and Debugging

### Check Service Status
```bash
# Isolation mode
docker ps --filter "label=mesh.isolation=true"

# Suite mode
docker ps --filter "label=mesh.suite=true"
```

### View Logs
```bash
# Isolation
docker logs service-a-isolation
docker logs service-b-isolation

# Suite
docker logs service-a-suite
docker logs service-b-suite
```

### Network Inspection
```bash
# Isolation networks
docker network ls --filter "label=mesh.network.type=isolation"

# Suite network
docker network ls --filter "label=mesh.network.type=suite"

# Inspect suite network connectivity
docker network inspect isolation-suite-pattern_suite-network
```

### Test Service Connectivity (Suite Mode)
```bash
# Execute from Service B container to test Service A
docker exec service-b-suite curl http://service-a:5000/health
```

## Performance Considerations

### Isolation Mode
- **Pros**: Complete independence, can distribute across machines
- **Cons**: No local network communication, requires external routing
- **Best for**: Production, distributed deployments, resilience

### Suite Mode
- **Pros**: Low latency, simple networking, easy development
- **Cons**: Single point of failure, all services on one machine
- **Best for**: Development, staging, small-scale production

## Troubleshooting

### Issue: Service B cannot reach Service A in suite mode

**Check 1: Verify network**
```bash
docker network inspect isolation-suite-pattern_suite-network
# Both containers should be listed
```

**Check 2: Verify environment variable**
```bash
docker exec service-b-suite env | grep SERVICE_A_URL
# Should show: SERVICE_A_URL=http://service-a:5000
```

**Check 3: Test connectivity**
```bash
docker exec service-b-suite ping service-a
docker exec service-b-suite curl http://service-a:5000/health
```

### Issue: Port conflicts

If you're running both isolation and suite modes simultaneously:
- Isolation uses: 5001, 6001
- Suite uses: 5001, 6001
- These will conflict!

**Solution**: Stop one mode before starting the other, or modify ports.

### Issue: Containers not starting

**Check dependency order (suite mode):**
```bash
docker-compose logs service-a
# Service A must be healthy before Service B starts
```

**Check build context:**
```bash
# Suite mode builds from parent directories
cd suite
docker-compose build --no-cache
```

## Advanced: Custom Deployment Scripts

### Example: Auto-detect and deploy
```bash
#!/bin/bash
# auto-deploy.sh

AVAILABLE_MACHINES=$(cat machines.txt | wc -l)

deploy_suite() {
    echo "Deploying in suite mode..."
    cd suite
    docker-compose up -d
    echo "Suite deployed on single machine"
}

deploy_distributed() {
    echo "Deploying in distributed mode..."

    # Deploy Service A on first machine
    ssh user@machine1 "cd service-a && docker-compose up -d"

    # Deploy Service B on second machine with Service A URL
    ssh user@machine2 "cd service-b && \
        SERVICE_A_URL=http://machine1:5001 \
        docker-compose up -d"

    echo "Distributed deployment complete"
}

if [ $AVAILABLE_MACHINES -eq 1 ]; then
    deploy_suite
else
    deploy_distributed
fi
```

## Summary

| Deployment Mode | Use When | Label | Network |
|----------------|----------|-------|---------|
| **Isolation** | Multiple machines, production, distributed | `mesh.isolation=true` | Separate networks |
| **Suite** | Single machine, dev, staging, small prod | `mesh.suite=true` | Shared network |

Choose based on:
- Available infrastructure
- Scale requirements
- Development vs production
- Resilience needs
