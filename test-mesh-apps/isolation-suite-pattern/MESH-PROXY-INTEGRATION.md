# Mesh-Proxy Integration

This document explains how to integrate the isolation/suite pattern with the IsleMesh proxy system.

## Overview

The mesh-proxy can be deployed in both isolation and suite modes to provide:
- SSL/TLS termination
- Load balancing
- Service routing
- Security policies

## Integration Pattern

### Suite Mode with Mesh-Proxy

```yaml
# suite/docker-compose.with-proxy.yml
version: '3.8'

services:
  mesh-proxy:
    build: ../../mesh-proxy
    container_name: mesh-proxy-suite
    ports:
      - "443:443"
      - "80:80"
    volumes:
      - ./proxy-config:/etc/nginx/conf.d
      - ./ssl:/etc/nginx/ssl
    environment:
      - DEPLOYMENT_MODE=suite
      - BACKEND_SERVICES=service-a:5000,service-b:6000
    labels:
      mesh.suite: "true"
      mesh.component: "proxy"
      mesh.suite.role: "gateway"
    networks:
      - suite-network
    depends_on:
      service-a:
        condition: service_healthy
      service-b:
        condition: service_healthy

  service-a:
    build: ../service-a
    container_name: service-a-suite
    # Remove exposed ports - only accessible via proxy
    expose:
      - "5000"
    environment:
      - SERVICE_NAME=service-a
      - PORT=5000
      - DEPLOYMENT_MODE=suite
    labels:
      mesh.suite: "true"
      mesh.service.name: "service-a"
      mesh.service.type: "api"
      mesh.proxy.backend: "true"
      mesh.proxy.route: "/api/service-a"
    networks:
      - suite-network

  service-b:
    build: ../service-b
    container_name: service-b-suite
    expose:
      - "6000"
    environment:
      - SERVICE_NAME=service-b
      - PORT=6000
      - DEPLOYMENT_MODE=suite
      - SERVICE_A_URL=http://service-a:5000
    labels:
      mesh.suite: "true"
      mesh.service.name: "service-b"
      mesh.service.type: "processor"
      mesh.proxy.backend: "true"
      mesh.proxy.route: "/api/service-b"
    depends_on:
      service-a:
        condition: service_healthy
    networks:
      - suite-network

networks:
  suite-network:
    driver: bridge
    labels:
      mesh.network.type: "suite"
      mesh.network.shared: "true"
```

### Proxy Configuration for Suite Mode

```nginx
# proxy-config/suite-routes.conf

# Service A routing
location /api/service-a/ {
    proxy_pass http://service-a:5000/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Mesh-Mode "suite";
}

# Service B routing
location /api/service-b/ {
    proxy_pass http://service-b:6000/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Mesh-Mode "suite";
}

# Health checks (internal only)
location /health {
    access_log off;
    return 200 "healthy\n";
    add_header Content-Type text/plain;
}
```

### Isolation Mode with Mesh-Proxy

Each service has its own proxy instance:

```yaml
# service-a/docker-compose.with-proxy.yml
version: '3.8'

services:
  mesh-proxy-a:
    build: ../../mesh-proxy
    container_name: mesh-proxy-service-a
    ports:
      - "443:443"
      - "80:80"
    volumes:
      - ./proxy-config:/etc/nginx/conf.d
      - ./ssl:/etc/nginx/ssl
    environment:
      - DEPLOYMENT_MODE=isolation
      - BACKEND_SERVICE=service-a:5000
    labels:
      mesh.isolation: "true"
      mesh.component: "proxy"
      mesh.service.name: "service-a"
    networks:
      - service-a-network

  service-a:
    build: .
    container_name: service-a-isolation
    expose:
      - "5000"
    environment:
      - SERVICE_NAME=service-a
      - PORT=5000
      - DEPLOYMENT_MODE=isolation
    labels:
      mesh.isolation: "true"
      mesh.service.name: "service-a"
    networks:
      - service-a-network

networks:
  service-a-network:
    driver: bridge
    labels:
      mesh.network.type: "isolation"
```

```yaml
# service-b/docker-compose.with-proxy.yml
version: '3.8'

services:
  mesh-proxy-b:
    build: ../../mesh-proxy
    container_name: mesh-proxy-service-b
    ports:
      - "443:443"
      - "80:80"
    volumes:
      - ./proxy-config:/etc/nginx/conf.d
      - ./ssl:/etc/nginx/ssl
    environment:
      - DEPLOYMENT_MODE=isolation
      - BACKEND_SERVICE=service-b:6000
      - EXTERNAL_SERVICE_A_URL=${SERVICE_A_URL}
    labels:
      mesh.isolation: "true"
      mesh.component: "proxy"
      mesh.service.name: "service-b"
    networks:
      - service-b-network

  service-b:
    build: .
    container_name: service-b-isolation
    expose:
      - "6000"
    environment:
      - SERVICE_NAME=service-b
      - PORT=6000
      - DEPLOYMENT_MODE=isolation
      - SERVICE_A_URL=${SERVICE_A_URL}
    labels:
      mesh.isolation: "true"
      mesh.service.name: "service-b"
    networks:
      - service-b-network

networks:
  service-b-network:
    driver: bridge
    labels:
      mesh.network.type: "isolation"
```

## Access Patterns

### Suite Mode with Proxy

```
External Client
    │
    │ HTTPS Request
    │
    ▼
https://mesh-app.local/api/service-a/
    │
    ▼
Mesh Proxy (443)
    │
    │ Internal HTTP
    │
    ├─► http://service-a:5000/
    │   (for /api/service-a/* requests)
    │
    └─► http://service-b:6000/
        (for /api/service-b/* requests)
```

### Isolation Mode with Proxy

```
External Client                External Client
    │                               │
    │ HTTPS                          │ HTTPS
    │                               │
    ▼                               ▼
Machine 1                       Machine 2
mesh-proxy-a (443)             mesh-proxy-b (443)
    │                               │
    │ Internal HTTP                 │ Internal HTTP
    │                               │
    ▼                               ▼
service-a:5000                 service-b:6000
                                    │
                                    │ External HTTPS
                                    │ (if needed)
                                    ▼
                            https://machine1/api/service-a/
```

## Dynamic Proxy Configuration

### Auto-configure based on labels

```python
#!/usr/bin/env python3
"""
Generate nginx config based on container labels
"""

import docker
import jinja2

client = docker.from_env()

def get_suite_services():
    """Get all services in suite mode"""
    containers = client.containers.list(
        filters={'label': 'mesh.suite=true'}
    )

    services = []
    for container in containers:
        if container.labels.get('mesh.proxy.backend') == 'true':
            services.append({
                'name': container.labels.get('mesh.service.name'),
                'route': container.labels.get('mesh.proxy.route'),
                'host': container.name,
                'port': container.labels.get('mesh.service.port', '5000')
            })

    return services

def generate_nginx_config(services, mode='suite'):
    """Generate nginx configuration"""

    template = jinja2.Template('''
# Auto-generated nginx config for {{ mode }} mode
# Generated: {{ timestamp }}

{% for service in services %}
# Route for {{ service.name }}
location {{ service.route }}/ {
    proxy_pass http://{{ service.host }}:{{ service.port }}/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Mesh-Mode "{{ mode }}";
    proxy_set_header X-Mesh-Service "{{ service.name }}";

    # Health check pass-through
    location {{ service.route }}/health {
        proxy_pass http://{{ service.host }}:{{ service.port }}/health;
    }
}
{% endfor %}
    ''')

    from datetime import datetime
    return template.render(
        mode=mode,
        services=services,
        timestamp=datetime.now().isoformat()
    )

if __name__ == '__main__':
    services = get_suite_services()
    config = generate_nginx_config(services)

    with open('/etc/nginx/conf.d/mesh-routes.conf', 'w') as f:
        f.write(config)

    # Reload nginx
    import subprocess
    subprocess.run(['nginx', '-s', 'reload'])
```

## SSL/TLS Configuration

### Shared Certificate (Suite Mode)

```yaml
# All services share the same domain certificate
services:
  mesh-proxy:
    volumes:
      - ./ssl/mesh-app.crt:/etc/nginx/ssl/mesh-app.crt
      - ./ssl/mesh-app.key:/etc/nginx/ssl/mesh-app.key
```

```nginx
# SSL configuration
server {
    listen 443 ssl http2;
    server_name mesh-app.local;

    ssl_certificate /etc/nginx/ssl/mesh-app.crt;
    ssl_certificate_key /etc/nginx/ssl/mesh-app.key;

    # Include all service routes
    include /etc/nginx/conf.d/suite-routes.conf;
}
```

### Separate Certificates (Isolation Mode)

```yaml
# service-a/docker-compose.with-proxy.yml
services:
  mesh-proxy-a:
    volumes:
      - ./ssl/service-a.crt:/etc/nginx/ssl/service.crt
      - ./ssl/service-a.key:/etc/nginx/ssl/service.key
```

```nginx
# Service A proxy
server {
    listen 443 ssl http2;
    server_name service-a.mesh-app.local;

    ssl_certificate /etc/nginx/ssl/service.crt;
    ssl_certificate_key /etc/nginx/ssl/service.key;

    location / {
        proxy_pass http://service-a:5000;
    }
}
```

## Service Discovery Integration

### Suite Mode: Direct DNS

```python
# Services can find each other via Docker DNS
SERVICE_A_URL = "http://service-a:5000"
SERVICE_B_URL = "http://service-b:6000"

# No external discovery needed
```

### Isolation Mode: External Discovery

```python
import consul
import os

# Connect to Consul
c = consul.Consul(host='consul.mesh-app.local')

# Discover Service A
_, services = c.health.service('service-a', passing=True)
if services:
    service_a = services[0]
    SERVICE_A_URL = f"https://{service_a['Service']['Address']}:{service_a['Service']['Port']}"
else:
    # Fallback to environment variable
    SERVICE_A_URL = os.getenv('SERVICE_A_URL')
```

## Load Balancing

### Suite Mode: Proxy handles load balancing

```nginx
# Multiple instances of same service
upstream service-a-backend {
    least_conn;
    server service-a-1:5000;
    server service-a-2:5000;
    server service-a-3:5000;
}

location /api/service-a/ {
    proxy_pass http://service-a-backend/;
}
```

### Isolation Mode: External load balancer

```
                    External LB
                         │
         ┌───────────────┼───────────────┐
         │               │               │
         ▼               ▼               ▼
    Machine 1       Machine 2       Machine 3
    Proxy A         Proxy A         Proxy A
         │               │               │
         ▼               ▼               ▼
    Service A       Service A       Service A
```

## Security Headers

### Suite Mode

```nginx
# Add security headers for all services
add_header X-Mesh-Mode "suite" always;
add_header X-Frame-Options "SAMEORIGIN" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-XSS-Protection "1; mode=block" always;
add_header Strict-Transport-Security "max-age=31536000" always;
```

### Isolation Mode

```nginx
# Each service can have different security policies
add_header X-Mesh-Mode "isolation" always;
add_header X-Service-ID "service-a" always;
add_header X-Frame-Options "DENY" always;
```

## Example: Complete Suite Deployment with Proxy

```bash
# Directory structure
isolation-suite-pattern/
├── suite-with-proxy/
│   ├── docker-compose.yml
│   ├── proxy-config/
│   │   ├── suite-routes.conf
│   │   └── security-headers.conf
│   └── ssl/
│       ├── mesh-app.crt
│       └── mesh-app.key

# Deploy
cd suite-with-proxy
docker-compose up -d

# Access services via proxy
curl https://mesh-app.local/api/service-a/
curl https://mesh-app.local/api/service-b/

# All traffic goes through single proxy
# Internal communication still direct (service-b -> service-a)
```

## Example: Complete Isolation Deployment with Proxy

```bash
# Machine 1
cd service-a
docker-compose -f docker-compose.with-proxy.yml up -d

# Machine 2
export SERVICE_A_URL=https://machine1.mesh-app.local/api/service-a
cd service-b
docker-compose -f docker-compose.with-proxy.yml up -d

# Access services
curl https://machine1.mesh-app.local/api/service-a/
curl https://machine2.mesh-app.local/api/service-b/
```

## Integration with isle-cli

```bash
# Generate mesh configuration for suite mode
./isle-cli scaffold --mode suite \
  --services service-a,service-b \
  --with-proxy \
  --domain mesh-app.local

# Generate mesh configuration for isolation mode
./isle-cli scaffold --mode isolation \
  --services service-a,service-b \
  --with-proxy \
  --separate-domains
```

This would auto-generate the appropriate docker-compose files with proxy configuration based on the deployment mode.

## Monitoring Proxy Metrics

### Add prometheus metrics to proxy

```nginx
# In proxy config
location /metrics {
    stub_status;
    access_log off;
    allow 172.16.0.0/12;  # Internal only
    deny all;
}
```

### Labels for monitoring

```yaml
mesh-proxy:
  labels:
    mesh.monitoring.prometheus: "true"
    mesh.monitoring.port: "9090"
    mesh.metrics.endpoint: "/metrics"
```

## Summary

| Aspect | Suite Mode | Isolation Mode |
|--------|-----------|----------------|
| **Proxy Instances** | 1 shared | 1 per service |
| **SSL Certificates** | 1 shared cert | 1 per service |
| **Routing** | Path-based | Domain-based |
| **Load Balancing** | Internal (nginx upstream) | External LB |
| **Service Discovery** | Docker DNS | Consul/external |
| **Network** | Shared | Separate |
| **Complexity** | Low | Medium |
| **Scalability** | Limited to 1 machine | Unlimited |

Choose based on your deployment requirements!
