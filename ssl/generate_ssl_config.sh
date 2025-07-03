#!/bin/bash
# @startup_script/generate_ssl_config.sh
# Generates an OpenSSL config file for use with SAN for a specific set of sub-domains.

# --- Load env config ---
# --- Load env config ---
ENV_FILE="$1"
BASE_DIR="${2:-$(pwd)}"
# Remove training slash if it exists (it usually will) so you can concatonate generate_ssl.sh safely.
BASE_DIR_REF="${BASE_DIR%/}"
GEN_SSL_DIR="${3:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
# Remove training slash if it exists (it usually will) so you can concatonate generate_ssl.sh safely.
GEN_SSL_DIR_REF="${GEN_SSL_DIR%/}"
# If this is true, we are making an ssl strictly for the subdomain, not one for the base url.
IS_SUBDOMAIN_REENCRYPT="${4:-false}"
SUBDOMAIN_REENCRYPT_NAME="${5:-}"

# --- Source env file ---
#echo "📥 Sourcing environment variables from $ENV_FILE..."
source "$ENV_FILE"

# --- Required Environment Variables ---
# BASE_URL              : The base domain or hostname for the cert (e.g., myapp.local)
# ENABLE_SUBDOMAINS    : Whether to include subdomain SAN entries (true/false)
# SUBDOMAINS  : Optional comma-separated list of subdomains (e.g., api.myapp.local,admin.myapp.local)
ENABLE_SUBDOMAINS=${ENABLE_SUBDOMAINS:-false}
SUBDOMAINS_LIST=${SUBDOMAINS:-}

# Exit on error
set -e


if [ "$IS_SUBDOMAIN_REENCRYPT" == "true" ] && [ -n "$SUBDOMAIN_REENCRYPT_NAME" ]; then
    # --- Generate Subject Alternative Name Entries for re-encrypting an app on a sub-domain to use ---
    #echo "🔁 Generating re-encrypt cert config for subdomain: ${SUBDOMAIN_REENCRYPT_NAME}.${BASE_URL}" >&2

    # Override base URL temporarily for the config
    BASE_URL="${SUBDOMAIN_REENCRYPT_NAME}.${BASE_URL}"

    # Disable SAN expansion since this is now a single-cert
    ENABLE_SUBDOMAINS="false"
    SAN_ENTRIES="DNS:${BASE_URL}"

    # --- Start: Add matching container name from REEMCRYPTED_SUBDOMAIN_CONTAINER_NAMES ---
    IFS=',' read -ra SUBDOMAINS_INDEXED <<< "$SUBDOMAINS"
    IFS=',' read -ra CONTAINER_NAMES <<< "$REEMCRYPTED_SUBDOMAIN_CONTAINER_NAMES"

    MATCH_INDEX=-1
    for i in "${!SUBDOMAINS_INDEXED[@]}"; do
        if [ "${SUBDOMAINS_INDEXED[$i]}" == "$SUBDOMAIN_REENCRYPT_NAME" ]; then
            MATCH_INDEX=$i
            break
        fi
    done

    if [ "$MATCH_INDEX" -ge 0 ] && [ "$MATCH_INDEX" -lt "${#CONTAINER_NAMES[@]}" ]; then
        ORIGIN_CONTAINER="${CONTAINER_NAMES[$MATCH_INDEX]}"
        SAN_ENTRIES="DNS:${BASE_URL},DNS:${ORIGIN_CONTAINER}"
    else
        echo "❌ Error: Could not resolve container name for subdomain '${SUBDOMAIN_REENCRYPT_NAME}'"
        exit 1
    fi
    # --- End: Add matching container name from REEMCRYPTED_SUBDOMAIN_CONTAINER_NAMES ---
else
    # --- Generate Subject Alternative Name Entries for a normal SSL Cert ---
    # Always include the main domain
    SAN_ENTRIES="DNS:$BASE_URL"
    # Iterate through the list of subdomains and generate the SAN DNS Lines needed.
    if [ "$ENABLE_SUBDOMAINS" == "true" ]; then
        # Confirm there is a list of subdomains in the SUBDOMAINS, and at least one subdomain.
        if [ -z "$SUBDOMAINS" ]; then
            echo "Error: ENABLE_SUBDOMAINS=true but SUBDOMAINS is empty"
            exit 1
        fi

        # Split the comma-separated list
        IFS=',' read -ra SUBDOMAINS <<< "$SUBDOMAINS"
        for subdomain in "${SUBDOMAINS[@]}"; do
            # Must enter subdomain.base_url
            SAN_ENTRIES+=",DNS:${subdomain}.${BASE_URL}"
        done
    fi
fi

# --- Write to OpenSSL config ---
OPENSSL_CONFIG_FILE=$(mktemp)

cat > "$OPENSSL_CONFIG_FILE" <<EOF
[req]
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no

[req_distinguished_name]
CN = $BASE_URL

[v3_req]
subjectAltName = $SAN_ENTRIES
EOF

export GENERATED_OPENSSL_CONFIG="$OPENSSL_CONFIG_FILE"

#echo "GENERATED_OPENSSL_CONFIG : $GENERATED_OPENSSL_CONFIG"

# Remove unneeded ENV vars, to ensure only intensional values are passed in on subsequent calls for defining SSL.
unset SAN_ENTRIES
unset SUBDOMAINS

# ✅ Only output the path — nothing else!  
# This makes this the stdout for this sub-shell and ensures it is the return value, if anything else is echo'd it will add noise
# and the other script will fail!  Any echos should be only for errors and return an exit 1.
echo "$OPENSSL_CONFIG_FILE"