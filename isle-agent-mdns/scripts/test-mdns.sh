#!/bin/bash
# Test mDNS service discovery from within the container

set -e

CONTAINER_NAME="${CONTAINER_NAME:-isle-agent-mdns}"

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Error: Container ${CONTAINER_NAME} is not running"
    exit 1
fi

echo "=========================================="
echo "Isle Agent mDNS Service Discovery Test"
echo "=========================================="
echo ""

echo "1. Checking Avahi daemon status..."
if docker exec "$CONTAINER_NAME" pgrep avahi-daemon >/dev/null 2>&1; then
    echo "   ✓ Avahi daemon is running"
else
    echo "   ✗ Avahi daemon is NOT running"
    exit 1
fi
echo ""

echo "2. Listing registered services..."
docker exec "$CONTAINER_NAME" ls -la /etc/avahi/services/ 2>/dev/null || echo "   No services registered yet"
echo ""

echo "3. Browsing all advertised services..."
echo "   (This will show services being broadcast over mDNS)"
docker exec "$CONTAINER_NAME" timeout 5 avahi-browse -a -t || true
echo ""

echo "4. Checking network interface..."
echo "   (mDNS should broadcast on the macvlan interface)"
docker exec "$CONTAINER_NAME" ip addr show
echo ""

echo "=========================================="
echo "Test complete!"
echo "=========================================="
echo ""
echo "To test from another device on the vLAN:"
echo "  avahi-browse -a"
echo "  # or"
echo "  dns-sd -B _https._tcp"
