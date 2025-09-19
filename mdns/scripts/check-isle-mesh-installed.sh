#!/bin/bash
set -e

ISLEMESH_DIR=$(sudo find / -type d -iname '*islemesh*' 2>/dev/null | head -n 1)
echo "ISLEMESH_DIR=$ISLEMESH_DIR"
CUSTOM_ENV_FILE="$1"
# Used to inidicate if IsleMesh mDNS has had an initial install attempt run.
INSTALL_FLAG="/etc/isle-mesh/.installed_started"
# Used to indicate if IsleMesh mDNS install completed successfully.
INSTALL_COMPLETE_FLAG="/etc/isle-mesh/.install_complete"


if [[ -f "$INSTALL_COMPLETE_FLAG" ]]; then
  echo "✅ IsleMesh mDNS was already installed successfully. Skipping setup."
  exit 0
fi

if [[ -f "$INSTALL_FLAG" ]]; then
  echo "⚠️  Previous install attempt detected but not completed. Running uninstall before re-installing clean."
else
  echo "ℹ️  No previous install attempt detected. Running install."
fi

echo "🔧 Starting full install..."
./install-mesh-mdns.sh $CUSTOM_ENV_FILE

# Create install marker
mkdir -p /etc/isle-mesh
touch "$INSTALL_FLAG"

echo "✅ Installation complete. Marked at $INSTALL_FLAG."