#!/bin/bash
set -e

echo "🧹 Uninstalling IsleMesh mDNS Setup..."

SERVICE_NAME="mesh-mdns.service"
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"
ENV_PATH="/etc/mesh-mdns.conf"
SCRIPT_DIR="/usr/local/bin/isle-mesh"
RESOLVED_CONF="/etc/systemd/resolved.conf.d/split-mdns.conf"
DNSMASQ_CONF="/etc/dnsmasq.d/split-dns.conf"

# 1. Stop and disable systemd service
if systemctl is-enabled --quiet "$SERVICE_NAME"; then
  echo "⛔ Disabling and stopping $SERVICE_NAME..."
  sudo systemctl disable --now "$SERVICE_NAME"
fi

# 2. Remove systemd service file
if [[ -f "$SERVICE_PATH" ]]; then
  echo "🗑️ Removing systemd unit: $SERVICE_PATH"
  sudo rm -f "$SERVICE_PATH"
fi

# 3. Remove environment file
if [[ -f "$ENV_PATH" ]]; then
  echo "🗑️ Removing environment file: $ENV_PATH"
  sudo rm -f "$ENV_PATH"
fi

# 4. Remove all scripts
if [[ -d "$SCRIPT_DIR" ]]; then
  echo "🗑️ Removing setup scripts at $SCRIPT_DIR"
  sudo rm -rf "$SCRIPT_DIR"
fi

# 5. Remove dnsmasq config (optional)
if [[ -f "$DNSMASQ_CONF" ]]; then
  echo "🧽 Removing dnsmasq split DNS config: $DNSMASQ_CONF"
  sudo rm -f "$DNSMASQ_CONF"
  echo "🔁 Restarting dnsmasq..."
  sudo systemctl restart dnsmasq || true
fi

# 6. Remove systemd-resolved split config (optional)
if [[ -f "$RESOLVED_CONF" ]]; then
  echo "🧽 Removing systemd-resolved split DNS config: $RESOLVED_CONF"
  sudo rm -f "$RESOLVED_CONF"
  echo "🔁 Restarting systemd-resolved..."
  sudo systemctl restart systemd-resolved
fi

# 7. Reload systemd
echo "🔃 Reloading systemd daemon..."
sudo systemctl daemon-reexec

# 8. Restore /etc/resolv.conf (if using systemd stub)
echo "🔁 Resetting /etc/resolv.conf to systemd stub..."
sudo ln -sf /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf

echo "✅ Uninstall complete. All mesh mDNS components removed."
