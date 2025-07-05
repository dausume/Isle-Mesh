#!/bin/bash
# setup-wifi-access.sh
# This ensures that systemd-networkd can still be used to access your
# normal ISP (Internet Service Provider) while still granting you
# the complete flexibility of being able to also utilize mdns
# and Isle-Mesh networking capabilities.

set -e

echo "📶 Wi-Fi Setup Script (systemd + wpa_supplicant)"

# Auto-detect wireless interface
WIFI_INTERFACE=$(iw dev | awk '$1=="Interface"{print $2}' | head -n1)

if [[ -z "$WIFI_INTERFACE" ]]; then
  echo "❌ No wireless interface found. Exiting."
  exit 1
fi

echo "📡 Detected wireless interface: $WIFI_INTERFACE"

# Prompt for SSID and password
read -rp "Enter Wi-Fi SSID: " SSID
read -rsp "Enter Wi-Fi Password: " PSK
echo ""

# Create wpa_supplicant config
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
sudo systemctl enable wpa_supplicant@"$WIFI_INTERFACE".service
sudo systemctl start wpa_supplicant@"$WIFI_INTERFACE".service

# Use 'resolvectl dns wlp2s0' (or whatever your isp interface is besides wlp2s0) - to check if the link is successful.
# Use 'networkctl status' to check if the network has become routable and online.

echo "✅ wpa_supplicant configured. You may now restart systemd-networkd if needed."