#!/usr/bin/env bash
#
# Complete network cache cleanup for isle-agent
# This removes ALL Docker networks and system bridges for IsleMesh
#

set -euo pipefail

echo "=== Cleaning IsleMesh Network Cache ==="
echo ""

# Step 1: Stop all isle-agent containers
echo "[1/6] Stopping isle-agent containers..."
docker stop isle-agent 2>/dev/null || echo "  No running containers"
docker rm -f isle-agent 2>/dev/null || echo "  No containers to remove"
echo ""

# Step 2: Remove Docker networks created by Compose
echo "[2/6] Removing Docker networks..."
docker network rm isle-br-0 2>/dev/null && echo "  Removed isle-br-0 Docker network" || echo "  isle-br-0 already removed"
docker network rm isle-agent-net 2>/dev/null && echo "  Removed isle-agent-net Docker network" || echo "  isle-agent-net already removed"
docker network rm isle-sample-app_default 2>/dev/null && echo "  Removed isle-sample-app_default network" || echo "  No sample app network"
echo ""

# Step 3: Remove system bridge (if exists)
echo "[3/6] Removing system bridge interface..."
if ip link show isle-br-0 &>/dev/null; then
    echo "  Found isle-br-0 system bridge, removing..."
    sudo ip link set isle-br-0 down 2>/dev/null || true
    sudo ip link delete isle-br-0 2>/dev/null || true
    echo "  System bridge removed"
else
    echo "  No system bridge found"
fi
echo ""

# Step 4: Prune unused Docker networks
echo "[4/6] Pruning all unused Docker networks..."
docker network prune -f
echo ""

# Step 5: Remove Docker Compose generated files
echo "[5/6] Removing temporary Compose files..."
rm -f /etc/isle-mesh/agent/docker-compose.mdns.yml 2>/dev/null && echo "  Removed mDNS compose file" || echo "  No mDNS compose file"
echo ""

# Step 6: Clear agent mode cache
echo "[6/6] Clearing agent mode cache..."
rm -f /etc/isle-mesh/agent/agent.mode 2>/dev/null && echo "  Cleared agent mode file" || echo "  No mode file"
echo ""

echo "=== Network Cache Cleared ==="
echo ""
echo "To verify cleanup:"
echo "  docker network ls | grep isle"
echo "  ip link show isle-br-0"
echo ""
