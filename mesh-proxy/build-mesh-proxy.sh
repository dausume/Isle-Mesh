#!/bin/bash

# build-mesh-proxy.sh
# Convenient wrapper script to build mesh-proxy configuration

set -e

# Default values
COMPOSE_FILE="../localhost-mdns/docker-compose.lh-mdns.yml"
DOMAIN="mesh-app.local"
BASE_CERT="mesh-app.crt"
BASE_KEY="mesh-app.key"
OUTPUT="output/nginx-mesh-proxy.conf"
MTLS_SERVICES=("backend")  # Services that require mTLS

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --compose)
            COMPOSE_FILE="$2"
            shift 2
            ;;
        --domain)
            DOMAIN="$2"
            shift 2
            ;;
        --output)
            OUTPUT="$2"
            shift 2
            ;;
        --help)
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  --compose FILE    Path to docker-compose.yml (default: $COMPOSE_FILE)"
            echo "  --domain DOMAIN   Base domain name (default: $DOMAIN)"
            echo "  --output FILE     Output configuration file (default: $OUTPUT)"
            echo "  --help            Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Build mTLS arguments
MTLS_ARGS=""
for service in "${MTLS_SERVICES[@]}"; do
    MTLS_ARGS="$MTLS_ARGS --service-mtls $service"
done

# Run the Python build script
python3 scripts/build-proxy-config.py \
    --compose "$COMPOSE_FILE" \
    --domain "$DOMAIN" \
    --base-cert "$BASE_CERT" \
    --base-key "$BASE_KEY" \
    --output "$OUTPUT" \
    $MTLS_ARGS

echo ""
echo "✓ Proxy configuration built successfully!"
echo "  Output: $OUTPUT"
echo ""
echo "To use this configuration, copy it to your proxy directory:"
echo "  cp $OUTPUT ../localhost-mdns/proxy/lh-mdns.proxy.conf"
