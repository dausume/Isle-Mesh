#!/bin/sh
#
# Isle Remote Agent Entrypoint
# Container entrypoint for remote mode:
#   1. udhcpc to get DHCP from router via macvlan
#   2. dbus + avahi for mDNS (with settle period before publishing)
#   3. Registry watcher + nginx (same as core)
#

set -e

SETTLE_SECONDS="${SETTLE_SECONDS:-5}"

echo "=================================================="
echo "  Isle Remote Agent - Nginx Proxy (Remote Mode)"
echo "=================================================="
echo ""
echo "  Isle:      ${ISLE_NAME:-unknown}"
echo "  VLAN ID:   ${VLAN_ID:-unknown}"
echo "  Router IP: ${ROUTER_IP:-unknown}"
echo ""

# --- Step 0: Find the macvlan (isle-facing) interface ---
# Docker's interface ORDER is not stable across attached networks —
# eth0 can be the INTERNAL bridge (172.20.x), which is exactly how
# DHCP silently ran on the wrong interface while the macvlan kept
# docker-IPAM's static self-assignment (it collided with the core
# agent's .2). Match VIRTUAL_MAC when given; else the non-172.20 one.
MACVLAN_IF=""
if [ -n "${VIRTUAL_MAC:-}" ]; then
    for d in /sys/class/net/*; do
        n=$(basename "$d"); [ "$n" = "lo" ] && continue
        [ "$(cat "$d/address" 2>/dev/null)" = "$VIRTUAL_MAC" ] && { MACVLAN_IF="$n"; break; }
    done
fi
if [ -z "$MACVLAN_IF" ]; then
    for d in /sys/class/net/*; do
        n=$(basename "$d"); [ "$n" = "lo" ] && continue
        ip -4 addr show "$n" 2>/dev/null | grep -q "inet 172\.20\." && continue
        MACVLAN_IF="$n"; break
    done
fi
MACVLAN_IF="${MACVLAN_IF:-eth0}"
echo "  Macvlan interface: ${MACVLAN_IF}"
echo ""

# --- Step 1: Get DHCP lease via macvlan interface ---
echo "[1/6] Obtaining DHCP lease..."

# Drop docker-IPAM's static self-assignment FIRST: each docker host
# picks it independently on the shared subnet, so keeping it risks an
# ACTIVE IP CONFLICT (the router's DHCP is the only real arbiter).
ip addr flush dev "$MACVLAN_IF" 2>/dev/null || true

# udhcpc is built into Alpine's busybox. Send our hostname so the
# router's lease table self-describes (dns-reconcile on the core
# maps device → agent IP by it; without it the lease shows '*').
if udhcpc -i "$MACVLAN_IF" -n -q -t 10 -T 3 -x hostname:"$(hostname)" 2>&1; then
    VLAN_IP=$(ip -4 -o addr show dev "$MACVLAN_IF" 2>/dev/null | awk '{split($4,a,"/"); print a[1]; exit}') || true
    echo "  DHCP lease obtained: ${VLAN_IP}"
else
    echo "  ERROR: DHCP failed on ${MACVLAN_IF} — no isle lease."
    echo "  Refusing a static fallback (it collides across hosts)."
    VLAN_IP="unknown"
fi
echo ""

# --- Step 2: Start D-Bus (required by avahi) ---
echo "[2/6] Starting D-Bus daemon..."
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

# --- Step 3: Settle period — observe mDNS before publishing ---
echo "[3/6] Settle period: observing existing mDNS traffic (${SETTLE_SECONDS}s)..."

# Start avahi in browse-only mode first to see what's already on the network.
# This lets us know what names exist before we start advertising.
mkdir -p /etc/avahi
cat > /etc/avahi/avahi-daemon.conf <<AVAHI_CONF
[server]
host-name=$(hostname)
domain-name=local
use-ipv4=yes
use-ipv6=no
allow-interfaces=${MACVLAN_IF}
enable-dbus=yes

[publish]
publish-addresses=no
publish-hinfo=no
publish-workstation=no

[reflector]
enable-reflector=no

[rlimits]
AVAHI_CONF

avahi-daemon -D 2>&1 || echo "  WARNING: Avahi daemon failed to start"
sleep 1

# Browse existing services during settle period
OBSERVED_NAMES=""
if avahi-daemon --check 2>/dev/null && command -v avahi-browse >/dev/null 2>&1; then
    echo "  Listening for existing mDNS names..."
    OBSERVED_NAMES=$(timeout "$SETTLE_SECONDS" avahi-browse -a -t -p 2>/dev/null \
        | grep '^=' | cut -d';' -f4 | sort -u || echo "")

    if [ -n "$OBSERVED_NAMES" ]; then
        OBSERVED_COUNT=$(echo "$OBSERVED_NAMES" | wc -l)
        echo "  Found $OBSERVED_COUNT existing mDNS name(s):"
        echo "$OBSERVED_NAMES" | while read -r name; do
            echo "    - $name"
        done
    else
        echo "  No existing mDNS names detected"
        sleep "$SETTLE_SECONDS"
    fi
else
    echo "  avahi-browse not available, waiting ${SETTLE_SECONDS}s..."
    sleep "$SETTLE_SECONDS"
fi
echo ""

# --- Step 4: Enable mDNS publishing ---
echo "[4/6] Enabling mDNS publishing..."

# Kill avahi, reconfigure with publishing enabled, restart
avahi-daemon -k 2>/dev/null || true
sleep 1

cat > /etc/avahi/avahi-daemon.conf <<AVAHI_CONF
[server]
host-name=$(hostname)
domain-name=local
use-ipv4=yes
use-ipv6=no
allow-interfaces=${MACVLAN_IF}
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
    echo "  Avahi daemon is running (publishing enabled)"
else
    echo "  WARNING: Avahi daemon is not running"
fi
echo ""

# --- Step 5: Publish mDNS addresses from registry ---
echo "[5/6] Setting up mDNS publishing..."

# Publish domains from registry via avahi-publish-address in background
# This reads the registry and publishes .local domains pointing to our VLAN IP
publish_mdns_domains() {
    local registry="/etc/nginx/registry.json"
    local published_pids=""

    # Wait for first publish cycle to let the network settle further
    sleep 5

    while true; do
        if [ -f "$registry" ] && [ "$VLAN_IP" != "unknown" ]; then
            # Get current VLAN IP (may change on DHCP renewal)
            local current_ip
            current_ip=$(ip -4 -o addr show dev "$MACVLAN_IF" 2>/dev/null | awk '{split($4,a,"/"); print a[1]; exit}'); [ -n "$current_ip" ] || current_ip="$VLAN_IP"

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

# --- Step 6: Start registry watcher + nginx ---
echo "[6/6] Starting nginx and registry watcher..."
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
