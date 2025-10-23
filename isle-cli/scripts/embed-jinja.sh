#!/bin/bash
# Script for managing embed-jinja using docker-compose.auto.yml

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Get the project root (parent of isle-cli)
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# Path to embed-jinja directory
EMBED_JINJA_DIR="$PROJECT_ROOT/embed-jinja"

# Check if embed-jinja directory exists
if [ ! -d "$EMBED_JINJA_DIR" ]; then
    echo "Error: embed-jinja directory not found at $EMBED_JINJA_DIR"
    exit 1
fi

# Change to embed-jinja directory
cd "$EMBED_JINJA_DIR" || exit 1

# Always use docker-compose.auto.yml
COMPOSE_FILE="docker-compose.auto.yml"

ACTION=${1:-help}

case $ACTION in
    up|start)
        echo "Starting embed-jinja auto workflow..."
        docker compose -f "$COMPOSE_FILE" up --build
        ;;
    down|stop)
        echo "Stopping embed-jinja services..."
        docker compose -f "$COMPOSE_FILE" down
        # Also stop the generated services if they exist
        if [ -f "./jinja-build/docker-compose.yml" ]; then
            echo "Stopping generated application services..."
            docker compose -f ./jinja-build/docker-compose.yml down
        fi
        ;;
    restart)
        echo "Restarting embed-jinja..."
        docker compose -f "$COMPOSE_FILE" down
        if [ -f "./jinja-build/docker-compose.yml" ]; then
            docker compose -f ./jinja-build/docker-compose.yml down
        fi
        docker compose -f "$COMPOSE_FILE" up --build
        ;;
    logs)
        echo "Viewing embed-jinja auto workflow logs..."
        docker compose -f "$COMPOSE_FILE" logs -f
        ;;
    app-logs)
        if [ -f "./jinja-build/docker-compose.yml" ]; then
            echo "Viewing application service logs..."
            docker compose -f ./jinja-build/docker-compose.yml logs -f
        else
            echo "Error: Generated docker-compose.yml not found. Run 'isle embed-jinja up' first."
            exit 1
        fi
        ;;
    status)
        echo "Auto workflow status:"
        docker compose -f "$COMPOSE_FILE" ps
        echo ""
        if [ -f "./jinja-build/docker-compose.yml" ]; then
            echo "Application services status:"
            docker compose -f ./jinja-build/docker-compose.yml ps
        else
            echo "No generated application services found."
        fi
        ;;
    clean)
        echo "Cleaning embed-jinja (removing generated files)..."
        docker compose -f "$COMPOSE_FILE" down
        if [ -f "./jinja-build/docker-compose.yml" ]; then
            docker compose -f ./jinja-build/docker-compose.yml down
        fi
        rm -rf ./jinja-build/*
        echo "Clean complete. Run 'isle embed-jinja up' to regenerate."
        ;;
    help|*)
        echo "embed-jinja management script (auto mode)"
        echo ""
        echo "Usage: isle embed-jinja [action]"
        echo ""
        echo "This script exclusively uses docker-compose.auto.yml which:"
        echo "  1. Runs ansible-setup to generate jinja-build/docker-compose.yml"
        echo "  2. Automatically starts the generated application services"
        echo ""
        echo "Actions:"
        echo "  up/start   - Start embed-jinja auto workflow (runs ansible then app)"
        echo "  down/stop  - Stop all embed-jinja services (auto workflow + app)"
        echo "  restart    - Stop and restart the entire workflow"
        echo "  logs       - View auto workflow logs (ansible-setup, auto-launcher)"
        echo "  app-logs   - View application service logs"
        echo "  status     - Show status of all containers"
        echo "  clean      - Stop services and remove generated files"
        echo "  help       - Show this help message"
        echo ""
        echo "Examples:"
        echo "  isle embed-jinja up        # Start the complete workflow"
        echo "  isle embed-jinja status    # Check service status"
        echo "  isle embed-jinja app-logs  # View application logs"
        echo "  isle embed-jinja clean     # Clean and reset"
        ;;
esac
