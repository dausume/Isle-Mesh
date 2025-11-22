#!/bin/bash
#
# Fix OpenWRT mDNS Configuration
# This script configures avahi-daemon on OpenWRT to advertise openwrt.local
#

set -e

ROUTER_IP="${1:-192.168.1.1}"

# Use dedicated Isle SSH key if it exists
ISLE_SSH_KEY="/etc/isle-mesh/router/ssh/isle_router_key"
if [[ -f "$ISLE_SSH_KEY" ]]; then
  SSH_CMD="sudo ssh -i $ISLE_SSH_KEY -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
  echo "Using dedicated Isle SSH key: $ISLE_SSH_KEY"
else
  SSH_CMD="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
  echo "Warning: Dedicated SSH key not found, using default SSH (will prompt for password)"
fi

echo "═══════════════════════════════════════════════════"
echo "  Fixing OpenWRT mDNS Configuration"
echo "═══════════════════════════════════════════════════"
echo ""
echo "Router IP: $ROUTER_IP"
echo ""
echo "This script will:"
echo "  1. Configure avahi-daemon on OpenWRT"
echo "  2. Set hostname to 'openwrt'"
echo "  3. Start avahi-daemon service"
echo "  4. Verify mDNS advertisement"
echo ""
read -p "Press Enter to continue..."

echo ""
echo "[1/4] Configuring avahi-daemon..."
$SSH_CMD root@$ROUTER_IP << 'ENDSSH'
# Configure avahi-daemon
cat > /etc/avahi/avahi-daemon.conf << 'EOF'
[server]
host-name=openwrt
domain-name=local
use-ipv4=yes
use-ipv6=no
allow-interfaces=br-lan,br-mgmt
deny-interfaces=eth1.10
ratelimit-interval-usec=1000000
ratelimit-burst=1000

[publish]
publish-addresses=yes
publish-hinfo=yes
publish-workstation=no
publish-domain=yes

[reflector]
enable-reflector=yes
reflect-ipv=no

[rlimits]
rlimit-core=0
rlimit-data=4194304
rlimit-fsize=0
rlimit-nofile=768
rlimit-stack=4194304
rlimit-nproc=3
EOF

echo "✓ Avahi config written"
ENDSSH

echo ""
echo "[2/4] Setting system hostname..."
$SSH_CMD root@$ROUTER_IP << 'ENDSSH'
# Set hostname
uci set system.@system[0].hostname='openwrt'
uci commit system
/etc/init.d/system reload
echo "✓ Hostname set to 'openwrt'"
ENDSSH

echo ""
echo "[3/4] Starting avahi-daemon..."
$SSH_CMD root@$ROUTER_IP << 'ENDSSH'
# Ensure dbus is running (avahi depends on it)
/etc/init.d/dbus enable
/etc/init.d/dbus start

# Enable and start avahi
/etc/init.d/avahi-daemon enable
/etc/init.d/avahi-daemon restart

# Wait a moment for avahi to initialize
sleep 3

# Check if avahi is running
if ps | grep -v grep | grep -q avahi-daemon; then
    echo "✓ Avahi daemon is running"
else
    echo "✗ Avahi daemon failed to start"
    exit 1
fi
ENDSSH

echo ""
echo "[4/4] Verifying mDNS advertisement..."
echo "Waiting 5 seconds for mDNS to propagate..."
sleep 5

echo ""
echo "Testing resolution from host:"
if avahi-resolve -n openwrt.local 2>/dev/null; then
    echo "✓ Successfully resolved openwrt.local from host"
elif getent hosts openwrt.local 2>/dev/null; then
    echo "✓ Successfully resolved openwrt.local from host (via getent)"
else
    echo "⚠ Cannot resolve openwrt.local from host yet"
    echo "  (This may take a few more seconds, or host avahi may not be running)"
fi

echo ""
echo "Testing resolution from isle-agent container:"
if docker exec isle-agent getent hosts openwrt.local 2>/dev/null; then
    echo "✓ Successfully resolved openwrt.local from agent"
else
    echo "⚠ Cannot resolve openwrt.local from agent yet"
    echo "  Waiting 10 more seconds..."
    sleep 10
    if docker exec isle-agent getent hosts openwrt.local 2>/dev/null; then
        echo "✓ Successfully resolved openwrt.local from agent (after delay)"
    else
        echo "✗ Still cannot resolve - avahi may need more time or configuration"
    fi
fi

echo ""
echo "═══════════════════════════════════════════════════"
echo "  Configuration Complete!"
echo "═══════════════════════════════════════════════════"
echo ""
echo "Next steps:"
echo "  1. Run: isle agent status"
echo "  2. Check if OpenWRT router is now detected"
echo ""
echo "If still not working, check:"
echo "  - Router logs: ssh root@$ROUTER_IP 'logread | grep avahi'"
echo "  - Agent logs: docker exec isle-agent avahi-browse -a -t"
echo ""
