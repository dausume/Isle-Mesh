#!/bin/bash
#
# sync-lh-mdns-and-agent-registry.sh
# Synchronizes localhost mDNS domains with agent registry configuration
#
# This script ensures that all .local domains registered in the agent
# registry are also configured for mDNS broadcasting.

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Configuration
AGENT_REGISTRY="${AGENT_REGISTRY:-/etc/isle-mesh/agent/registry.json}"
MDNS_DOMAIN_LIST="${MDNS_DOMAIN_LIST:-/usr/local/etc/mesh-mdns-domains.list}"
DRY_RUN="${DRY_RUN:-false}"

# Logging functions
log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# Check if required commands are available
check_requirements() {
    local missing=0

    if ! command -v jq &> /dev/null; then
        log_error "jq is required but not installed"
        missing=1
    fi

    return $missing
}

# Extract .local domains from agent registry
extract_agent_domains() {
    local registry_file="$1"

    if [[ ! -f "$registry_file" ]]; then
        log_warn "Agent registry not found: $registry_file"
        echo ""
        return 0
    fi

    # Extract all domains from apps that end with .local
    jq -r '.apps | to_entries[] | .value.domain | select(endswith(".local"))' "$registry_file" 2>/dev/null | sort -u || echo ""
}

# Read current mDNS domain list
read_mdns_domains() {
    local domain_list="$1"

    if [[ ! -f "$domain_list" ]]; then
        echo ""
        return 0
    fi

    # Read non-comment, non-empty lines
    grep -v '^#' "$domain_list" 2>/dev/null | grep -v '^[[:space:]]*$' | sort -u || echo ""
}

# Add domain to mDNS list
add_mdns_domain() {
    local domain="$1"
    local domain_list="$2"

    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Would add domain: $domain"
        return 0
    fi

    # Create directory if it doesn't exist
    sudo mkdir -p "$(dirname "$domain_list")" 2>/dev/null || mkdir -p "$(dirname "$domain_list")"

    # Add domain if not already present
    if ! grep -Fxq "$domain" "$domain_list" 2>/dev/null; then
        echo "$domain" | sudo tee -a "$domain_list" > /dev/null 2>&1 || echo "$domain" >> "$domain_list"
        log_success "Added domain to mDNS list: $domain"
        return 0
    else
        log_info "Domain already in mDNS list: $domain"
        return 1
    fi
}

# Remove domain from mDNS list
remove_mdns_domain() {
    local domain="$1"
    local domain_list="$2"

    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Would remove domain: $domain"
        return 0
    fi

    if [[ ! -f "$domain_list" ]]; then
        return 1
    fi

    # Remove domain from list
    local temp_file
    temp_file=$(mktemp)
    grep -Fxv "$domain" "$domain_list" > "$temp_file" 2>/dev/null || true

    if sudo mv "$temp_file" "$domain_list" 2>/dev/null || mv "$temp_file" "$domain_list"; then
        log_success "Removed domain from mDNS list: $domain"
        return 0
    else
        rm -f "$temp_file"
        log_error "Failed to remove domain: $domain"
        return 1
    fi
}

# Reload mDNS service
reload_mdns_service() {
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Would reload mesh-mdns.service"
        return 0
    fi

    # Check if service exists and is running
    if systemctl is-active --quiet mesh-mdns.service 2>/dev/null; then
        log_info "Reloading mesh-mdns.service..."
        if sudo systemctl reload mesh-mdns.service 2>/dev/null; then
            log_success "mDNS service reloaded"
            return 0
        else
            log_warn "Failed to reload mesh-mdns.service"
            return 1
        fi
    else
        log_warn "mesh-mdns.service is not running - skipping reload"
        return 1
    fi
}

# Main sync function
sync_domains() {
    log_info "Starting mDNS <-> Agent Registry sync..."
    echo ""

    # Extract domains from agent registry
    log_info "Reading agent registry: $AGENT_REGISTRY"
    local agent_domains
    agent_domains=$(extract_agent_domains "$AGENT_REGISTRY")

    local agent_count=0
    if [[ -n "$agent_domains" ]]; then
        agent_count=$(echo "$agent_domains" | wc -l)
    fi
    log_info "Found $agent_count .local domain(s) in agent registry"

    # Read current mDNS domain list
    log_info "Reading mDNS domain list: $MDNS_DOMAIN_LIST"
    local mdns_domains
    mdns_domains=$(read_mdns_domains "$MDNS_DOMAIN_LIST")

    local mdns_count=0
    if [[ -n "$mdns_domains" ]]; then
        mdns_count=$(echo "$mdns_domains" | wc -l)
    fi
    log_info "Found $mdns_count domain(s) in mDNS list"
    echo ""

    # Track if any changes were made
    local changes_made=false

    # Add domains from agent registry to mDNS list
    if [[ -n "$agent_domains" ]]; then
        log_info "Syncing agent domains to mDNS list..."
        while IFS= read -r domain; do
            if [[ -n "$domain" ]]; then
                if add_mdns_domain "$domain" "$MDNS_DOMAIN_LIST"; then
                    changes_made=true
                fi
            fi
        done <<< "$agent_domains"
        echo ""
    fi

    # Optionally remove domains from mDNS list that are not in agent registry
    # This is commented out by default to be conservative - uncomment if you want automatic cleanup
    # log_info "Checking for orphaned mDNS domains..."
    # if [[ -n "$mdns_domains" ]]; then
    #     while IFS= read -r domain; do
    #         if [[ -n "$domain" ]] && ! echo "$agent_domains" | grep -Fxq "$domain"; then
    #             log_warn "Domain in mDNS list but not in agent registry: $domain"
    #             # Uncomment to auto-remove:
    #             # if remove_mdns_domain "$domain" "$MDNS_DOMAIN_LIST"; then
    #             #     changes_made=true
    #             # fi
    #         fi
    #     done <<< "$mdns_domains"
    # fi

    # Reload mDNS service if changes were made
    if [[ "$changes_made" == "true" ]]; then
        echo ""
        reload_mdns_service
    else
        log_info "No changes needed - domains are already in sync"
    fi

    echo ""
    log_success "Sync complete!"
}

# Show help
show_help() {
    cat <<EOF
${BOLD}sync-lh-mdns-and-agent-registry.sh${NC}

Synchronizes localhost mDNS domains with agent registry configuration.

${BOLD}Usage:${NC}
  $0 [options]

${BOLD}Options:${NC}
  --dry-run              Show what would be done without making changes
  --help, -h             Show this help message

${BOLD}Environment Variables:${NC}
  AGENT_REGISTRY         Path to agent registry.json
                         (default: /etc/isle-mesh/agent/registry.json)

  MDNS_DOMAIN_LIST       Path to mDNS domain list
                         (default: /usr/local/etc/mesh-mdns-domains.list)

  DRY_RUN                Set to 'true' to enable dry-run mode
                         (default: false)

${BOLD}Description:${NC}
  This script ensures that all .local domains registered in the agent
  registry are also configured for mDNS broadcasting. It:

  1. Reads the agent registry to find all registered app domains
  2. Filters for .local domains (required for mDNS)
  3. Adds missing domains to the mDNS broadcast list
  4. Reloads the mesh-mdns service if changes were made

${BOLD}Examples:${NC}
  # Normal sync
  $0

  # Dry-run to see what would change
  $0 --dry-run

  # Use custom paths
  AGENT_REGISTRY=/custom/path/registry.json $0

EOF
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --help|-h)
            show_help
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# Main execution
main() {
    check_requirements || exit 1
    sync_domains
}

main
