#!/bin/sh
# Unregister a mesh app service from Avahi mDNS
# Usage: unregister-service <app-name>

set -e

APP_NAME="${1:?App name required}"
SERVICE_FILE="/etc/avahi/services/${APP_NAME}.service"

if [ ! -f "$SERVICE_FILE" ]; then
    echo "Service ${APP_NAME} is not registered (file not found)"
    exit 0
fi

echo "Unregistering mDNS service for ${APP_NAME}..."

rm -f "$SERVICE_FILE"

echo "Service file removed: ${SERVICE_FILE}"

# Avahi automatically detects removed service files
if pgrep avahi-daemon >/dev/null 2>&1; then
    echo "Avahi will stop advertising this service shortly..."
fi

echo "Service ${APP_NAME} unregistered successfully!"
