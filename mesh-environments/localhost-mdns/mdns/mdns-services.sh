#!/bin/bash
set -e

echo "🔧 Creating /run/dbus..."
mkdir -p /run/dbus

# 🔍 Detect non-loopback interfaces (e.g., eth0, docker0, etc.)
#EXTRA_INTERFACES=$(ip -o link show | awk -F': ' '{print $2}' | grep -v '^lo$' | paste -sd "," -)
# Get all veth and br- interfaces and include loopback
#MATCHED_IFACES=$(ip -o link show | awk -F': ' '$2 ~ /^veth/ || $2 ~ /^br-/ || $2 == "lo" { print $2 }' | paste -sd, -)
# NOTE : To debug if there are unkown interfaces, on your local computer terminal use :   sudo tcpdump -i any -n port 5353

# Get all current interfaces
ALL_INTERFACES=$(ip -o link show | awk -F': ' '{print $2}')

# Strip @ifN suffixes and deduplicate
CLEANED_INTERFACES=$(echo "$ALL_INTERFACES" | sed 's/@.*//' | sort -u)

#echo "extra interfaces : $EXTRA_INTERFACES"

#echo "matched interfaces : $MATCHED_IFACES"

# 🛠️ Path to the Avahi config file
AVAHI_CONF="/etc/avahi/avahi-daemon.conf"

# 📝 Backup for safety
cp "$AVAHI_CONF" "$AVAHI_CONF.bak"

# 🔁 Update or insert allow-interfaces line
# Remove any interfaces with @if
#if grep -q "^allow-interfaces=" "$AVAHI_CONF"; then
#  sed -i "s|^allow-interfaces=.*|allow-interfaces=lo,$MATCHED_IFACES|" "$AVAHI_CONF"
#else
#  # Add under [server] section
#  sed -i "/^\[server\]/a allow-interfaces=lo,$EXTRA_INTERFACES" "$AVAHI_CONF"
#fi

# 🔁 Update or insert deny-interfaces line
# We should dis-allow unsafe interfaces from docker with @if, but we should later include macvlan
# interfaces on docker so we can enable different docker-compose structures accessing one another
# via sandboxed virtual networks.
#if grep -q "^deny-interfaces=" "$AVAHI_CONF"; then
#  sed -i "s|^deny-interfaces=.*|deny-interfaces=lo,$MATCHED_IFACES|" "$AVAHI_CONF"
#else
#  # Add under [server] section
#  sed -i "/^\[server\]/a allow-interfaces=lo,$EXTRA_INTERFACES" "$AVAHI_CONF"
#fi

# Define safe and blocked filters
SAFE_PATTERN='^(lo|wlp|enp|eth)[0-9]*$'
BLOCKED_PATTERN='^(docker|veth|br)[0-9a-z\-]*$'

# Filter interfaces
ALLOW_IFACES=""
DENY_IFACES=""

while read -r iface; do
  if [[ "$iface" =~ $SAFE_PATTERN ]]; then
    ALLOW_IFACES+="${iface},"
  elif [[ "$iface" =~ $BLOCKED_PATTERN ]]; then
    DENY_IFACES+="${iface},"
  fi
done <<< "$CLEANED_INTERFACES"

# Trim trailing commas
ALLOW_IFACES="${ALLOW_IFACES%,}"
DENY_IFACES="${DENY_IFACES%,}"

echo "✅ Allow interfaces: $ALLOW_IFACES"
echo "🚫 Deny interfaces:  $DENY_IFACES"

# Remove existing lines
sudo sed -i '/^allow-interfaces=/d' "$AVAHI_CONF"
sudo sed -i '/^deny-interfaces=/d' "$AVAHI_CONF"

# Inject under [server]
sudo sed -i "/^\[server\]/a allow-interfaces=$ALLOW_IFACES" "$AVAHI_CONF"
sudo sed -i "/^\[server\]/a deny-interfaces=$DENY_IFACES" "$AVAHI_CONF"

# Print the new state of the file to check it worked.
echo "📝 New Avahi Config:"
grep "^allow-interfaces=" "$AVAHI_CONF"

# Restart Avahi cleanly
echo "🔄 Restarting avahi-daemon..."
sudo systemctl stop avahi-daemon.socket avahi-daemon || true
# The following lines are dangerous, can crash computer.
#sudo pkill -f avahi-daemon || true
#sudo rm -rf /var/run/avahi-daemon/pid
#sudo systemctl daemon-reexec
sudo systemctl daemon-reload
sudo systemctl start avahi-daemon

echo "🔧 Starting dbus-daemon..."
# Start dbus-daemon in the background and capture PID
#dbus-daemon --system &
#DBUS_PID=$!
#sleep 1

#echo "🚀 Starting avahi-daemon..."
# Start avahi-daemon in the background and capture PID
#avahi-daemon --no-chroot --no-drop-root --debug &
#AVAHI_PID=$!
#sleep 2

echo "✅ mDNS services published"

# Wait on avahi-daemon (don't let container exit)
#wait $AVAHI_PID


# Keep container alive
#tail -f /dev/null