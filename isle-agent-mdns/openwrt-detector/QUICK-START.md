# Quick Start Guide - OpenWRT Router Detection

Get nginx fallback proxy configured with event-driven detection in 5 minutes.

## Prerequisites

- `virsh` / libvirt installed (for router management)
- `nginx` installed on host
- OpenWRT router VM running (or ability to start one)

## Step 1: Verify Prerequisites

```bash
# Check if virsh is available
which virsh

# Check if you can run virsh commands
virsh list --all

# Check if nginx is installed
which nginx
```

### If you need libvirt:
```bash
sudo apt-get install qemu-kvm libvirt-daemon-system libvirt-clients
sudo usermod -aG libvirt $USER
# Log out and log back in
```

### If you need nginx:
```bash
sudo apt-get install nginx
```

## Step 2: Make Sure You Have a Router

```bash
# Check if router exists
isle router list

# If no router, initialize one
sudo isle router init

# Check status
isle router status
```

## Step 3: Install Event-Driven Detection (Recommended)

```bash
cd /home/dustin/Desktop/IsleMesh/isle-agent-mdns/openwrt-detector

# Install libvirt hook for automatic detection
sudo ./install-libvirt-hook.sh
```

**Expected output:**
```
╔═══════════════════════════════════════════════════════════════╗
║     Install Libvirt Hook for Router Detection                ║
╚═══════════════════════════════════════════════════════════════╝

[INFO] Creating hook script: /etc/libvirt/hooks/qemu
[✓] Hook script created: /etc/libvirt/hooks/qemu
[INFO] Restarting libvirtd to load hook...
[✓] Libvirtd restarted successfully

╔═══════════════════════════════════════════════════════════════╗
║                    Installation Complete                      ║
╚═══════════════════════════════════════════════════════════════╝

✅ Libvirt hook installed successfully

The nginx configuration will now automatically update when:
  • Router VM starts
  • Router VM stops
  • Router VM shuts down
```

**Alternative: Run detection script manually once:**
```bash
sudo ./detect-and-configure-nginx.sh
```

## Step 4: Verify the Configuration

```bash
# Check the generated nginx config
sudo cat /etc/nginx/conf.d/openwrt-fallback.conf

# Test nginx configuration
sudo nginx -t

# Check nginx status
sudo systemctl status nginx
```

## Step 5: Test the Event-Driven Detection

```bash
# Stop the router (this will trigger the hook)
sudo isle router down openwrt-isle-router

# Check logs to see the hook fired
sudo journalctl -t libvirt-hook -n 20

# Check that nginx config was updated (should show router stopped)
sudo cat /etc/nginx/conf.d/openwrt-fallback.conf | head -10

# Start the router again (triggers hook again)
sudo isle router up openwrt-isle-router

# Wait a moment for router to boot
sleep 10

# Check logs again
sudo journalctl -t libvirt-hook -n 20

# Check nginx config (should show router running with IP)
sudo cat /etc/nginx/conf.d/openwrt-fallback.conf | head -15
```

## Step 6: Test the Proxy (Optional)

```bash
# Test if nginx is proxying (will forward to router)
curl -v http://localhost/
```

## Step 7: View Logs

```bash
# View hook trigger events
sudo journalctl -t libvirt-hook -f

# View detector execution logs
sudo tail -f /var/log/isle-nginx-detector.log
```

## Test Scenarios

### Scenario 1: Router Running → Stop Router (Instant Detection)

```bash
# Check current config (should show router enabled)
sudo cat /etc/nginx/conf.d/openwrt-fallback.conf | head -20

# Stop router (hook fires instantly)
sudo isle router down openwrt-isle-router

# Check logs immediately (no 30s wait!)
sudo journalctl -t libvirt-hook -n 5

# Check config updated instantly
sudo cat /etc/nginx/conf.d/openwrt-fallback.conf | head -20
```

### Scenario 2: No Router → Start Router (Instant Detection)

```bash
# Start router (hook fires on start event)
sudo isle router up openwrt-isle-router

# Check logs immediately
sudo journalctl -t libvirt-hook -n 5

# Wait briefly for router to get IP (~10s)
sleep 10

# Check config (should show router enabled with IP)
sudo cat /etc/nginx/conf.d/openwrt-fallback.conf | head -20
```

## Troubleshooting

### Script says "Router VM not found"

```bash
# Check if router exists
isle router list

# If not, initialize one
sudo isle router init
```

### Script says "No permission to access libvirt"

```bash
# Add yourself to libvirt group
sudo usermod -aG libvirt $USER

# Log out and log back in
```

### Script says "Router is running but not reachable"

```bash
# Check router status
isle router status

# Wait a moment (router might still be booting)
sleep 10

# Try again
sudo ./detect-and-configure-nginx.sh
```

### Nginx fails to reload

```bash
# Check nginx syntax
sudo nginx -t

# Check nginx error log
sudo tail -f /var/log/nginx/error.log

# Check if nginx is running
sudo systemctl status nginx
```

## Configuration Options

You can customize the script with environment variables:

```bash
# Change router VM name
export ROUTER_VM_NAME="my-custom-router"

# Change nginx config location
export NGINX_CONFIG_PATH="/etc/nginx/sites-enabled/openwrt-fallback.conf"

# Disable nginx proxy
export NGINX_ENABLED="false"

# Run script
sudo -E ./detect-and-configure-nginx.sh
```

## Architecture

```
┌────────────────────────────────────────┐
│  Host System                            │
│                                         │
│  ┌──────────────────────────────────┐  │
│  │  Systemd Timer / Cron            │  │
│  │  (every 30s)                     │  │
│  └────────────┬─────────────────────┘  │
│               ↓                         │
│  ┌──────────────────────────────────┐  │
│  │  detect-and-configure-nginx.sh   │  │
│  │                                   │  │
│  │  1. Check: virsh list --all      │  │
│  │  2. Get MAC: virsh dumpxml       │  │
│  │  3. Get IP: arp -n               │  │
│  │  4. Test: ping router_ip         │  │
│  │  5. Generate nginx config        │  │
│  │  6. Reload: nginx -s reload      │  │
│  └────────────┬─────────────────────┘  │
│               ↓                         │
│  ┌──────────────────────────────────┐  │
│  │  Nginx (/etc/nginx/conf.d/)      │  │
│  │  - Proxies unknown .local        │  │
│  │  - Routes to OpenWRT router      │  │
│  └────────────┬─────────────────────┘  │
└───────────────┼─────────────────────────┘
                ↓
    ┌───────────────────────────┐
    │  OpenWRT Router (VM)      │
    │  - Manages vLAN apps      │
    │  - Routes .vlan domains   │
    └───────────────────────────┘
```

## Benefits of This Approach

✅ **Instant**: Event-driven detection, no polling delays (<100ms response)
✅ **Efficient**: Zero CPU usage when idle, triggers only on actual state changes
✅ **Simple**: Just a bash script + libvirt hook, no Docker complexity
✅ **Fast**: Native execution, no container overhead
✅ **Integrated**: Uses same commands as `isle-cli`
✅ **Automatic**: Updates nginx immediately when router state changes
✅ **Reliable**: Direct access to libvirt and nginx

## Next Steps

1. **Integrate with CLI**: Consider adding `isle router configure-nginx` command
2. **Monitor logs**: Check systemd journal or cron logs periodically
3. **Customize**: Adjust nginx config template for your needs
4. **Expand**: Add support for multiple routers or custom health checks

## Clean Up

### Uninstall libvirt hook:

```bash
cd /home/dustin/Desktop/IsleMesh/isle-agent-mdns/openwrt-detector
sudo ./uninstall-libvirt-hook.sh
```

### Remove nginx config:

```bash
sudo rm /etc/nginx/conf.d/openwrt-fallback.conf
sudo nginx -s reload
```

That's it! You now have automatic event-driven OpenWRT router detection and nginx configuration.
