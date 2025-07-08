#!/bin/bash
set -e

echo "🧹 Resetting system: Uninstalling IsleMesh mDNS and restoring default Ubuntu networking..."

SERVICE_NAME="mesh-mdns.service"
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"
ENV_PATH="/etc/mesh-mdns.conf"
SCRIPT_DIR="/usr/local/bin/isle-mesh"
RESOLVED_CONF="/etc/systemd/resolved.conf.d/split-mdns.conf"
DNSMASQ_CONF="/etc/dnsmasq.d/split-dns.conf"
INSTALL_FLAG="/etc/isle-mesh/.installed"

# 1. Stop and disable mesh-mDNS systemd service
if systemctl is-enabled --quiet "$SERVICE_NAME"; then
  echo "⛔ Disabling and stopping $SERVICE_NAME..."
  sudo systemctl disable --now "$SERVICE_NAME"
fi

# 2. Remove service, env, and install marker
echo "🗑️ Cleaning service files and flags..."
sudo rm -f "$SERVICE_PATH"
sudo rm -f "$ENV_PATH"
sudo rm -rf "$SCRIPT_DIR"
sudo rm -f "$INSTALL_FLAG"

# 3. Remove dnsmasq config
if [[ -f "$DNSMASQ_CONF" ]]; then
  echo "🧽 Removing dnsmasq config..."
  sudo rm -f "$DNSMASQ_CONF"
  sudo systemctl restart dnsmasq || true
fi

# 4. Remove resolved split config
if [[ -f "$RESOLVED_CONF" ]]; then
  echo "🧽 Removing resolved config..."
  sudo rm -f "$RESOLVED_CONF"
  sudo systemctl restart systemd-resolved || true
fi

# 5. Detect and stop any wpa_supplicant@*.service
WIFI_INTERFACE=$(iw dev | awk '$1=="Interface"{print $2}' | head -n 1)

if [[ -n "$WIFI_INTERFACE" ]]; then
  echo "📡 Disabling wpa_supplicant@$WIFI_INTERFACE.service..."
  sudo systemctl stop wpa_supplicant@"$WIFI_INTERFACE".service || true
  sudo systemctl disable wpa_supplicant@"$WIFI_INTERFACE".service || true
else
  echo "⚠️ No wireless interface found to disable wpa_supplicant service."
fi

# 6. Disable systemd-networkd and resolved
echo "🔻 Disabling systemd-networkd and resolved..."
sudo systemctl disable --now systemd-networkd || true
sudo systemctl disable --now systemd-resolved || true

# 7. Remove any lingering .network files
echo "🧹 Removing systemd .network definitions..."
sudo rm -f /etc/systemd/network/*.network

# 8. Re-enable and restart NetworkManager
echo "🔁 Re-enabling NetworkManager and restoring /etc/resolv.conf..."
sudo systemctl enable --now NetworkManager || true
sudo systemctl restart NetworkManager || true

# 9. Fix resolv.conf if pointing to systemd-resolved
if grep -q "nameserver 127.0.0.53" /etc/resolv.conf; then
  echo "🔧 Cleaning up /etc/resolv.conf: removing systemd-resolved nameserver and inserting public resolvers..."
  sudo sed -i '/^nameserver 127\.0\.0\.53$/d' /etc/resolv.conf
  echo -e "nameserver 1.1.1.1\nnameserver 8.8.8.8" | sudo tee -a /etc/resolv.conf > /dev/null
fi


# 10. Optionally purge avahi and dnsmasq
echo "📦 Removing dnsmasq and avahi..."
sudo apt purge -y dnsmasq avahi-daemon || true
sudo apt autoremove -y

# 11. Reload systemd
echo "🔃 Reloading systemd daemon..."
sudo systemctl daemon-reexec

# 12. Show final network status
echo "✅ Mesh networking stack removed and system reverted to NetworkManager."
echo "🔎 Final device status:"
nmcli device status