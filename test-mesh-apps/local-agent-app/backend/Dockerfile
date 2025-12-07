# Backend Dockerfile
# Serves static HTML with mTLS (HTTPS + client cert verification)

FROM python:3.11-slim

WORKDIR /app

# Copy application code
COPY app.py .
COPY entrypoint.sh /entrypoint.sh

# Ensure entrypoint is executable
RUN chmod +x /entrypoint.sh

# Install dependencies
RUN pip install --no-cache-dir falcon

# SSL certs will be mounted via volume
# Expected: /ssl/certs/api.local-app.local.crt
#           /ssl/keys/api.local-app.local.key
#           /ssl/certs/local-app.local.crt (for mTLS verification)

# Use custom entrypoint script
ENTRYPOINT ["/entrypoint.sh"]
