#!/bin/bash
# install-mesh-mdns.sh
set -e

# Parse arguments
ISOLATED_MODE=false
CUSTOM_ENV_FILE=""
ISLEMESH_DIR="${ISLEMESH_DIR:-/etc/isle-mesh}"

for arg in "$@"; do
    case $arg in
        -i|--isolated)
            ISOLATED_MODE=true
            shift
            ;;
        *)
            if [ -z "$CUSTOM_ENV_FILE" ]; then
                CUSTOM_ENV_FILE="$arg"
            elif [ -z "$ISLEMESH_DIR" ] || [ "$ISLEMESH_DIR" = "/etc/isle-mesh" ]; then
                ISLEMESH_DIR="$arg"
            fi
            shift
            ;;
    esac
done

if [ "$ISOLATED_MODE" = true ]; then
    echo "🚀 Starting localhost-mdns setup (ISOLATED MODE - no router integration)..."
else
    echo "🚀 Starting full IsleMesh mDNS setup (with router integration)..."
fi

# Directory where setup scripts are located in the IsleMesh project code.
SCRIPTS_SOURCE_DIR="$ISLEMESH_DIR/mdns/scripts"
echo "ISLEMESH_DIR=$ISLEMESH_DIR"

if [ ! -f "$CUSTOM_ENV_FILE" ]; then
  echo "❌ Provided env file does not exist: $CUSTOM_ENV_FILE"
  exit 1
fi

echo "📄 Using env file: $CUSTOM_ENV_FILE"

# === Create target directory ===
TARGET_SCRIPTS_DIR="/usr/local/bin/isle-mesh"
echo "📁 Creating script directory at $TARGET_SCRIPTS_DIR..."
sudo mkdir -p "$TARGET_SCRIPTS_DIR"

# === Copy setup scripts ===
echo "📦 Copying setup scripts to $TARGET_SCRIPTS_DIR..."
sudo cp $SCRIPTS_SOURCE_DIR/setup-wifi-access.sh "$TARGET_SCRIPTS_DIR/"
sudo cp $SCRIPTS_SOURCE_DIR/switch-to-systemd-networkd.sh "$TARGET_SCRIPTS_DIR/"
sudo cp $SCRIPTS_SOURCE_DIR/split-dns-on-host.sh "$TARGET_SCRIPTS_DIR/"
sudo cp $SCRIPTS_SOURCE_DIR/mesh-mdns-broadcast.sh "$TARGET_SCRIPTS_DIR/"
sudo cp $SCRIPTS_SOURCE_DIR/mesh-mdns-domains-add.sh "$TARGET_SCRIPTS_DIR/"
sudo cp $SCRIPTS_SOURCE_DIR/mesh-mdns-domains-remove.sh "$TARGET_SCRIPTS_DIR/"
sudo cp $SCRIPTS_SOURCE_DIR/mesh-mdns-domains-list.sh "$TARGET_SCRIPTS_DIR/"
sudo cp $SCRIPTS_SOURCE_DIR/mesh-mdns-domains-detect.sh "$TARGET_SCRIPTS_DIR/"
# Make scripts executable for everyone, since they will be removed when we are done.
sudo chmod a+x "$TARGET_SCRIPTS_DIR/"*.sh

# === Run Setup Steps ===

# Prepare Wi-Fi access for ISP, BEFORE switching to systemd-networkd so we do not lose connectivity
# during the install process.
echo "📶 Configuring Wi-Fi..."
sudo "$TARGET_SCRIPTS_DIR/setup-wifi-access.sh"

# Switch to systemd-networkd and systemd-resolved
echo "🔧 Enabling systemd-networkd + resolved..."
sudo "$TARGET_SCRIPTS_DIR/switch-to-systemd-networkd.sh"

echo "🌐 Setting up split-DNS routing..."
sudo "$TARGET_SCRIPTS_DIR/split-dns-on-host.sh"

# === Install and configure avahi-daemon ===
echo "📡 Installing avahi-daemon for mDNS broadcasting..."
if [ -f /.dockerenv ]; then
  # Running in Docker - use nsenter to run apt on the host
  nsenter --target 1 --mount --uts --ipc --net --pid -- apt-get update -qq
  nsenter --target 1 --mount --uts --ipc --net --pid -- apt-get install -y avahi-daemon avahi-utils
  nsenter --target 1 --mount --uts --ipc --net --pid -- systemctl enable avahi-daemon
  nsenter --target 1 --mount --uts --ipc --net --pid -- systemctl start avahi-daemon
else
  # Running directly on host
  sudo apt-get update -qq
  sudo apt-get install -y avahi-daemon avahi-utils
  sudo systemctl enable avahi-daemon
  sudo systemctl start avahi-daemon
fi

# === Configure systemd service ===
TARGET_SERVICE_PATH="/etc/systemd/system/mesh-mdns.service"
TARGET_ENV_PATH="/usr/local/etc/mesh-mdns.conf"

echo "📁 Copying env file to $TARGET_ENV_PATH..."
sudo mkdir -p /usr/local/etc
sudo cp "$CUSTOM_ENV_FILE" "$TARGET_ENV_PATH"
sudo chmod 644 "$TARGET_ENV_PATH"

echo "📡 Writing systemd service to $TARGET_SERVICE_PATH..."
sudo cp "$ISLEMESH_DIR/mdns/mesh-mdns.service" "$TARGET_SERVICE_PATH"

echo "🔄 Reloading systemd and enabling mesh-mdns.service..."
# Use nsenter to run systemctl on the host system (needed when running from container)
if [ -f /.dockerenv ]; then
  # Running in Docker - use nsenter to access host's systemd
  nsenter --target 1 --mount --uts --ipc --net --pid -- systemctl daemon-reexec
  nsenter --target 1 --mount --uts --ipc --net --pid -- systemctl enable --now mesh-mdns.service
else
  # Running directly on host
  sudo systemctl daemon-reexec
  sudo systemctl enable --now mesh-mdns.service
fi

echo "🔍 Testing DNS resolution of mesh-app.local via dig..."
dig +short mesh-app.local || echo "⚠️ DNS may not be routing as expected"

echo "✅ Setup complete."
echo "   📁 Scripts installed to: $TARGET_SCRIPTS_DIR"
echo "   📄 Config file at: $TARGET_ENV_PATH"
echo "   🔧 Service file at: $TARGET_SERVICE_PATH"
echo "   📡 mDNS broadcasting is active."
echo ""
echo "⚙️  Next steps:"
echo "   Configure domains to broadcast using one of:"
echo "   - isle mdns detect-domains (auto-detect from isle-mesh.yml)"
echo "   - isle mdns add-domain <domain> (manual)"
echo "   - isle mdns list-domains (view configured domains)"
echo ""
echo "   Note: Legacy SUBDOMAINS env var is still supported as fallback"
