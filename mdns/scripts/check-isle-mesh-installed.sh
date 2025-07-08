#!/bin/bash
set -e

INSTALL_FLAG="/etc/isle-mesh/.installed"

if [[ -f "$INSTALL_FLAG" ]]; then
  echo "✅ IsleMesh mDNS already installed. Skipping setup."
  exit 0
fi

echo "🔧 Starting full install..."
./install-mesh-mdns.sh ./mesh-mdns.env

# Create install marker
mkdir -p /etc/isle-mesh
touch "$INSTALL_FLAG"

echo "✅ Installation complete. Marked at $INSTALL_FLAG."