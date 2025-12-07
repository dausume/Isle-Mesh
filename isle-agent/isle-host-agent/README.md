# Isle Host Agent

The **Isle Host Agent** is a lightweight systemd service that **extends the existing mesh-mdns infrastructure** to support agent-specific functionality.

## Purpose

- **Leverages Existing mDNS**: Uses the established mesh-mdns broadcast system
- **Adds Relay Mode**: Can relay mDNS info to isle-agent-sync container
- **Configurable Modes**: Switch between broadcast-only, relay-only, or both
- **No Duplication**: Builds on top of existing mdns scripts, doesn't reimplement

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│ Host Machine                                            │
│                                                         │
│  ┌───────────────────────────────────────────────────┐ │
│  │ isle-host-agent (systemd service)                 │ │
│  │                                                   │ │
│  │  Mode: broadcast (default)                        │ │
│  │  └─> Uses existing mesh-mdns-broadcast.sh        │ │
│  │                                                   │ │
│  │  Mode: relay                                      │ │
│  │  └─> Relays domains to isle-agent-sync via HTTP  │ │
│  │                                                   │ │
│  │  Mode: both                                       │ │
│  │  └─> Broadcasts mDNS + Relays to sync            │ │
│  └───────────────────────────────────────────────────┘ │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

## Components

### 1. Relay Script
**File**: `isle-host-agent-relay.sh`

- Wrapper around existing mesh-mdns system
- Supports multiple modes (broadcast, relay, both)
- Relays mDNS info to isle-agent-sync via HTTP POST

### 2. Systemd Service
**File**: `isle-host-agent.service`

- Runs the relay script as a systemd service
- Auto-restarts on failure
- Depends on `avahi-daemon.service`

### 3. Configuration File
**File**: `host-agent.conf`

- Configures mode, relay endpoint, and intervals
- Uses same domain list as mesh-mdns (`/usr/local/etc/mesh-mdns-domains.list`)

## Installation

### Prerequisites

```bash
# Install avahi-daemon
sudo apt-get update
sudo apt-get install -y avahi-daemon avahi-utils

# Start and enable avahi-daemon
sudo systemctl start avahi-daemon
sudo systemctl enable avahi-daemon
```

### Install the Service

```bash
# Copy the broadcast script
sudo mkdir -p /usr/local/bin/isle-mesh
sudo cp isle-host-agent-broadcast.sh /usr/local/bin/isle-mesh/
sudo chmod +x /usr/local/bin/isle-mesh/isle-host-agent-broadcast.sh

# Copy the config file
sudo mkdir -p /etc/isle-mesh/agent
sudo cp host-agent.conf /etc/isle-mesh/agent/

# Create default domain list
sudo tee /etc/isle-mesh/agent/agent-domains.list > /dev/null <<EOF
# Isle Agent mDNS Domains
agent.local
EOF

# Copy systemd service
sudo cp isle-host-agent.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Enable and start the service
sudo systemctl enable isle-host-agent
sudo systemctl start isle-host-agent
```

## Usage

### Start the Service

```bash
sudo systemctl start isle-host-agent
```

### Stop the Service

```bash
sudo systemctl stop isle-host-agent
```

### Check Status

```bash
sudo systemctl status isle-host-agent
```

### View Logs

```bash
# View recent logs
sudo journalctl -u isle-host-agent -n 50

# Follow logs in real-time
sudo journalctl -u isle-host-agent -f
```

### Add Domains

Edit the domain list file:

```bash
sudo nano /etc/isle-mesh/agent/agent-domains.list
```

Add one domain per line:

```
# Isle Agent mDNS Domains
agent.local
api.agent.local
dashboard.agent.local
```

Then restart the service:

```bash
sudo systemctl restart isle-host-agent
```

## Testing

### Test mDNS Broadcasting

From another machine on the same network:

```bash
# Discover mDNS services
avahi-browse -a

# Resolve a specific domain
avahi-resolve -n agent.local

# Ping the agent
ping agent.local
```

### Test from the Same Host

```bash
# Check that avahi-publish is running
ps aux | grep avahi-publish

# Resolve locally
avahi-resolve -n agent.local
```

## Troubleshooting

### Service Won't Start

```bash
# Check if avahi-daemon is running
sudo systemctl status avahi-daemon

# Check for errors in logs
sudo journalctl -u isle-host-agent -n 100
```

### Domains Not Resolving

```bash
# Check if broadcasts are active
ps aux | grep avahi-publish

# Check firewall (mDNS uses UDP port 5353)
sudo ufw status
sudo ufw allow 5353/udp

# Test avahi-publish manually
avahi-publish -a -R test.local 127.0.0.1
# Then from another terminal:
avahi-resolve -n test.local
```

### Domain List File Missing

The service will create a default domain list if missing, but you can manually create it:

```bash
sudo mkdir -p /etc/isle-mesh/agent
sudo tee /etc/isle-mesh/agent/agent-domains.list > /dev/null <<EOF
agent.local
EOF
```

## Integration with Other Components

The Isle Host Agent works alongside:

- **isle-agent-sync**: Python container that receives mDNS from other devices
- **isle-vlan-agent**: Nginx container that proxies requests to apps

Together, these three components form the complete Isle Agent architecture.

## Configuration Reference

### Environment Variables (host-agent.conf)

| Variable | Default | Description |
|----------|---------|-------------|
| `TARGET_IP` | `127.0.0.1` | IP address to broadcast for domains |
| `MAX_CONCURRENT` | `0` | Max concurrent avahi-publish processes (0=unlimited) |
| `VERBOSE` | `false` | Enable verbose logging |
| `AGENT_NAME` | `isle-agent` | Name for logging |
| `DOMAIN_LIST_FILE` | `/etc/isle-mesh/agent/agent-domains.list` | Path to domain list |

### Domain List Format

The domain list file (`agent-domains.list`) supports:
- One domain per line
- Comments starting with `#`
- Empty lines (ignored)

Example:

```
# Main agent domain
agent.local

# Additional subdomains
api.agent.local
dashboard.agent.local

# App-specific domains
app1.local
app2.local
```

## Security Considerations

The systemd service includes security hardening:
- `ProtectSystem=strict`: Prevents writing to most of the filesystem
- `ProtectHome=yes`: Prevents access to user home directories
- `PrivateTmp=yes`: Uses isolated /tmp
- `NoNewPrivileges=yes`: Prevents privilege escalation

## See Also

- **isle-agent-sync**: `/home/detts/Isle-Mesh/isle-agent/isle-agent-sync/README.md`
- **isle-vlan-agent**: `/home/detts/Isle-Mesh/isle-agent/isle-vlan-agent/README.md`
- **Avahi Documentation**: https://www.avahi.org/
