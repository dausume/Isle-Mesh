#!/bin/bash

# Isle-Mesh Scaffold Script
# Converts a docker-compose app into a mesh-app with automated SSL and proxy
# This script parses docker-compose.yml files and generates all necessary Isle-Mesh configuration

set -e

# Directory paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SSL_DIR="$PROJECT_ROOT/ssl"
MESH_PROXY_DIR="$PROJECT_ROOT/mesh-proxy"

# Default configuration values
COMPOSE_FILE=""
OUTPUT_DIR="."
DOMAIN=""  # Will be populated from CLI, compose file, or default
DOMAIN_FROM_CLI=false  # Track if user explicitly set domain via CLI
PROJECT_NAME=""
ENV_NAME="production"

# Display Isle-Mesh banner
print_banner() {
  echo "╔═══════════════════════════════════════════════════════════════╗"
  echo "║                                                               ║"
  echo "║           Isle-Mesh Scaffold - Docker Compose to Mesh        ║"
  echo "║                                                               ║"
  echo "╚═══════════════════════════════════════════════════════════════╝"
}

# Display help information and usage examples
print_help() {
  echo "Isle-Mesh Scaffold"
  echo ""
  echo "Automatically converts a docker-compose app into a mesh-app with:"
  echo "  • Automated SSL certificate generation"
  echo "  • Auto-generated nginx proxy configuration"
  echo "  • setup.yml for environment management"
  echo "  • isle-mesh.yml for mesh configuration"
  echo ""
  echo "Usage: isle scaffold <docker-compose-file> [options]"
  echo ""
  echo "Options:"
  echo "  -o, --output DIR      Output directory (default: current directory)"
  echo "  -d, --domain DOMAIN   Base domain (default: mesh-app.local)"
  echo "  -n, --name NAME       Project name (default: extracted from compose file)"
  echo "  -e, --env ENV         Environment name (default: production)"
  echo "  -h, --help            Show this help message"
  echo ""
  echo "Examples:"
  echo "  isle scaffold docker-compose.yml"
  echo "  isle scaffold docker-compose.yml -o ./mesh-output -d myapp.local"
  echo "  isle scaffold ./app/docker-compose.yml -n myapp -e dev"
  echo ""
}

# Parse command-line arguments and set configuration variables
parse_args() {
  while [[ $# -gt 0 ]]; do
    case $1 in
      -o|--output)
        OUTPUT_DIR="$2"
        shift 2
        ;;
      -d|--domain)
        DOMAIN="$2"
        DOMAIN_FROM_CLI=true
        shift 2
        ;;
      -n|--name)
        PROJECT_NAME="$2"
        shift 2
        ;;
      -e|--env)
        ENV_NAME="$2"
        shift 2
        ;;
      -h|--help)
        print_help
        exit 0
        ;;
      *)
        # First positional argument is the compose file
        if [ -z "$COMPOSE_FILE" ]; then
          COMPOSE_FILE="$1"
        else
          echo "Error: Unknown option: $1"
          print_help
          exit 1
        fi
        shift
        ;;
    esac
  done
}

# Validate that all required system dependencies are installed
validate_dependencies() {
  echo "[1/10] Validating dependencies..."

  # Array to track missing dependencies
  local missing_deps=()

  # Check for Docker (required for container management)
  if ! command -v docker &> /dev/null; then
    missing_deps+=("docker")
  fi

  # Check for yq (required for YAML parsing)
  if ! command -v yq &> /dev/null; then
    missing_deps+=("yq")
  fi

  # Check for Python 3 (required for proxy config generation)
  if ! command -v python3 &> /dev/null; then
    missing_deps+=("python3")
  fi

  # Check for OpenSSL (required for certificate generation)
  if ! command -v openssl &> /dev/null; then
    missing_deps+=("openssl")
  fi

  # Exit if any dependencies are missing
  if [ ${#missing_deps[@]} -ne 0 ]; then
    echo "Error: Missing required dependencies:"
    for dep in "${missing_deps[@]}"; do
      echo "  ✗ $dep"
    done
    echo ""
    echo "Please install missing dependencies and try again."
    exit 1
  fi

  echo "✓ All dependencies available"
}

# Validate that the docker-compose file exists and is valid YAML
validate_compose_file() {
  if [ -z "$COMPOSE_FILE" ]; then
    echo "Error: Docker compose file required"
    print_help
    exit 1
  fi

  if [ ! -f "$COMPOSE_FILE" ]; then
    echo "Error: File not found: $COMPOSE_FILE"
    exit 1
  fi

  # Validate it's parseable YAML
  if ! yq eval '.' "$COMPOSE_FILE" > /dev/null 2>&1; then
    echo "Error: Invalid YAML in $COMPOSE_FILE"
    exit 1
  fi

  echo "✓ Valid docker-compose file"
}

# Analyze the docker-compose file and extract service information
analyze_compose() {
  echo "[2/10] Analyzing docker-compose file..."

  # Extract project name from compose file if not provided via CLI
  if [ -z "$PROJECT_NAME" ]; then
    PROJECT_NAME=$(yq eval '.name // ""' "$COMPOSE_FILE")
    if [ -z "$PROJECT_NAME" ]; then
      # Fallback to parent directory name
      PROJECT_NAME=$(basename "$(dirname "$(realpath "$COMPOSE_FILE")")")
    fi
  fi

  # Extract mesh.domain from compose file labels if not provided via CLI
  # Priority: CLI flag > compose label > default (mesh-app.local)
  if [ "$DOMAIN_FROM_CLI" = false ]; then
    local compose_domain=$(yq eval '.labels."mesh.domain" // ""' "$COMPOSE_FILE")
    if [ -n "$compose_domain" ] && [ "$compose_domain" != "null" ]; then
      DOMAIN="$compose_domain"
      echo "✓ Using domain from compose file: $DOMAIN"
    else
      # Use default if not in compose file
      DOMAIN="mesh-app.local"
      echo "✓ Using default domain: $DOMAIN"
    fi
  else
    echo "✓ Using domain from CLI: $DOMAIN"
  fi

  # Count services in the compose file
  local services=$(yq eval '.services | keys | .[]' "$COMPOSE_FILE")
  local service_count=$(echo "$services" | wc -l)

  echo "✓ Project: ${PROJECT_NAME}"
  echo "✓ Found $service_count service(s):"

  # Display details for each service
  while IFS= read -r service; do
    # Extract port (from expose or ports directive)
    local port=$(yq eval ".services.$service.expose[0] // .services.$service.ports[0]" "$COMPOSE_FILE" | sed 's/:.*//')
    # Extract subdomain label or use service name as default
    local subdomain=$(yq eval ".services.$service.labels.\"mesh.subdomain\" // \"$service\"" "$COMPOSE_FILE")
    # Check if mTLS is enabled for this service
    local mtls=$(yq eval ".services.$service.labels.\"mesh.mtls\" // \"false\"" "$COMPOSE_FILE")

    echo "  • $service"
    echo "    Port: ${port:-"(not exposed)"}"
    echo "    Subdomain: $subdomain.$DOMAIN"
    echo "    mTLS: $mtls"
  done <<< "$services"
}

# Create the output directory structure for generated files
setup_output_directory() {
  echo "[3/10] Setting up output directory..."

  # Create directory structure for generated files
  mkdir -p "$OUTPUT_DIR"
  mkdir -p "$OUTPUT_DIR/ssl"        # For SSL certificates and keys
  mkdir -p "$OUTPUT_DIR/proxy"      # For nginx proxy configuration
  mkdir -p "$OUTPUT_DIR/config"     # For environment configuration files

  # Convert to absolute path for consistency
  OUTPUT_DIR=$(realpath "$OUTPUT_DIR")

  echo "✓ Output directory: $OUTPUT_DIR"
}

# Generate SSL configuration file and certificates for all services
generate_ssl_config() {
  echo "[4/10] Generating SSL configuration..."

  # Create SSL environment configuration file
  local ssl_env_file="$OUTPUT_DIR/config/ssl.env.conf"

  cat > "$ssl_env_file" <<EOF
# SSL Configuration for $PROJECT_NAME
# Generated by isle scaffold

# Base domain and certificate naming
BASE_URL=$DOMAIN
CERT_AND_KEY_NAME=$PROJECT_NAME
APP_NAME=$PROJECT_NAME

# Certificate directories (relative to output directory)
PROXY_CERT_DIR=ssl/certs
PROXY_KEY_DIR=ssl/keys
CERT_DIR=ssl/certs
KEY_DIR=ssl/keys

# Certificate settings (optional)
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

# Subdomains (extracted from docker-compose)
EOF

  # Extract all service subdomains for inclusion in SSL certificate SANs
  local services=$(yq eval '.services | keys | .[]' "$COMPOSE_FILE")
  local subdomains=()

  while IFS= read -r service; do
    local subdomain=$(yq eval ".services.$service.labels.\"mesh.subdomain\" // \"$service\"" "$COMPOSE_FILE")
    subdomains+=("$subdomain")
  done <<< "$services"

  # Write subdomains as space-separated list
  echo "SUBDOMAINS=\"${subdomains[*]}\"" >> "$ssl_env_file"

  echo "✓ SSL config created: $ssl_env_file"

  # Run the SSL generation script if available
  echo "  Generating SSL certificates..."

  if [ -f "$SSL_DIR/generate_mesh_ssl.sh" ]; then
    bash "$SSL_DIR/generate_mesh_ssl.sh" "$ssl_env_file" "$OUTPUT_DIR" "$OUTPUT_DIR/ssl" 2>&1 | sed 's/^/    /'
    echo "✓ SSL certificates generated"
  else
    echo "⚠ SSL generation script not found, skipping..."
  fi
}

# Generate nginx proxy configuration using the mesh-proxy builder
generate_proxy_config() {
  echo "[5/10] Generating proxy configuration..."

  local proxy_output="$OUTPUT_DIR/proxy/nginx-mesh-proxy.conf"

  # Extract services that require mutual TLS authentication
  local services=$(yq eval '.services | keys | .[]' "$COMPOSE_FILE")
  local mtls_services=()

  while IFS= read -r service; do
    local mtls=$(yq eval ".services.$service.labels.\"mesh.mtls\" // \"false\"" "$COMPOSE_FILE")
    if [ "$mtls" = "true" ]; then
      local subdomain=$(yq eval ".services.$service.labels.\"mesh.subdomain\" // \"$service\"" "$COMPOSE_FILE")
      # Add mTLS flags for proxy builder
      mtls_services+=("--service-mtls" "$subdomain")
    fi
  done <<< "$services"

  # Run the Python proxy configuration builder script
  if [ -f "$MESH_PROXY_DIR/scripts/build-proxy-config.py" ]; then
    python3 "$MESH_PROXY_DIR/scripts/build-proxy-config.py" \
      --compose "$(realpath "$COMPOSE_FILE")" \
      --domain "$DOMAIN" \
      --base-cert "$PROJECT_NAME.crt" \
      --base-key "$PROJECT_NAME.key" \
      "${mtls_services[@]}" \
      --output "$proxy_output" 2>&1 | sed 's/^/    /'

    echo "✓ Proxy config generated: $proxy_output"
  else
    echo "⚠ Proxy builder not found, skipping..."
  fi
}

# Extract environment files and variables from docker-compose
extract_env_config() {
  echo "[6/10] Extracting environment configuration..."

  local services=$(yq eval '.services | keys | .[]' "$COMPOSE_FILE")
  local env_manifest="$OUTPUT_DIR/config/env-manifest.json"

  # Initialize JSON manifest
  echo "{" > "$env_manifest"
  echo "  \"source_compose\": \"$(realpath "$COMPOSE_FILE")\"," >> "$env_manifest"
  echo "  \"generated_at\": \"$(date -Iseconds)\"," >> "$env_manifest"
  echo "  \"services\": {" >> "$env_manifest"

  local first_service=true

  while IFS= read -r service; do
    # Skip proxy services
    if [[ "$service" =~ proxy ]] || [[ "$service" =~ nginx ]] || [[ "$service" =~ traefik ]]; then
      continue
    fi

    if [ "$first_service" = false ]; then
      echo "," >> "$env_manifest"
    fi
    first_service=false

    echo "    \"$service\": {" >> "$env_manifest"

    # Extract env_file references
    local env_files=$(yq eval ".services.$service.env_file // []" "$COMPOSE_FILE" | grep -v "^null$" | sed 's/^- //')
    echo "      \"env_files\": [" >> "$env_manifest"

    if [ ! -z "$env_files" ] && [ "$env_files" != "[]" ]; then
      local first_env_file=true
      while IFS= read -r env_file; do
        if [ ! -z "$env_file" ]; then
          if [ "$first_env_file" = false ]; then
            echo "," >> "$env_manifest"
          fi
          first_env_file=false

          # Resolve env file path relative to compose file location
          local compose_dir=$(dirname "$(realpath "$COMPOSE_FILE")")
          local resolved_path="$compose_dir/$env_file"

          echo -n "        {\"path\": \"$env_file\", \"resolved\": \"$resolved_path\"}" >> "$env_manifest"

          # Copy env file to config directory if it exists
          if [ -f "$resolved_path" ]; then
            cp "$resolved_path" "$OUTPUT_DIR/config/$(basename "$env_file")"
            echo "  ✓ Copied env file: $env_file"
          fi
        fi
      done <<< "$env_files"
    fi

    echo "" >> "$env_manifest"
    echo "      ]," >> "$env_manifest"

    # Extract inline environment variables
    echo "      \"environment\": {" >> "$env_manifest"
    local env_vars=$(yq eval ".services.$service.environment // {}" "$COMPOSE_FILE" -o=json)

    if [ "$env_vars" != "{}" ] && [ "$env_vars" != "null" ]; then
      echo "$env_vars" | jq -r 'to_entries | .[] | "        \"" + .key + "\": \"" + (.value | tostring) + "\""' | paste -sd ',' >> "$env_manifest"
    fi

    echo "" >> "$env_manifest"
    echo -n "      }" >> "$env_manifest"
    echo "" >> "$env_manifest"
    echo -n "    }" >> "$env_manifest"

  done <<< "$services"

  echo "" >> "$env_manifest"
  echo "  }" >> "$env_manifest"
  echo "}" >> "$env_manifest"

  echo "✓ Environment manifest created: $env_manifest"
}

# Generate setup.yml with environment configurations
generate_setup_yml() {
  echo "[7/10] Generating setup.yml..."

  local setup_file="$OUTPUT_DIR/setup.yml"
  local env_manifest="$OUTPUT_DIR/config/env-manifest.json"

  # Get list of all services
  local services=$(yq eval '.services | keys | .[]' "$COMPOSE_FILE")

  # Write initial setup.yml structure with current environment
  cat > "$setup_file" <<EOF
# setup.yml - Environment configuration for $PROJECT_NAME
# Generated by isle scaffold
# Source: $(realpath "$COMPOSE_FILE")

current-setup:
  env: $ENV_NAME

environments:
  $ENV_NAME:
    domain: $DOMAIN
    projects:
      $PROJECT_NAME: { path: . }
EOF

  # Add service configurations with ports and env files
  while IFS= read -r service; do
    # Skip proxy/load balancer services
    if [[ "$service" =~ proxy ]] || [[ "$service" =~ nginx ]] || [[ "$service" =~ traefik ]]; then
      continue
    fi

    local port=$(yq eval ".services.$service.expose[0] // .services.$service.ports[0]" "$COMPOSE_FILE" | sed 's/:.*//')

    # Get env files for this service from manifest if it exists
    local env_file_list=""
    if [ -f "$env_manifest" ] && command -v jq &> /dev/null; then
      env_file_list=$(jq -r ".services.\"$service\".env_files[]?.path // empty" "$env_manifest" 2>/dev/null | paste -sd ',' -)
    fi

    # Build service config line
    local config_parts="port: '$port'"
    if [ ! -z "$env_file_list" ]; then
      config_parts="$config_parts, env_files: [$env_file_list]"
    fi

    if [ ! -z "$port" ] && [ "$port" != "null" ]; then
      echo "      $service: { $config_parts }" >> "$setup_file"
    fi
  done <<< "$services"

  # Add development environment configuration
  cat >> "$setup_file" <<EOF

  dev:
    domain: localhost
    expose_ports_on_localhost: true
    projects:
      $PROJECT_NAME: { path: . }
EOF

  # Add same services to dev environment
  while IFS= read -r service; do
    # Skip proxy/load balancer services
    if [[ "$service" =~ proxy ]] || [[ "$service" =~ nginx ]] || [[ "$service" =~ traefik ]]; then
      continue
    fi

    local port=$(yq eval ".services.$service.expose[0] // .services.$service.ports[0]" "$COMPOSE_FILE" | sed 's/:.*//')

    # Get env files for this service
    local env_file_list=""
    if [ -f "$env_manifest" ] && command -v jq &> /dev/null; then
      env_file_list=$(jq -r ".services.\"$service\".env_files[]?.path // empty" "$env_manifest" 2>/dev/null | paste -sd ',' -)
    fi

    local config_parts="port: '$port'"
    if [ ! -z "$env_file_list" ]; then
      config_parts="$config_parts, env_files: [$env_file_list]"
    fi

    if [ ! -z "$port" ] && [ "$port" != "null" ]; then
      echo "      $service: { $config_parts }" >> "$setup_file"
    fi
  done <<< "$services"

  echo "✓ setup.yml created: $setup_file"
}

# Generate docker-compose.mesh-app.yml with mesh integration
generate_mesh_compose() {
  echo "[8/10] Generating docker-compose.mesh-app.yml..."

  local mesh_compose="$OUTPUT_DIR/docker-compose.mesh-app.yml"
  local compose_dir=$(dirname "$(realpath "$COMPOSE_FILE")")

  # Write header
  cat > "$mesh_compose" <<EOF
# docker-compose.mesh-app.yml
# Generated by Isle-Mesh scaffold from $(basename "$COMPOSE_FILE")
# This compose file integrates your application with Isle-Mesh proxy and SSL

version: '3.8'

# Project-level mesh configuration
labels:
  mesh.domain: "$DOMAIN"
  mesh.enabled: "true"

networks:
  ${PROJECT_NAME}_meshnet:
    driver: bridge

services:
  # Mesh Proxy Service
  mesh-proxy:
    image: nginx:alpine
    container_name: ${PROJECT_NAME}_mesh_proxy
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./proxy/nginx-mesh-proxy.conf:/etc/nginx/nginx.conf:ro
      - ./ssl/certs:/etc/nginx/ssl/certs:ro
      - ./ssl/keys:/etc/nginx/ssl/keys:ro
    networks:
      - ${PROJECT_NAME}_meshnet
    depends_on:
EOF

  # Add depends_on for all application services
  local services=$(yq eval '.services | keys | .[]' "$COMPOSE_FILE")
  while IFS= read -r service; do
    if [[ "$service" =~ proxy ]] || [[ "$service" =~ nginx ]] || [[ "$service" =~ traefik ]]; then
      continue
    fi
    echo "      - $service" >> "$mesh_compose"
  done <<< "$services"

  # Add each application service from original compose
  while IFS= read -r service; do
    # Skip existing proxy services
    if [[ "$service" =~ proxy ]] || [[ "$service" =~ nginx ]] || [[ "$service" =~ traefik ]]; then
      continue
    fi

    echo "" >> "$mesh_compose"
    echo "  # Service: $service" >> "$mesh_compose"
    echo "  $service:" >> "$mesh_compose"

    # Copy service definition from original compose, excluding env_file and networks
    yq eval ".services.$service | del(.env_file) | del(.networks)" "$COMPOSE_FILE" | sed 's/^/    /' >> "$mesh_compose"

    # Ensure the service is on the mesh network
    echo "    networks:" >> "$mesh_compose"
    echo "      - ${PROJECT_NAME}_meshnet" >> "$mesh_compose"

    # Add env_file references with updated paths to config directory
    local env_files=$(yq eval ".services.$service.env_file // []" "$COMPOSE_FILE" | grep -v "^null$" | sed 's/^- //')
    if [ ! -z "$env_files" ] && [ "$env_files" != "[]" ]; then
      echo "    env_file:" >> "$mesh_compose"
      while IFS= read -r env_file; do
        if [ ! -z "$env_file" ]; then
          echo "      - ./config/$(basename "$env_file")" >> "$mesh_compose"
        fi
      done <<< "$env_files"
    fi

  done <<< "$services"

  echo "✓ docker-compose.mesh-app.yml created: $mesh_compose"
}

# Generate isle-mesh.yml with mesh network configuration
generate_isle_mesh_yml() {
  echo "[9/10] Generating isle-mesh.yml..."

  local mesh_file="$OUTPUT_DIR/isle-mesh.yml"

  # Write mesh configuration header with project metadata
  cat > "$mesh_file" <<EOF
# isle-mesh.yml - Mesh network configuration for $PROJECT_NAME
# Generated by isle scaffold

mesh:
  name: $PROJECT_NAME
  domain: $DOMAIN
  version: "1.0.0"

network:
  name: ${PROJECT_NAME}_meshnet
  driver: bridge

ssl:
  enabled: true
  cert_dir: ./ssl/certs
  key_dir: ./ssl/keys
  base_cert: $PROJECT_NAME.crt
  base_key: $PROJECT_NAME.key

proxy:
  enabled: true
  config: ./proxy/nginx-mesh-proxy.conf
  type: nginx
  ports:
    http: 80
    https: 443

services:
EOF

  # Add each service configuration (skip proxy services)
  local services=$(yq eval '.services | keys | .[]' "$COMPOSE_FILE")

  while IFS= read -r service; do
    # Skip proxy/load balancer services
    if [[ "$service" =~ proxy ]] || [[ "$service" =~ nginx ]] || [[ "$service" =~ traefik ]]; then
      continue
    fi

    local port=$(yq eval ".services.$service.expose[0] // .services.$service.ports[0]" "$COMPOSE_FILE" | sed 's/:.*//')
    local subdomain=$(yq eval ".services.$service.labels.\"mesh.subdomain\" // \"$service\"" "$COMPOSE_FILE")
    local mtls=$(yq eval ".services.$service.labels.\"mesh.mtls\" // \"false\"" "$COMPOSE_FILE")

    # Write service configuration block
    cat >> "$mesh_file" <<EOF
  $service:
    subdomain: $subdomain
    port: ${port:-8443}
    mtls: $mtls
    url: https://$subdomain.$DOMAIN
EOF
  done <<< "$services"

  # Add mDNS and automation settings
  cat >> "$mesh_file" <<EOF

mdns:
  enabled: true
  publish_services: true
  discovery: true

automation:
  auto_ssl_renewal: false
  auto_proxy_rebuild: true
  watch_compose_changes: false
EOF

  echo "✓ isle-mesh.yml created: $mesh_file"
}

# Register domains with mDNS system if installed
register_domains() {
  echo "[10/10] Registering domains with mDNS..."

  # Check if mDNS service is installed and running (primary indicator)
  if ! systemctl list-unit-files 2>/dev/null | grep -q "mesh-mdns.service"; then
    echo "⚠ mDNS not installed - skipping domain registration"
    echo "  Install mDNS with: isle mdns install"
    echo "  Then register domains with: isle mdns detect-domains"
    return 0
  fi

  # Path to domain detection script
  local detect_script="$PROJECT_ROOT/mdns/scripts/mesh-mdns-domains-detect.sh"

  if [ ! -f "$detect_script" ]; then
    echo "⚠ Domain detection script not found - skipping"
    return 0
  fi

  # Paths to the generated files
  local mesh_config="$OUTPUT_DIR/isle-mesh.yml"
  local mesh_compose="$OUTPUT_DIR/docker-compose.mesh-app.yml"

  echo "  Detecting and registering domains from generated files..."

  # Run domain detection in append mode (don't replace existing domains)
  if bash "$detect_script" "$mesh_config" "$mesh_compose" "append" 2>&1 | sed 's/^/    /'; then
    echo "✓ Domains registered with mDNS"

    # Check if mDNS service is running and offer to reload
    if systemctl is-active --quiet mesh-mdns.service 2>/dev/null; then
      echo ""
      echo "  mDNS service is running. Reload to broadcast new domains?"
      echo "  Run: sudo systemctl restart mesh-mdns.service"
      echo "  Or: isle mdns reload"
    fi
  else
    echo "⚠ Failed to register domains (this may require sudo)"
  fi
}

# Generate comprehensive README with setup instructions
generate_readme() {
  local readme_file="$OUTPUT_DIR/ISLE-MESH-README.md"

  cat > "$readme_file" <<EOF
# $PROJECT_NAME - Isle-Mesh Configuration

This directory contains auto-generated Isle-Mesh configuration for your Docker Compose application.

## Generated Files

### Configuration Files
- \`setup.yml\` - Environment and project configuration with env file tracking
- \`docker-compose.mesh-app.yml\` - Mesh-integrated Docker Compose file
- \`isle-mesh.yml\` - Mesh network and service configuration
- \`config/env-manifest.json\` - Manifest tracking environment files and variables
- \`config/ssl.env.conf\` - SSL certificate configuration
- \`config/*.env\` - Copied environment files from your original project

### SSL Certificates
- \`ssl/certs/\` - Generated SSL certificates
- \`ssl/keys/\` - Private keys

### Proxy Configuration
- \`proxy/nginx-mesh-proxy.conf\` - Auto-generated nginx configuration

## Quick Start

### 1. Review Configuration
Check the generated \`isle-mesh.yml\` and \`setup.yml\` files to ensure they match your requirements.

### 2. Set as Current Project (optional)
Set this as your current Isle-Mesh project for easier management:

\`\`\`bash
isle config set-project $OUTPUT_DIR
\`\`\`

### 3. Update Hosts File (for local development)
Add the following entries to your \`/etc/hosts\`:

\`\`\`
127.0.0.1 $DOMAIN
EOF

  # Add subdomain entries (exclude proxy services)
  local services=$(yq eval '.services | keys | .[]' "$COMPOSE_FILE")
  while IFS= read -r service; do
    # Skip if service name contains 'proxy' or 'nginx' or 'traefik'
    if [[ "$service" =~ proxy ]] || [[ "$service" =~ nginx ]] || [[ "$service" =~ traefik ]]; then
      continue
    fi

    local subdomain=$(yq eval ".services.$service.labels.\"mesh.subdomain\" // \"$service\"" "$COMPOSE_FILE")
    echo "127.0.0.1 $subdomain.$DOMAIN" >> "$readme_file"
  done <<< "$services"

  cat >> "$readme_file" <<EOF
\`\`\`

### 4. Trust SSL Certificates
For local development, you may need to trust the generated certificates:

\`\`\`bash
# On Linux
sudo cp ssl/certs/$PROJECT_NAME.crt /usr/local/share/ca-certificates/
sudo update-ca-certificates

# On macOS
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ssl/certs/$PROJECT_NAME.crt
\`\`\`

### 5. Deploy with Docker Compose
Use the generated mesh-integrated compose file:

\`\`\`bash
docker compose -f docker-compose.mesh-app.yml up -d
\`\`\`

Or use the original compose file (without mesh proxy):

\`\`\`bash
docker compose -f $(basename "$COMPOSE_FILE") up -d
\`\`\`

### 6. Access Your Services
- Base domain: https://$DOMAIN
EOF

  while IFS= read -r service; do
    # Skip if service name contains 'proxy' or 'nginx' or 'traefik'
    if [[ "$service" =~ proxy ]] || [[ "$service" =~ nginx ]] || [[ "$service" =~ traefik ]]; then
      continue
    fi

    local subdomain=$(yq eval ".services.$service.labels.\"mesh.subdomain\" // \"$service\"" "$COMPOSE_FILE")
    echo "- $service: https://$subdomain.$DOMAIN" >> "$readme_file"
  done <<< "$services"

  cat >> "$readme_file" <<EOF

## Isle-Mesh Commands

\`\`\`bash
# Manage proxy
isle proxy up              # Start proxy services
isle proxy down            # Stop proxy services
isle proxy logs            # View logs

# Manage SSL
isle ssl list              # List certificates
isle ssl info $PROJECT_NAME   # Show certificate info
isle ssl verify $PROJECT_NAME # Verify certificate

# Manage mDNS
isle mdns install          # Install mDNS system
isle mdns status           # Check mDNS status
\`\`\`

## Troubleshooting

### Can't access services
1. Check if containers are running: \`docker compose ps\`
2. Check proxy logs: \`docker compose logs proxy\`
3. Verify DNS resolution: \`ping $DOMAIN\`

### SSL certificate errors
1. Ensure certificates are installed in system trust store
2. Check certificate validity: \`isle ssl verify $PROJECT_NAME\`
3. Regenerate if needed: \`isle ssl generate-mesh config/ssl.env.conf\`

### Service not responding
1. Check service logs: \`docker compose logs <service-name>\`
2. Verify port configuration in docker-compose.yml
3. Check proxy configuration: \`cat proxy/nginx-mesh-proxy.conf\`

## Additional Resources

- Isle-Mesh Documentation: [Link to docs]
- Docker Compose Reference: https://docs.docker.com/compose/
- Nginx Configuration: https://nginx.org/en/docs/

---

Generated by Isle-Mesh Scaffold v0.0.1
$(date)
EOF

  echo "✓ README created: $readme_file"
}

# Print final summary of all generated files
print_summary() {
  echo ""
  echo "╔═══════════════════════════════════════════════════════════════╗"
  echo "║                    Scaffold Complete!                         ║"
  echo "╚═══════════════════════════════════════════════════════════════╝"
  echo ""
  echo "Project: $PROJECT_NAME"
  echo "Domain: $DOMAIN"
  echo "Output: $OUTPUT_DIR"
  echo ""
  echo "Generated files:"
  echo "  • setup.yml (environment configuration with env file tracking)"
  echo "  • docker-compose.mesh-app.yml (mesh-integrated compose file)"
  echo "  • isle-mesh.yml (mesh network configuration)"
  echo "  • config/env-manifest.json (environment file tracking)"
  echo "  • config/ssl.env.conf (SSL configuration)"
  echo "  • config/*.env (copied environment files)"
  echo "  • ssl/certs/ (SSL certificates)"
  echo "  • ssl/keys/ (private keys)"
  echo "  • proxy/nginx-mesh-proxy.conf (nginx proxy configuration)"
  echo "  • ISLE-MESH-README.md (setup instructions)"
  echo ""
  echo "Next steps:"
  echo "  1. Review generated configuration files"
  echo "  2. Set as current project: isle config set-project $OUTPUT_DIR"
  echo "  3. Read ISLE-MESH-README.md for setup instructions"
  echo "  4. Update your /etc/hosts file with domain entries"
  echo "  5. Deploy with: docker compose -f docker-compose.mesh-app.yml up -d"
  echo ""
}

# Main execution flow - orchestrates all scaffold operations
main() {
  print_banner
  parse_args "$@"

  # Validation phase
  validate_dependencies
  validate_compose_file

  # Analysis phase
  analyze_compose

  # Generation phase
  setup_output_directory
  generate_ssl_config
  generate_proxy_config
  extract_env_config
  generate_setup_yml
  generate_mesh_compose
  generate_isle_mesh_yml
  register_domains
  generate_readme

  # Completion summary
  print_summary
}

# Entry point
main "$@"
