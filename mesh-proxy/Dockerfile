# Dockerfile for mesh-proxy builder
# This container can analyze a docker-compose.yml and generate nginx configuration

FROM python:3.11-slim

# Install dependencies
RUN apt-get update && apt-get install -y \
    wget \
    bash \
    && rm -rf /var/lib/apt/lists/*

# Install yq (YAML processor)
RUN wget -qO /usr/local/bin/yq https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64 \
    && chmod +x /usr/local/bin/yq

# Install Python dependencies
RUN pip install --no-cache-dir jinja2

# Create working directory
WORKDIR /mesh-proxy

# Copy all mesh-proxy files
COPY segments/ ./segments/
COPY templates/ ./templates/
COPY scripts/ ./scripts/
COPY build-mesh-proxy.sh ./

# Make scripts executable
RUN chmod +x build-mesh-proxy.sh scripts/*.sh scripts/*.py

# Create output directory
RUN mkdir -p output

# Set entrypoint
COPY docker-entrypoint.sh /
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["--help"]
