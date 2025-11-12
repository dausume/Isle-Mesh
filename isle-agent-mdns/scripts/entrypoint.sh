#!/bin/sh
set -e

echo "==================================="
echo "Isle Agent with mDNS starting..."
echo "==================================="

# Ensure D-Bus directory exists and has correct permissions
mkdir -p /var/run/dbus
rm -f /var/run/dbus/pid

# Ensure Avahi directories exist
mkdir -p /etc/avahi/services
mkdir -p /var/run/avahi-daemon

# Auto-register services from registry if it exists
if [ -f /etc/isle-mesh/agent/registry.json ]; then
    echo "Checking for services to register..."

    # Extract domains from registry and create service files
    # This will be populated by the register-service script
    if command -v jq >/dev/null 2>&1; then
        # Parse registry and auto-register services
        # (This is a placeholder - actual implementation would parse the JSON)
        echo "Registry found, services will be auto-registered"
    fi
fi

echo "Starting services via supervisord..."
echo "  - D-Bus system daemon"
echo "  - Avahi mDNS daemon"
echo "  - Nginx proxy"
echo "==================================="

# Execute the CMD
exec "$@"
