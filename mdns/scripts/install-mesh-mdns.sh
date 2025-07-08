#!/bin/bash
# install-mesh-mdns.sh
set -e

echo "🚀 Starting full IsleMesh mDNS setup..."

CUSTOM_ENV_FILE="$1"

if [[ ! -f "$CUSTOM_ENV_FILE" ]]; then
  echo "❌ Provided env file does not exist: $CUSTOM_ENV_FILE"
  exit 1
fi

echo "📄 Using env file: $CUSTOM_ENV_FILE"

# === Create target directory ===
TARGET_DIR="/usr/local/bin/isle-mesh"
echo "📁 Creating script directory at $TARGET_DIR..."
sudo mkdir -p "$TARGET_DIR"

# === Copy setup scripts ===
echo "📦 Copying setup scripts to $TARGET_DIR..."
sudo cp ./setup-wifi-access.sh "$TARGET_DIR/"
sudo cp ./setup-networkd.sh "$TARGET_DIR/"
sudo cp ./split-dns-on-host.sh "$TARGET_DIR/"
sudo cp ./mesh-mdns-broadcast.sh "$TARGET_DIR/"
sudo chmod +x "$TARGET_DIR/"*.sh

# === Run Setup Steps ===
echo "📶 Configuring Wi-Fi..."
sudo "$TARGET_DIR/setup-wifi-access.sh"

echo "🔧 Enabling systemd-networkd + resolved..."
sudo "$TARGET_DIR/setup-networkd.sh"

echo "🌐 Setting up split-DNS routing..."
sudo "$TARGET_DIR/split-dns-on-host.sh"

# === Configure systemd service ===
SERVICE_PATH="/etc/systemd/system/mesh-mdns.service"
FINAL_ENV_PATH="/etc/mesh-mdns.conf"

echo "📁 Copying env file to $FINAL_ENV_PATH..."
sudo cp "$CUSTOM_ENV_FILE" "$FINAL_ENV_PATH"
sudo chmod 644 "$FINAL_ENV_PATH"

echo "📡 Writing systemd service to $SERVICE_PATH..."
sudo cp "./mesh-mdns.service" $SERVICE_PATH

echo "🔄 Reloading systemd and enabling mesh-mdns.service..."
sudo systemctl daemon-reexec
sudo systemctl enable --now mesh-mdns.service

echo "🔍 Testing DNS resolution of mesh-app.local via dig..."
dig +short mesh-app.local || echo "⚠️ DNS may not be routing as expected"

echo "✅ Setup complete. Scripts installed to $TARGET_DIR, env file is at $FINAL_ENV_PATH, and mDNS broadcasting is active."
