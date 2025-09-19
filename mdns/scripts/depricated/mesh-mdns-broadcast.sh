#!/bin/bash
# mesh-mdns-broadcast.sh
# This file is generally copied to the local system and then run using a systemd-service
# as well as a pre-set environment file for congiuration.

# === Config from environment ===
APP_NAME="${APP_NAME:-mesh-app}"
CERT_AND_KEY_NAME="${CERT_AND_KEY_NAME:-$APP_NAME}"
BASE_URL="${APP_NAME}.local"
SUBDOMAINS="${SUBDOMAINS:-backend,frontend}"
MAX_CONCURRENT="${MAX_CONCURRENT:-1}"
TARGET_IP="${TARGET_IP:-127.0.0.1}"

echo "APP_NAME : $APP_NAME"
echo "BASE_URL : $BASE_URL"
echo "SUBDOMAINS : $SUBDOMAINS"
echo "MAX_CONCURRENT : $MAX_CONCURRENT"
echo "TARGET_IP : $TARGET_IP"

# === Build domain list ===
IFS=',' read -ra SUBS <<< "$SUBDOMAINS"

HOSTS=("$BASE_URL")
for sub in "${SUBS[@]}"; do
  HOSTS+=("${sub}.${BASE_URL}")
done

echo "🌐 Broadcasting mDNS for: ${HOSTS[*]}"
echo "🔁 Using MAX_CONCURRENT=$MAX_CONCURRENT"

# Broadcast each using avahi-publish -a -R (re-announcing, backgrounded)
for fqdn in "${FQDNS[@]}"; do
  echo "📡 Publishing: $fqdn → 127.0.0.1"
  avahi-publish -a -R "$fqdn" 127.0.0.1 &
done

# Wait for all background jobs (so systemd doesn't kill this shell)
wait

# Leave one blank line at the end to ensure compatibility with legacy systems/.
# Keep container alive
tail -f /dev/null