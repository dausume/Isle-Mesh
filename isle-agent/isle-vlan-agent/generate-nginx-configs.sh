#!/bin/sh
#
# Generate Nginx Configs from Registry (Unified Format)
# Reads registry.json and generates nginx upstream/server configs
#
# Registry format (unified apps format):
# {
#   "apps": {
#     "myapp": {
#       "domain": "myapp.local",
#       "services": [
#         { "name": "api", "subdomain": "api", "container": "myapp-api-1", "port": 3000, "protocol": "http" }
#       ],
#       "modes": ["local"],
#       "updated_at": "..."
#     }
#   }
# }
#
# Usage: generate-nginx-configs.sh <registry-file> <output-dir>

set -e

REGISTRY_FILE="$1"
OUTPUT_DIR="$2"

if [ -z "$REGISTRY_FILE" ] || [ -z "$OUTPUT_DIR" ]; then
    echo "Usage: $0 <registry-file> <output-dir>"
    exit 1
fi

if [ ! -f "$REGISTRY_FILE" ]; then
    echo "ERROR: Registry file not found: $REGISTRY_FILE"
    exit 1
fi

# Create output directory if it doesn't exist
mkdir -p "$OUTPUT_DIR"

# Create a temp directory for new configs
TEMP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_DIR" EXIT

# Extract app names from the unified registry
APPS=$(jq -r '.apps | to_entries[] | select(.value.services | type == "array" and length > 0) | .key' "$REGISTRY_FILE" 2>/dev/null || echo "")

if [ -z "$APPS" ]; then
    echo "No apps with services arrays found in registry"
    # Clear all configs
    rm -f "$OUTPUT_DIR"/*.conf
    exit 0
fi

APP_COUNT=$(echo "$APPS" | wc -l)
echo "Generating configs for $APP_COUNT app(s)..."

# Generate a config for each app
echo "$APPS" | while IFS= read -r APP_NAME; do
    if [ -z "$APP_NAME" ]; then
        continue
    fi

    # Extract app details
    APP_DOMAIN=$(jq -r ".apps.\"$APP_NAME\".domain // \"\"" "$REGISTRY_FILE" 2>/dev/null)

    if [ -z "$APP_DOMAIN" ] || [ "$APP_DOMAIN" = "null" ]; then
        echo "  Warning: No domain for app: $APP_NAME, skipping"
        continue
    fi

    # Sanitize app name for file name
    SAFE_APP_NAME=$(echo "$APP_NAME" | sed 's/[^a-zA-Z0-9._-]/_/g')
    CONFIG_FILE="$TEMP_DIR/${SAFE_APP_NAME}.conf"

    echo "  Generating config for app: $APP_NAME ($APP_DOMAIN)"

    # Start config file
    cat > "$CONFIG_FILE" <<EOF
# Auto-generated config for app: $APP_NAME
# Domain: $APP_DOMAIN
# Generated at: $(date -Iseconds)
EOF

    # Iterate over each service in the app
    SERVICE_COUNT=$(jq -r ".apps.\"$APP_NAME\".services | length" "$REGISTRY_FILE" 2>/dev/null || echo "0")

    SERVICE_INDEX=0
    while [ "$SERVICE_INDEX" -lt "$SERVICE_COUNT" ]; do
        SVC_NAME=$(jq -r ".apps.\"$APP_NAME\".services[$SERVICE_INDEX].name // \"\"" "$REGISTRY_FILE")
        SVC_SUBDOMAIN=$(jq -r ".apps.\"$APP_NAME\".services[$SERVICE_INDEX].subdomain // \"\"" "$REGISTRY_FILE")
        SVC_CONTAINER=$(jq -r ".apps.\"$APP_NAME\".services[$SERVICE_INDEX].container // \"\"" "$REGISTRY_FILE")
        SVC_PORT=$(jq -r ".apps.\"$APP_NAME\".services[$SERVICE_INDEX].port // 80" "$REGISTRY_FILE")
        SVC_PROTOCOL=$(jq -r ".apps.\"$APP_NAME\".services[$SERVICE_INDEX].protocol // \"http\"" "$REGISTRY_FILE")

        if [ -z "$SVC_CONTAINER" ] || [ "$SVC_CONTAINER" = "null" ]; then
            echo "    Warning: No container for service $SVC_NAME, skipping"
            SERVICE_INDEX=$((SERVICE_INDEX + 1))
            continue
        fi

        # Build the server_name from subdomain + domain
        if [ -n "$SVC_SUBDOMAIN" ] && [ "$SVC_SUBDOMAIN" != "null" ]; then
            SERVER_NAME="${SVC_SUBDOMAIN}.${APP_DOMAIN}"
        else
            SERVER_NAME="${APP_DOMAIN}"
        fi

        # Sanitize upstream name
        SAFE_UPSTREAM=$(echo "${SAFE_APP_NAME}_${SVC_NAME}" | sed 's/[^a-zA-Z0-9_]/_/g')

        echo "    Service: $SVC_NAME -> ${SVC_CONTAINER}:${SVC_PORT} (${SERVER_NAME})"

        cat >> "$CONFIG_FILE" <<EOF

# Service: $SVC_NAME
upstream ${SAFE_UPSTREAM}_backend {
    server ${SVC_CONTAINER}:${SVC_PORT};
    keepalive 32;
}

server {
    listen 80;
    listen [::]:80;
    server_name ${SERVER_NAME};

    # Redirect HTTP to HTTPS
    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name ${SERVER_NAME};

    # SSL certificates
    ssl_certificate /etc/nginx/ssl/certs/${APP_DOMAIN}.crt;
    ssl_certificate_key /etc/nginx/ssl/keys/${APP_DOMAIN}.key;

    # Proxy to backend
    location / {
        proxy_pass ${SVC_PROTOCOL}://${SAFE_UPSTREAM}_backend;
        proxy_http_version 1.1;

        # Standard proxy headers
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Port \$server_port;

        # WebSocket support
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";

        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;

        # Buffering
        proxy_buffering on;
        proxy_buffer_size 4k;
        proxy_buffers 8 4k;
        proxy_busy_buffers_size 8k;
    }
}
EOF

        SERVICE_INDEX=$((SERVICE_INDEX + 1))
    done

done

# Move all new configs to output directory
# First, remove old configs
rm -f "$OUTPUT_DIR"/*.conf

# Copy new configs
if [ -n "$(ls -A $TEMP_DIR 2>/dev/null)" ]; then
    cp "$TEMP_DIR"/*.conf "$OUTPUT_DIR/" 2>/dev/null || true
    echo "Generated $(ls -1 $TEMP_DIR/*.conf 2>/dev/null | wc -l) config file(s)"
else
    echo "No config files to generate"
fi

exit 0
