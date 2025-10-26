#!/bin/bash
# mesh-mdns-domains-list.sh
# List all domains in the mDNS broadcast list

DOMAIN_LIST_FILE="${DOMAIN_LIST_FILE:-/usr/local/etc/mesh-mdns-domains.list}"

if [ ! -f "$DOMAIN_LIST_FILE" ]; then
  echo "No domains configured (file not found: $DOMAIN_LIST_FILE)"
  exit 0
fi

DOMAIN_COUNT=$(grep -c ^ "$DOMAIN_LIST_FILE" 2>/dev/null || echo "0")

if [ "$DOMAIN_COUNT" -eq 0 ]; then
  echo "No domains configured"
  exit 0
fi

echo "📡 mDNS Broadcast Domains ($DOMAIN_COUNT):"
cat "$DOMAIN_LIST_FILE"
