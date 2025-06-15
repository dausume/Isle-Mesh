#!/bin/bash
# @mesh-apps/ssl/generate_mesh_ssl.sh
# 🔐 Generate all required SSL certificates for base URL + subdomains (with optional re-encryption)

set -e

echo "🔧 Starting mesh SSL generation..."

# --- Load env config ---
ENV_FILE="$1"
BASE_DIR="${2:-$(pwd)}"
# Remove training slash if it exists (it usually will) so you can concatonate generate_ssl.sh safely.
BASE_DIR_REF="${BASE_DIR%/}"
GEN_SSL_DIR="${3:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

# Remove training slash if it exists (it usually will) so you can concatonate generate_ssl.sh safely.
GEN_SSL_DIR_REF="${GEN_SSL_DIR%/}"

# 🧪 Debug log
#echo "📄 ENV_FILE: '$ENV_FILE'"
#echo "📁 GEN_SSL_DIR: '$GEN_SSL_DIR'"
#cho "📌 BASE_DIR: '$BASE_DIR'"

# --- Check env file existence ---
if [[ -z "$ENV_FILE" ]]; then
  echo "❌ Error: No .env.conf file specified."
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "❌ Error: File does not exist: $ENV_FILE"
  exit 1
fi

# --- Source env file ---
#echo "📥 Sourcing environment variables from $ENV_FILE..."
source "$ENV_FILE"

#echo "BASE_URL : $BASE_URL"
# Ensure GEN_SSL_DIR ends without a trailing slash

# --- Check required vars ---
REQUIRED_VARS=("BASE_URL" "CERT_AND_KEY_NAME" "PROXY_CERT_DIR" "PROXY_KEY_DIR")
for var in "${REQUIRED_VARS[@]}"; do
  if [ -z "${!var:-}" ]; then
    echo "❌ Missing required env var: $var"
    exit 1
  fi
done

# --- Set defaults if not already set ---
export OVERWRITE_SSL="${OVERWRITE_SSL:-false}"
export SSL_PROTOCOL="${SSL_PROTOCOL:-rsa:4096}"
export ENABLE_SUBDOMAINS="${ENABLE_SUBDOMAINS:-false}"

# --- Check generate_ssl.sh exists ---
GEN_SCRIPT="$GEN_SSL_DIR_REF/generate_ssl.sh"
#echo "GEN_SCRIPT : $GEN_SCRIPT"
if [[ ! -x "$GEN_SCRIPT" ]]; then
  echo "❌ Non-executable: $GEN_SCRIPT"
  exit 1
fi

# --- Primary cert ---
echo "🔐 Generating primary SSL cert for $BASE_URL..."

export CERT_AND_KEY_NAME
export SSL_URL="$BASE_URL"
export CERT_DIR="$PROXY_CERT_DIR"
export KEY_DIR="$PROXY_KEY_DIR"

#echo "📍 Current working directory: $(pwd)"

echo "'$GEN_SCRIPT' '$ENV_FILE' '$BASE_DIR' '$GEN_SSL_DIR'"

cmd="'$GEN_SCRIPT' '$ENV_FILE' '$BASE_DIR' '$GEN_SSL_DIR'"

eval "$cmd"

echo "Executed eval"

# Call initial cert generation script for the base .local app while defining what the ssl subdomains should be.
# We make it into a single string then try to execute it as a string.
#bash "$GEN_SCRIPT" "$ENV_FILE" "$GEN_SSL_DIR" "$BASE_DIR"

echo "Generated primary cert, going through to create subdomain re-encrypt certs."

# --- Subdomain certs ---
if [[ "$ENABLE_SUBDOMAINS" == "true" && -n "$SUBDOMAINS" ]]; then
  IFS=',' read -ra SUBDOMAIN_ARRAY <<< "$SUBDOMAINS"
  IFS=',' read -ra REENCRYPTED_ARRAY <<< "${REENCRYPTED_SUBDOMAINS:-}"

  for sub in "${REENCRYPTED_ARRAY[@]}"; do
    echo "🌐 Subdomain: $sub.$BASE_URL"

    export CERT_AND_KEY_NAME="${sub}.${APP_NAME}"
    export SSL_URL="${sub}.${BASE_URL}"

    "$GEN_SCRIPT" "$ENV_FILE" "$BASE_DIR" "$GEN_SSL_DIR" "true" "$sub"
  done
fi

echo "✅ All certificates generated."