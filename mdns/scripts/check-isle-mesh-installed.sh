#!/bin/bash
set -e
# Should instead either load the default or load from the provided custom env file.

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# When running in Docker, ISLEMESH_DIR will be /etc/isle-mesh
# When running from CLI, we use the script's parent directory structure
ISLEMESH_DIR="${1:-/etc/isle-mesh}"
echo "ISLEMESH_DIR=$ISLEMESH_DIR"

# Determine the actual scripts source directory
# If running from source (scripts are in current location), use that
# Otherwise fall back to ISLEMESH_DIR
if [ -f "$SCRIPT_DIR/install-mesh-mdns.sh" ]; then
  SCRIPTS_SOURCE_DIR="$SCRIPT_DIR"
else
  SCRIPTS_SOURCE_DIR="$ISLEMESH_DIR/mdns/scripts"
fi

CUSTOM_ENV_FILE="${2:-$SCRIPTS_SOURCE_DIR/mesh-mdns.conf}"
# Used to inidicate if IsleMesh mDNS has had an initial install attempt run.
INSTALL_FLAG="$ISLEMESH_DIR/.installed_started"
# Used to indicate if IsleMesh mDNS install completed successfully.
INSTALL_COMPLETE_FLAG="$ISLEMESH_DIR/.install_complete"


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

# Create install directory and mark installation as started
sudo mkdir -p $ISLEMESH_DIR
sudo touch "$INSTALL_FLAG"

echo "📁 IsleMesh directory contents before install:"
echo $(ls -l -all $ISLEMESH_DIR)

# Run the installation using the detected scripts source directory
bash "$SCRIPTS_SOURCE_DIR/install-mesh-mdns.sh" "$CUSTOM_ENV_FILE" "$ISLEMESH_DIR"

# Mark installation as complete
sudo touch "$INSTALL_COMPLETE_FLAG"

echo "✅ Installation complete. Marked at $INSTALL_COMPLETE_FLAG"