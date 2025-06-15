#!/bin/bash
set -e

echo "🔍 Detecting upstream DNS server..."
UPSTREAM_DNS=$(resolvectl status | grep 'Current DNS Server' | awk '{print $4}')
if [[ -z "$UPSTREAM_DNS" ]]; then
  echo "❌ No upstream DNS server detected. Exiting."
  exit 1
fi
echo "🌐 Detected upstream DNS: $UPSTREAM_DNS"

echo "📦 Installing dnsmasq if needed..."
sudo apt-get update -qq
sudo apt-get install -y dnsmasq

echo "🧹 Cleaning any old dnsmasq config..."
sudo rm -f /etc/dnsmasq.d/split-dns.conf

# 🔎 Pick an unused 127.0.0.X address
echo "🔎 Searching for unused 127.0.0.X IP..."
for i in {2..254}; do
  if ! ip addr show lo | grep -q "127.0.0.$i"; then
    DNSMASQ_IP="127.0.0.$i"
    echo "✅ Selected unused loopback alias: $DNSMASQ_IP"
    break
  fi
done

if [[ -z "$DNSMASQ_IP" ]]; then
  echo "❌ Failed to find an available 127.0.0.X address."
  exit 1
fi

# Add alias to loopback if not already present
if ! ip addr show lo | grep -q "$DNSMASQ_IP"; then
  echo "➕ Adding alias $DNSMASQ_IP to loopback..."
  sudo ip addr add "$DNSMASQ_IP/8" dev lo
fi

echo "⚙️ Writing dnsmasq split DNS config..."
cat <<EOF | sudo tee /etc/dnsmasq.d/split-dns.conf > /dev/null
interface=lo
listen-address=$DNSMASQ_IP
bind-interfaces
address=/.local/$DNSMASQ_IP
address=/.mesh/$DNSMASQ_IP
no-resolv
server=$UPSTREAM_DNS
EOF

echo "🔧 Ensuring systemd-resolved is active..."
sudo systemctl enable systemd-resolved
sudo systemctl start systemd-resolved

echo "⚙️ Configuring systemd-resolved to forward .local and .mesh to $DNSMASQ_IP..."
sudo mkdir -p /etc/systemd/resolved.conf.d
sudo tee /etc/systemd/resolved.conf.d/split-mdns.conf > /dev/null <<EOF
[Resolve]
DNS=$DNSMASQ_IP
Domains=~local ~mesh
EOF

echo "🔄 Restarting dnsmasq..."
sudo systemctl restart dnsmasq

echo "🔄 Restarting systemd-resolved..."
sudo systemctl restart systemd-resolved

echo "🔧 Updating /etc/resolv.conf to use systemd-resolved stub..."
sudo ln -sf /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf

echo "✅ Split DNS for .local and .mesh is now active via $DNSMASQ_IP"

echo "🔎 Testing .local/.mesh routing via dig..."
dig +short @${DNSMASQ_IP} mesh-app.local || echo "❌ Failed: dig could not resolve mesh-app.local"
dig +short mesh-app.local || echo "ℹ️ Check: systemd-resolved may not be forwarding as expected"