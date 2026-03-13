#!/bin/bash
#
# Verify Health App Configuration
# Quick test to ensure the default health.local app is properly configured
#

set -e

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

REGISTRY_FILE="/etc/isle-mesh/agent/registry.json"
MDNS_LIST="/usr/local/etc/mesh-mdns-domains.list"

echo ""
echo "═══════════════════════════════════════════════════"
echo "  Isle Agent - Health App Verification"
echo "═══════════════════════════════════════════════════"
echo ""

# Check 1: Registry file exists
echo -n "Check 1: Registry file exists... "
if [[ -f "$REGISTRY_FILE" ]]; then
    echo -e "${GREEN}✓${NC}"
else
    echo -e "${RED}✗${NC}"
    echo "  Registry file not found: $REGISTRY_FILE"
    exit 1
fi

# Check 2: Health app exists in registry
echo -n "Check 2: Health app in registry... "
if command -v jq &>/dev/null; then
    if jq -e '.apps.health' "$REGISTRY_FILE" >/dev/null 2>&1; then
        echo -e "${GREEN}✓${NC}"

        # Show health app details
        echo ""
        echo "Health App Configuration:"
        jq -r '.apps.health | "  Name:        \(.name)\n  Domain:      \(.domain)\n  Target:      \(.target)\n  Port:        \(.port)\n  Protocol:    \(.protocol)\n  Description: \(.description)"' "$REGISTRY_FILE"
        echo ""
    else
        echo -e "${RED}✗${NC}"
        echo "  Health app not found in registry"
        exit 1
    fi
else
    echo -e "${YELLOW}⚠${NC}"
    echo "  jq not available, cannot verify"
fi

# Check 3: health.local in mDNS list
echo -n "Check 3: health.local in mDNS list... "
if [[ -f "$MDNS_LIST" ]]; then
    if grep -Fxq "health.local" "$MDNS_LIST"; then
        echo -e "${GREEN}✓${NC}"
    else
        echo -e "${YELLOW}⚠${NC}"
        echo "  health.local not yet in mDNS list (will be added by watcher)"
    fi
else
    echo -e "${YELLOW}⚠${NC}"
    echo "  mDNS list file not found: $MDNS_LIST"
fi

# Check 4: isle-host-agent service status
echo -n "Check 4: Host agent running... "
if systemctl is-active --quiet isle-host-agent 2>/dev/null; then
    echo -e "${GREEN}✓${NC}"

    # Check if watcher is working
    echo -n "Check 5: Watcher logs present... "
    if sudo journalctl -u isle-host-agent -n 5 --no-pager 2>/dev/null | grep -q "watcher"; then
        echo -e "${GREEN}✓${NC}"
    else
        echo -e "${YELLOW}⚠${NC}"
        echo "  No watcher activity in recent logs"
    fi
else
    echo -e "${RED}✗${NC}"
    echo "  Host agent is not running"
    echo "  Start with: sudo systemctl start isle-host-agent"
fi

# Check 6: Can resolve health.local via mDNS
echo ""
echo -n "Check 6: health.local DNS resolution... "
if command -v avahi-resolve &>/dev/null; then
    if timeout 3 avahi-resolve -n health.local &>/dev/null; then
        local_ip=$(avahi-resolve -n health.local 2>/dev/null | awk '{print $2}')
        echo -e "${GREEN}✓${NC}"
        echo "  Resolves to: $local_ip"
    else
        echo -e "${YELLOW}⚠${NC}"
        echo "  Cannot resolve health.local (may take a moment to propagate)"
    fi
else
    echo -e "${YELLOW}⚠${NC}"
    echo "  avahi-resolve not available"
fi

# Check 7: Can access health endpoint
echo ""
echo -n "Check 7: Health endpoint accessible... "
if curl -sf http://localhost/health >/dev/null 2>&1; then
    echo -e "${GREEN}✓${NC}"
    health_response=$(curl -s http://localhost/health)
    echo "  Response: $health_response"
elif curl -sf http://localhost:80/health >/dev/null 2>&1; then
    echo -e "${GREEN}✓${NC}"
    health_response=$(curl -s http://localhost:80/health)
    echo "  Response: $health_response"
else
    echo -e "${YELLOW}⚠${NC}"
    echo "  Health endpoint not responding (vlan agent may not be running)"
    echo "  Check: docker ps | grep isle-vlan-agent"
fi

echo ""
echo "═══════════════════════════════════════════════════"
echo "  Verification Complete"
echo "═══════════════════════════════════════════════════"
echo ""
echo "Summary:"
echo "  - Registry contains health app configuration"
echo "  - Health app will be auto-synced to mDNS by watcher"
echo "  - Access via: http://health.local/health (once propagated)"
echo "  - Direct access: http://localhost/health"
echo ""
