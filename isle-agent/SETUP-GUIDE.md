# Isle-Agent Setup Guide

## Step-by-Step Setup Process

### Step 1: Set Up Permissions

First, you need to configure permissions for the isle-agent:

```bash
sudo isle permissions agent
```

This will:
- Create the `isle-mesh` group
- Add your user to the group
- Set up proper permissions for `/etc/isle-mesh/agent/`

After running this command, you need to either:
- Log out and log back in, OR
- Run: `newgrp isle-mesh` in your current terminal

### Step 2: Start the Isle-Agent

Once permissions are set up, start the isle-agent:

```bash
sudo isle agent start
```

This will:
- Initialize `/etc/isle-mesh/agent/` directory structure
- Create base nginx configuration with health endpoint
- Create empty registry for tracking apps
- Start the isle-agent Docker container
- Create the `isle-agent-net` Docker network

### Step 3: Verify the Agent is Running

Check the agent status:

```bash
isle agent status
```

You should see output like:
```
✓ Isle-agent container is running
✓ Nginx is healthy
✓ Config is valid

Registered apps: 0
No apps registered yet
```

### Step 4: Test the Health Endpoint

The isle-agent has a default health check page accessible at:

**HTTP:** http://localhost/health

Test it with curl:

```bash
curl http://localhost/health
```

You should see:
```
isle-agent healthy
```

### Step 5: Test the Default Landing Page

When no apps are registered, the default page shows:

**HTTP:** http://localhost/

```bash
curl http://localhost/
```

You should see:
```
No mesh apps registered
```

## Understanding the Default Configuration

### Health Endpoint

- **URL**: http://localhost/health
- **Purpose**: Verify isle-agent is running
- **Response**: "isle-agent healthy"
- **Used by**: Docker healthcheck

### Default Server

- **URL**: http://localhost/ (any path except /health)
- **Purpose**: Catch-all for unregistered domains
- **Response**: "No mesh apps registered"
- **Behavior**: Returns 404 when no apps are registered

### Directory Structure

After initialization, you'll have:

```
/etc/isle-mesh/agent/
├── docker-compose.yml          # Agent container definition
├── nginx.conf                  # Master nginx config
├── registry.json               # Domain registry (empty)
├── configs/                    # App fragments (empty)
├── ssl/
│   ├── certs/                  # SSL certificates
│   └── keys/                   # SSL keys
└── logs/
    ├── access.log
    └── error.log
```

### Docker Network

The agent creates:

- **Network**: `isle-agent-net`
- **Subnet**: 172.20.0.0/16
- **Purpose**: Apps connect here to reach the agent

## Next: Register Your First App

Once the agent is running, you can register the local-agent-app:

```bash
cd /home/detts/Isle-Mesh/test-mesh-apps/local-agent-app

# Set as current project
isle app config set-project .

# Generate SSL certificates (if needed)
# isle ssl generate local-app.local

# Start the app (will auto-register with agent)
isle app up --build
```

The app will:
1. Start backend and frontend services
2. Auto-detect `proxy.type: isle-agent` in isle-mesh.yml
3. Generate nginx fragment
4. Register with isle-agent
5. Reload agent config
6. Register with mDNS
7. Become accessible at:
   - https://app.local-app.local (frontend)
   - https://api.local-app.local (backend)

## Troubleshooting

### Permission denied errors

```bash
# Re-run permissions setup
sudo isle permissions agent

# Reload group membership
newgrp isle-mesh
```

### Agent won't start

```bash
# Check if ports 80/443 are already in use
sudo netstat -tulpn | grep :80
sudo netstat -tulpn | grep :443

# View agent logs
isle agent logs

# Check Docker is running
sudo systemctl status docker
```

### Can't access health endpoint

```bash
# Verify agent container is running
docker ps | grep isle-agent

# Check nginx is responding inside container
docker exec isle-agent nginx -t

# View nginx error logs
docker exec isle-agent cat /var/log/nginx/error.log
```

### Network issues

```bash
# Verify isle-agent-net exists
docker network ls | grep isle-agent

# Recreate if missing
docker network create \
  --driver bridge \
  --subnet 172.20.0.0/16 \
  isle-agent-net
```

## Advanced Configuration

### View Registry

```bash
cat /etc/isle-mesh/agent/registry.json | jq .
```

### Manually Reload Agent

```bash
sudo isle agent reload
```

### View All Registered Apps

```bash
isle agent summary
```

### Test Nginx Configuration

```bash
isle agent test
```

## Browser Testing (Once Apps Are Registered)

After registering local-agent-app, you can test in your browser:

### 1. Health Check
- URL: http://localhost/health
- Expected: "isle-agent healthy"

### 2. Frontend App
- URL: https://app.local-app.local
- Expected: "✅ Local Agent App Active" page
- Note: You'll get SSL warnings (self-signed cert)

### 3. Backend API
- URL: https://api.local-app.local
- Expected: mTLS connection (requires client cert)
- Note: Browser won't work for this (needs client cert)

For backend testing, use curl with certs:
```bash
curl -k \
  --cert /path/to/ssl/certs/local-app.local.crt \
  --key /path/to/ssl/keys/local-app.local.key \
  https://api.local-app.local
```

## Cleaning Up

### Remove a Specific App

```bash
# Remove fragment
sudo rm /etc/isle-mesh/agent/configs/local-app.conf

# Reload agent
sudo isle agent reload
```

### Stop Agent

```bash
sudo isle agent stop
```

### Completely Remove Agent

```bash
# Remove agent but keep configs
sudo isle agent destroy

# Remove everything including configs
sudo isle agent destroy --full
```
