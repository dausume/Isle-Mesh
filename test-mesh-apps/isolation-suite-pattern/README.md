# Isolation vs Suite Deployment Pattern

This test case demonstrates two fundamental deployment patterns for mesh applications:

1. **Isolation Mode**: Services that can run independently without dependencies
2. **Suite Mode**: Services that are combined and interconnected on a single machine

## Purpose

This pattern provides the foundation for dynamic deployment logic that can:
- Deploy all services on a single machine (suite mode)
- Deploy services across multiple machines (distributed isolation mode)
- Mix and match based on resource availability and deployment requirements

## Directory Structure

```
isolation-suite-pattern/
├── service-a/              # Independent API service
│   ├── Dockerfile
│   ├── docker-compose.yml  # Isolation deployment
│   ├── app.py
│   └── requirements.txt
├── service-b/              # Independent processor service
│   ├── Dockerfile
│   ├── docker-compose.yml  # Isolation deployment
│   ├── app.py
│   └── requirements.txt
└── suite/
    └── docker-compose.yml  # Suite deployment (both services)
```

## Label System

### Isolation Mode Labels
```yaml
labels:
  mesh.isolation: "true"
  mesh.suite: "false"
  mesh.deployment.standalone: "true"
```

### Suite Mode Labels
```yaml
labels:
  mesh.isolation: "false"
  mesh.suite: "true"
  mesh.deployment.standalone: "false"
  mesh.suite.role: "<role-name>"
  mesh.depends_on: "<service-name>"  # Optional dependency
```

## Service Descriptions

### Service A (Data Provider)
- Simple Flask API that provides data
- Can run completely independently
- No external service dependencies
- Port: 5001 (host) → 5000 (container)

**Endpoints:**
- `GET /` - Service information
- `GET /health` - Health check
- `GET /api/data` - Returns sample data

### Service B (Data Processor)
- Flask API that processes data
- Can run independently or consume data from Service A
- Behavior changes based on deployment mode
- Port: 6001 (host) → 6000 (container)

**Endpoints:**
- `GET /` - Service information and connectivity status
- `GET /health` - Health check
- `GET /api/process` - Process data (with or without Service A integration)
- `GET /api/status` - Deployment mode and configuration status

## Running the Services

### Isolation Mode (Each service independently)

Run Service A alone:
```bash
cd service-a
docker-compose up -d
```

Run Service B alone:
```bash
cd service-b
docker-compose up -d
```

Test isolation mode:
```bash
# Service A
curl http://localhost:5001/
curl http://localhost:5001/api/data

# Service B (no integration)
curl http://localhost:6001/
curl http://localhost:6001/api/process
```

### Suite Mode (Both services together)

Run the suite:
```bash
cd suite
docker-compose up -d
```

Test suite mode with integration:
```bash
# Service A
curl http://localhost:5001/

# Service B (with Service A integration)
curl http://localhost:6001/api/status
curl http://localhost:6001/api/process
```

In suite mode, Service B's `/api/process` endpoint will fetch data from Service A and include it in the response.

## Key Differences

| Aspect | Isolation Mode | Suite Mode |
|--------|---------------|------------|
| **Network** | Separate bridge networks | Shared suite-network |
| **Service Discovery** | No inter-service communication | Service names resolve via Docker DNS |
| **Dependencies** | None | Service B depends on Service A |
| **Environment** | `DEPLOYMENT_MODE=isolation` | `DEPLOYMENT_MODE=suite` |
| **Service URLs** | Not configured | `SERVICE_A_URL=http://service-a:5000` |
| **Container Names** | `*-isolation` | `*-suite` |

## Dynamic Deployment Logic

This pattern enables future deployment automation:

### Single Machine Deployment (Suite)
```yaml
# Use suite/docker-compose.yml
# All services share network
# Internal service-to-service communication
# Lower latency, simpler networking
```

### Multi-Machine Deployment (Distributed Isolation)
```yaml
# Machine 1: service-a/docker-compose.yml
# Machine 2: service-b/docker-compose.yml
# External URLs configured via environment variables
# Service discovery via external DNS or service mesh
# Higher resilience, better resource distribution
```

### Hybrid Deployment
```yaml
# Some services in suite mode on Machine 1
# Other services in isolation mode on Machine 2
# Mix and match based on requirements
```

## Deployment Decision Logic (Pseudocode)

```python
def deploy_application(services, machines):
    if len(machines) == 1:
        # Single machine: use suite mode
        deploy_suite(services, machines[0])
    else:
        # Multiple machines: distribute services
        for service in services:
            machine = select_machine(service, machines)
            deploy_isolation(service, machine)
            configure_service_discovery(service, machine)
```

## Testing the Pattern

### Verify Isolation Independence
```bash
# Start only Service A
cd service-a && docker-compose up -d
curl http://localhost:5001/health  # Should work

# Start only Service B
cd service-b && docker-compose up -d
curl http://localhost:6001/health  # Should work independently
```

### Verify Suite Integration
```bash
# Start suite
cd suite && docker-compose up -d

# Wait for services to be healthy
sleep 10

# Test integration
curl http://localhost:6001/api/process
# Should return data fetched from Service A
```

### Verify Labels
```bash
# Check isolation labels
docker inspect service-a-isolation | jq '.[0].Config.Labels'

# Check suite labels
docker inspect service-a-suite | jq '.[0].Config.Labels'
```

## Cleanup

```bash
# Stop isolation services
cd service-a && docker-compose down
cd service-b && docker-compose down

# Stop suite
cd suite && docker-compose down

# Clean up everything
docker-compose down -v
docker system prune -f
```

## Future Enhancements

1. **Service Registry**: Add consul or etcd for service discovery across machines
2. **Load Balancing**: Add nginx/traefik for routing between distributed services
3. **Configuration Management**: External config service for dynamic environment variables
4. **Health Monitoring**: Centralized monitoring for all deployment modes
5. **Auto-scaling**: Scale services based on load (suite mode)
6. **Migration Tools**: Move services between isolation and suite modes dynamically

## Use Cases

### Development
- Run services in isolation for focused testing
- Run suite locally for full integration testing

### Staging
- Suite mode on single staging server
- Cost-effective full-stack testing

### Production - Small Scale
- Suite mode for simplicity
- All services on one or few machines

### Production - Large Scale
- Isolation mode across multiple machines
- Independent scaling and deployment
- Geographic distribution

### Disaster Recovery
- Quick failover between machines
- Redeploy suite elsewhere if primary fails
