#!/bin/bash

# Isle-Mesh Scaffold Script
# Converts a docker-compose app into a mesh-app with automated SSL and proxy
# This script parses docker-compose.yml files and generates all necessary Isle-Mesh configuration

set -e

# Directory paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SSL_DIR="$PROJECT_ROOT/ssl"
MESH_PROXY_DIR="$PROJECT_ROOT/mesh-app-scaffolding"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

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

  # Check for jq (required for JSON manipulation)
  if ! command -v jq &> /dev/null; then
    missing_deps+=("jq")
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

    # Copy certs to isle-agent SSL directory so vlan-agent can serve them
    local agent_ssl_certs="/etc/isle-mesh/agent/ssl/certs"
    local agent_ssl_keys="/etc/isle-mesh/agent/ssl/keys"
    if [ -d "/etc/isle-mesh/agent" ]; then
      mkdir -p "$agent_ssl_certs" "$agent_ssl_keys" 2>/dev/null || sudo mkdir -p "$agent_ssl_certs" "$agent_ssl_keys"
      if [ -d "$OUTPUT_DIR/ssl/certs" ]; then
        cp "$OUTPUT_DIR/ssl/certs/"* "$agent_ssl_certs/" 2>/dev/null || sudo cp "$OUTPUT_DIR/ssl/certs/"* "$agent_ssl_certs/" 2>/dev/null || true
      fi
      if [ -d "$OUTPUT_DIR/ssl/keys" ]; then
        cp "$OUTPUT_DIR/ssl/keys/"* "$agent_ssl_keys/" 2>/dev/null || sudo cp "$OUTPUT_DIR/ssl/keys/"* "$agent_ssl_keys/" 2>/dev/null || true
      fi
      echo "✓ SSL certificates copied to isle-agent directory"
    fi
  else
    echo "⚠ SSL generation script not found, skipping..."
  fi
}

# Proxy configuration is handled by the isle-vlan-agent unified proxy
# The agent generates nginx configs automatically from registry.json
generate_proxy_config() {
  echo "[5/10] Proxy configuration..."
  echo "  Skipping per-app proxy config generation"
  echo "  The isle-vlan-agent generates proxy configs from the registry automatically"
  echo "✓ Proxy will be handled by isle-vlan-agent"
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

  # Write header — uses isle-agent-net external network (no per-app proxy)
  cat > "$mesh_compose" <<EOF
# docker-compose.mesh-app.yml
# Generated by Isle-Mesh scaffold from $(basename "$COMPOSE_FILE")
# This compose file integrates your application with the isle-vlan-agent unified proxy
# No per-app proxy container — the isle-vlan-agent handles all proxying

version: '3.8'

# Project-level mesh configuration
labels:
  mesh.domain: "$DOMAIN"
  mesh.enabled: "true"

networks:
  isle-agent-net:
    external: true
    name: isle-agent-net

services:
EOF

  # Add each application service from original compose
  local services=$(yq eval '.services | keys | .[]' "$COMPOSE_FILE")
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

    # Connect to isle-agent-net so the vlan-agent can reach this container
    echo "    networks:" >> "$mesh_compose"
    echo "      - isle-agent-net" >> "$mesh_compose"

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
  name: isle-agent-net
  external: true

ssl:
  enabled: true
  cert_dir: ./ssl/certs
  key_dir: ./ssl/keys
  base_cert: $PROJECT_NAME.crt
  base_key: $PROJECT_NAME.key

proxy:
  type: isle-agent
  enabled: true

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

# Check if mDNS is installed, offer to install if not
# Returns: 0 if mDNS is installed/ready, 1 if not available
ensure_mdns_installed() {
  local context="$1"

  # Check if mDNS service is installed
  if systemctl list-unit-files 2>/dev/null | grep -q "mesh-mdns.service"; then
    # Service exists, check if it's running
    if ! systemctl is-active --quiet mesh-mdns.service 2>/dev/null; then
      echo -e "${YELLOW}⚠️  mDNS service is installed but not running${NC}"
      echo -e "${BLUE}Starting mDNS service...${NC}"
      if sudo systemctl start mesh-mdns.service 2>/dev/null; then
        echo -e "${GREEN}✓ mDNS service started${NC}"
      else
        echo -e "${RED}✗ Failed to start mDNS service${NC}"
        return 1
      fi
    fi
    return 0
  fi

  # mDNS not installed - offer to install
  echo ""
  echo -e "${YELLOW}╔═══════════════════════════════════════════════════════════════╗${NC}"
  echo -e "${YELLOW}║           mDNS Service Not Installed                          ║${NC}"
  echo -e "${YELLOW}╚═══════════════════════════════════════════════════════════════╝${NC}"
  echo ""
  echo -e "${BLUE}The localhost-mdns service is required to ${context}.${NC}"
  echo ""
  echo "This service:"
  echo "  • Broadcasts .local domain names on your network"
  echo "  • Enables automatic service discovery"
  echo "  • Required for mesh networking functionality"
  echo ""
  echo -e "${YELLOW}Would you like to install localhost-mdns now? (y/N)${NC}"

  read -r response

  if [[ "$response" =~ ^[Yy]$ ]]; then
    echo ""
    echo -e "${BLUE}Installing localhost-mdns...${NC}"
    echo ""

    # Run the mdns install command
    if bash "$SCRIPT_DIR/mdns.sh" install; then
      echo ""
      echo -e "${GREEN}✓ localhost-mdns installed successfully${NC}"
      echo ""
      echo -e "${BLUE}Continuing with ${context}...${NC}"
      echo ""
      return 0
    else
      echo ""
      echo -e "${RED}✗ Failed to install localhost-mdns${NC}"
      echo ""
      echo -e "${YELLOW}You can install it manually later with:${NC}"
      echo "  isle mdns install"
      echo ""
      return 1
    fi
  else
    echo ""
    echo -e "${YELLOW}Skipping mDNS installation.${NC}"
    echo ""
    echo "You can install it later with:"
    echo "  isle mdns install"
    echo ""
    echo "Domain registration will be skipped for now."
    echo ""
    return 1
  fi
}

# Update registry.json with the new app information
update_registry() {
  # Temporarily disable exit on error for this function
  set +e

  echo "[10/11] Updating registry.json..."

  # Determine registry.json location (isle-agent directory)
  local registry_file="$PROJECT_ROOT/isle-agent/registry.json"

  # Check if jq is available
  if ! command -v jq &> /dev/null; then
    echo -e "${YELLOW}⚠ jq not found - skipping registry update${NC}"
    echo "  Install jq to enable automatic registry updates"
    set -e  # Re-enable before returning
    return 0
  fi

  # Create registry.json with empty structure if it doesn't exist
  if [ ! -f "$registry_file" ]; then
    echo "  Creating new registry.json..."
    mkdir -p "$(dirname "$registry_file")"
    cat > "$registry_file" <<EOF
{
  "domains": {},
  "subdomains": {},
  "apps": {}
}
EOF
  fi

  echo "  Extracting service information..."

  # Extract service information from compose file
  local services
  services=$(yq eval '.services | keys | .[]' "$COMPOSE_FILE") || {
    echo -e "${RED}✗ Failed to extract services from compose file${NC}"
    return 1
  }

  local subdomains_array=()
  local services_json="[]"

  while IFS= read -r service; do
    # Skip proxy services
    if [[ "$service" =~ proxy ]] || [[ "$service" =~ nginx ]] || [[ "$service" =~ traefik ]]; then
      continue
    fi

    local subdomain
    subdomain=$(yq eval ".services.$service.labels.\"mesh.subdomain\" // \"$service\"" "$COMPOSE_FILE") || subdomain="$service"
    subdomains_array+=("$subdomain.$DOMAIN")

    # Extract port
    local port
    port=$(yq eval ".services.$service.expose[0] // .services.$service.ports[0]" "$COMPOSE_FILE" | sed 's/:.*//')
    if [ -z "$port" ] || [ "$port" = "null" ]; then
      port="80"
    fi

    # Extract container_name from compose file, fall back to Docker Compose convention
    local container_name
    container_name=$(yq eval ".services.$service.container_name // \"\"" "$COMPOSE_FILE")
    if [ -z "$container_name" ] || [ "$container_name" = "null" ]; then
      container_name="${PROJECT_NAME}-${service}-1"
    fi

    # Build services JSON array entry
    services_json=$(echo "$services_json" | jq \
      --arg name "$service" \
      --arg subdomain "$subdomain" \
      --arg container "$container_name" \
      --argjson port "$port" \
      --arg protocol "http" \
      '. + [{"name": $name, "subdomain": $subdomain, "container": $container, "port": $port, "protocol": $protocol}]')

  done <<< "$services"

  local service_count
  service_count=$(echo "$services_json" | jq 'length')
  echo "  Found $service_count services to register"

  # Determine mode (local vs isle)
  local mode="local"

  # Generate timestamp in ISO format
  local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%S.%6N")

  # Create temporary file for jq operations
  local temp_file=$(mktemp)

  # Update domains section
  jq --arg domain "$DOMAIN" --arg app "$PROJECT_NAME" \
    '.domains[$domain] = $app' "$registry_file" > "$temp_file" && mv "$temp_file" "$registry_file" || {
    echo -e "${RED}✗ Failed to update domains section${NC}"
    return 1
  }

  # Update subdomains section
  for subdomain in "${subdomains_array[@]}"; do
    jq --arg subdomain "$subdomain" --arg app "$PROJECT_NAME" \
      '.subdomains[$subdomain] = $app' "$registry_file" > "$temp_file" && mv "$temp_file" "$registry_file" || {
      echo -e "${RED}✗ Failed to update subdomain: $subdomain${NC}"
      return 1
    }
  done

  # Update apps section with unified format (services as array of objects)
  jq --arg app "$PROJECT_NAME" \
     --arg domain "$DOMAIN" \
     --argjson services "$services_json" \
     --arg mode "$mode" \
     --arg timestamp "$timestamp" \
    '.apps[$app] = {
      "domain": $domain,
      "services": $services,
      "modes": [$mode],
      "updated_at": $timestamp
    }' "$registry_file" > "$temp_file" && mv "$temp_file" "$registry_file" || {
    echo -e "${RED}✗ Failed to update apps section${NC}"
    return 1
  }

  # Touch the file to ensure mtime change triggers the registry watcher
  touch "$registry_file"

  echo -e "${GREEN}✓ Registry updated: $registry_file${NC}"
  echo "  Added domain: $DOMAIN -> $PROJECT_NAME"
  echo "  Added $service_count service(s) with container details"
  echo "  Added app metadata for $PROJECT_NAME"

  # Re-enable exit on error
  set -e
}

# Check if isle-vlan-agent is running and notify user
notify_agent_status() {
  echo ""
  echo -e "${BLUE}Checking isle-vlan-agent status...${NC}"

  if docker ps --filter "name=isle-vlan-agent" --filter "status=running" --format '{{.Names}}' 2>/dev/null | grep -q "^isle-vlan-agent$"; then
    echo -e "${GREEN}✓ isle-vlan-agent is running${NC}"
    echo "  Your app will be proxied by the agent after running: isle app up"
  else
    echo ""
    echo -e "${YELLOW}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║  isle-vlan-agent is NOT running                                ║${NC}"
    echo -e "${YELLOW}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "  The isle-vlan-agent is required to proxy traffic to your app."
    echo "  Start it before deploying your app:"
    echo ""
    echo "    isle agent start"
    echo ""
    echo "  Then deploy your app:"
    echo ""
    echo "    isle app up"
    echo ""
  fi
}

# Register domains with mDNS system if installed
register_domains() {
  echo "[11/11] Registering domains with mDNS..."

  # Ensure mDNS is installed and running
  if ! ensure_mdns_installed "register your app domains"; then
    return 0
  fi

  # Path to domain detection script
  local detect_script="$PROJECT_ROOT/mdns/scripts/mesh-mdns-domains-detect.sh"

  if [ ! -f "$detect_script" ]; then
    echo -e "${YELLOW}⚠ Domain detection script not found - skipping${NC}"
    return 0
  fi

  # Paths to the generated files
  local mesh_config="$OUTPUT_DIR/isle-mesh.yml"
  local mesh_compose="$OUTPUT_DIR/docker-compose.mesh-app.yml"

  echo "  Detecting and registering domains from generated files..."

  # Run domain detection in append mode (don't replace existing domains)
  if bash "$detect_script" "$mesh_config" "$mesh_compose" "append" 2>&1 | sed 's/^/    /'; then
    echo -e "${GREEN}✓ Domains registered with mDNS${NC}"

    # Automatically reload mDNS service
    echo "  Reloading mDNS broadcast service..."
    if sudo systemctl restart mesh-mdns.service 2>/dev/null; then
      echo -e "${GREEN}✓ mDNS service reloaded - domains are now broadcasting${NC}"
    else
      echo -e "${YELLOW}⚠ Failed to reload mDNS service${NC}"
      echo "  Run manually: isle mdns reload"
    fi
  else
    echo -e "${YELLOW}⚠ Failed to register domains${NC}"
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
- \`docker-compose.mesh-app.yml\` - Mesh-integrated Docker Compose file (uses isle-agent-net)
- \`isle-mesh.yml\` - Mesh network and service configuration
- \`config/env-manifest.json\` - Manifest tracking environment files and variables
- \`config/ssl.env.conf\` - SSL certificate configuration
- \`config/*.env\` - Copied environment files from your original project

### SSL Certificates
- \`ssl/certs/\` - Generated SSL certificates (also copied to isle-agent)
- \`ssl/keys/\` - Private keys (also copied to isle-agent)

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

### 5. Ensure the Isle Agent is Running

\`\`\`bash
isle agent start
\`\`\`

### 6. Deploy Your App

\`\`\`bash
isle app up
\`\`\`

The isle-vlan-agent will automatically proxy traffic to your app containers.

### 7. Access Your Services
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
1. Check if containers are running: \`isle app ps\`
2. Check agent is running: \`isle agent status\`
3. Check agent logs: \`docker logs isle-vlan-agent\`
4. Verify DNS resolution: \`ping $DOMAIN\`

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
  echo "  • docker-compose.mesh-app.yml (mesh-integrated compose file, uses isle-agent-net)"
  echo "  • isle-mesh.yml (mesh network configuration)"
  echo "  • config/env-manifest.json (environment file tracking)"
  echo "  • config/ssl.env.conf (SSL configuration)"
  echo "  • config/*.env (copied environment files)"
  echo "  • ssl/certs/ (SSL certificates)"
  echo "  • ssl/keys/ (private keys)"
  echo "  • ISLE-MESH-README.md (setup instructions)"
  echo ""
  echo "Registry updated:"
  echo "  • $PROJECT_ROOT/isle-agent/registry.json (app registered with container details)"
  echo ""
  echo "Next steps:"
  echo "  1. Ensure the isle-agent is running: isle agent start"
  echo "  2. Review generated configuration files"
  echo "  3. Deploy with: isle app up"
  echo "  4. The isle-vlan-agent will automatically proxy your app"
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
  update_registry
  register_domains
  notify_agent_status
  generate_readme

  # Completion summary
  print_summary
}

# Entry point
main "$@"
