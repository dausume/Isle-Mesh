#!/bin/bash

# parse-docker-compose.sh
# Parses docker-compose.yml and extracts service information for proxy configuration
# Outputs JSON format that can be consumed by the Python template generator

set -e

# Input docker-compose file
COMPOSE_FILE="${1:-docker-compose.yml}"

if [ ! -f "$COMPOSE_FILE" ]; then
    echo "Error: Docker compose file not found: $COMPOSE_FILE" >&2
    exit 1
fi

# Check if yq is available (YAML processor)
# Try to find yq in common locations
if command -v yq &> /dev/null; then
    YQ_CMD="yq"
elif [ -f "$HOME/.local/bin/yq" ]; then
    YQ_CMD="$HOME/.local/bin/yq"
elif [ -f "/usr/local/bin/yq" ]; then
    YQ_CMD="/usr/local/bin/yq"
else
    echo "Error: yq is required but not installed. Please install yq (https://github.com/mikefarah/yq)" >&2
    exit 1
fi

# Extract service information using yq
echo "{"
echo "  \"services\": ["

FIRST=true
for service in $($YQ_CMD eval '.services | keys | .[]' "$COMPOSE_FILE"); do
    if [ "$FIRST" = false ]; then
        echo ","
    fi
    FIRST=false

    # Extract service details
    SERVICE_NAME="$service"

    # Try to extract exposed port (first exposed port if multiple)
    EXPOSED_PORT=$($YQ_CMD eval ".services.$service.expose[0]" "$COMPOSE_FILE" 2>/dev/null || echo "null")

    # If no expose, try to get from ports mapping
    if [ "$EXPOSED_PORT" = "null" ] || [ -z "$EXPOSED_PORT" ]; then
        PORT_MAPPING=$($YQ_CMD eval ".services.$service.ports[0]" "$COMPOSE_FILE" 2>/dev/null || echo "null")
        if [ "$PORT_MAPPING" != "null" ] && [ -n "$PORT_MAPPING" ]; then
            # Extract internal port from mapping like "8080:8443"
            EXPOSED_PORT=$(echo "$PORT_MAPPING" | sed 's/.*://g')
        else
            EXPOSED_PORT="80"
        fi
    fi

    # Check for labels that might indicate proxy configuration
    MTLS=$($YQ_CMD eval ".services.$service.labels.\"mesh.mtls\"" "$COMPOSE_FILE" 2>/dev/null || echo "false")
    SUBDOMAIN=$($YQ_CMD eval ".services.$service.labels.\"mesh.subdomain\"" "$COMPOSE_FILE" 2>/dev/null || echo "$service")

    # Clean up null/empty values
    [ "$MTLS" = "null" ] && MTLS="false"
    [ "$SUBDOMAIN" = "null" ] && SUBDOMAIN="$service"

    echo -n "    {"
    echo -n "\"name\": \"$SERVICE_NAME\", "
    echo -n "\"port\": \"$EXPOSED_PORT\", "
    echo -n "\"subdomain\": \"$SUBDOMAIN\", "
    echo -n "\"mtls\": $MTLS"
    echo -n "}"
done

echo ""
echo "  ]"
echo "}"
