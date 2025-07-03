#!/bin/bash
# @startup_script/generate_ssl.sh

# This is meant to wrap ssl generation to make it easier to use with other .sh files for initializing
# docker compose setups automatically.  This is for making a single SSL cert via environment variables,
# for orchestrating SSL for a more complex app or compose setup, use the generate_ssl_orchestration.sh file.
# This is designed for securing simple apps on mesh-networks through automated orchestration.

# Example usage in a .sh file, made for a containerized nginx proxy being used just for the local device:
# BASE_DIR set automatically in the container, should be something like ...
# export CERT_DIR=nginx/ssl/certs
# export KEY_DIR=nginx/ssl/keys
# export CERT_AND_KEY_NAME=proxy_ssl_cert
# export SSL_URL=localhost
# If your cert has expired (expires once per year), re-run this with the extra option to re-make your cert
# WARNING: If your server has two-way connections, you should instead 
# export OVERWRITE_SSL=true

# Example usage when making the ssl specific to an endpoint serving an independent server from a virtual machine
# being served through an nginx proxy, using ECC protocol.

# Exit on error
set -e

echo "STARTED generate_ssl.sh" >&2

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

echo "loaded config"  >&2

# Optional debug
echo "IS_SUBDOMAIN_REENCRYPT: $IS_SUBDOMAIN_REENCRYPT"
echo "SUBDOMAIN_REENCRYPT_NAME: $SUBDOMAIN_REENCRYPT_NAME"

# --- Source env file ---
echo "📥 Sourcing environment variables in generate_ssl.sh from $ENV_FILE..."
source "$ENV_FILE"

# --- Optional/default Environment Variables ---
# Sets a default value of false to OVERWRITE_SSL if a value was not set manually.
OVERWRITE_SSL=${OVERWRITE_SSL:-false}
SSL_PROTOCOL=${SSL_PROTOCOL:-rsa:4096}
ENABLE_SUBDOMAINS=${ENABLE_SUBDOMAINS:-false}

echo "BASE_URL : $BASE_URL"

echo "APP_NAME : $APP_NAME"

# --- Required Environment Variables ---
REQUIRED_VARS=("BASE_DIR" "CERT_DIR" "KEY_DIR" "CERT_AND_KEY_NAME" "BASE_URL" "GEN_SSL_DIR")
for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var:-}" ]; then
        echo "Error: Environment variable '$var' is not set."
        echo "Error: '$var' environment variable was not set while running generate_ssl.sh"
        echo "This script should usually be used with automation in a setup process, so write your code or shell file to dynamically change"
        echo "these environment variables each time to create as many SSH files as you need for your overall setup."
        echo "Please ensure you set all of the following required variables:"
        echo "CERT_AND_KEY_NAME to the name you want on your SSL certificate and key before running the generate_ssl script."
        echo "BASE_DIR pointing to the base directory of your container, should be set automatically by your container, if not set it manually to what it should be."
        echo "CERT_DIR the relative path after your BASE_DIR path, where your Cert file will be generated."
        echo "KEY_DIR the relative path after your BASE_DIR path, where your Key file will be generated."
        echo "BASE_URL is the name of the URL we are generating SSL Certs to secure (localhost if it is local, someApp.local if it is mDNS, someApp.vpn if it is served through OpenVPN networks)"
        echo "Note that that CERT_AND_KEY_NAME is erased each time you go through this, to ensure you throw an error if you accidentally call this many times just overwriting your own stuff."
        echo "There are also OPTIONAL environment variables : OVERWRITE_SSL, SSL_PROTOCOL"
        exit 1
    fi
done


echo "calling generate_ssl_config.sh"
# --- This handles generating the config file, handling the url and url-subdomains ---
OPENSSL_CONFIG_PATH=""
if [ "$IS_SUBDOMAIN_REENCRYPT" = "true" ] && [ -n "$SUBDOMAIN_REENCRYPT_NAME" ]; then
    OPENSSL_CONFIG_PATH=$($GEN_SSL_DIR_REF/generate_ssl_config.sh $ENV_FILE $BASE_DIR $GEN_SSL_DIR  $IS_SUBDOMAIN_REENCRYPT $SUBDOMAIN_REENCRYPT_NAME)
    CERT_AND_KEY_NAME="$SUBDOMAIN_REENCRYPT_NAME.$CERT_AND_KEY_NAME"
    echo "New Cert and Key Name : $CERT_AND_KEY_NAME"
else
    OPENSSL_CONFIG_PATH=$($GEN_SSL_DIR_REF/generate_ssl_config.sh $ENV_FILE $BASE_DIR $GEN_SSL_DIR )
fi

# --- Derive Full Directory Paths ---
FULL_CERT_DIR_PATH="$BASE_DIR/$CERT_DIR"
FULL_KEY_DIR_PATH="$BASE_DIR/$KEY_DIR"

# Derive Full File Paths
FULL_CERT_FILE_PATH="$FULL_CERT_DIR_PATH/$CERT_AND_KEY_NAME.crt"
FULL_KEY_FILE_PATH="$FULL_KEY_DIR_PATH/$CERT_AND_KEY_NAME.key"

# --- Ensure Directories exist and are writable ---
for dir in "$FULL_CERT_DIR_PATH" "$FULL_KEY_DIR_PATH"; do
    if [ -d "$dir" ]; then
        if [ ! -w "$dir" ]; then
            echo "Error: Directory '$dir' exists but is not writable."
            exit 1
        fi
    else
        mkdir -p "$dir"
    fi
done

# --- Check Existing Files ---
if [ -f "$FULL_CERT_FILE_PATH" ] || [ -f "$FULL_KEY_FILE_PATH" ]; then
    if [ "$OVERWRITE_SSL" == "false" ]; then
        echo "SSL Certificate or key already exists at one of the following paths : '$FULL_CERT_FILE_PATH', '$FULL_KEY_FILE_PATH'"
        echo "SSL Cert-Key pairs must always be generated together, you cannot expect ones generated separately to work together."
        echo "If you want to overwrite the existing certs with a new cert-pair, set the env var OVERWRITE_SSL to be 'true'."
        exit 1
    fi
fi

echo "----- BEGIN GENERATED OPENSSL CONFIG -----"
cat "$OPENSSL_CONFIG_PATH"
echo "----- END GENERATED OPENSSL CONFIG -----"

# --- Generate SSL Certificate ---
openssl req -x509 -newkey "$SSL_PROTOCOL" \
    -keyout "$FULL_KEY_FILE_PATH" \
    -out "$FULL_CERT_FILE_PATH" \
    -days 365 -nodes \
    -config "$OPENSSL_CONFIG_PATH" \
    -extensions v3_req

# Delete the config so it cannot be accidentally re-used
rm "$OPENSSL_CONFIG_PATH"
# Delete all ENV vars except for BASE_DIR since it is foundational to containers and may be re-used through multiple
# calls to this ssl generation script.
# Unset all env vars used by this script except BASE_DIR
VARS_TO_UNSET=(
  CERT_DIR KEY_DIR CERT_AND_KEY_NAME BASE_URL
  OVERWRITE_SSL SSL_PROTOCOL ENABLE_SUBDOMAINS
  OPENSSL_CONFIG_PATH SSL_SUBDOMAINS_LIST
)
for var in "${VARS_TO_UNSET[@]}"; do
  unset "$var"
done
unset VARS_TO_UNSET