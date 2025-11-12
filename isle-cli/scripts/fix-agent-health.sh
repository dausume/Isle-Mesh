#!/bin/bash
#
# Fix Agent Healthcheck
# Copies the corrected docker-compose.yml and restarts the agent
#

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$CLI_DIR")"

SOURCE_COMPOSE="${PROJECT_ROOT}/isle-agent/docker-compose.yml"
DEST_COMPOSE="/etc/isle-mesh/agent/docker-compose.yml"

echo -e "${BLUE}[INFO]${NC} Fixing agent healthcheck issue..."
echo ""

# Check if source exists
if [[ ! -f "$SOURCE_COMPOSE" ]]; then
    echo -e "${RED}[ERROR]${NC} Source docker-compose.yml not found: $SOURCE_COMPOSE"
    exit 1
fi

# Show the issue
echo -e "${BLUE}[INFO]${NC} Current deployed healthcheck:"
grep -A 1 "test:.*wget" "$DEST_COMPOSE" 2>/dev/null || echo "  (file not readable)"
echo ""

echo -e "${BLUE}[INFO]${NC} Corrected healthcheck (from source):"
grep -A 1 "test:.*wget" "$SOURCE_COMPOSE"
echo ""

# Copy the file
echo -e "${BLUE}[INFO]${NC} Copying corrected docker-compose.yml..."
sudo cp "$SOURCE_COMPOSE" "$DEST_COMPOSE"

# Verify the copy
echo -e "${GREEN}[SUCCESS]${NC} File copied"
echo ""

# Restart agent
echo -e "${BLUE}[INFO]${NC} Restarting agent..."
cd /etc/isle-mesh/agent
docker compose up -d

echo ""
echo -e "${BLUE}[INFO]${NC} Waiting for healthcheck..."
sleep 35

# Check status
if docker ps --filter "name=isle-agent" --format '{{.Status}}' | grep -q "healthy"; then
    echo -e "${GREEN}[SUCCESS]${NC} Agent is now healthy!"
else
    echo -e "${BLUE}[INFO]${NC} Agent status:"
    docker ps --filter "name=isle-agent" --format '{{.Status}}'
    echo ""
    echo -e "${BLUE}[INFO]${NC} Check again in a few moments with: docker ps | grep isle-agent"
fi

echo ""
echo -e "${GREEN}[COMPLETE]${NC} Agent healthcheck fixed"
