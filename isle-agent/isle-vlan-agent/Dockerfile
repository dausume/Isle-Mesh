# Isle VLAN Agent - Nginx Reverse Proxy with Self-Reloading Config Generator
FROM nginx:alpine

# Install utilities including jq for JSON parsing
RUN apk add --no-cache bash curl jq

# Copy registry watcher script (watches registry.json for changes)
COPY registry-watcher.sh /usr/local/bin/registry-watcher.sh
RUN chmod +x /usr/local/bin/registry-watcher.sh

# Copy config generator script (generates nginx configs from registry.json)
COPY generate-nginx-configs.sh /usr/local/bin/generate-nginx-configs.sh
RUN chmod +x /usr/local/bin/generate-nginx-configs.sh

# Copy entrypoint
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Create config directory
RUN mkdir -p /etc/nginx/configs

# Use custom entrypoint
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
