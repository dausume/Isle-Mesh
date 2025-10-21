#!/bin/bash
# Setup systemd-networkd
# Detects the primary interface being used currently and shifts the user from using 
# the default NetworkManager to using systemd.

set -e

echo "detecting the default route interface and DNS..."

# Call the ip route query, get the line from it that contains both default and dhcp, return the 5th item in the list which is always the interface name.
INTERFACE=$(ip route | grep default | awk '{print $5}' | head -n 1)

# Print the interface name to make certain it is valid.
echo "INTERFACE : $INTERFACE"

# Parse the resolv.conf to get the current active DNS server (nameserver), this should work for ANY resolver being used, since all of them rely on this in ubuntu.
DNS=$(grep '^nameserver' /etc/resolv.conf | head -n 1 | awk '{print $2}')

if [[ -z "$INTERFACE" || -z "$DNS" ]]; then
    echo "Failed to detect active interface or DNS server.  Must have an internet connection for this install process until we implement Mesh-App Install network functionality.  Aborting."
    exit 1
fi

echo "Detected DNS providing interface : $INTERFACE"
echo "Detected DNS Server IP: $DNS"

NETWORKD_DIR="/etc/systemd/network"

# Create network folder if it does not exist
sudo mkdir -p "$NETWORKD_DIR"

# Creating network for normal internet access while choosing not to send mDNS to the wifi router since
# we know many ISPs block it anyways
# Let's just assume we always avoid the DNS router to simplify things.
# The wpa_supplicant config created in setup-wifi-access.sh will handle the wifi connection.
cat <<EOF | sudo tee "$NETWORKD_DIR/20-wifi.network"
[Match]
Name=$INTERFACE

[Network]
DHCP=yes
MulticastDNS=no
DNS=$DNS
EOF

# Enable systemd-networkd and systemd-resolved
# Use nsenter to run systemctl on the host system (needed when running from container)
if [ -f /.dockerenv ]; then
  # Running in Docker - use nsenter to access host's systemd
  nsenter --target 1 --mount --uts --ipc --net --pid -- systemctl enable --now systemd-networkd
  nsenter --target 1 --mount --uts --ipc --net --pid -- systemctl enable --now systemd-resolved
else
  # Running directly on host
  sudo systemctl enable --now systemd-networkd
  sudo systemctl enable --now systemd-resolved
fi

# Link the stub resolver to /etc/resolv.conf so that normal DNS resolution works.
# First remove the existing file/symlink, then create the new symlink
# Handle the case where /etc/resolv.conf might be a bind mount (common in containers)
if mountpoint -q /etc/resolv.conf 2>/dev/null; then
  echo "⚠️ /etc/resolv.conf is a mount point, unmounting first..."
  sudo umount /etc/resolv.conf || true
fi
# Use truncate instead of rm to handle busy files, then unlink
sudo truncate -s 0 /etc/resolv.conf 2>/dev/null || true
sudo unlink /etc/resolv.conf 2>/dev/null || sudo rm -f /etc/resolv.conf
sudo ln -sf /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf

echo "⏳ Waiting for systemd-networkd to assign DNS..."
sleep 10  # Give time for DHCP+DNS to finish starting up.

# Test if normal DNS is able to resolve using the dns server using dig.
TEST_DOMAIN="example.com"
echo "🔎 Testing DNS resolution for $TEST_DOMAIN..."
if dig +short "$TEST_DOMAIN" > /dev/null; then
  echo "✅ DNS resolution working as expected."
else
  echo "❌ DNS resolution failed. Reverting to NetworkManager."
  if [ -f /.dockerenv ]; then
    nsenter --target 1 --mount --uts --ipc --net --pid -- systemctl enable --now NetworkManager
  else
    sudo systemctl enable --now NetworkManager
  fi
  sudo rm -f /etc/systemd/network/20-wifi.network
  sudo dhclient "$INTERFACE"
  exit 1
fi
