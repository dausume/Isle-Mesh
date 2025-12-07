# Isle VLAN Agent - Nginx Reverse Proxy with Reload Watcher
FROM nginx:alpine

# Install basic utilities
RUN apk add --no-cache bash curl

# Copy reload watcher script
COPY reload-watcher.sh /usr/local/bin/reload-watcher.sh
RUN chmod +x /usr/local/bin/reload-watcher.sh

# Copy entrypoint
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Use custom entrypoint
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
