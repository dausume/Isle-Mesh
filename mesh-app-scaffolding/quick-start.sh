#!/bin/bash

# quick-start.sh
# Quick start script for mesh-proxy builder
# Automatically detects Docker or uses local build

set -e

echo "╔══════════════════════════════════════════════════════════╗"
echo "║          Mesh Proxy Builder - Quick Start               ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# Check if Docker is available
if command -v docker &> /dev/null && docker ps &> /dev/null; then
    echo "✓ Docker detected - using containerized build"
    echo ""

    # Check if image exists
    if docker images | grep -q mesh-proxy-builder; then
        echo "✓ Docker image found"
    else
        echo "Building Docker image (this may take a minute)..."
        docker build -t mesh-proxy-builder . -q
        echo "✓ Docker image built"
    fi

    echo ""
    echo "Running proxy builder..."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    docker-compose run --rm mesh-proxy-builder "$@"

elif [ -f "build-mesh-proxy.sh" ]; then
    echo "⚠ Docker not available - using local build"
    echo ""

    # Check dependencies
    MISSING_DEPS=false

    if ! command -v yq &> /dev/null && ! [ -f "$HOME/.local/bin/yq" ]; then
        echo "✗ yq not found"
        MISSING_DEPS=true
    else
        echo "✓ yq found"
    fi

    if ! python3 -c "import jinja2" 2>/dev/null; then
        echo "✗ Jinja2 not found"
        MISSING_DEPS=true
    else
        echo "✓ Jinja2 found"
    fi

    if [ "$MISSING_DEPS" = true ]; then
        echo ""
        echo "Installing missing dependencies..."
        make install-deps
        echo ""
    fi

    echo ""
    echo "Running proxy builder..."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    ./build-mesh-proxy.sh "$@"

else
    echo "✗ Error: Cannot find build script"
    echo "  Make sure you're in the mesh-proxy directory"
    exit 1
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✓ Build complete!"
echo ""
echo "Next steps:"
echo "  1. Review the generated config: output/nginx-mesh-proxy.conf"
echo "  2. Copy to your proxy: cp output/nginx-mesh-proxy.conf ../localhost-mdns/proxy/lh-mdns.proxy.conf"
echo "  3. Rebuild your proxy container"
echo ""
