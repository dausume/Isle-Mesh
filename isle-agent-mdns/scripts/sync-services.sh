#!/bin/bash
# Synchronize mDNS service registrations from registry.json
# This script reads the Isle Agent registry and ensures all apps have mDNS services

set -e

REGISTRY_FILE="${REGISTRY_FILE:-/etc/isle-mesh/agent/registry.json}"
CONTAINER_NAME="${CONTAINER_NAME:-isle-agent-mdns}"

if [ ! -f "$REGISTRY_FILE" ]; then
    echo "Registry file not found: $REGISTRY_FILE"
    echo "No services to sync."
    exit 0
fi

if ! command -v jq >/dev/null 2>&1; then
    echo "Error: jq is required but not installed"
    exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Error: Container ${CONTAINER_NAME} is not running"
    exit 1
fi

echo "Synchronizing mDNS services from registry..."

# Extract app names from registry
APPS=$(jq -r '.apps | keys[]' "$REGISTRY_FILE")

if [ -z "$APPS" ]; then
    echo "No apps found in registry"
    exit 0
fi

# Register each app
for APP in $APPS; do
    DOMAIN=$(jq -r ".apps.\"${APP}\".domain" "$REGISTRY_FILE")

    if [ "$DOMAIN" != "null" ] && [ -n "$DOMAIN" ]; then
        echo "Registering service for app: ${APP} (${DOMAIN})"

        docker exec "$CONTAINER_NAME" register-service "$APP" "$DOMAIN" 443 https

        # Also register subdomains if any
        SUBDOMAINS=$(jq -r ".apps.\"${APP}\".subdomains[]?" "$REGISTRY_FILE" 2>/dev/null || true)
        for SUBDOMAIN in $SUBDOMAINS; do
            if [ -n "$SUBDOMAIN" ]; then
                # Extract subdomain prefix (e.g., "api" from "api.myapp.local")
                SUBDOMAIN_PREFIX=$(echo "$SUBDOMAIN" | cut -d. -f1)
                echo "  - Registering subdomain: ${SUBDOMAIN}"
                docker exec "$CONTAINER_NAME" register-service "${APP}-${SUBDOMAIN_PREFIX}" "$SUBDOMAIN" 443 https
            fi
        done
    fi
done

echo "Service synchronization complete!"
echo ""
echo "To verify mDNS services, run:"
echo "  docker exec ${CONTAINER_NAME} avahi-browse -a -t"
