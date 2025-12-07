#!/bin/bash
# Entrypoint for isle-agent-sync container

set -e

echo "========================================"
echo "Isle Agent Sync - Starting"
echo "========================================"

# Run the Python app
exec python3 /app/app.py
