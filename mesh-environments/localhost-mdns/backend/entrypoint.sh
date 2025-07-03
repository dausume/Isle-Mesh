#!/bin/bash

echo "🔐 Starting Falcon backend directly with mTLS..."
python app.py || {
    echo "❌ Python failed."
}


# Keeps the container alive so you can inspect it manually if needed.
# Use: sudo docker exec -it <container_id> /bin/sh
# To find container_id: sudo docker container ps
tail -f /dev/null