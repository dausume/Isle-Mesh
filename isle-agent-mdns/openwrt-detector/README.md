# OpenWRT Router Detection & Nginx Fallback Proxy

Simple host-based script to detect OpenWRT router and configure nginx fallback proxy.

## Overview

This script:
1. Checks if the OpenWRT router VM exists and is running (via `virsh`)
2. Gets the router's IP address from libvirt and ARP table
3. Generates nginx configuration for fallback proxy
4. Automatically reloads nginx when configuration changes

## How It Works

```
┌─────────────────────────────────────────────────┐
│  detect-and-configure-nginx.sh (runs on host)   │
│                                                  │
│  1. virsh list --all | grep openwrt-isle-router │
│  2. virsh dumpxml → get MAC address             │
│  3. arp -n → lookup IP by MAC                    │
│  4. ping → test connectivity                     │
│  5. Generate nginx config                        │
│  6. nginx -s reload                              │
└─────────────────────────────────────────────────┘
                      ↓
          ┌──────────────────────┐
          │  Nginx Fallback Proxy │
          │  (routes unknown .local)│
          └──────────────────────┘
                      ↓
          ┌──────────────────────┐
          │  OpenWRT Router      │
          │  (manages vLAN apps)  │
          └──────────────────────┘
```

## Quick Start

### Recommended: Event-Driven (Libvirt Hook)

Install a libvirt hook that automatically triggers nginx reconfiguration when router state changes:

```bash
# Install the hook (one-time setup)
sudo ./install-libvirt-hook.sh
```

That's it! The hook will now automatically:
- Detect when router VM starts
- Detect when router VM stops
- Reconfigure nginx immediately
- No polling, no delays, no wasted resources

**Uninstall:**
```bash
sudo ./uninstall-libvirt-hook.sh
```

### Alternative: Run Manually

```bash
# Run once to generate config
sudo ./detect-and-configure-nginx.sh
```

### Alternative: Polling (Systemd Timer)

If you can't use libvirt hooks, create systemd service and timer for periodic checking:

```bash
# Create service file
sudo tee /etc/systemd/system/isle-nginx-detector.service << 'EOF'
[Unit]
Description=Isle OpenWRT Router Detection & Nginx Configuration
After=network.target libvirtd.service

[Service]
Type=oneshot
ExecStart=/path/to/IsleMesh/isle-agent-mdns/openwrt-detector/detect-and-configure-nginx.sh
StandardOutput=journal
StandardError=journal
EOF

# Create timer file (runs every 30 seconds)
sudo tee /etc/systemd/system/isle-nginx-detector.timer << 'EOF'
[Unit]
Description=Isle OpenWRT Router Detection Timer
Requires=isle-nginx-detector.service

[Timer]
OnBootSec=10s
OnUnitActiveSec=30s
AccuracySec=5s

[Install]
WantedBy=timers.target
EOF

# Enable and start timer
sudo systemctl daemon-reload
sudo systemctl enable isle-nginx-detector.timer
sudo systemctl start isle-nginx-detector.timer

# Check status
sudo systemctl status isle-nginx-detector.timer
sudo systemctl list-timers isle-nginx-detector.timer
```

**View logs:**
```bash
# View hook trigger logs
sudo journalctl -t libvirt-hook -f

# View detector logs
sudo tail -f /var/log/isle-nginx-detector.log
```

## Configuration

Environment variables:

```bash
# Router VM name (default: openwrt-isle-router)
export ROUTER_VM_NAME="openwrt-isle-router"

# Nginx config path (default: /etc/nginx/conf.d/openwrt-fallback.conf)
export NGINX_CONFIG_PATH="/etc/nginx/conf.d/openwrt-fallback.conf"

# Enable/disable nginx proxy (default: true)
export NGINX_ENABLED="true"
```

## Generated Nginx Configurations

### Router Running & Reachable

```nginx
# OpenWRT Fallback Proxy - ENABLED
# Generated at 2025-01-20T10:30:00-05:00

# Router Status: RUNNING
# Router VM: openwrt-isle-router
# Router IP: 192.168.100.1

server {
    listen 80 default_server;
    server_name _;

    location / {
        proxy_pass http://192.168.100.1:80;
        proxy_set_header Host $host;
        # ... full proxy config
    }
}
```

### Router Not Available

```nginx
# OpenWRT Fallback Proxy - DISABLED
# Generated at 2025-01-20T10:30:00-05:00

# Router Status: not_found
# Router VM: openwrt-isle-router

# ❌ Router VM not found
#    Initialize router with: sudo isle router init

server {
    listen 8080;
    server_name _;

    location / {
        return 503 "OpenWRT router not available. Status: not_found";
        add_header Content-Type text/plain;
    }
}
```

## Troubleshooting

### Check Router Status

```bash
# Manually check if router is running
isle router status

# Check if VM exists
virsh list --all | grep openwrt-isle-router

# Get router MAC
virsh dumpxml openwrt-isle-router | grep "mac address"

# Check ARP table
arp -n
```

### Script Not Working

```bash
# Run with verbose output
sudo bash -x ./detect-and-configure-nginx.sh

# Check nginx error log
sudo tail -f /var/log/nginx/error.log

# Test nginx config
sudo nginx -t
```

### Permissions Issues

```bash
# Add your user to libvirt group (no sudo needed for virsh)
sudo usermod -aG libvirt $USER

# Log out and log back in for group changes to take effect
```

## Integration with Isle-CLI

This script works alongside `isle-cli` commands:

```bash
# Initialize router (creates VM)
sudo isle router init

# Start router
sudo isle router up openwrt-isle-router

# Check router status
isle router status

# Script will automatically detect changes and update nginx
```

## Logs

### View Libvirt Hook Logs

```bash
# View hook trigger events
sudo journalctl -t libvirt-hook -f

# View detector execution logs
sudo tail -f /var/log/isle-nginx-detector.log
```

### View Systemd Timer Logs (if using timer)

```bash
# View recent logs
sudo journalctl -u isle-nginx-detector.service -f

# View all logs from today
sudo journalctl -u isle-nginx-detector.service --since today
```

## Approach Comparison

| Aspect | Libvirt Hook (Recommended) | Systemd Timer | Old Docker/mDNS |
|--------|----------------------------|---------------|-----------------|
| Trigger | ⚡ Event-driven | ⏰ Poll every 30s | ⏰ Poll every 30s |
| Latency | ✅ Instant (~100ms) | ⚠️ Up to 30s delay | ⚠️ Up to 30s delay |
| Resource Usage | ✅ Zero when idle | ⚠️ Constant polling | ❌ Container overhead |
| Complexity | ✅ Simple hook | ✅ Simple timer | ❌ Docker + mDNS API |
| Detection Method | ✅ libvirt events | ✅ virsh commands | ❌ mDNS (broken with VLANs) |
| Maintenance | ✅ Self-contained | ✅ Easy to debug | ❌ Multiple components |
| Integration | ✅ Native libvirt | ✅ Works with CLI | ❌ Separate system |

## Architecture

### Event-Driven (Libvirt Hook)

```
┌────────────────────────────────────────────────────────────┐
│  Host System                                                │
│                                                             │
│  ┌──────────────────────────────────────────────┐          │
│  │  OpenWRT Router VM                           │          │
│  │  (starts/stops) → triggers libvirt event     │          │
│  └─────────────────────┬────────────────────────┘          │
│                        ↓ (instant)                          │
│  ┌──────────────────────────────────────────────┐          │
│  │  /etc/libvirt/hooks/qemu                     │          │
│  │  • Receives VM state change event            │          │
│  │  • Calls detect-and-configure-nginx.sh       │          │
│  └─────────────────────┬────────────────────────┘          │
│                        ↓                                    │
│  ┌──────────────────────────────────────────────┐          │
│  │  detect-and-configure-nginx.sh               │          │
│  │                                               │          │
│  │  • Checks: virsh list --all                  │          │
│  │  • Gets MAC: virsh dumpxml                   │          │
│  │  • Looks up IP: arp -n                       │          │
│  │  • Tests: ping router_ip                     │          │
│  │  • Generates: nginx config                   │          │
│  │  • Reloads: nginx -s reload                  │          │
│  └─────────────────────┬────────────────────────┘          │
│                        ↓                                    │
│  ┌──────────────────────────────────────────────┐          │
│  │  /etc/nginx/conf.d/openwrt-fallback.conf     │          │
│  │                                               │          │
│  │  server {                                     │          │
│  │    listen 80 default_server;                 │          │
│  │    proxy_pass http://192.168.100.1:80;       │          │
│  │  }                                            │          │
│  └─────────────────────┬────────────────────────┘          │
│                        ↓                                    │
│  ┌──────────────────────────────────────────────┐          │
│  │  Nginx (on host)                             │          │
│  │  • Proxies unknown .local domains            │          │
│  │  • Routes to OpenWRT router                  │          │
│  └─────────────────────┬────────────────────────┘          │
└────────────────────────┼────────────────────────────────────┘
                         ↓
         ┌───────────────────────────────────┐
         │  OpenWRT Router (VM on vLAN)      │
         │  • Manages mesh applications      │
         │  • Routes .vlan domains            │
         └───────────────────────────────────┘
```

## Benefits

✅ **Event-Driven**: Instant response to router state changes (<100ms)
✅ **Zero Overhead**: No polling, no wasted CPU cycles when idle
✅ **Simple**: Just a bash script + libvirt hook, no Docker complexity
✅ **Fast**: Native execution, no container overhead
✅ **Integrated**: Uses same `virsh` commands as `isle-cli`
✅ **Maintainable**: Easy to read, debug, and modify
✅ **Reliable**: Direct access to libvirt, no socket mounting issues
✅ **Flexible**: Hook, manual, or timer - your choice

## Future Enhancements

Potential improvements:

1. **CLI Integration**: Add `isle router configure-nginx` command that calls this script
2. **Webhooks**: Trigger script automatically when router state changes (via libvirt hooks)
3. **Multiple Routers**: Support detecting and proxying to multiple routers
4. **Health Monitoring**: More sophisticated health checks before enabling proxy
5. **Metrics**: Export router status and proxy health metrics
