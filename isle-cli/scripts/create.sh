#!/bin/bash

# Isle Create Command
# One-command setup: Creates agent, router, and sample app

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$CLI_DIR")"

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Configuration
SAMPLE_APP_NAME="sample"
SAMPLE_DOMAIN="sample.local"  # Using .local for mDNS; nginx will auto-add .isle variant
SAMPLE_APP_DIR="/tmp/isle-sample-app"

show_help() {
    echo -e "${BOLD}Isle Create${NC} - One-Command Complete Setup"
    echo -e ""
    echo -e "╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║                    WHAT THIS DOES                             ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝"
    echo -e ""
    echo -e "The ${CYAN}isle create${NC} command sets up a complete Isle Mesh environment"
    echo -e "with a single command. It will:"
    echo -e ""
    echo -e "  ${GREEN}1.${NC} Start the Isle Agent (unified nginx proxy)"
    echo -e "  ${GREEN}2.${NC} Install mDNS system (service discovery infrastructure)"
    echo -e "  ${GREEN}3.${NC} Initialize and start the OpenWRT Router"
    echo -e "  ${GREEN}4.${NC} Deploy a sample Python app at ${CYAN}${SAMPLE_DOMAIN}${NC}"
    echo -e "      (also accessible via ${CYAN}sample.isle${NC} after join protocol)"
    echo -e ""
    echo -e "The sample app demonstrates how Isle Mesh dual-domain support works"
    echo -e "and provides instructions for removing it and deploying your own apps."
    echo -e ""
    echo -e "╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║                    USAGE                                      ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝"
    echo -e ""
    echo -e "  ${CYAN}isle create${NC}                  Complete setup with defaults"
    echo -e "  ${CYAN}isle create --help${NC}           Show this help message"
    echo -e ""
    echo -e "╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║                    REQUIREMENTS                               ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝"
    echo -e ""
    echo -e "  - Docker installed and running"
    echo -e "  - User in docker group"
    echo -e "  - libvirt/KVM for router VM"
    echo -e "  - Sudo access for router setup"
    echo -e ""
    echo -e "╔═══════════════════════════════════════════════════════════════╗"
    echo -e "║                    AFTER SETUP                                ║"
    echo -e "╚═══════════════════════════════════════════════════════════════╝"
    echo -e ""
    echo -e "Once complete, you can access:"
    echo -e "  ${CYAN}https://${SAMPLE_DOMAIN}${NC}      Sample app with instructions"
    echo -e ""
    echo -e "And manage your isle with:"
    echo -e "  ${CYAN}isle agent status${NC}           View agent status"
    echo -e "  ${CYAN}isle router status${NC}          View router status"
    echo -e "  ${CYAN}isle app help${NC}               Learn about app management"
    echo -e ""
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

log_error() {
    echo -e "${RED}[✗]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_step() {
    echo -e ""
    echo -e "${CYAN}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC} ${BOLD}$1${NC}"
    echo -e "${CYAN}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo -e ""
}

# Check prerequisites
check_prerequisites() {
    log_step "Checking Prerequisites"

    # Check Docker
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed"
        echo ""
        echo "Please install Docker first:"
        echo "  https://docs.docker.com/get-docker/"
        exit 1
    fi
    log_success "Docker is installed"

    # Check if Docker daemon is running
    if ! docker ps &> /dev/null; then
        log_warning "Docker daemon is not accessible or user doesn't have permissions"
        echo ""
        echo "Note: You may need to use sudo for Docker commands, or:"
        echo "  1. Ensure Docker daemon is running"
        echo "  2. Add your user to the docker group: sudo usermod -aG docker \$USER"
        echo "  3. Log out and log back in to apply group changes"
        echo ""
    else
        log_success "Docker daemon is running"
    fi

    # Check for Docker systemd D-Bus issues (common in sandboxed environments)
    log_info "Checking Docker container creation..."
    if ! bash "$SCRIPT_DIR/fix-docker-cgroups.sh" check &> /dev/null; then
        log_warning "Docker has systemd D-Bus communication issues"
        log_info "This commonly occurs in sandboxed environments (VS Code snap, etc.)"
        echo ""
        echo "Docker is configured to use 'systemd' cgroup driver but cannot"
        echo "communicate with systemd's D-Bus. This prevents containers from starting."
        echo ""
        echo "Solution: Switch Docker to use 'cgroupfs' driver instead."
        echo ""
        if [[ -t 0 && "${ISLE_ASSUME_YES:-}" != "1" ]]; then
            read -p "Would you like to automatically fix this? (requires sudo) [Y/n]: " -n 1 -r
            echo ""
        else
            REPLY="Y"; log_info "Auto-fixing Docker cgroup config (non-interactive)"
        fi
        if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
            log_info "Applying Docker configuration fix..."
            if sudo bash "$SCRIPT_DIR/fix-docker-cgroups.sh" fix; then
                log_success "Docker has been fixed and is now working"
            else
                log_error "Failed to fix Docker configuration"
                echo ""
                echo "You may need to:"
                echo "  1. Reboot your system"
                echo "  2. Manually restart Docker: sudo systemctl restart docker"
                echo "  3. Check /var/log/docker.log for errors"
                exit 1
            fi
        else
            log_error "Cannot continue without fixing Docker"
            echo ""
            echo "To fix manually, run:"
            echo "  sudo bash $SCRIPT_DIR/fix-docker-cgroups.sh fix"
            exit 1
        fi
    else
        log_success "Docker container creation is working"
    fi

    # Check libvirt
    if ! command -v virsh &> /dev/null; then
        log_warning "libvirt is not installed (required for router)"
        echo ""
        echo "Install with:"
        echo "  sudo apt-get install qemu-kvm libvirt-daemon-system libvirt-clients"
        echo ""
        if [[ -t 0 ]]; then
            read -p "Do you want to continue without the router? (y/N): " confirm
        else
            confirm=""; log_error "libvirt missing and no terminal — cannot build the router (install qemu-kvm + libvirt)"
        fi
        if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
            exit 1
        fi
        SKIP_ROUTER=true
    else
        log_success "libvirt is installed"
    fi

    # Check sshpass (needed for password-protected router access)
    if ! command -v sshpass &> /dev/null; then
        log_warning "sshpass is not installed (required for password-protected router access)"
        echo ""
        echo "Install with:"
        echo "  sudo apt-get install sshpass"
        echo ""
        log_info "Note: Passwordless SSH will work without sshpass"
    fi

    # Check sudo
    if ! sudo -n true 2>/dev/null; then
        log_info "Sudo access will be needed for router setup"
        log_info "You may be prompted for your password"
    fi

    echo ""
}

# Ensure .isle DNS resolution is configured on the host
# .isle queries are forwarded to the OpenWRT router's dnsmasq, which holds
# the authoritative isle domain mappings. This is the real isle network path —
# NOT localhost resolution (that was the mDNS scaffolding approach).
#
# The router's dnsmasq resolves app.isle → 10.10.0.X (the agent's DHCP lease
# on that device's isle-br-0 macvlan interface).
ensure_isle_dns() {
    local SPLIT_DNS="/etc/dnsmasq.d/split-dns.conf"
    local RESOLVED_CONF="/etc/systemd/resolved.conf.d/split-mdns.conf"
    local changed=false

    # Router IP for DNS forwarding
    # Use the management IP (192.168.1.1) because the host always has a route
    # to it via br-mgmt. The isle subnet IP (10.10.0.1) is only reachable from
    # devices that have a DHCP lease on the isle network.
    local ROUTER_ISLE_IP="${ROUTER_ISLE_IP:-192.168.1.1}"

    # Remove any stale address=/.isle/ localhost resolution (mDNS scaffolding)
    if grep -q 'address=/.isle/' "$SPLIT_DNS" 2>/dev/null; then
        log_info "Removing stale localhost .isle resolution (upgrading to router forwarding)..."
        sed -i '/address=\/.isle\//d' "$SPLIT_DNS"
        changed=true
    fi

    # Forward .isle queries to the OpenWRT router's dnsmasq
    local CURRENT_SERVER
    CURRENT_SERVER=$(grep 'server=/.isle/' "$SPLIT_DNS" 2>/dev/null | head -1 || echo "")

    if [ -z "$CURRENT_SERVER" ]; then
        log_info "Adding .isle DNS forwarding to router at ${ROUTER_ISLE_IP}..."
        echo "server=/.isle/${ROUTER_ISLE_IP}" >> "$SPLIT_DNS"
        changed=true
    elif ! echo "$CURRENT_SERVER" | grep -q "${ROUTER_ISLE_IP}"; then
        # Router IP changed — update the forwarding rule
        log_info "Updating .isle DNS forwarding to ${ROUTER_ISLE_IP}..."
        sed -i "s|server=/.isle/.*|server=/.isle/${ROUTER_ISLE_IP}|" "$SPLIT_DNS"
        changed=true
    fi

    # Add ~isle to systemd-resolved domains if not already present.
    # The drop-in may not exist (fresh machine, or a previous destroy --purge
    # removed it) — create it rather than sed-ing a missing file.
    if ! grep -q '~isle' "$RESOLVED_CONF" 2>/dev/null; then
        log_info "Adding ~isle to systemd-resolved split-DNS domains..."
        mkdir -p "$(dirname "$RESOLVED_CONF")"
        if [ -f "$RESOLVED_CONF" ] && grep -q '^Domains=' "$RESOLVED_CONF"; then
            sed -i 's/^Domains=\(.*\)/Domains=\1 ~isle/' "$RESOLVED_CONF"
        else
            { [ -f "$RESOLVED_CONF" ] || echo "[Resolve]"; echo "Domains=~isle"; } >> "$RESOLVED_CONF"
        fi
        changed=true
    fi

    if [ "$changed" = true ]; then
        systemctl restart dnsmasq 2>/dev/null || true
        systemctl restart systemd-resolved 2>/dev/null || true
        log_success ".isle DNS forwarding configured (router: ${ROUTER_ISLE_IP})"
    else
        log_success ".isle DNS forwarding already configured (router: ${ROUTER_ISLE_IP})"
    fi
}

# Step 1: Create/Start Isle Agent
setup_agent() {
    log_step "Step 1: Setting up Isle Agent"

    # Check if agent is already running
    if docker ps | grep -q isle-agent; then
        log_success "Isle Agent is already running"
        return 0
    fi

    log_info "Starting Isle Agent..."

    # DURABLE REGISTRY: do NOT wipe registered apps on setup. The registry is the
    # platform's memory of which isle-apps are installed on the mesh; bring-up
    # reconciles it. Only initialize the file if it is missing.
    local REGISTRY="/etc/isle-mesh/agent/registry.json"
    if [[ ! -f "$REGISTRY" ]]; then
        mkdir -p "$(dirname "$REGISTRY")"
        echo '{"domains": {}, "subdomains": {}, "apps": {}}' > "$REGISTRY"
    fi

    # Check if agent scripts exist
    if [[ ! -d "$PROJECT_ROOT/isle-agent" ]]; then
        log_error "Isle Agent directory not found"
        echo "Expected location: $PROJECT_ROOT/isle-agent"
        exit 1
    fi

    # Start the agent
    if bash "$SCRIPT_DIR/agent.sh" start; then
        log_success "Isle Agent started successfully"
    else
        log_error "Failed to start Isle Agent"
        exit 1
    fi

    echo ""
}

# Step 2: Install mDNS System
setup_mdns_system() {
    log_step "Step 2: Setting up mDNS System"

    # Check if mDNS system is already running
    if systemctl is-active --quiet mesh-mdns.service 2>/dev/null; then
        log_success "mDNS system is already installed and running"
        return 0
    fi

    log_info "Installing mDNS system infrastructure..."
    log_info "This configures systemd, dnsmasq, and mDNS broadcasting"
    echo ""

    # Install mDNS system
    if bash "$SCRIPT_DIR/mdns.sh" system install; then
        log_success "mDNS system installed successfully"

        # Wait a moment for service to start
        sleep 2

        # Verify it's running
        if systemctl is-active --quiet mesh-mdns.service 2>/dev/null; then
            log_success "mesh-mdns.service is running"
        else
            log_warning "mesh-mdns.service may not be running yet"
            log_info "Check status with: isle mdns system status"
        fi
    else
        log_warning "mDNS system installation failed or incomplete"
        log_info "You can manually install it later with: isle mdns system install"
        log_info "Continuing with setup..."
    fi

    echo ""
}

# Step 3b (self-forming): bridge already-connected, non-ISP ethernet cables into the isle.
# Hotplug covers cables plugged AFTER install; this covers cables already plugged at create
# time, so the isle self-forms with no manual `isle router add-connection`. Never touches the
# ISP uplink or wifi (SSH path). Non-interactive (passes --iface).
add_connected_cables() {
    if [[ "$SKIP_ROUTER" == true ]]; then return 0; fi
    log_step "Step 3b: Bridging connected isle cables (self-forming)"
    local isp_iface eth added=0
    isp_iface="$(ip route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}' | head -1)"
    for eth in $(ls /sys/class/net 2>/dev/null); do
        [[ "$eth" == "lo" ]] && continue
        [[ -d "/sys/class/net/$eth/wireless" ]] && continue           # never wifi (ISP/SSH path)
        [[ -e "/sys/class/net/$eth/device" ]] || continue             # physical NIC only (skip bridges/veth/docker)
        [[ "$eth" == "$isp_iface" ]] && continue                      # never the ISP uplink
        [[ "$(cat "/sys/class/net/$eth/carrier" 2>/dev/null)" == "1" ]] || continue  # cable actually plugged
        if ip -4 addr show "$eth" 2>/dev/null | grep -q 'inet '; then continue; fi   # skip if it already has an IP
        [[ -e "/sys/class/net/$eth/master" ]] && continue             # skip if already bridged
        log_info "Detected connected isle cable '$eth' — bridging into the isle..."
        if sudo bash "$SCRIPT_DIR/router.sh" add-connection --iface "$eth"; then
            log_success "Cable '$eth' added to the isle"
            added=$((added + 1))
        else
            log_warning "Could not add cable '$eth' (add later: isle router add-connection --iface $eth)"
        fi
    done
    [[ $added -eq 0 ]] && log_info "No new isle cables to bridge (plug one in anytime — hotplug adds it)."
    echo ""
}

# Step 3: Create/Start Isle Router
setup_router() {
    if [[ "$SKIP_ROUTER" == true ]]; then
        log_warning "Skipping router setup (libvirt not available)"
        return 0
    fi

    log_step "Step 3: Setting up Isle Router"

    # Check if router is already running
    # Try without sudo first (for users with libvirt group access), then with sudo
    if virsh list --state-running 2>/dev/null | grep -q "openwrt-isle-router" || \
       sudo virsh list --state-running 2>/dev/null | grep -q "openwrt-isle-router"; then
        log_success "Isle Router is already running"
        return 0
    fi

    # Check if router exists but is stopped
    if virsh list --all 2>/dev/null | grep -q "openwrt-isle-router" || \
       sudo virsh list --all 2>/dev/null | grep -q "openwrt-isle-router"; then
        log_info "Router exists but is stopped. Recreating bridges and starting..."
        # Bridge creation + virsh start both need sudo
        if sudo bash "$SCRIPT_DIR/router.sh" up openwrt-isle-router; then
            log_success "Isle Router started successfully"
            return 0
        else
            log_error "Failed to start existing router"
            exit 1
        fi
    fi

    log_info "Initializing new OpenWRT router..."
    log_info "This will take a few minutes..."
    echo ""

    # Initialize router with sudo
    if sudo bash "$SCRIPT_DIR/router.sh" init; then
        log_success "Isle Router initialized and started successfully"
    else
        log_error "Failed to initialize Isle Router"
        exit 1
    fi

    echo ""
}

# Step 4: Create and deploy sample app
setup_sample_app() {
    log_step "Step 4: Setting up Sample Application"

    log_info "Creating sample app directory..."
    rm -rf "$SAMPLE_APP_DIR"
    mkdir -p "$SAMPLE_APP_DIR"

    # Create the sample app files
    log_info "Generating sample application files..."

    # Create app.py with informational content
    cat > "$SAMPLE_APP_DIR/app.py" << 'EOF'
#!/usr/bin/env python3
"""
Isle Mesh Sample Application
Demonstrates how Isle Mesh works and provides setup instructions
"""

from flask import Flask, render_template_string
from datetime import datetime
import os

app = Flask(__name__)

# HTML template with instructions
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Isle Mesh - Sample Application</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            line-height: 1.6;
            color: #333;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 900px;
            margin: 0 auto;
            background: white;
            border-radius: 10px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 40px;
            text-align: center;
        }
        .header h1 {
            font-size: 2.5em;
            margin-bottom: 10px;
        }
        .header p {
            font-size: 1.2em;
            opacity: 0.9;
        }
        .content {
            padding: 40px;
        }
        .section {
            margin-bottom: 40px;
        }
        .section h2 {
            color: #667eea;
            margin-bottom: 15px;
            font-size: 1.8em;
            border-bottom: 3px solid #667eea;
            padding-bottom: 10px;
        }
        .section h3 {
            color: #764ba2;
            margin-top: 20px;
            margin-bottom: 10px;
            font-size: 1.3em;
        }
        .section p, .section li {
            margin-bottom: 10px;
            font-size: 1.1em;
        }
        .section ul {
            margin-left: 30px;
        }
        .code-block {
            background: #f4f4f4;
            border-left: 4px solid #667eea;
            padding: 15px;
            margin: 15px 0;
            font-family: 'Courier New', monospace;
            overflow-x: auto;
        }
        .highlight {
            background: #fff3cd;
            padding: 2px 6px;
            border-radius: 3px;
            font-weight: bold;
        }
        .status-badge {
            display: inline-block;
            background: #28a745;
            color: white;
            padding: 5px 15px;
            border-radius: 20px;
            font-size: 0.9em;
            margin: 10px 0;
        }
        .warning {
            background: #fff3cd;
            border-left: 4px solid #ffc107;
            padding: 15px;
            margin: 15px 0;
        }
        .footer {
            background: #f8f9fa;
            padding: 20px 40px;
            text-align: center;
            color: #666;
        }
        a {
            color: #667eea;
            text-decoration: none;
        }
        a:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🏝️ Welcome to Isle Mesh!</h1>
            <p>Your mesh networking environment is up and running</p>
            <span class="status-badge">✓ Sample App Active</span>
        </div>

        <div class="content">
            <div class="section">
                <h2>🎉 Congratulations!</h2>
                <p>You've successfully set up your Isle Mesh environment. This sample application is running at <span class="highlight">{{ domain }}</span> to demonstrate how the system works.</p>
                <p><strong>Current time:</strong> {{ timestamp }}</p>
            </div>

            <div class="section">
                <h2>🔍 What is Isle Mesh?</h2>
                <p>Isle Mesh is a zero-configuration mesh networking system for containerized applications. It provides:</p>
                <ul>
                    <li><strong>Isolated Networks:</strong> Each "isle" is a separate virtual network for security</li>
                    <li><strong>Automatic Service Discovery:</strong> Apps find each other via mDNS (.local domains)</li>
                    <li><strong>SSL Termination:</strong> Automatic HTTPS for all your services</li>
                    <li><strong>OpenWRT Router:</strong> Virtual router for network isolation and VLAN support</li>
                    <li><strong>Unified Proxy:</strong> Single nginx container (isle-agent) serves all apps</li>
                </ul>
            </div>

            <div class="section">
                <h2>🏗️ Your Current Setup</h2>
                <p>The <code>isle create</code> command set up three components:</p>

                <h3>1. Isle Agent (Unified Proxy)</h3>
                <p>A single nginx container that serves all your mesh applications. Each app registers its config fragment, and the agent merges them together.</p>
                <div class="code-block">
$ isle agent status
</div>

                <h3>2. Isle Router (OpenWRT VM)</h3>
                <p>A virtual OpenWRT router that provides network isolation, VLAN support, and DHCP for your isles.</p>
                <div class="code-block">
$ isle router status
</div>

                <h3>3. Sample App (This Page!)</h3>
                <p>A simple Python Flask app running at <strong>{{ domain }}</strong> to demonstrate the system.</p>
            </div>

            <div class="section">
                <h2>🚀 Next Steps: Deploy Your Own App</h2>

                <h3>Step 1: Remove This Sample App</h3>
                <p>When you're ready to deploy your own application, remove this sample:</p>
                <div class="code-block">
# Stop and remove the sample app<br>
cd {{ app_dir }}<br>
isle app down -v<br>
<br>
# Or just delete the directory<br>
rm -rf {{ app_dir }}
</div>

                <h3>Step 2: Create Your Own App</h3>
                <p>You can either initialize a new app or convert an existing docker-compose project:</p>
                <div class="code-block">
# Option A: Initialize a new mesh app<br>
isle app init -d myapp.isle<br>
cd mesh-myapp.isle<br>
isle app up --build<br>
<br>
# Option B: Convert existing docker-compose<br>
isle app scaffold docker-compose.yml -d myapp.isle<br>
cd mesh-myapp.isle<br>
isle app up
</div>

                <h3>Step 3: Access Your App</h3>
                <p>Your app will be available at the domain you specified (e.g., <code>https://myapp.isle</code>).</p>
            </div>

            <div class="section">
                <h2>📚 Useful Commands</h2>
                <div class="code-block">
# View all registered apps<br>
isle agent status<br>
<br>
# Check router and network status<br>
isle router status<br>
<br>
# View app logs<br>
isle app logs<br>
<br>
# List running services<br>
isle app ps<br>
<br>
# Discover all .local domains<br>
isle router discover<br>
<br>
# Get help on any command<br>
isle help<br>
isle app help<br>
isle agent help<br>
isle router help
</div>
            </div>

            <div class="warning">
                <strong>⚠️ Note:</strong> This is a sample/demo application. It's meant to help you understand how Isle Mesh works. Feel free to explore the code in <code>{{ app_dir }}</code> and modify it as needed!
            </div>
        </div>

        <div class="footer">
            <p>Isle Mesh - Zero-configuration mesh networking for containerized applications</p>
            <p style="margin-top: 10px;">Learn more: <a href="#">Documentation</a> | <a href="#">GitHub</a></p>
        </div>
    </div>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(
        HTML_TEMPLATE,
        domain=os.getenv('DOMAIN', 'sample.isle'),
        timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        app_dir=os.getenv('APP_DIR', '/tmp/isle-sample-app')
    )

@app.route('/health')
def health():
    return {'status': 'healthy', 'service': 'isle-sample-app'}

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
EOF

    # Create requirements.txt
    cat > "$SAMPLE_APP_DIR/requirements.txt" << 'EOF'
Flask==3.0.0
Werkzeug==3.0.1
EOF

    # Create Dockerfile
    cat > "$SAMPLE_APP_DIR/Dockerfile" << 'EOF'
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

ENV PORT=5000
ENV DOMAIN=sample.isle

EXPOSE 5000

CMD ["python", "app.py"]
EOF

    # Create .env file
    cat > "$SAMPLE_APP_DIR/.env" << EOF
DOMAIN=${SAMPLE_DOMAIN}
APP_DIR=${SAMPLE_APP_DIR}
PORT=5000
EOF

    # Create docker-compose.yml
    cat > "$SAMPLE_APP_DIR/docker-compose.yml" << EOF
version: '3.8'

services:
  sample:
    build: .
    container_name: isle-sample-app
    environment:
      - DOMAIN=${SAMPLE_DOMAIN}
      - APP_DIR=${SAMPLE_APP_DIR}
      - PORT=5000
    restart: unless-stopped
    labels:
      - "isle.mesh.enable=true"
      - "mesh.domain=${SAMPLE_DOMAIN}"
      - "isle.mesh.port=5000"
    networks:
      - isle-agent-net

networks:
  isle-agent-net:
    external: true
EOF

    log_success "Sample app files created"

    # Start the sample app using docker-compose directly
    log_info "Starting sample app..."
    cd "$SAMPLE_APP_DIR"

    # Use docker compose (new) or docker-compose (legacy) depending on what's available
    DOCKER_COMPOSE_CMD="docker compose"
    if ! command -v docker &> /dev/null || ! docker compose version &> /dev/null 2>&1; then
        if command -v docker-compose &> /dev/null; then
            DOCKER_COMPOSE_CMD="docker-compose"
        else
            log_error "Neither 'docker compose' nor 'docker-compose' is available"
            exit 1
        fi
    fi

    if $DOCKER_COMPOSE_CMD up -d --build; then
        log_success "Sample app deployed successfully"
    else
        log_error "Failed to deploy sample app"
        exit 1
    fi

    # Register sample app with the agent
    log_info "Registering sample app with Isle Agent..."

    # 1. Write sample app entry to registry.json
    if bash "$SCRIPT_DIR/agent.sh" register \
        --name "${SAMPLE_APP_NAME}" \
        --domain "${SAMPLE_DOMAIN}" \
        --container "isle-sample-app" \
        --port 5000 \
        --protocol http; then
        log_success "Sample app registered in agent registry"
    else
        log_warning "Could not register sample app, but it is running"
    fi

    # 2. Add sample.local to mDNS broadcast list
    if bash "$SCRIPT_DIR/mdns.sh" domain add "${SAMPLE_DOMAIN}" 2>/dev/null; then
        log_success "Added ${SAMPLE_DOMAIN} to mDNS broadcast"
    else
        log_warning "Could not add ${SAMPLE_DOMAIN} to mDNS (may already exist)"
    fi

    # 3. Generate self-signed SSL cert for sample.local
    local ssl_cert_dir="/etc/isle-mesh/agent/ssl/certs"
    local ssl_key_dir="/etc/isle-mesh/agent/ssl/keys"
    mkdir -p "$ssl_cert_dir" "$ssl_key_dir"

    if [[ ! -f "${ssl_cert_dir}/${SAMPLE_DOMAIN}.crt" ]]; then
        log_info "Generating self-signed SSL certificate for ${SAMPLE_DOMAIN}..."
        if openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
            -keyout "${ssl_key_dir}/${SAMPLE_DOMAIN}.key" \
            -out "${ssl_cert_dir}/${SAMPLE_DOMAIN}.crt" \
            -subj "/CN=${SAMPLE_DOMAIN}" \
            -addext "subjectAltName=DNS:${SAMPLE_DOMAIN},DNS:${SAMPLE_DOMAIN%.local}.isle" 2>/dev/null; then
            log_success "Generated SSL certificate for ${SAMPLE_DOMAIN}"
        else
            log_warning "Could not generate SSL cert for ${SAMPLE_DOMAIN}"
        fi
    else
        log_info "SSL certificate for ${SAMPLE_DOMAIN} already exists"
    fi

    echo ""
}

# Show completion message
show_completion() {
    echo ""
    echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║                 🎉 SETUP COMPLETE! 🎉                         ║${NC}"
    echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${BOLD}Your Isle Mesh environment is ready!${NC}"
    echo ""
    echo -e "${CYAN}Components Running:${NC}"
    echo -e "  ✓ Isle Agent (unified proxy)"
    echo -e "  ✓ mDNS System (service discovery)"
    echo -e "  ✓ Isle Router (OpenWRT VM)"
    echo -e "  ✓ Sample Application"
    echo ""
    echo -e "${CYAN}Access Your Sample App:${NC}"
    echo -e "  ${BOLD}https://${SAMPLE_DOMAIN}${NC} (mDNS, HTTPS)"
    echo -e "  ${BOLD}https://sample.isle${NC} (after join protocol completes)"
    echo ""
    echo -e "${CYAN}View Status:${NC}"
    echo -e "  isle agent status        View agent and registered apps"
    echo -e "  isle router status       View router and network info"
    echo ""
    echo -e "${CYAN}Next Steps:${NC}"
    echo -e "  1. Visit ${BOLD}https://${SAMPLE_DOMAIN}${NC} for detailed instructions"
    echo -e "  2. When ready, remove the sample app and deploy your own"
    echo -e "  3. Run ${BOLD}isle help${NC} to see all available commands"
    echo ""
    echo -e "${YELLOW}Note:${NC} The sample app code is at: ${SAMPLE_APP_DIR}"
    echo ""
}

# Main execution
# Turn discovery mode ON after install (default-on until next reboot) so the operator
# can plug devices/cables in one-by-one with no extra steps, then finalize. The boot_id
# stamp in the session makes it end automatically at the next reboot.
setup_discovery() {
    log_step "Enabling Device Discovery Mode"
    # always-available host: user services survive reboot with no interactive login
    command -v loginctl >/dev/null 2>&1 && sudo loginctl enable-linger "${SUDO_USER:-$USER}" >/dev/null 2>&1 \
        && log_info "Linger enabled (always-available: no login needed after reboot)" || true
    if [[ -f "$SCRIPT_DIR/lib/discovery-mode.sh" ]] && command -v jq >/dev/null 2>&1; then
        # shellcheck source=/dev/null
        source "$SCRIPT_DIR/lib/discovery-mode.sh"
        local sid; sid="$(dm_start 0)"
        log_success "Discovery mode ON (session ${sid}) — plug in devices/cables now; no extra steps."
        log_info "It auto-ends on reboot. Toggle anytime: isle discovery start | stop"
    else
        log_warning "Could not enable discovery mode (jq or discovery lib missing)."
    fi
    echo ""
}

main() {
    case "${1:-}" in
        help|--help|-h)
            show_help
            exit 0
            ;;
        *)
            echo ""
            echo -e "${BOLD}╔═══════════════════════════════════════════════════════════════╗${NC}"
            echo -e "${BOLD}║          Isle Mesh - Complete Environment Setup               ║${NC}"
            echo -e "${BOLD}╚═══════════════════════════════════════════════════════════════╝${NC}"
            echo ""

            check_prerequisites
            ensure_isle_dns
            setup_mdns_system
            # Router before agent: the agent's macvlan network needs the
            # isle-br-0 bridge (created by router setup) as its parent, and
            # its DHCP lease comes from the router. On a fresh/purged host
            # the old order failed at docker network creation.
            setup_router
            add_connected_cables
            setup_agent
            setup_sample_app
            setup_discovery
            show_completion
            ;;
    esac
}

main "$@"
