#!/bin/sh
#
# Isle Remote Agent Entrypoint
# Container entrypoint for remote mode:
#   1. udhcpc to get DHCP from router via macvlan
#   2. dbus + avahi for mDNS broadcasting
#   3. Registry watcher + nginx (same as core)
#

set -e

echo "=================================================="
echo "  Isle Remote Agent - Nginx Proxy (Remote Mode)"
echo "=================================================="
echo ""
echo "  Isle:      ${ISLE_NAME:-unknown}"
echo "  VLAN ID:   ${VLAN_ID:-unknown}"
echo "  Router IP: ${ROUTER_IP:-unknown}"
echo ""

# --- Step 1: Get DHCP lease via macvlan interface ---
echo "[1/5] Obtaining DHCP lease..."

# udhcpc is built into Alpine's busybox
# eth0 should be the macvlan interface connected to the VLAN
if udhcpc -i eth0 -n -q -t 10 -T 3 2>&1; then
    VLAN_IP=$(ip -4 addr show eth0 | grep -oP '(?<=inet\s)\d+(\.\d+){3}')
    echo "  DHCP lease obtained: ${VLAN_IP}"
else
    echo "  WARNING: DHCP failed on eth0, continuing anyway..."
    echo "  The container may not have VLAN connectivity."
    VLAN_IP="unknown"
fi
echo ""

# --- Step 2: Start D-Bus (required by avahi) ---
echo "[2/5] Starting D-Bus daemon..."
mkdir -p /run/dbus
if [ -f /run/dbus/pid ]; then
    rm -f /run/dbus/pid
fi
dbus-daemon --system --nofork --nopidfile &
DBUS_PID=$!
sleep 1

if kill -0 $DBUS_PID 2>/dev/null; then
    echo "  D-Bus daemon running (PID: $DBUS_PID)"
else
    echo "  WARNING: D-Bus failed to start, mDNS will not work"
fi
echo ""

# --- Step 3: Start Avahi for mDNS ---
echo "[3/5] Starting Avahi daemon..."

# Configure avahi
mkdir -p /etc/avahi
cat > /etc/avahi/avahi-daemon.conf <<AVAHI_CONF
[server]
host-name=$(hostname)
domain-name=local
use-ipv4=yes
use-ipv6=no
allow-interfaces=eth0
enable-dbus=yes

[publish]
publish-addresses=yes
publish-hinfo=no
publish-workstation=no

[reflector]
enable-reflector=no

[rlimits]
AVAHI_CONF

avahi-daemon -D 2>&1 || echo "  WARNING: Avahi daemon failed to start"
sleep 1

if avahi-daemon --check 2>/dev/null; then
    echo "  Avahi daemon is running"
else
    echo "  WARNING: Avahi daemon is not running"
fi
echo ""

# --- Step 4: Publish mDNS addresses from registry ---
echo "[4/5] Setting up mDNS publishing..."

# Publish domains from registry via avahi-publish-address in background
# This reads the registry and publishes .local domains pointing to our VLAN IP
publish_mdns_domains() {
    local registry="/etc/nginx/registry.json"
    local published_pids=""

    while true; do
        if [ -f "$registry" ] && [ "$VLAN_IP" != "unknown" ]; then
            # Get current VLAN IP (may change on DHCP renewal)
            local current_ip
            current_ip=$(ip -4 addr show eth0 | grep -oP '(?<=inet\s)\d+(\.\d+){3}' || echo "$VLAN_IP")

            # Extract .local domains from registry
            local domains
            domains=$(jq -r '.apps[].domain // empty' "$registry" 2>/dev/null | grep '\.local$' || true)

            for domain in $domains; do
                # Check if already being published
                if ! echo "$published_pids" | grep -q "$domain"; then
                    echo "[mdns-publish] Publishing ${domain} -> ${current_ip}"
                    avahi-publish-address "$domain" "$current_ip" &
                    published_pids="$published_pids $domain:$!"
                fi
            done
        fi
        sleep 30
    done
}

publish_mdns_domains &
MDNS_PID=$!
echo "  mDNS publisher running (PID: $MDNS_PID)"
echo ""

# --- Step 5: Start registry watcher + nginx ---
echo "[5/5] Starting nginx and registry watcher..."
echo ""

# Copy initial nginx.conf if needed
if [ ! -f /etc/nginx/nginx.conf ]; then
    if [ -f /etc/nginx/nginx-initial.conf ]; then
        cp /etc/nginx/nginx-initial.conf /etc/nginx/nginx.conf
        echo "  Initial nginx config copied"
    else
        echo "  WARNING: No initial nginx config found"
    fi
fi

# Ensure configs directory
mkdir -p /etc/nginx/configs

# Start registry watcher in background
/usr/local/bin/registry-watcher.sh &
WATCHER_PID=$!
echo "  Registry watcher PID: $WATCHER_PID"

# Cleanup handler
cleanup() {
    echo "Shutting down remote agent..."
    kill $WATCHER_PID 2>/dev/null || true
    kill $MDNS_PID 2>/dev/null || true
    avahi-daemon -k 2>/dev/null || true
    kill $DBUS_PID 2>/dev/null || true
    exit 0
}
trap cleanup SIGTERM SIGINT

# Start nginx in foreground
echo "  Starting nginx..."
echo ""
exec nginx -g "daemon off;"
