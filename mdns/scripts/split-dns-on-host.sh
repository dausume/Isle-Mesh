#!/bin/bash
# split-dns-on-host.sh
# Provides split DNS capabilities to allow the usage of ISP (Internet Service Provider)
# Based DNS alongside the DNS of our custom implemented mesh network.
set -e

echo "🔍 Detecting upstream DNS server..."
UPSTREAM_DNS=$(resolvectl status | grep 'Current DNS Server' | awk '{print $4}' | head -n1)
if [[ -z "$UPSTREAM_DNS" ]] && [ "$(cat /etc/polari/install-mode 2>/dev/null)" != offline ]; then
  echo "❌ No upstream DNS server detected. Exiting."
  exit 1
fi
echo "🌐 Detected upstream DNS: $UPSTREAM_DNS"

echo "📦 Installing dnsmasq if needed..."
if [ "$(cat /etc/polari/install-mode 2>/dev/null)" = offline ]; then
  for package in dnsmasq; do
    [ "$(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null)" = installed ] || {
      echo "offline apt/: missing $package; stage the complete medium first" >&2; exit 1;
    }
  done
elif [ -f /.dockerenv ]; then
  # Running in Docker - use nsenter to install on host
  nsenter --target 1 --mount --uts --ipc --net --pid -- apt-get update -qq
  nsenter --target 1 --mount --uts --ipc --net --pid -- apt-get install -y dnsmasq
else
  # Running directly on host
  sudo apt-get update -qq
  sudo apt-get install -y dnsmasq
fi

echo "🧹 Cleaning any old dnsmasq config..."
sudo rm -f /etc/dnsmasq.d/split-dns.conf

# 🔎 Pick an unused 127.0.0.X address - loopback addresses for virtual IPs hosted locally
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

# Configure dnsmasq for split DNS
echo "⚙️ Writing dnsmasq split DNS config..."
cat <<EOF | sudo tee /etc/dnsmasq.d/split-dns.conf > /dev/null
interface=lo
listen-address=$DNSMASQ_IP
bind-interfaces
address=/.local/$DNSMASQ_IP
address=/.mesh/$DNSMASQ_IP
no-resolv
${UPSTREAM_DNS:+server=$UPSTREAM_DNS}
EOF

# Ensure dnsmasq is enabled and started -> Should already be active if using systemd-networkd, setup
# once already in the switch-to-systemd-networkd.sh script.
#echo "🔧 Ensuring systemd-resolved is active..."
#sudo systemctl enable systemd-resolved
#sudo systemctl start systemd-resolved
MESH_DOMAINS=("local" "mesh")
# Build " ~local ~mesh ..."
ROUTE_DOMAINS="$(printf ' ~%s' "${MESH_DOMAINS[@]}")"
ROUTE_DOMAINS="${ROUTE_DOMAINS# }"

# Configure systemd-resolved to forward .local and .mesh to dnsmasq
# Instead of setting domains statically, we should use the MESH_DOMAINS array to customizability
# for configuration.  We need to substitute the ~local ~mesh part with a dynamic generation from the array.
# Backup version if this does not work: Domains=~local ~mesh 

echo "⚙️ Configuring systemd-resolved to forward .local and .mesh to $DNSMASQ_IP..."
sudo mkdir -p /etc/systemd/resolved.conf.d
sudo tee /etc/systemd/resolved.conf.d/split-mdns.conf > /dev/null <<EOF
[Resolve]
DNS=$DNSMASQ_IP
Domains=$ROUTE_DOMAINS
EOF

echo "🔄 Enabling and starting dnsmasq..."
if [ -f /.dockerenv ]; then
  # Running in Docker - use nsenter to access host's systemd
  nsenter --target 1 --mount --uts --ipc --net --pid -- systemctl enable dnsmasq
  nsenter --target 1 --mount --uts --ipc --net --pid -- systemctl restart dnsmasq
else
  # Running directly on host
  sudo systemctl enable dnsmasq
  sudo systemctl restart dnsmasq
fi

echo "🔄 Restarting systemd-resolved..."
if [ -f /.dockerenv ]; then
  # Running in Docker - use nsenter to access host's systemd
  nsenter --target 1 --mount --uts --ipc --net --pid -- systemctl restart systemd-resolved
else
  # Running directly on host
  sudo systemctl restart systemd-resolved
fi

echo "🔧 Updating /etc/resolv.conf to use systemd-resolved stub..."
sudo ln -sf /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf

echo "✅ Split DNS for .local and .mesh is now active over loopback IP : $DNSMASQ_IP"

echo "🔎 Testing .local/.mesh routing via dig..."
dig +short @${DNSMASQ_IP} mesh-app.local || echo "❌ Failed: dig could not resolve mesh-app.local"
dig +short mesh-app.local || echo "ℹ️ Check: systemd-resolved may not be forwarding as expected"