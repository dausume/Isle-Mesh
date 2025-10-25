# Architecture Overview

## Label-Based Deployment Selection

```
┌─────────────────────────────────────────────────────────┐
│         Deployment Decision Logic                       │
│                                                          │
│  ┌────────────────────────────────────────────────┐     │
│  │  Check available infrastructure:               │     │
│  │  - How many machines?                          │     │
│  │  - Resource constraints?                       │     │
│  │  - Network topology?                           │     │
│  └────────────────┬───────────────────────────────┘     │
│                   │                                      │
│                   ▼                                      │
│         ┌─────────────────┐                              │
│         │  Decision Point │                              │
│         └────────┬────────┘                              │
│                  │                                       │
│        ┌─────────┴─────────┐                             │
│        ▼                   ▼                             │
│  One Machine        Multiple Machines                    │
│        │                   │                             │
│        ▼                   ▼                             │
│  ┌──────────┐      ┌──────────────┐                     │
│  │  Suite   │      │  Isolation   │                     │
│  │  Mode    │      │  Mode        │                     │
│  └──────────┘      └──────────────┘                     │
│        │                   │                             │
│        │                   │                             │
│  mesh.suite=true    mesh.isolation=true                  │
└────────┼───────────────────┼─────────────────────────────┘
         │                   │
         ▼                   ▼
```

## Isolation Mode Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Machine 1                             │
│  ┌───────────────────────────────────────────────────┐  │
│  │ Service A Container (service-a-isolation)         │  │
│  │                                                    │  │
│  │  ┌──────────────────────────────────────────┐    │  │
│  │  │ Flask App on Port 5000                   │    │  │
│  │  │                                           │    │  │
│  │  │ Environment:                              │    │  │
│  │  │  - DEPLOYMENT_MODE=isolation              │    │  │
│  │  │  - SERVICE_NAME=service-a                 │    │  │
│  │  │                                           │    │  │
│  │  │ Labels:                                   │    │  │
│  │  │  - mesh.isolation=true                    │    │  │
│  │  │  - mesh.suite=false                       │    │  │
│  │  │  - mesh.deployment.standalone=true        │    │  │
│  │  └──────────────────────────────────────────┘    │  │
│  │                        │                          │  │
│  │                        │ Port 5000                │  │
│  └────────────────────────┼──────────────────────────┘  │
│  ┌────────────────────────┼──────────────────────────┐  │
│  │  Bridge Network        │                          │  │
│  │  (service-a-network)   │                          │  │
│  └────────────────────────┼──────────────────────────┘  │
│                           │                             │
│                      Port 5001 (Host)                   │
└───────────────────────────┼─────────────────────────────┘
                            │
                            ▼
                    External Access

┌─────────────────────────────────────────────────────────┐
│                    Machine 2                             │
│  ┌───────────────────────────────────────────────────┐  │
│  │ Service B Container (service-b-isolation)         │  │
│  │                                                    │  │
│  │  ┌──────────────────────────────────────────┐    │  │
│  │  │ Flask App on Port 6000                   │    │  │
│  │  │                                           │    │  │
│  │  │ Environment:                              │    │  │
│  │  │  - DEPLOYMENT_MODE=isolation              │    │  │
│  │  │  - SERVICE_NAME=service-b                 │    │  │
│  │  │  - SERVICE_A_URL=not set                  │    │  │
│  │  │                                           │    │  │
│  │  │ Labels:                                   │    │  │
│  │  │  - mesh.isolation=true                    │    │  │
│  │  │  - mesh.suite=false                       │    │  │
│  │  │  - mesh.deployment.standalone=true        │    │  │
│  │  └──────────────────────────────────────────┘    │  │
│  │                        │                          │  │
│  │                        │ Port 6000                │  │
│  └────────────────────────┼──────────────────────────┘  │
│  ┌────────────────────────┼──────────────────────────┐  │
│  │  Bridge Network        │                          │  │
│  │  (service-b-network)   │                          │  │
│  └────────────────────────┼──────────────────────────┘  │
│                           │                             │
│                      Port 6001 (Host)                   │
└───────────────────────────┼─────────────────────────────┘
                            │
                            ▼
                    External Access

      No inter-service communication
      Each service is completely independent
```

## Suite Mode Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Single Machine                        │
│                                                          │
│  ┌────────────────────────────────────────────────────┐ │
│  │         Shared Network (suite-network)             │ │
│  │                                                     │ │
│  │  ┌───────────────────────┐                         │ │
│  │  │ Service A Container   │                         │ │
│  │  │ (service-a-suite)     │                         │ │
│  │  │                       │                         │ │
│  │  │ Environment:          │                         │ │
│  │  │  - DEPLOYMENT_MODE=   │                         │ │
│  │  │    suite              │                         │ │
│  │  │                       │                         │ │
│  │  │ Labels:               │                         │ │
│  │  │  - mesh.isolation=    │                         │ │
│  │  │    false              │                         │ │
│  │  │  - mesh.suite=true    │                         │ │
│  │  │  - mesh.suite.role=   │                         │ │
│  │  │    data-provider      │                         │ │
│  │  │                       │                         │ │
│  │  │ Port: 5000            │                         │ │
│  │  └───────────┬───────────┘                         │ │
│  │              │                                      │ │
│  │              │ Docker DNS: service-a:5000           │ │
│  │              │                                      │ │
│  │              ▼                                      │ │
│  │  ┌───────────────────────┐                         │ │
│  │  │ Service B Container   │                         │ │
│  │  │ (service-b-suite)     │◄────────────────┐       │ │
│  │  │                       │  Service calls  │       │ │
│  │  │ Environment:          │  Service A      │       │ │
│  │  │  - DEPLOYMENT_MODE=   │                 │       │ │
│  │  │    suite              │                 │       │ │
│  │  │  - SERVICE_A_URL=     │                 │       │ │
│  │  │    http://service-a:  │─────────────────┘       │ │
│  │  │    5000               │                         │ │
│  │  │                       │                         │ │
│  │  │ Labels:               │                         │ │
│  │  │  - mesh.isolation=    │                         │ │
│  │  │    false              │                         │ │
│  │  │  - mesh.suite=true    │                         │ │
│  │  │  - mesh.suite.role=   │                         │ │
│  │  │    data-processor     │                         │ │
│  │  │  - mesh.depends_on=   │                         │ │
│  │  │    service-a          │                         │ │
│  │  │                       │                         │ │
│  │  │ Port: 6000            │                         │ │
│  │  └───────────┬───────────┘                         │ │
│  │              │                                      │ │
│  └──────────────┼──────────────────────────────────────┘ │
│                 │                                        │
│            Port 5001, 6001 (Host)                        │
└─────────────────┼────────────────────────────────────────┘
                  │
                  ▼
          External + Internal Access
```

## Communication Flow

### Isolation Mode: External Request Only

```
User/Client
    │
    │ HTTP Request
    │
    ├─► http://machine1:5001/ ──► Service A
    │                              (Responds independently)
    │
    └─► http://machine2:6001/ ──► Service B
                                   (Responds independently)
                                   (No Service A data)
```

### Suite Mode: Internal Communication

```
User/Client
    │
    │ HTTP Request
    │
    ├─► http://localhost:5001/ ──► Service A
    │                               (Responds independently)
    │
    └─► http://localhost:6001/api/process
              │
              ▼
          Service B
              │
              │ Internal HTTP Request
              │ http://service-a:5000/api/data
              │
              ▼
          Service A ──────────┐
              │                │
              │ Returns data   │
              │                │
              └────────────────┘
                      │
                      ▼
                  Service B
                  Combines data
                  Returns to client
```

## Deployment Decision Matrix

```
┌─────────────────┬──────────────┬──────────────┬──────────────┐
│  Scenario       │  Machines    │  Mode        │  Labels      │
├─────────────────┼──────────────┼──────────────┼──────────────┤
│  Development    │  1 (local)   │  Suite       │  suite=true  │
│  Testing        │  1           │  Suite       │  suite=true  │
│  Small Prod     │  1           │  Suite       │  suite=true  │
│  Medium Prod    │  2-5         │  Isolation   │  iso=true    │
│  Large Prod     │  5+          │  Isolation   │  iso=true    │
│  Distributed    │  Geographic  │  Isolation   │  iso=true    │
│  Hybrid         │  Mixed       │  Both        │  Both        │
└─────────────────┴──────────────┴──────────────┴──────────────┘
```

## Container Lifecycle

### Isolation Mode Startup

```
1. Service A starts
   └─► Creates service-a-network
       └─► Container: service-a-isolation
           └─► Binds to port 5001
               └─► Ready

2. Service B starts (independent)
   └─► Creates service-b-network
       └─► Container: service-b-isolation
           └─► Binds to port 6001
               └─► Ready

No dependency between 1 and 2
Can start in any order
Can run on different machines
```

### Suite Mode Startup

```
1. Network creation
   └─► docker-compose creates suite-network

2. Service A starts first
   └─► Container: service-a-suite
       └─► Joins suite-network
           └─► Health check starts
               └─► Becomes healthy

3. Service B waits for Service A
   └─► depends_on: service-a (condition: healthy)
       └─► Service A is healthy
           └─► Service B starts
               └─► Container: service-b-suite
                   └─► Joins suite-network
                       └─► Gets SERVICE_A_URL env var
                           └─► Can resolve service-a via DNS
                               └─► Ready

Strict dependency order
Must be on same Docker host
Shares network namespace
```

## Label Usage in Automation

```python
# Pseudocode for deployment automation

def deploy_service(service_config, infrastructure):
    """
    Dynamically deploy based on infrastructure
    """

    # Count available machines
    machine_count = len(infrastructure.machines)

    if machine_count == 1:
        # Single machine: use suite mode
        labels = {
            'mesh.isolation': 'false',
            'mesh.suite': 'true',
            'mesh.deployment.standalone': 'false'
        }
        deploy_with_shared_network(service_config, labels)

    else:
        # Multiple machines: use isolation mode
        labels = {
            'mesh.isolation': 'true',
            'mesh.suite': 'false',
            'mesh.deployment.standalone': 'true'
        }
        deploy_distributed(service_config, labels, infrastructure)


def check_deployment_mode(container):
    """
    Check what mode a container is running in
    """
    if container.labels.get('mesh.suite') == 'true':
        return 'suite'
    elif container.labels.get('mesh.isolation') == 'true':
        return 'isolation'
    else:
        return 'unknown'
```

## Network Isolation Details

### Isolation Mode Networks

```
service-a-network (bridge)
├── Labels:
│   ├── mesh.network.type=isolation
│   └── mesh.network.service=service-a
├── Containers:
│   └── service-a-isolation (172.18.0.2)
└── No connection to other networks

service-b-network (bridge)
├── Labels:
│   ├── mesh.network.type=isolation
│   └── mesh.network.service=service-b
├── Containers:
│   └── service-b-isolation (172.19.0.2)
└── No connection to other networks
```

### Suite Mode Network

```
suite-network (bridge)
├── Labels:
│   ├── mesh.network.type=suite
│   ├── mesh.network.shared=true
│   └── mesh.suite.name=service-suite
├── Containers:
│   ├── service-a-suite (172.20.0.2)
│   │   └── DNS: service-a.suite-network
│   └── service-b-suite (172.20.0.3)
│       └── DNS: service-b.suite-network
└── Internal routing between all containers
```

## Resource Allocation

### Isolation Mode
- Each service: Own network namespace
- Memory: Isolated per container
- CPU: Isolated per container
- Storage: Separate volumes if needed
- Ports: Can use same internal ports (5000, 6000) on different machines

### Suite Mode
- Shared network namespace
- Memory: Shared pool on single machine
- CPU: Shared pool on single machine
- Storage: Can share volumes
- Ports: Must use different internal ports or rely on Docker networking
