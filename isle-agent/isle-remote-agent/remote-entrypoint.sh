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

# Canonical registered identity survives OS hostname changes and container upgrades.
ISLE_HOSTNAME=$(cat /etc/isle-mesh/device-name)
printf '%s' "$ISLE_HOSTNAME" | grep -Eq '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$' || { echo 'Invalid canonical device identity'; exit 1; }
MACVLAN_IF=""
[ -n "${VIRTUAL_MAC:-}" ] || { echo 'Missing registered virtual MAC; refusing to guess interface'; exit 1; }
for d in /sys/class/net/*; do
    [ "$(cat "$d/address")" = "$VIRTUAL_MAC" ] && MACVLAN_IF=${d##*/}
done
[ -n "$MACVLAN_IF" ] || { echo 'Registered MAC interface not found'; exit 1; }
# Foreground DHCP remains alive for renew/rebind; no quit-after-lease flag.
rm -f /run/isle-lease
udhcpc -f -n -i "$MACVLAN_IF" -t 10 -T 3 -s /usr/local/bin/lease-hook.sh -x hostname:"$ISLE_HOSTNAME" &
DHCP_PID=$!
echo "$DHCP_PID" > /run/isle-dhcp.pid
tries=0
until [ -s /run/isle-lease ]; do
    kill -0 "$DHCP_PID" 2>/dev/null || { echo 'DHCP acquisition failed'; exit 1; }
    tries=$((tries+1)); [ "$tries" -lt 40 ] || { kill "$DHCP_PID"; exit 1; }
    sleep 1
done
read -r _ VLAN_IP _ < /run/isle-lease

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
host-name=$ISLE_HOSTNAME
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
host-name=$ISLE_HOSTNAME
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
    registry=/etc/nginx/registry.json
    published_pids=""; previous=""
    trap 'for p in $published_pids; do kill "$p" 2>/dev/null || true; done; exit 0' TERM INT
    while true; do
        current_ip=""; domains=""
        if [ -s /run/isle-lease ] && [ -f "$registry" ]; then
            read -r lease_if lease_ip expires < /run/isle-lease
            if [ "$expires" -gt "$(date +%s)" ]; then
                current_ip=$(ip -4 -o addr show dev "$MACVLAN_IF" | awk '{split($4,a,"/"); print a[1]; exit}')
                domains=$(jq -r '.apps[].domain // empty' "$registry" 2>/dev/null | grep '\.local$' | sort -u || true)
            fi
        fi
        desired="$current_ip $domains"
        if [ "$desired" != "$previous" ]; then
            for p in $published_pids; do kill "$p" 2>/dev/null || true; done
            published_pids=""; previous="$desired"
            if [ -n "$current_ip" ]; then
                for domain in $domains; do
                    avahi-publish-address "$domain" "$current_ip" &
                    published_pids="$published_pids $!"
                done
            fi
        fi
        sleep 5
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
    kill "$DHCP_PID" 2>/dev/null || true
    kill "${NGINX_PID:-}" 2>/dev/null || true
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
nginx -g "daemon off;" &
NGINX_PID=$!
# A dead DHCP client must restart acquisition rather than leave nginx green.
while kill -0 "$NGINX_PID" 2>/dev/null && kill -0 "$DHCP_PID" 2>/dev/null; do sleep 2; done
kill "$NGINX_PID" "$DHCP_PID" 2>/dev/null || true
exit 1
