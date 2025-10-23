#!/bin/bash
# Script for managing mesh-proxy docker-compose

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Get the project root (parent of isle-cli)
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# Path to mesh-proxy directory
MESH_PROXY_DIR="$PROJECT_ROOT/mesh-proxy"

# Check if mesh-proxy directory exists
if [ ! -d "$MESH_PROXY_DIR" ]; then
    echo "Error: mesh-proxy directory not found at $MESH_PROXY_DIR"
    exit 1
fi

# Change to mesh-proxy directory
cd "$MESH_PROXY_DIR" || exit 1

ACTION=${1:-help}

case $ACTION in
    up)
        echo "Starting mesh-proxy services..."
        docker compose up -d
        ;;
    down)
        echo "Stopping mesh-proxy services..."
        docker compose down
        ;;
    build)
        echo "Building mesh-proxy..."
        docker compose build
        ;;
    rebuild)
        echo "Rebuilding mesh-proxy..."
        docker compose down
        docker compose build
        docker compose up -d
        ;;
    logs)
        echo "Viewing mesh-proxy logs..."
        docker compose logs -f
        ;;
    status)
        echo "mesh-proxy status:"
        docker compose ps
        ;;
    run)
        echo "Running mesh-proxy-builder..."
        docker compose run mesh-proxy-builder
        ;;
    watch)
        echo "Starting mesh-proxy-watcher..."
        docker compose up mesh-proxy-watcher
        ;;
    help|*)
        echo "mesh-proxy management script"
        echo ""
        echo "Usage: isle mesh-proxy [action]"
        echo ""
        echo "Actions:"
        echo "  up       - Start mesh-proxy services in detached mode"
        echo "  down     - Stop mesh-proxy services"
        echo "  build    - Build mesh-proxy images"
        echo "  rebuild  - Stop, rebuild, and restart services"
        echo "  logs     - View and follow logs"
        echo "  status   - Show running containers"
        echo "  run      - Run mesh-proxy-builder once"
        echo "  watch    - Start mesh-proxy-watcher (auto-rebuild on changes)"
        echo "  help     - Show this help message"
        ;;
esac
