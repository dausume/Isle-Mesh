#!/bin/bash
# mesh-mdns-broadcast.sh
# This file is generally copied to the local system and then run using a systemd-service
# as well as a pre-set environment file for configuration.

# === Config from environment ===
DOMAIN_LIST_FILE="${DOMAIN_LIST_FILE:-/usr/local/etc/mesh-mdns-domains.list}"
TARGET_IP="${TARGET_IP:-127.0.0.1}"
MAX_CONCURRENT="${MAX_CONCURRENT:-0}"  # 0 = unlimited
VERBOSE="${VERBOSE:-false}"

# Fallback to legacy SUBDOMAINS method if domain list file doesn't exist
APP_NAME="${APP_NAME:-mesh-app}"
BASE_URL="${APP_NAME}.local"
SUBDOMAINS="${SUBDOMAINS:-}"

[ "$VERBOSE" = "true" ] && echo "TARGET_IP: $TARGET_IP"
[ "$VERBOSE" = "true" ] && echo "MAX_CONCURRENT: $MAX_CONCURRENT"
[ "$VERBOSE" = "true" ] && echo "DOMAIN_LIST_FILE: $DOMAIN_LIST_FILE"

# === Load domains ===
HOSTS=()

if [ -f "$DOMAIN_LIST_FILE" ]; then
  # Read from domain list file
  while IFS= read -r domain; do
    # Skip empty lines and comments
    [[ -n "$domain" && ! "$domain" =~ ^# ]] && HOSTS+=("$domain")
  done < "$DOMAIN_LIST_FILE"
  [ "$VERBOSE" = "true" ] && echo "Loaded ${#HOSTS[@]} domain(s) from $DOMAIN_LIST_FILE"
elif [ -n "$SUBDOMAINS" ]; then
  # Legacy fallback: build from SUBDOMAINS env var
  IFS=',' read -ra SUBS <<< "$SUBDOMAINS"
  HOSTS=("$BASE_URL")
  for sub in "${SUBS[@]}"; do
    HOSTS+=("${sub}.${BASE_URL}")
  done
  [ "$VERBOSE" = "true" ] && echo "Built ${#HOSTS[@]} domain(s) from SUBDOMAINS (legacy mode)"
else
  echo "❌ No domains configured. Create $DOMAIN_LIST_FILE or set SUBDOMAINS"
  exit 1
fi

# === Broadcast domains ===
ACTIVE_JOBS=0

for fqdn in "${HOSTS[@]}"; do
  # Wait if we've hit the concurrent limit
  if [ "$MAX_CONCURRENT" -gt 0 ]; then
    while [ "$ACTIVE_JOBS" -ge "$MAX_CONCURRENT" ]; do
      wait -n 2>/dev/null || true
      ((ACTIVE_JOBS--))
    done
  fi

  # Start broadcast (no echo in loop - minimal overhead)
  avahi-publish -a -R "$fqdn" "$TARGET_IP" &
  ((ACTIVE_JOBS++))
done

[ "$VERBOSE" = "true" ] && echo "Broadcasting ${#HOSTS[@]} domain(s) to $TARGET_IP"

# Wait for all background jobs
wait

# Keep container alive
exec tail -f /dev/null