#!/bin/bash

# docker-entrypoint.sh
# Entrypoint script for mesh-proxy builder container
# Handles automated proxy configuration generation

set -e

# Default values from environment variables
COMPOSE_FILE="${INPUT_COMPOSE:-/input/localhost-mdns/docker-compose.lh-mdns.yml}"
DOMAIN="${BASE_DOMAIN:-mesh-app.local}"
CERT="${BASE_CERT:-mesh-app.crt}"
KEY="${BASE_KEY:-mesh-app.key}"
OUTPUT="${OUTPUT_FILE:-/mesh-proxy/output/nginx-mesh-proxy.conf}"
WATCH_MODE="${WATCH_MODE:-false}"
WATCH_INTERVAL="${WATCH_INTERVAL:-10}"

# Parse command line arguments
HELP_MODE=false
CUSTOM_COMPOSE=""
CUSTOM_DOMAIN=""
CUSTOM_OUTPUT=""
MTLS_ARGS=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --help|-h)
            HELP_MODE=true
            shift
            ;;
        --compose)
            CUSTOM_COMPOSE="$2"
            shift 2
            ;;
        --domain)
            CUSTOM_DOMAIN="$2"
            shift 2
            ;;
        --output)
            CUSTOM_OUTPUT="$2"
            shift 2
            ;;
        --service-mtls)
            MTLS_ARGS="$MTLS_ARGS --service-mtls $2"
            shift 2
            ;;
        --watch)
            WATCH_MODE=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Show help
if [ "$HELP_MODE" = true ]; then
    cat <<EOF
Mesh Proxy Builder Container

Usage:
  docker-compose run mesh-proxy-builder [options]

Options:
  --help                Show this help message
  --compose FILE        Path to docker-compose.yml (default: $COMPOSE_FILE)
  --domain DOMAIN       Base domain name (default: $DOMAIN)
  --output FILE         Output configuration file (default: $OUTPUT)
  --service-mtls NAME   Service that requires mTLS (can be used multiple times)
  --watch               Watch mode: rebuild on file changes

Environment Variables:
  INPUT_COMPOSE         Default docker-compose file path
  BASE_DOMAIN           Default domain name
  BASE_CERT             SSL certificate filename
  BASE_KEY              SSL key filename
  MTLS_SERVICES         Space-separated list of mTLS services
  OUTPUT_FILE           Default output file path
  WATCH_MODE            Enable watch mode (true/false)
  WATCH_INTERVAL        Watch interval in seconds

Examples:
  # Build with defaults
  docker-compose run mesh-proxy-builder

  # Build with custom domain
  docker-compose run mesh-proxy-builder --domain custom.local

  # Build with custom compose file
  docker-compose run mesh-proxy-builder --compose /input/other/docker-compose.yml

  # Build with mTLS services
  docker-compose run mesh-proxy-builder --service-mtls backend --service-mtls api

  # Run in watch mode (rebuilds when compose file changes)
  docker-compose up mesh-proxy-watcher

EOF
    exit 0
fi

# Override with custom values if provided
[ -n "$CUSTOM_COMPOSE" ] && COMPOSE_FILE="$CUSTOM_COMPOSE"
[ -n "$CUSTOM_DOMAIN" ] && DOMAIN="$CUSTOM_DOMAIN"
[ -n "$CUSTOM_OUTPUT" ] && OUTPUT="$CUSTOM_OUTPUT"

# Build mTLS arguments from environment variable if not already set
if [ -z "$MTLS_ARGS" ] && [ -n "$MTLS_SERVICES" ]; then
    for service in $MTLS_SERVICES; do
        MTLS_ARGS="$MTLS_ARGS --service-mtls $service"
    done
fi

# Function to build the proxy configuration
build_proxy() {
    echo "================================================"
    echo "Mesh Proxy Builder"
    echo "================================================"
    echo "Input:  $COMPOSE_FILE"
    echo "Domain: $DOMAIN"
    echo "Output: $OUTPUT"
    echo "================================================"
    echo ""

    # Check if input file exists
    if [ ! -f "$COMPOSE_FILE" ]; then
        echo "Error: Docker compose file not found: $COMPOSE_FILE" >&2
        echo "Available files in /input:" >&2
        find /input -name "*.yml" -o -name "*.yaml" 2>/dev/null || echo "  (none found)" >&2
        exit 1
    fi

    # Run the build script
    python3 /mesh-proxy/scripts/build-proxy-config.py \
        --compose "$COMPOSE_FILE" \
        --domain "$DOMAIN" \
        --base-cert "$CERT" \
        --base-key "$KEY" \
        --output "$OUTPUT" \
        $MTLS_ARGS

    echo ""
    echo "✓ Configuration built successfully!"
    echo "  Output: $OUTPUT"
    echo ""

    # Show some stats
    if [ -f "$OUTPUT" ]; then
        LINES=$(wc -l < "$OUTPUT")
        SIZE=$(du -h "$OUTPUT" | cut -f1)
        echo "  Stats: $LINES lines, $SIZE"
    fi
}

# Function to watch for changes
watch_and_build() {
    echo "Starting watch mode..."
    echo "Monitoring: $COMPOSE_FILE"
    echo "Checking every ${WATCH_INTERVAL}s for changes"
    echo ""

    # Build once initially
    build_proxy

    # Get initial checksum
    LAST_CHECKSUM=$(md5sum "$COMPOSE_FILE" 2>/dev/null | cut -d' ' -f1 || echo "")

    echo ""
    echo "Watching for changes... (Press Ctrl+C to stop)"

    while true; do
        sleep "$WATCH_INTERVAL"

        # Check if file still exists
        if [ ! -f "$COMPOSE_FILE" ]; then
            echo "Warning: Input file disappeared: $COMPOSE_FILE"
            continue
        fi

        # Get current checksum
        CURRENT_CHECKSUM=$(md5sum "$COMPOSE_FILE" | cut -d' ' -f1)

        # Compare checksums
        if [ "$CURRENT_CHECKSUM" != "$LAST_CHECKSUM" ]; then
            echo ""
            echo "⚡ Change detected! Rebuilding..."
            echo ""
            build_proxy
            LAST_CHECKSUM="$CURRENT_CHECKSUM"
            echo ""
            echo "Watching for changes..."
        fi
    done
}

# Main execution
if [ "$WATCH_MODE" = true ]; then
    watch_and_build
else
    build_proxy
fi
