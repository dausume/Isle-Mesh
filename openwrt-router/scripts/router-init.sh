#!/usr/bin/env bash
# router-init.sh - Wrapper for complete Isle-Mesh router initialization
# This script orchestrates the full router setup process via setup-isle-mesh-router.sh

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# Pass all arguments through to setup-isle-mesh-router.sh
# This includes --help, --step, and all other options
# The complete setup script handles:
# 1. VM initialization (router-init.main.sh)
# 2. Package download (download-packages.sh)
# 3. vLAN configuration and package installation (isle-vlan-router-config.main.sh)
# 4. DHCP setup (configure-dhcp-vlan.sh)
# 5. Discovery beacon (configure-discovery.sh)
exec bash "$SCRIPT_DIR/setup-isle-mesh-router.sh" "$@"
