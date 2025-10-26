#!/bin/bash
# mesh-mdns-domains-add.sh
# Add a domain to the mDNS broadcast list

set -e

DOMAIN_LIST_FILE="${DOMAIN_LIST_FILE:-/usr/local/etc/mesh-mdns-domains.list}"
DOMAIN="$1"

if [ -z "$DOMAIN" ]; then
  echo "Usage: $0 <domain>"
  exit 1
fi

# Validate domain format (basic check for .local TLD)
if [[ ! "$DOMAIN" =~ \.local$ ]]; then
  echo "⚠️  Warning: Domain '$DOMAIN' does not end with .local"
  echo "mDNS typically requires .local TLD. Continue anyway? (y/N)"
  read -r response
  [[ ! "$response" =~ ^[Yy]$ ]] && exit 0
fi

# Create file if it doesn't exist
sudo mkdir -p "$(dirname "$DOMAIN_LIST_FILE")"
sudo touch "$DOMAIN_LIST_FILE"

# Check if domain already exists
if grep -Fxq "$DOMAIN" "$DOMAIN_LIST_FILE" 2>/dev/null; then
  echo "Domain '$DOMAIN' already exists in broadcast list"
  exit 0
fi

# Add domain
echo "$DOMAIN" | sudo tee -a "$DOMAIN_LIST_FILE" > /dev/null
echo "✅ Added domain: $DOMAIN"
