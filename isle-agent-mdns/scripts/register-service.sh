#!/bin/sh
# Register a mesh app service with Avahi mDNS
# Usage: register-service <app-name> <domain> [port] [service-type]

set -e

APP_NAME="${1:?App name required}"
DOMAIN="${2:?Domain required}"
PORT="${3:-443}"
SERVICE_TYPE="${4:-https}"

SERVICE_FILE="/etc/avahi/services/${APP_NAME}.service"

echo "Registering mDNS service for ${APP_NAME}..."
echo "  Domain: ${DOMAIN}"
echo "  Port: ${PORT}"
echo "  Type: ${SERVICE_TYPE}"

# Create Avahi service file
cat > "$SERVICE_FILE" <<EOF
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">${APP_NAME}</name>
  <service>
    <type>_${SERVICE_TYPE}._tcp</type>
    <port>${PORT}</port>
    <txt-record>path=/</txt-record>
    <txt-record>isle-mesh=true</txt-record>
    <txt-record>version=1.0</txt-record>
  </service>
  <service>
    <type>_http._tcp</type>
    <port>80</port>
    <txt-record>path=/</txt-record>
    <txt-record>isle-mesh=true</txt-record>
  </service>
</service-group>
EOF

echo "Service file created at: ${SERVICE_FILE}"

# Reload Avahi if it's running
if pgrep avahi-daemon >/dev/null 2>&1; then
    echo "Reloading Avahi daemon..."
    # Avahi automatically picks up new service files
    # Just verify it's running
    if command -v avahi-browse >/dev/null 2>&1; then
        echo "Service should be advertising shortly..."
    fi
else
    echo "Warning: Avahi daemon not running yet (will auto-load on start)"
fi

echo "Service ${APP_NAME} registered successfully!"
