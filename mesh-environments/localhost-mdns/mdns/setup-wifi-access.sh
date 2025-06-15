#!/bin/bash
set -e

echo "📶 Wi-Fi Setup Script (systemd + wpa_supplicant)"

# Prompt for SSID and password
read -rp "Enter Wi-Fi SSID: " SSID
read -rsp "Enter Wi-Fi Password: " PSK
echo ""

# Create wpa_supplicant config
WPA_CONF="/etc/wpa_supplicant/wpa_supplicant-wlan0.conf"
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

# Enable wpa_supplicant service for wlan0
echo "🚀 Enabling wpa_supplicant@wlan0.service..."
sudo systemctl enable wpa_supplicant@wlan0.service
sudo systemctl start wpa_supplicant@wlan0.service

# Use 'resolvectl dns wlp2s0' - to check if the link is successful.
# Use 'networkctl status' to check if the network has become routable and online.

echo "✅ wpa_supplicant configured. You may now restart systemd-networkd if needed."