#!/bin/bash
# install-mesh-mdns.sh
set -e

echo "🚀 Starting full IsleMesh mDNS setup..."
# Directory where IsleMesh open source project code is located and can be used for setup.
ISLEMESH_SOURCE_DIR=$(sudo find / -type d -iname '*islemesh*' 2>/dev/null | head -n 1)
# Directory where setup scripts are located in the IsleMesh project code.
SCRIPTS_SOURCE_DIR="$ISLEMESH_SOURCE_DIR/mdns/scripts"
echo "ISLEMESH_SOURCE_DIR=$ISLEMESH_SOURCE_DIR"

# Specify the file path to the custom environment configuration file for defining the mesh network settings.
CUSTOM_ENV_FILE="$1"

if [[ ! -f "$CUSTOM_ENV_FILE" ]]; then
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

# === Configure systemd service ===
TARGET_SERVICE_PATH="/etc/systemd/system/mesh-mdns.service"
TARGET_ENV_PATH="/etc/mesh-mdns.conf"

echo "📁 Copying env file to $TARGET_ENV_PATH..."
sudo cp "$CUSTOM_ENV_FILE" "$TARGET_ENV_PATH"
sudo chmod 644 "$TARGET_ENV_PATH"

echo "📡 Writing systemd service to $TARGET_SERVICE_PATH..."
sudo cp "./mesh-mdns.service" $TARGET_SERVICE_PATH

echo "🔄 Reloading systemd and enabling mesh-mdns.service..."
sudo systemctl daemon-reexec
sudo systemctl enable --now mesh-mdns.service

echo "🔍 Testing DNS resolution of mesh-app.local via dig..."
dig +short mesh-app.local || echo "⚠️ DNS may not be routing as expected"

echo "✅ Setup complete. Scripts installed to $TARGET_SCRIPTS_DIR, env file is at $TARGET_ENV_PATH, and mDNS broadcasting is active."
