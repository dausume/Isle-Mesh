#!/bin/bash
# Generate ssl.env.conf from docker-compose.mesh-app.yml and isle-mesh.yml
# Usage: generate-ssl-env-config.sh [mesh-app-directory]

set -e

# Get directory (default to current directory)
MESH_DIR="${1:-.}"
MESH_DIR=$(realpath "$MESH_DIR")

# Files to read from
COMPOSE_FILE="$MESH_DIR/docker-compose.mesh-app.yml"
ISLE_MESH_FILE="$MESH_DIR/isle-mesh.yml"
OUTPUT_FILE="$MESH_DIR/config/ssl.env.conf"

# Validate files exist
if [ ! -f "$COMPOSE_FILE" ]; then
  echo "Error: docker-compose.mesh-app.yml not found in $MESH_DIR"
  exit 1
fi

if [ ! -f "$ISLE_MESH_FILE" ]; then
  echo "Error: isle-mesh.yml not found in $MESH_DIR"
  exit 1
fi

# Check for yq
if ! command -v yq &> /dev/null; then
  echo "Error: yq is required but not installed"
  echo "Install with: sudo apt install yq (or brew install yq on macOS)"
  exit 1
fi

echo "Generating ssl.env.conf from mesh configuration..."

# Extract values from isle-mesh.yml
DOMAIN=$(yq eval '.mesh.domain' "$ISLE_MESH_FILE")
PROJECT_NAME=$(yq eval '.mesh.name' "$ISLE_MESH_FILE")
BASE_CERT=$(yq eval '.ssl.base_cert' "$ISLE_MESH_FILE")
BASE_KEY=$(yq eval '.ssl.base_key' "$ISLE_MESH_FILE")
CERT_DIR=$(yq eval '.ssl.cert_dir' "$ISLE_MESH_FILE")
KEY_DIR=$(yq eval '.ssl.key_dir' "$ISLE_MESH_FILE")

# Extract cert name (without extension)
CERT_AND_KEY_NAME="${BASE_CERT%.crt}"

# Extract subdomains from isle-mesh.yml services
SUBDOMAINS=$(yq eval '.services | keys | .[]' "$ISLE_MESH_FILE" | tr '\n' ' ' | sed 's/ $//')

# Create config directory if it doesn't exist
mkdir -p "$(dirname "$OUTPUT_FILE")"

# Generate ssl.env.conf
cat > "$OUTPUT_FILE" <<EOF
# SSL Configuration for $PROJECT_NAME
# Auto-generated from docker-compose.mesh-app.yml and isle-mesh.yml

# Base domain and certificate naming
BASE_URL=$DOMAIN
CERT_AND_KEY_NAME=$CERT_AND_KEY_NAME
APP_NAME=$PROJECT_NAME

# Certificate directories (relative to project root)
PROXY_CERT_DIR=$CERT_DIR
PROXY_KEY_DIR=$KEY_DIR
CERT_DIR=$CERT_DIR
KEY_DIR=$KEY_DIR

# Certificate settings
CERT_COUNTRY=US
CERT_STATE=State
CERT_CITY=City
CERT_ORG=$PROJECT_NAME
CERT_ORG_UNIT=IT
CERT_COMMON_NAME=$DOMAIN

# Certificate validity (in days)
CERT_DAYS=365

# Enable subdomain support
ENABLE_SUBDOMAINS=true

# Subdomains (extracted from isle-mesh.yml)
SUBDOMAINS="$SUBDOMAINS"
EOF

echo "✓ Generated: $OUTPUT_FILE"
echo ""
echo "Configuration:"
echo "  Domain: $DOMAIN"
echo "  Project: $PROJECT_NAME"
echo "  Subdomains: $SUBDOMAINS"
echo ""
echo "Next step: Generate SSL certificates with:"
echo "  cd $MESH_DIR && isle ssl generate-mesh config/ssl.env.conf"
