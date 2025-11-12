# Quick Start Guide: Isle Agent with mDNS

Get the Isle Agent with mDNS up and running in 5 minutes.

## Prerequisites

- Docker and docker-compose installed
- `isle-br-0` bridge interface exists
- OpenWRT router configured and running
- Basic Isle Agent setup completed

## Step 1: Create Required Directories

```bash
sudo mkdir -p /etc/isle-mesh/agent/mdns/services
sudo chmod -R 755 /etc/isle-mesh/agent/mdns
```

## Step 2: Build and Start

```bash
cd /home/dustin/Desktop/IsleMesh/isle-agent-mdns

# Build the image
docker-compose build

# Start the container
docker-compose up -d

# Verify it's running
docker logs isle-agent-mdns
```

You should see output indicating:
- D-Bus daemon started
- Avahi daemon started
- Nginx started

## Step 3: Register Your First Service

```bash
# Register a test service
docker exec isle-agent-mdns register-service testapp testapp.local 443 https

# Verify it was registered
docker exec isle-agent-mdns ls /etc/avahi/services/
```

## Step 4: Test mDNS Broadcasting

```bash
# From inside the container
docker exec isle-agent-mdns avahi-browse -a -t

# You should see your testapp service listed
```

## Step 5: Test from Another Device on vLAN

From any device connected to the same vLAN:

```bash
# Linux/macOS
avahi-browse -a

# macOS with dns-sd
dns-sd -B _https._tcp

# You should see "testapp" service advertised
```

## Step 6: Sync All Apps from Registry

If you already have apps registered in the Isle Agent:

```bash
# Make sync script executable
chmod +x scripts/sync-services.sh

# Run sync
./scripts/sync-services.sh

# Verify all services are registered
docker exec isle-agent-mdns ls /etc/avahi/services/
```

## Verification Checklist

- [ ] Container is running: `docker ps | grep isle-agent-mdns`
- [ ] D-Bus is running: `docker exec isle-agent-mdns pgrep dbus-daemon`
- [ ] Avahi is running: `docker exec isle-agent-mdns pgrep avahi-daemon`
- [ ] Nginx is running: `docker exec isle-agent-mdns pgrep nginx`
- [ ] Services are registered: `docker exec isle-agent-mdns ls /etc/avahi/services/`
- [ ] mDNS is broadcasting: `docker exec isle-agent-mdns avahi-browse -a -t`
- [ ] Services visible on vLAN: Test from another device

## Common Issues

### Container won't start

```bash
# Check if ports are already in use
sudo netstat -tulpn | grep -E ':(80|443|5353)'

# Check docker logs
docker logs isle-agent-mdns
```

### Avahi daemon not running

```bash
# Restart the services
docker exec isle-agent-mdns supervisorctl restart dbus avahi

# Check status
docker exec isle-agent-mdns supervisorctl status
```

### Services not visible on vLAN

```bash
# Verify macvlan network
docker network inspect isle-br-0

# Check bridge exists
ip link show isle-br-0

# Enable multicast on bridge
sudo ip link set isle-br-0 multicast on
```

## Next Steps

1. **Auto-sync on app changes**: Integrate `sync-services.sh` into your app deployment workflow
2. **Monitor services**: Set up monitoring of mDNS advertisements
3. **Configure subdomains**: Register API and other subdomains for each app
4. **Enable IPv6**: Update avahi-daemon.conf if needed

## Cleanup/Uninstall

```bash
# Stop and remove container
docker-compose down

# Remove the image
docker rmi isle-agent-mdns:latest

# Optional: Remove service files
sudo rm -rf /etc/isle-mesh/agent/mdns
```

## Ready to Use!

Your Isle Agent is now broadcasting services over mDNS on the vLAN only. Other devices on the vLAN can discover your services at `<app-name>.local`.
