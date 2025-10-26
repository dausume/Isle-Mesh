#!/bin/bash
# Isle-CLI SSL Management Script
# Manages SSL certificate generation, listing, cleaning, and verification for Isle-Mesh

set -e

# Get the project root (two levels up from this script)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SSL_DIR="$PROJECT_ROOT/ssl"

# Parse the action from first argument
ACTION="$1"
shift || true

# Display help information showing all available commands
print_help() {
  echo "Isle-Mesh SSL Management"
  echo ""
  echo "Usage: isle ssl <action> [options]"
  echo ""
  echo "Actions:"
  echo "  auto [dir]                 - Auto-generate config & SSL from mesh-app (default: current dir)"
  echo "  generate <env-file>        - Generate SSL certificates using basic generator"
  echo "  generate-mesh <env-file>   - Generate mesh SSL with subdomains"
  echo "  generate-config <env-file> - Generate OpenSSL config for SAN/subdomains"
  echo "  create-env-config [dir]    - Create ssl.env.conf from docker-compose.mesh-app.yml"
  echo "  list                       - List all generated certificates"
  echo "  clean                      - Remove all generated certificates"
  echo "  info <cert-name>           - Show info about a specific certificate"
  echo "  verify <cert-name>         - Verify a certificate"
  echo "  help                       - Show this help message"
  echo ""
  echo "Examples:"
  echo "  # Auto-generate everything from mesh-app directory"
  echo "  cd /path/to/mesh-app && isle ssl auto"
  echo "  "
  echo "  # Or step by step:"
  echo "  isle ssl create-env-config /path/to/mesh-app"
  echo "  isle ssl generate-mesh /path/to/mesh-app/config/ssl.env.conf"
  echo "  "
  echo "  # Other commands"
  echo "  isle ssl list"
  echo "  isle ssl info my-cert"
  echo "  isle ssl clean"
  echo ""
}

# List all SSL certificates, private keys, and keystores
list_certs() {
  echo "SSL Certificates:"
  echo ""

  # Check for certificate files in the certs directory
  if [ -d "$SSL_DIR/certs" ] && [ "$(ls -A $SSL_DIR/certs 2>/dev/null)" ]; then
    echo "Certificates:"
    ls -lh "$SSL_DIR/certs" | grep -v "^total" | awk '{print "  " $9 " (" $5 ")"}'
  else
    echo "No certificates found"
  fi

  echo ""

  # Check for private key files in the keys directory
  if [ -d "$SSL_DIR/keys" ] && [ "$(ls -A $SSL_DIR/keys 2>/dev/null)" ]; then
    echo "Private Keys:"
    ls -lh "$SSL_DIR/keys" | grep -v "^total" | awk '{print "  " $9 " (" $5 ")"}'
  else
    echo "No private keys found"
  fi

  echo ""

  # Check for keystore files (e.g., .p12, .jks) in the stores directory
  if [ -d "$SSL_DIR/stores" ] && [ "$(ls -A $SSL_DIR/stores 2>/dev/null)" ]; then
    echo "Key Stores:"
    ls -lh "$SSL_DIR/stores" | grep -v "^total" | awk '{print "  " $9 " (" $5 ")"}'
  fi
}

# Remove all generated SSL certificates, keys, and stores
clean_certs() {
  echo "Cleaning SSL certificates..."

  # Remove all certificate files
  if [ -d "$SSL_DIR/certs" ]; then
    rm -rf "$SSL_DIR/certs"/*
    echo "✓ Cleaned certs directory"
  fi

  # Remove all private key files
  if [ -d "$SSL_DIR/keys" ]; then
    rm -rf "$SSL_DIR/keys"/*
    echo "✓ Cleaned keys directory"
  fi

  # Remove all keystore files
  if [ -d "$SSL_DIR/stores" ]; then
    rm -rf "$SSL_DIR/stores"/*
    echo "✓ Cleaned stores directory"
  fi

  echo "✓ All SSL certificates removed"
}

# Display detailed information about a specific certificate
cert_info() {
  local cert_name="$1"

  # Validate that a certificate name was provided
  if [ -z "$cert_name" ]; then
    echo "Error: Certificate name required"
    echo "Usage: isle ssl info <cert-name>"
    exit 1
  fi

  # Try to find the certificate file (with or without .crt extension)
  local cert_path="$SSL_DIR/certs/${cert_name}.crt"

  if [ ! -f "$cert_path" ]; then
    cert_path="$SSL_DIR/certs/${cert_name}"
  fi

  # If certificate doesn't exist, show available certificates
  if [ ! -f "$cert_path" ]; then
    echo "Error: Certificate not found: $cert_name"
    echo "Available certificates:"
    ls -1 "$SSL_DIR/certs" 2>/dev/null || echo "  (none)"
    exit 1
  fi

  # Display full certificate details using OpenSSL
  echo "Certificate Information: ${cert_name}"
  echo ""
  openssl x509 -in "$cert_path" -text -noout
}

# Verify a certificate and display its key properties
verify_cert() {
  local cert_name="$1"

  # Validate that a certificate name was provided
  if [ -z "$cert_name" ]; then
    echo "Error: Certificate name required"
    echo "Usage: isle ssl verify <cert-name>"
    exit 1
  fi

  # Try to find the certificate file (with or without .crt extension)
  local cert_path="$SSL_DIR/certs/${cert_name}.crt"

  if [ ! -f "$cert_path" ]; then
    cert_path="$SSL_DIR/certs/${cert_name}"
  fi

  # Exit if certificate doesn't exist
  if [ ! -f "$cert_path" ]; then
    echo "Error: Certificate not found: $cert_name"
    exit 1
  fi

  echo "Verifying certificate: ${cert_name}"
  echo ""

  # Check validity dates (notBefore and notAfter)
  echo "Validity:"
  openssl x509 -in "$cert_path" -noout -dates

  echo ""
  # Display the certificate subject (who it was issued to)
  echo "Subject:"
  openssl x509 -in "$cert_path" -noout -subject

  echo ""
  # Display the certificate issuer (who signed it)
  echo "Issuer:"
  openssl x509 -in "$cert_path" -noout -issuer

  echo ""
  # Display Subject Alternative Names (additional domains/IPs covered)
  echo "Subject Alternative Names:"
  openssl x509 -in "$cert_path" -noout -ext subjectAltName
}

# Main command handler - routes to appropriate function based on action
case "$ACTION" in
  # Auto-generate config and SSL certificates from mesh-app directory
  auto)
    MESH_DIR="${1:-.}"
    MESH_DIR=$(realpath "$MESH_DIR")

    echo "Auto-generating SSL config and certificates for: $MESH_DIR"

    # Step 1: Generate ssl.env.conf
    echo ""
    echo "[1/2] Generating ssl.env.conf..."
    bash "$SCRIPT_DIR/generate-ssl-env-config.sh" "$MESH_DIR"

    # Step 2: Generate SSL certificates
    echo ""
    echo "[2/2] Generating SSL certificates..."
    SSL_ENV_FILE="$MESH_DIR/config/ssl.env.conf"

    if [ ! -f "$SSL_ENV_FILE" ]; then
      echo "Error: Failed to generate ssl.env.conf"
      exit 1
    fi

    bash "$SSL_DIR/generate_mesh_ssl.sh" "$SSL_ENV_FILE" "$MESH_DIR" "$SSL_DIR"

    echo ""
    echo "✓ SSL certificates generated successfully!"
    echo ""
    echo "Certificates location:"
    echo "  Certs: $MESH_DIR/ssl/certs/"
    echo "  Keys:  $MESH_DIR/ssl/keys/"
    ;;

  # Create ssl.env.conf from docker-compose.mesh-app.yml and isle-mesh.yml
  create-env-config)
    MESH_DIR="${1:-.}"
    bash "$SCRIPT_DIR/generate-ssl-env-config.sh" "$MESH_DIR"
    ;;

  # Generate a basic SSL certificate using the simple generator
  generate)
    ENV_FILE="$1"
    if [ -z "$ENV_FILE" ]; then
      echo "Error: Environment file required"
      echo "Usage: isle ssl generate <env-file>"
      exit 1
    fi

    if [ ! -f "$ENV_FILE" ]; then
      echo "Error: File not found: $ENV_FILE"
      exit 1
    fi

    echo "Generating SSL certificate..."
    bash "$SSL_DIR/generate_ssl.sh" "$ENV_FILE" "$PROJECT_ROOT" "$SSL_DIR"
    echo "✓ SSL certificate generated"
    ;;

  # Generate mesh SSL certificates with support for multiple subdomains
  generate-mesh)
    ENV_FILE="$1"
    if [ -z "$ENV_FILE" ]; then
      echo "Error: Environment file required"
      echo "Usage: isle ssl generate-mesh <env-file>"
      exit 1
    fi

    if [ ! -f "$ENV_FILE" ]; then
      echo "Error: File not found: $ENV_FILE"
      exit 1
    fi

    echo "Generating mesh SSL certificates..."
    bash "$SSL_DIR/generate_mesh_ssl.sh" "$ENV_FILE" "$PROJECT_ROOT" "$SSL_DIR"
    echo "✓ Mesh SSL certificates generated"
    ;;

  # Generate OpenSSL configuration file for SAN (Subject Alternative Names)
  generate-config)
    ENV_FILE="$1"
    if [ -z "$ENV_FILE" ]; then
      echo "Error: Environment file required"
      echo "Usage: isle ssl generate-config <env-file>"
      exit 1
    fi

    if [ ! -f "$ENV_FILE" ]; then
      echo "Error: File not found: $ENV_FILE"
      exit 1
    fi

    echo "Generating SSL config..."
    bash "$SSL_DIR/generate_ssl_config.sh" "$ENV_FILE" "$PROJECT_ROOT" "$SSL_DIR"
    echo "✓ SSL config generated"
    ;;

  # List all certificates, keys, and stores
  list)
    list_certs
    ;;

  # Remove all generated SSL files (with confirmation prompt)
  clean)
    read -p "Are you sure you want to remove all SSL certificates? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
      clean_certs
    else
      echo "Cancelled"
    fi
    ;;

  # Show detailed information about a specific certificate
  info)
    cert_info "$1"
    ;;

  # Verify a certificate and show its properties
  verify)
    verify_cert "$1"
    ;;

  # Show help if no action or 'help' action specified
  help|"")
    print_help
    ;;

  # Handle unknown actions
  *)
    echo "Error: Unknown action: $ACTION"
    echo ""
    print_help
    exit 1
    ;;
esac
