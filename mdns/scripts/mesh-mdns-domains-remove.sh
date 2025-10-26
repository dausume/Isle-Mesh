#!/bin/bash
# mesh-mdns-domains-remove.sh
# Remove a domain from the mDNS broadcast list

set -e

DOMAIN_LIST_FILE="${DOMAIN_LIST_FILE:-/usr/local/etc/mesh-mdns-domains.list}"
DOMAIN="$1"

if [ -z "$DOMAIN" ]; then
  echo "Usage: $0 <domain>"
  exit 1
fi

if [ ! -f "$DOMAIN_LIST_FILE" ]; then
  echo "❌ Domain list file not found: $DOMAIN_LIST_FILE"
  exit 1
fi

# Check if domain exists
if ! grep -Fxq "$DOMAIN" "$DOMAIN_LIST_FILE"; then
  echo "Domain '$DOMAIN' not found in broadcast list"
  exit 0
fi

# Remove domain using sed (avoids creating temp file)
sudo sed -i "/^${DOMAIN//\./\\.}\$/d" "$DOMAIN_LIST_FILE"
echo "✅ Removed domain: $DOMAIN"
