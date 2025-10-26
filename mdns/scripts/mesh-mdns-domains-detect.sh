#!/bin/bash
# mesh-mdns-domains-detect.sh
# Detect domains from isle-mesh.yml and docker-compose files

set -e

DOMAIN_LIST_FILE="${DOMAIN_LIST_FILE:-/usr/local/etc/mesh-mdns-domains.list}"
MESH_CONFIG="${1:-./isle-mesh.yml}"
COMPOSE_FILE="${2:-./docker-compose.mesh-app.yml}"
MODE="${3:-append}"  # append|replace

DETECTED_DOMAINS=()
BASE_DOMAIN=""

# Parse isle-mesh.yml if it exists
if [ -f "$MESH_CONFIG" ]; then
  echo "📄 Parsing $MESH_CONFIG..."

  # Extract base domain (mesh.domain)
  BASE_DOMAIN=$(grep -E '^\s+domain:' "$MESH_CONFIG" | awk '{print $2}' | tr -d '"' || echo "")

  if [ -n "$BASE_DOMAIN" ]; then
    DETECTED_DOMAINS+=("$BASE_DOMAIN")

    # Extract service subdomains and build FQDN
    while IFS= read -r line; do
      SERVICE_NAME=$(echo "$line" | awk -F: '{print $1}' | xargs)

      # Get subdomain for this service
      SUBDOMAIN=$(awk "/^  ${SERVICE_NAME}:/,/^  [a-z]/ {if (/subdomain:/) print \$2}" "$MESH_CONFIG" | tr -d '"' | head -n1)

      if [ -n "$SUBDOMAIN" ]; then
        DETECTED_DOMAINS+=("${SUBDOMAIN}.${BASE_DOMAIN}")
      fi
    done < <(grep -E '^  [a-z][a-z0-9_-]+:$' "$MESH_CONFIG" | grep -A10 'services:' || true)
  fi
fi

# Parse docker-compose for mesh.domain and mesh.subdomain labels
if [ -f "$COMPOSE_FILE" ]; then
  echo "📄 Parsing $COMPOSE_FILE..."

  # Extract base domain from docker-compose labels if not already found
  if [ -z "$BASE_DOMAIN" ]; then
    BASE_DOMAIN=$(grep -E '^\s+mesh\.domain:' "$COMPOSE_FILE" | awk '{print $2}' | tr -d '"' || echo "")
    if [ -n "$BASE_DOMAIN" ]; then
      DETECTED_DOMAINS+=("$BASE_DOMAIN")
      echo "  ✓ Using domain from docker-compose: $BASE_DOMAIN"
    fi
  fi

  # Use default domain if still not found
  if [ -z "$BASE_DOMAIN" ]; then
    BASE_DOMAIN="mesh-app.local"
    DETECTED_DOMAINS+=("$BASE_DOMAIN")
    echo "  ✓ Using default domain: $BASE_DOMAIN"
  fi

  # Extract mesh.subdomain labels and construct domains
  while IFS= read -r subdomain; do
    subdomain=$(echo "$subdomain" | tr -d '"' | xargs)
    if [ -n "$subdomain" ] && [ -n "$BASE_DOMAIN" ]; then
      DETECTED_DOMAINS+=("${subdomain}.${BASE_DOMAIN}")
    fi
  done < <(grep -E 'mesh\.subdomain:' "$COMPOSE_FILE" | awk '{print $2}' || true)

  # Also detect service names and use them as subdomains if no explicit subdomain label
  if command -v yq &> /dev/null; then
    while IFS= read -r service; do
      # Skip proxy services
      if [[ "$service" =~ proxy ]] || [[ "$service" =~ nginx ]]; then
        continue
      fi

      # Check if service has explicit subdomain label
      SUBDOMAIN=$(yq eval ".services.\"$service\".labels.\"mesh.subdomain\" // \"\"" "$COMPOSE_FILE" 2>/dev/null)

      # If no explicit label, use service name as subdomain
      if [ -z "$SUBDOMAIN" ] || [ "$SUBDOMAIN" = "null" ]; then
        SUBDOMAIN="$service"
      fi

      if [ -n "$SUBDOMAIN" ]; then
        DETECTED_DOMAINS+=("${SUBDOMAIN}.${BASE_DOMAIN}")
      fi
    done < <(yq eval '.services | keys | .[]' "$COMPOSE_FILE" 2>/dev/null || true)
  fi
fi

# Remove duplicates
UNIQUE_DOMAINS=($(printf '%s\n' "${DETECTED_DOMAINS[@]}" | sort -u))

if [ ${#UNIQUE_DOMAINS[@]} -eq 0 ]; then
  echo "⚠️  No domains detected"
  exit 0
fi

echo "🔍 Detected ${#UNIQUE_DOMAINS[@]} domain(s):"
printf '  - %s\n' "${UNIQUE_DOMAINS[@]}"

# Write to file
sudo mkdir -p "$(dirname "$DOMAIN_LIST_FILE")"

if [ "$MODE" = "replace" ]; then
  printf '%s\n' "${UNIQUE_DOMAINS[@]}" | sudo tee "$DOMAIN_LIST_FILE" > /dev/null
  echo "✅ Replaced domain list"
else
  # Append mode - only add new domains
  ADDED=0
  for domain in "${UNIQUE_DOMAINS[@]}"; do
    if ! grep -Fxq "$domain" "$DOMAIN_LIST_FILE" 2>/dev/null; then
      echo "$domain" | sudo tee -a "$DOMAIN_LIST_FILE" > /dev/null
      ((ADDED++))
    fi
  done
  echo "✅ Added $ADDED new domain(s)"
fi
