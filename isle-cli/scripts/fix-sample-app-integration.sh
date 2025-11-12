#!/bin/bash
#
# Fix Sample App Integration with Agent
# Connects sample app to agent network and creates nginx config
#

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_CONFIGS_DIR="/etc/isle-mesh/agent/configs"
SAMPLE_APP_DIR="/tmp/isle-sample-app"

log_step() {
    echo ""
    echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}$1${NC}"
    echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
    echo ""
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*"
}

log_step "Step 1: Fix Agent Healthcheck"

# Fix the deployed docker-compose.yml
log_info "Copying corrected agent docker-compose.yml..."
sudo cp "${SCRIPT_DIR}/../../isle-agent/docker-compose.yml" /etc/isle-mesh/agent/docker-compose.yml

# Restart agent
log_info "Restarting agent with fixed healthcheck..."
cd /etc/isle-mesh/agent && docker compose up -d
sleep 5

log_success "Agent healthcheck fixed"

log_step "Step 2: Connect Sample App to Agent Network"

# Update sample app docker-compose to use agent network
log_info "Updating sample app docker-compose.yml..."
cat > "${SAMPLE_APP_DIR}/docker-compose.yml" << 'EOF'
version: '3.8'

services:
  sample:
    build: .
    container_name: isle-sample-app
    ports:
      - "5000:5000"
    environment:
      - DOMAIN=sample.local
      - APP_DIR=/tmp/isle-sample-app
      - PORT=5000
    restart: unless-stopped
    labels:
      - "isle.mesh.enable=true"
      - "isle.mesh.domain=sample.local"
      - "isle.mesh.port=5000"
      - "isle.mesh.container=isle-sample-app"
    networks:
      - isle-agent-net

networks:
  isle-agent-net:
    external: true
    name: isle-agent-net
EOF

log_success "Docker compose updated"

# Recreate sample app
log_info "Recreating sample app on agent network..."
cd "${SAMPLE_APP_DIR}"
docker compose down 2>/dev/null || true
docker compose up -d --build

sleep 3
log_success "Sample app connected to agent network"

log_step "Step 3: Create Nginx Config Fragment for Sample App"

# Create config fragment
log_info "Creating nginx config fragment..."
sudo tee "${AGENT_CONFIGS_DIR}/sample.conf" > /dev/null << 'NGINX_CONF'
# Configuration for sample app
# Domain: sample.local / sample.vlan

# Upstream definition
upstream sample_backend {
    server isle-sample-app:5000;
    keepalive 32;
}

# HTTP server
server {
    listen 80;
    server_name sample.local sample.vlan;

    location / {
        proxy_pass http://sample_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    location /health {
        access_log off;
        proxy_pass http://sample_backend/health;
    }
}

# HTTPS server
server {
    listen 443 ssl;
    server_name sample.local sample.vlan;

    # SSL configuration (self-signed for now)
    # TODO: Add SSL cert generation
    # ssl_certificate /etc/nginx/ssl/certs/sample.crt;
    # ssl_certificate_key /etc/nginx/ssl/keys/sample.key;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # For now, redirect HTTPS to HTTP since we don't have certs yet
    return 301 http://$host$request_uri;
}
NGINX_CONF

log_success "Config fragment created"

log_step "Step 4: Reload Agent with New Config"

# Test nginx config
log_info "Testing nginx configuration..."
if docker exec isle-agent nginx -t 2>&1; then
    log_success "Nginx config test passed"
else
    log_error "Nginx config test failed"
    exit 1
fi

# Reload nginx
log_info "Reloading nginx..."
docker exec isle-agent nginx -s reload

log_success "Agent reloaded"

log_step "Step 5: Verify Connectivity"

# Wait a moment for everything to settle
sleep 3

# Test if agent can reach sample app
log_info "Testing agent -> sample app connectivity..."
if docker exec isle-agent wget -q -O - http://isle-sample-app:5000/health 2>&1 | grep -q "healthy"; then
    log_success "Agent can reach sample app directly"
else
    log_error "Agent cannot reach sample app directly"
fi

# Test if proxy works
log_info "Testing nginx proxy to sample app..."
if docker exec isle-agent wget -q -O - http://sample.local/ 2>&1 | grep -q "Isle Mesh"; then
    log_success "Nginx proxy to sample app is working!"
else
    log_error "Nginx proxy test failed"
    log_info "This might be a DNS resolution issue inside the container"
fi

# Show status
echo ""
log_step "Current Status"

echo -e "${BOLD}Networks:${NC}"
echo -e "  Agent:      $(docker inspect isle-agent --format='{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}')"
echo -e "  Sample App: $(docker inspect isle-sample-app --format='{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}')"

echo ""
echo -e "${BOLD}Test from host:${NC}"
echo -e "  Direct access:  ${CYAN}curl http://localhost:5000/${NC}"
echo -e "  Via agent:      ${CYAN}curl http://localhost/${NC} (if DNS resolves sample.local)"

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}       Setup Complete!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo ""

log_info "Sample app should now be accessible via the agent proxy"
log_info "Once mDNS/avahi is configured, it will be at http://sample.local/"
