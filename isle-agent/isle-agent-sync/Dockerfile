# Isle Agent Sync Dockerfile
# Python container that receives mDNS data and generates nginx configs
FROM python:3.11-slim

# Copy application code
COPY app.py /app/app.py

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh

# Ensure it is executable
RUN chmod +x /entrypoint.sh

# Install Python dependencies
RUN pip install --no-cache-dir falcon

# Set working directory
WORKDIR /app

# Expose port for API
EXPOSE 8888

# Use custom entrypoint script
ENTRYPOINT ["/entrypoint.sh"]
