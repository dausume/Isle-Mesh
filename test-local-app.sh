#!/usr/bin/env bash
#
# Simple test script for bringing up agent + local-agent-app
# Tests .local domain access via mDNS
#

set -e

PROJECT_ROOT="/home/detts/Isle-Mesh"
AGENT_DIR="${PROJECT_ROOT}/isle-agent"
APP_DIR="${PROJECT_ROOT}/test-mesh-apps/local-agent-app"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}=== Isle Mesh - Local Agent Test ===${NC}\n"

# Step 1: Start isle-agent
echo -e "${BLUE}Step 1: Starting isle-agent...${NC}"
cd "${AGENT_DIR}"
if docker compose ps | grep -q "isle"; then
    echo -e "${GREEN}✓${NC} isle-agent already running"
else
    echo "Starting docker-compose..."
    docker compose up -d
    echo -e "${GREEN}✓${NC} isle-agent started"
fi
echo

# Step 2: Wait for services to be healthy
echo -e "${BLUE}Step 2: Waiting for services to become healthy...${NC}"
echo "Waiting for nginx..."
timeout=30
while ! curl -sf http://localhost/ >/dev/null 2>&1; do
    sleep 1
    timeout=$((timeout - 1))
    if [ $timeout -le 0 ]; then
        echo -e "${RED}✗${NC} nginx failed to start"
        exit 1
    fi
done
echo -e "${GREEN}✓${NC} nginx is responding"

echo "Checking avahi-daemon..."
if docker compose ps | grep avahi-daemon | grep -q Up; then
    echo -e "${GREEN}✓${NC} avahi-daemon is running (mDNS enabled)"
else
    echo -e "${YELLOW}⚠${NC} avahi-daemon may not be running"
fi
echo

# Step 3: Start the test app
echo -e "${BLUE}Step 3: Starting local-agent-app...${NC}"
cd "${APP_DIR}"
if docker compose ps | grep -q "local-app"; then
    echo -e "${GREEN}✓${NC} local-agent-app already running"
else
    echo "Building and starting app..."
    docker compose up -d --build
    echo -e "${GREEN}✓${NC} local-agent-app started"
fi
echo

# Step 4: Check running containers
echo -e "${BLUE}Step 4: Verifying containers...${NC}"
echo "Running containers:"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E "NAMES|local-app|isle"
echo

# Step 5: Show access instructions
echo -e "${BLUE}Step 5: Access Information${NC}"
echo ""
echo "The app containers are running. However, to access via .local domains you need to:"
echo ""
echo -e "${YELLOW}A. Register the app with isle-agent:${NC}"
echo "   1. Generate nginx config fragment:"
echo "      cd ${PROJECT_ROOT}/isle-agent"
echo "      python3 scripts/generate-app-fragment.py \\"
echo "        --app-name local-app \\"
echo "        --compose ${APP_DIR}/docker-compose.yml \\"
echo "        --domain local-app.local \\"
echo "        --mode local \\"
echo "        --output /etc/isle-mesh/agent/configs/local-app.conf"
echo ""
echo "   2. Reload nginx:"
echo "      docker exec isle-vlan-agent nginx -s reload"
echo ""
echo -e "${YELLOW}B. Test access:${NC}"
echo "   Direct access (bypassing .local):"
echo "   - Frontend: http://localhost:8081"
echo "   - Backend:  http://localhost:8100"
echo ""
echo "   Via .local (requires registration + mDNS):"
echo "   - https://app.local-app.local"
echo "   - https://api.local-app.local"
echo ""
echo -e "${YELLOW}C. Verify mDNS:${NC}"
echo "   avahi-browse -a | grep local-app"
echo ""
echo -e "${GREEN}Done!${NC}"
