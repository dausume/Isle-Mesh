# Isle Agent mDNS Receiver Dockerfile
# Based on mesh-prototypes/localhost-mdns/backend/Dockerfile.lh-mdns.backend
# This container receives mDNS data forwarded from localhost-mdns on the host
FROM python:3.11-slim

# Copy application code
COPY app.py /app/app.py

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh

# Ensure it is executable
RUN chmod +x /entrypoint.sh

# Install Python dependencies (only falcon, no mDNS libs needed)
RUN pip install --no-cache-dir falcon

# Set working directory
WORKDIR /app

# Expose port for API (localhost-mdns will POST to this)
EXPOSE 8888

# Use custom entrypoint script
ENTRYPOINT ["/entrypoint.sh"]
