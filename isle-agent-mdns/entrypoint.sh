#!/bin/bash
# Isle Agent mDNS Receiver entrypoint
# Based on mesh-prototypes/localhost-mdns/backend/entrypoint.sh

echo "📥 Starting Isle Agent mDNS Receiver..."
echo "Waiting for mDNS data from localhost-mdns on host..."
python /app/app.py || {
    echo "❌ Python application failed."
    exit 1
}

# Keeps the container alive so you can inspect it manually if needed.
# Use: docker exec -it isle-agent-mdns /bin/sh
tail -f /dev/null
