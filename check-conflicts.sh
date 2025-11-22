#!/bin/bash
#
# Conflict Checker for OpenWRT + localhost-mdns + isle-agent-mdns
# Verifies that all components can run together without conflicts
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║        IsleMesh Conflict Checker                              ║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""

CONFLICTS=0
WARNINGS=0

# Function to check if port is in use
check_port() {
    local port=$1
    local desc=$2

    if netstat -tuln 2>/dev/null | grep -q ":${port} " || ss -tuln 2>/dev/null | grep -q ":${port} "; then
        echo -e "${YELLOW}⚠ Port ${port} is in use${NC} (needed for: ${desc})"
        WARNINGS=$((WARNINGS + 1))
        return 1
    else
        echo -e "${GREEN}✓ Port ${port} is available${NC} (${desc})"
        return 0
    fi
}

# Function to check Docker network
check_docker_network() {
    local network=$1

    if docker network ls --format "{{.Name}}" 2>/dev/null | grep -q "^${network}$"; then
        echo -e "${YELLOW}⚠ Docker network '${network}' already exists${NC}"
        WARNINGS=$((WARNINGS + 1))
        return 1
    else
        echo -e "${GREEN}✓ Docker network '${network}' available${NC}"
        return 0
    fi
}

# Function to check bridge interface
check_bridge() {
    local bridge=$1

    if ip link show "$bridge" &>/dev/null; then
        echo -e "${GREEN}✓ Bridge '${bridge}' exists${NC}"
        return 0
    else
        echo -e "${YELLOW}⚠ Bridge '${bridge}' not found${NC} (will be created)"
        WARNINGS=$((WARNINGS + 1))
        return 1
    fi
}

echo -e "${BLUE}[1] Checking Port Availability${NC}"
echo "───────────────────────────────────────────────────────────────"
check_port 80 "localhost-mdns proxy HTTP"
check_port 443 "localhost-mdns proxy HTTPS"
check_port 8080 "localhost-mdns frontend (optional)"
check_port 8100 "localhost-mdns backend (optional)"
check_port 8888 "isle-agent-mdns API"
echo ""

echo -e "${BLUE}[2] Checking Network Interfaces${NC}"
echo "───────────────────────────────────────────────────────────────"
check_bridge "isle-br-0"
echo ""

echo -e "${BLUE}[3] Checking Docker Networks${NC}"
echo "───────────────────────────────────────────────────────────────"
# Note: These networks might exist and that's OK
if docker network ls --format "{{.Name}}" 2>/dev/null | grep -q "^meshnet$"; then
    echo -e "${GREEN}✓ Docker network 'meshnet' exists${NC} (localhost-mdns)"
else
    echo -e "${BLUE}ℹ Docker network 'meshnet' will be created${NC}"
fi

if docker network ls --format "{{.Name}}" 2>/dev/null | grep -q "^isle-mdns-net$"; then
    echo -e "${GREEN}✓ Docker network 'isle-mdns-net' exists${NC} (isle-agent-mdns)"
else
    echo -e "${BLUE}ℹ Docker network 'isle-mdns-net' will be created${NC}"
fi
echo ""

echo -e "${BLUE}[4] Checking Component Status${NC}"
echo "───────────────────────────────────────────────────────────────"

# Check OpenWRT Router
if command -v virsh &>/dev/null; then
    if virsh domstate openwrt-isle-router 2>/dev/null | grep -q "running"; then
        echo -e "${GREEN}✓ OpenWRT router is running${NC}"
    elif virsh dominfo openwrt-isle-router &>/dev/null; then
        echo -e "${YELLOW}⚠ OpenWRT router exists but is not running${NC}"
    else
        echo -e "${BLUE}ℹ OpenWRT router not created yet${NC}"
    fi
else
    echo -e "${YELLOW}⚠ virsh not found - cannot check OpenWRT router${NC}"
    WARNINGS=$((WARNINGS + 1))
fi

# Check localhost-mdns containers
if docker ps --format "{{.Names}}" 2>/dev/null | grep -q "mesh-proxy"; then
    echo -e "${GREEN}✓ localhost-mdns is running${NC}"
elif docker ps -a --format "{{.Names}}" 2>/dev/null | grep -q "mesh-proxy"; then
    echo -e "${YELLOW}⚠ localhost-mdns exists but is not running${NC}"
else
    echo -e "${BLUE}ℹ localhost-mdns not started yet${NC}"
fi

# Check isle-agent-mdns
if docker ps --format "{{.Names}}" 2>/dev/null | grep -q "isle-agent-mdns"; then
    echo -e "${GREEN}✓ isle-agent-mdns is running${NC}"
elif docker ps -a --format "{{.Names}}" 2>/dev/null | grep -q "isle-agent-mdns"; then
    echo -e "${YELLOW}⚠ isle-agent-mdns exists but is not running${NC}"
else
    echo -e "${BLUE}ℹ isle-agent-mdns not started yet${NC}"
fi
echo ""

echo -e "${BLUE}[5] Checking IP Range Conflicts${NC}"
echo "───────────────────────────────────────────────────────────────"
# Check for conflicting IP ranges
if ip addr show 2>/dev/null | grep -q "192.168.1."; then
    echo -e "${YELLOW}⚠ Host has 192.168.1.x interface${NC} (OpenWRT uses 192.168.1.1)"
    echo -e "${BLUE}  This is OK if it's the br-mgmt bridge${NC}"
else
    echo -e "${GREEN}✓ No 192.168.1.x conflicts${NC}"
fi

if ip addr show 2>/dev/null | grep -q "10\.10\.0\."; then
    echo -e "${YELLOW}⚠ Host has 10.10.0.x interface${NC} (OpenWRT VLAN uses 10.10.0.1)"
    echo -e "${BLUE}  This may be OK if it's part of the isle network${NC}"
else
    echo -e "${GREEN}✓ No 10.10.0.x conflicts${NC}"
fi
echo ""

echo -e "${BLUE}[6] Resource Check${NC}"
echo "───────────────────────────────────────────────────────────────"

# Check available memory
AVAILABLE_MEM=$(free -m | awk '/^Mem:/{print $7}')
REQUIRED_MEM=800

if [ "$AVAILABLE_MEM" -gt "$REQUIRED_MEM" ]; then
    echo -e "${GREEN}✓ Sufficient memory available${NC} (${AVAILABLE_MEM}MB available, ${REQUIRED_MEM}MB needed)"
else
    echo -e "${RED}✗ Low memory${NC} (${AVAILABLE_MEM}MB available, ${REQUIRED_MEM}MB recommended)"
    CONFLICTS=$((CONFLICTS + 1))
fi

# Check disk space
AVAILABLE_DISK=$(df -BM . | awk 'NR==2 {print $4}' | sed 's/M//')
REQUIRED_DISK=1000

if [ "$AVAILABLE_DISK" -gt "$REQUIRED_DISK" ]; then
    echo -e "${GREEN}✓ Sufficient disk space${NC} (${AVAILABLE_DISK}MB available)"
else
    echo -e "${RED}✗ Low disk space${NC} (${AVAILABLE_DISK}MB available, ${REQUIRED_DISK}MB recommended)"
    CONFLICTS=$((CONFLICTS + 1))
fi
echo ""

echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""

# Summary
if [ $CONFLICTS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo -e "${GREEN}✓ NO CONFLICTS OR WARNINGS${NC}"
    echo -e "${GREEN}All components can run together safely!${NC}"
    exit 0
elif [ $CONFLICTS -eq 0 ]; then
    echo -e "${YELLOW}⚠ ${WARNINGS} WARNING(S) FOUND${NC}"
    echo -e "${YELLOW}Components can likely run together, but check warnings above${NC}"
    exit 0
else
    echo -e "${RED}✗ ${CONFLICTS} CONFLICT(S) FOUND${NC}"
    echo -e "${RED}Please resolve conflicts before running all components${NC}"
    exit 1
fi
