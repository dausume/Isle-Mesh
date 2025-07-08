#!/bin/bash
# start-avahi-on-host.sh
set -e

REQUIRED_PACKAGES=(
  avahi-daemon
  avahi-utils
  libnss-mdns
  dbus
  iproute2
  avahi-autoipd
)

MISSING_PACKAGES=()

#echo "🔍 Checking for missing packages..."
#for pkg in "${REQUIRED_PACKAGES[@]}"; do
#  if ! dpkg -s "$pkg" >/dev/null 2>&1; then
#    echo "❌ Missing: $pkg"
#    MISSING_PACKAGES+=("$pkg")
#  else
#    echo "✅ Installed: $pkg"
#  fi
#done

#if [ ${#MISSING_PACKAGES[@]} -gt 0 ]; then
#  echo "📦 Installing missing packages: ${MISSING_PACKAGES[*]}"
#  sudo apt-get update
#  sudo apt-get install -y "${MISSING_PACKAGES[@]}"
#  sudo apt-get clean
#  sudo rm -rf /var/lib/apt/lists/*
#else
#  echo "🎉 All required packages are already installed."
#fi

# ---- Ensure all needed dependencies are installed ----
#sudo apt-get update && \
#sudo apt-get install -y \
#  avahi-daemon \
#  avahi-utils \
#  libnss-mdns \
#  dbus \
#  iproute2 \
#  avahi-autoipd \
#  && sudo apt-get clean && \
#  sudo rm -rf /var/lib/apt/lists/*

# ---- Handle D-Bus Setup ----
echo "🔧 Ensuring /run/dbus exists..."
sudo mkdir -p /run/dbus

# ---- Handle Services Setup ----
SOURCE_SERVICE_DIR="./avahi-urls"
TARGET_SERVICE_DIR="/etc/avahi/services"

echo "📂 Syncing Avahi service files from $SOURCE_SERVICE_DIR to $TARGET_SERVICE_DIR"
sudo mkdir -p "$TARGET_SERVICE_DIR"

if compgen -G "$SOURCE_SERVICE_DIR/*.service" > /dev/null; then
  ls -lh "$SOURCE_SERVICE_DIR"/*.service
else
  echo "❌ No .service files found in $SOURCE_SERVICE_DIR"
  exit 1
fi

for file in "$SOURCE_SERVICE_DIR"/*.service; do
  echo "🔄 Copying: $file → $TARGET_SERVICE_DIR"
  sudo cp "$file" "$TARGET_SERVICE_DIR/"
done

sudo chown root:root "$TARGET_SERVICE_DIR"/*.service
sudo chmod 644 "$TARGET_SERVICE_DIR"/*.service

echo "📋 Final service files in $TARGET_SERVICE_DIR:"
ls -lh "$TARGET_SERVICE_DIR"/*.service

# ---- Handle Interface Auto-Detection ----
MATCHED_IFACES=$(ip -o link show | awk -F': ' '$2 ~ /^veth/ || $2 ~ /^br-/ || $2 == "lo" || $2 ~ /^en/ || $2 ~ /^wl/ { print $2 }' | paste -sd, -)
echo "🕵️ Matched Interfaces for Avahi: $MATCHED_IFACES"

# ---- Avahi Config Management ----
SOURCE_AVAHI_CONF="./avahi-daemon.conf"
SYSTEM_AVAHI_CONF="/etc/avahi/avahi-daemon.conf"

if [ ! -f "$SOURCE_AVAHI_CONF" ]; then
  echo "❌ Missing template config: $SOURCE_AVAHI_CONF"
  exit 1
fi

# Backup system config
if [ ! -f "$SYSTEM_AVAHI_CONF.bak" ]; then
  sudo cp "$SYSTEM_AVAHI_CONF" "$SYSTEM_AVAHI_CONF.bak"
  echo "📦 Backed up system config to $SYSTEM_AVAHI_CONF.bak"
fi

# Patch allow-interfaces in system config
if grep -q "^allow-interfaces=" "$SYSTEM_AVAHI_CONF"; then
  sudo sed -i "s|^allow-interfaces=.*|allow-interfaces=$MATCHED_IFACES|" "$SYSTEM_AVAHI_CONF"
else
  sudo sed -i "/^\[server\]/a allow-interfaces=$MATCHED_IFACES" "$SYSTEM_AVAHI_CONF"
fi

echo "📝 Updated system Avahi config with interfaces:"
grep "^allow-interfaces=" "$SYSTEM_AVAHI_CONF"

# Ensure required entries exist in the hosts: line of /etc/nsswitch.conf

set -e

FILE="/etc/nsswitch.conf"
LINE_PREFIX="hosts:"
REQUIRED_ENTRIES=("files" "mdns4_minimal" "[NOTFOUND=return]" "dns" "mdns4")

echo "🔍 Checking and updating $FILE..."

# Read the current line
CURRENT_LINE=$(grep "^$LINE_PREFIX" "$FILE")

if [ -z "$CURRENT_LINE" ]; then
  echo "❌ No 'hosts:' line found in $FILE."
  exit 1
fi

# Split line into words
read -ra CURRENT_ENTRIES <<< "${CURRENT_LINE#$LINE_PREFIX}"

# Convert to associative array for fast lookup
declare -A EXISTING=()
for word in "${CURRENT_ENTRIES[@]}"; do
  EXISTING["$word"]=1
done

# Add missing required entries
for required in "${REQUIRED_ENTRIES[@]}"; do
  if [[ -z "${EXISTING[$required]}" ]]; then
    echo "➕ Adding missing entry: $required"
    CURRENT_ENTRIES+=("$required")
  fi
done

# Create new line
NEW_LINE="$LINE_PREFIX ${CURRENT_ENTRIES[*]}"

# Replace the line in-place
sudo sed -i "s/^$LINE_PREFIX.*/$NEW_LINE/" "$FILE"

echo "✅ Final hosts line:"
grep "^$LINE_PREFIX" "$FILE"

# (Optional) Overwrite system config with your local template
# echo "📄 Overwriting system Avahi config with local template..."
# sudo cp "$SOURCE_AVAHI_CONF" "$SYSTEM_AVAHI_CONF"

# --- Firewall Configuration ----


# --- Fix Resolver Configuration ----

# Go through each interface on resolvectl and ensure mdns is enabled
sudo resolvectl mdns wlp2s0 yes
sudo resolvectl mdns lo yes
sudo resolvectl mdns docker0 yes
sudo resolvectl mdns br-7079115288f1 yes


# ---- Restart Avahi ----
echo "♻️ Restarting avahi-daemon..."
sudo systemctl restart avahi-daemon

echo "🔎 Published Services:"
avahi-browse -a -t