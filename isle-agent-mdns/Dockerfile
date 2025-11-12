FROM nginx:alpine

# Install Avahi mDNS daemon and required dependencies
RUN apk add --no-cache \
    avahi \
    avahi-tools \
    dbus \
    supervisor

# Create directories for Avahi services
RUN mkdir -p /etc/avahi/services

# Copy Avahi configuration
COPY avahi/avahi-daemon.conf /etc/avahi/avahi-daemon.conf

# Copy service management scripts
COPY scripts/register-service.sh /usr/local/bin/register-service
COPY scripts/unregister-service.sh /usr/local/bin/unregister-service
COPY scripts/entrypoint.sh /entrypoint.sh

RUN chmod +x /usr/local/bin/register-service \
             /usr/local/bin/unregister-service \
             /entrypoint.sh

# Create supervisor configuration for running both nginx and avahi
RUN mkdir -p /etc/supervisor/conf.d
COPY avahi/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Expose ports
EXPOSE 80 443 5353/udp

# Use supervisor to manage both nginx and avahi-daemon
ENTRYPOINT ["/entrypoint.sh"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
