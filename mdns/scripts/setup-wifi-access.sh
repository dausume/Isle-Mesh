#!/bin/bash
# setup-wifi-access.sh
#
# This ensures that systemd-networkd can still be used to access your
# normal ISP (Internet Service Provider) while still granting you
# the complete flexibility of being able to also utilize mdns
# and Isle-Mesh networking capabilities.

set -e

echo "📶 Wi-Fi Setup Script (systemd + wpa_supplicant)"

# Auto-detect wireless interface
WIFI_INTERFACE=$(iw dev 2>/dev/null | awk '$1=="Interface"{print $2}' | head -n1 || true)

# Fallback 1: Try nmcli if iw failed
if [[ -z "$WIFI_INTERFACE" ]] && command -v nmcli &>/dev/null; then
  WIFI_INTERFACE=$(nmcli -t -f DEVICE,TYPE device 2>/dev/null | grep ':wifi$' | cut -d: -f1 | head -n1 || true)
fi

# Fallback 2: Try ip link for wireless interfaces (wl* pattern)
if [[ -z "$WIFI_INTERFACE" ]] && command -v ip &>/dev/null; then
  WIFI_INTERFACE=$(ip link show 2>/dev/null | grep -oP '^\d+: \Kwl[^:]+' | head -n1 || true)
fi

if [[ -z "$WIFI_INTERFACE" ]]; then
  echo "ℹ️  No wireless interface detected - skipping Wi-Fi setup."
  echo "   (Mesh-mdns will continue without Wi-Fi configuration)"
  exit 0
fi

echo "📡 Detected wireless interface: $WIFI_INTERFACE"

# Get SSID from Network Manager (currently connected network)
SSID=$(nmcli -t -f active,ssid dev wifi 2>/dev/null | grep '^yes' | cut -d: -f2 || true)

if [[ -z "$SSID" ]]; then
  echo "ℹ️  No active Wi-Fi connection found in Network Manager - skipping Wi-Fi setup."
  echo "   Connect to Wi-Fi first if you want to configure it for systemd-networkd."
  echo "   (Mesh-mdns will continue without Wi-Fi configuration)"
  exit 0
fi

echo "📡 Found active connection to: $SSID"

# Get password from Network Manager connection profile
PSK=$(sudo nmcli -s -g 802-11-wireless-security.psk connection show "$SSID" 2>/dev/null || true)

if [[ -z "$PSK" ]]; then
  echo "ℹ️  Could not retrieve password from Network Manager for SSID: $SSID"
  echo "   The connection may not have a saved password or may use a different authentication method."
  echo "   (Mesh-mdns will continue without Wi-Fi configuration)"
  exit 0
fi

echo "✅ Successfully retrieved credentials from Network Manager"

# Create wpa_supplicant config
# The file should be named wpa_supplicant-<interface>.conf
# This will allow systemd-networkd to use your normal Wi-Fi connection from your ISP.
# Example: wpa_supplicant-wlp2s0.conf
WPA_CONF="/etc/wpa_supplicant/wpa_supplicant-${WIFI_INTERFACE}.conf"
echo "🔧 Writing wpa_supplicant config to $WPA_CONF..."
sudo tee "$WPA_CONF" > /dev/null <<EOF
ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev
update_config=1
country=US

network={{
    ssid="\"$SSID\""
    psk="\"$PSK\""
}}
EOF

# Enable wpa_supplicant service
echo "🚀 Enabling wpa_supplicant@${WIFI_INTERFACE}.service..."
# Use nsenter to run systemctl on the host system (needed when running from container)
if [ -f /.dockerenv ]; then
  # Running in Docker - use nsenter to access host's systemd
  nsenter --target 1 --mount --uts --ipc --net --pid -- systemctl enable wpa_supplicant@"$WIFI_INTERFACE".service
  nsenter --target 1 --mount --uts --ipc --net --pid -- systemctl start wpa_supplicant@"$WIFI_INTERFACE".service
else
  # Running directly on host
  sudo systemctl enable wpa_supplicant@"$WIFI_INTERFACE".service
  sudo systemctl start wpa_supplicant@"$WIFI_INTERFACE".service
fi

# Use 'resolvectl dns wlp2s0' (or whatever your isp interface is besides wlp2s0) - to check if the link is successful.
# Use 'networkctl status' to check if the network has become routable and online.

echo "✅ wpa_supplicant configured. You may now restart systemd-networkd if needed."