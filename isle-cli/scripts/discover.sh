#!/bin/bash

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color

# Pretty printing functions
print_header() {
    echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║${NC}  $1"
    echo -e "${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
}

print_section() {
    echo ""
    echo -e "${CYAN}▸ $1${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

print_service() {
    echo -e "  ${GREEN}✓${NC} $1"
}

print_url() {
    echo -e "    ${MAGENTA}→${NC} $1"
}

print_info() {
    echo -e "    ${YELLOW}ℹ${NC} $1"
}

print_error() {
    echo -e "  ${RED}✗${NC} $1"
}

print_warning() {
    echo -e "  ${YELLOW}⚠${NC} $1"
}

# Function to check if a URL is accessible
check_url() {
    local url=$1
    local timeout=${2:-2}

    if curl -k -s -o /dev/null -w "%{http_code}" --connect-timeout "$timeout" "$url" | grep -qE "^(200|301|302|401|403)$"; then
        return 0
    else
        return 1
    fi
}

# Function to discover .local domains via mDNS/Avahi
discover_mdns() {
    print_section "Discovering via mDNS (Avahi)"

    local found=0

    if command -v avahi-browse &> /dev/null; then
        # Browse for HTTP services
        local services=$(timeout 3 avahi-browse -at 2>/dev/null | grep -E "(_http\._tcp|_https\._tcp)" | grep "\.local" | awk '{print $4}' | sort -u)

        if [ -n "$services" ]; then
            while IFS= read -r service; do
                if [ -n "$service" ]; then
                    print_service "$service"
                    print_url "http://${service}.local"
                    print_url "https://${service}.local"
                    found=$((found + 1))
                fi
            done <<< "$services"
        fi

        # Also check for any .local hostnames
        local hosts=$(timeout 3 avahi-browse -at 2>/dev/null | grep "\.local" | awk '{print $4}' | grep -v "^=" | sort -u | head -10)

        if [ -n "$hosts" ]; then
            while IFS= read -r host; do
                if [ -n "$host" ] && ! grep -q "$host" <<< "$services"; then
                    print_service "$host (discovered host)"
                    print_url "http://${host}.local"
                    found=$((found + 1))
                fi
            done <<< "$hosts"
        fi
    elif command -v dns-sd &> /dev/null; then
        # macOS alternative using dns-sd
        print_info "Using dns-sd for discovery (macOS)"
        timeout 3 dns-sd -B _http._tcp local. 2>/dev/null | grep "\.local" | while read -r line; do
            service=$(echo "$line" | awk '{print $7}' | sed 's/\.$//')
            if [ -n "$service" ]; then
                print_service "$service"
                print_url "http://${service}"
                found=$((found + 1))
            fi
        done
    else
        print_warning "Neither avahi-browse nor dns-sd found"
        print_info "Install avahi-utils: sudo apt-get install avahi-utils"
        return 1
    fi

    if [ $found -eq 0 ]; then
        print_warning "No mDNS services discovered"
    fi

    return 0
}

# Function to discover via Docker container labels
discover_docker_labels() {
    print_section "Discovering via Docker Container Labels"

    if ! command -v docker &> /dev/null; then
        print_error "Docker not found"
        return 1
    fi

    if ! docker ps &> /dev/null; then
        print_error "Cannot access Docker daemon"
        return 1
    fi

    local found=0

    # Get all running containers with mesh or domain labels
    local containers=$(docker ps --format "{{.ID}}:{{.Names}}" 2>/dev/null)

    if [ -z "$containers" ]; then
        print_warning "No running Docker containers found"
        return 0
    fi

    while IFS=: read -r container_id container_name; do
        # Check for various domain-related labels
        local domain=$(docker inspect "$container_id" 2>/dev/null | jq -r '.[0].Config.Labels["mesh.domain"] // empty')
        local subdomain=$(docker inspect "$container_id" 2>/dev/null | jq -r '.[0].Config.Labels["mesh.subdomain"] // empty')
        local service_name=$(docker inspect "$container_id" 2>/dev/null | jq -r '.[0].Config.Labels["mesh.service.name"] // empty')
        local proxy_routes=$(docker inspect "$container_id" 2>/dev/null | jq -r '.[0].Config.Labels["mesh.proxy.route"] // empty')

        # Build URL from labels
        if [ -n "$domain" ]; then
            if [ -n "$subdomain" ]; then
                local url="https://${subdomain}.${domain}"
            else
                local url="https://${domain}"
            fi

            print_service "$container_name"
            print_url "$url"

            if [ -n "$proxy_routes" ]; then
                print_info "Routes: $proxy_routes"
            fi

            found=$((found + 1))
        elif [ -n "$service_name" ]; then
            # Check if it's a .local service
            if [[ "$service_name" == *".local"* ]]; then
                print_service "$container_name ($service_name)"
                print_url "http://${service_name}"
                found=$((found + 1))
            fi
        fi
    done <<< "$containers"

    # Also check for mesh-proxy containers specifically
    local proxies=$(docker ps --filter "label=mesh.component=proxy" --format "{{.ID}}:{{.Names}}" 2>/dev/null)

    if [ -n "$proxies" ]; then
        echo ""
        print_info "Found mesh-proxy containers:"

        while IFS=: read -r proxy_id proxy_name; do
            print_service "$proxy_name (mesh proxy)"

            # Try to extract domain from environment variables
            local domains=$(docker exec "$proxy_id" env 2>/dev/null | grep -i "domain\|server_name" | grep -i "\.local" || echo "")
            if [ -n "$domains" ]; then
                echo "$domains" | while IFS= read -r line; do
                    local domain_val=$(echo "$line" | cut -d= -f2 | tr -d '"' | tr -d "'")
                    if [ -n "$domain_val" ]; then
                        print_url "https://${domain_val}"
                    fi
                done
            fi

            found=$((found + 1))
        done <<< "$proxies"
    fi

    if [ $found -eq 0 ]; then
        print_warning "No containers with mesh domain labels found"
    fi

    return 0
}

# Function to discover via nginx configuration files
discover_nginx_configs() {
    print_section "Discovering via Nginx Configuration Files"

    local found=0

    # Check running nginx containers
    if command -v docker &> /dev/null && docker ps &> /dev/null; then
        local nginx_containers=$(docker ps --filter "ancestor=nginx" --format "{{.ID}}:{{.Names}}" 2>/dev/null)

        if [ -z "$nginx_containers" ]; then
            nginx_containers=$(docker ps --filter "name=proxy" --format "{{.ID}}:{{.Names}}" 2>/dev/null)
        fi

        if [ -n "$nginx_containers" ]; then
            while IFS=: read -r container_id container_name; do
                print_info "Checking $container_name for .local domains"

                # Extract server_name directives from nginx config
                local domains=$(docker exec "$container_id" sh -c 'cat /etc/nginx/conf.d/*.conf /etc/nginx/nginx.conf 2>/dev/null' 2>/dev/null | \
                    grep -i "server_name" | \
                    grep -o "[a-zA-Z0-9.-]*\.local" | \
                    sort -u)

                if [ -n "$domains" ]; then
                    while IFS= read -r domain; do
                        if [ -n "$domain" ]; then
                            print_service "$domain (from $container_name)"
                            print_url "https://${domain}"
                            found=$((found + 1))
                        fi
                    done <<< "$domains"
                fi
            done <<< "$nginx_containers"
        fi
    fi

    # Check host system nginx configs
    if [ -d /etc/nginx ]; then
        print_info "Checking host nginx configuration"

        local host_domains=$(grep -r "server_name" /etc/nginx/ 2>/dev/null | \
            grep -o "[a-zA-Z0-9.-]*\.local" | \
            sort -u)

        if [ -n "$host_domains" ]; then
            while IFS= read -r domain; do
                if [ -n "$domain" ]; then
                    print_service "$domain (from host nginx)"
                    print_url "https://${domain}"
                    found=$((found + 1))
                fi
            done <<< "$host_domains"
        fi
    fi

    if [ $found -eq 0 ]; then
        print_warning "No .local domains found in nginx configurations"
    fi

    return 0
}

# Function to discover via /etc/hosts
discover_etc_hosts() {
    print_section "Discovering via /etc/hosts"

    if [ ! -r /etc/hosts ]; then
        print_error "Cannot read /etc/hosts"
        return 1
    fi

    local hosts=$(grep "\.local" /etc/hosts | grep -v "^#" | awk '{print $2}' | sort -u)

    if [ -n "$hosts" ]; then
        while IFS= read -r host; do
            if [ -n "$host" ]; then
                print_service "$host"
                print_url "http://${host}"
                print_url "https://${host}"
            fi
        done <<< "$hosts"
    else
        print_warning "No .local entries found in /etc/hosts"
    fi

    return 0
}

# Function to test discovered URLs
test_discovered_urls() {
    print_section "Testing Discovered URLs"

    local urls=()

    # Collect all URLs from different sources
    print_info "Collecting URLs from all discovery methods..."

    # From Docker labels
    if command -v docker &> /dev/null && docker ps &> /dev/null; then
        local container_domains=$(docker ps --format "{{.ID}}" 2>/dev/null | while read -r id; do
            docker inspect "$id" 2>/dev/null | jq -r '.[0].Config.Labels["mesh.domain"] // empty'
        done | grep -v "^$" | sort -u)

        while IFS= read -r domain; do
            [ -n "$domain" ] && urls+=("https://${domain}")
        done <<< "$container_domains"
    fi

    # From /etc/hosts
    local host_domains=$(grep "\.local" /etc/hosts 2>/dev/null | grep -v "^#" | awk '{print $2}' | sort -u)
    while IFS= read -r domain; do
        [ -n "$domain" ] && urls+=("https://${domain}")
    done <<< "$host_domains"

    # Test each unique URL
    if [ ${#urls[@]} -eq 0 ]; then
        print_warning "No URLs to test"
        return 0
    fi

    echo ""
    for url in "${urls[@]}"; do
        printf "  Testing %-40s " "$url"
        if check_url "$url" 2; then
            echo -e "${GREEN}✓ Accessible${NC}"
        else
            echo -e "${RED}✗ Not accessible${NC}"
        fi
    done

    return 0
}

# Function to show summary
show_summary() {
    print_section "Summary"

    local total_domains=0
    local accessible=0

    # Count unique domains from all sources
    local all_domains=""

    # Docker
    if command -v docker &> /dev/null && docker ps &> /dev/null; then
        all_domains+=$(docker ps --format "{{.ID}}" 2>/dev/null | while read -r id; do
            docker inspect "$id" 2>/dev/null | jq -r '.[0].Config.Labels["mesh.domain"] // empty'
        done)$'\n'
    fi

    # /etc/hosts
    all_domains+=$(grep "\.local" /etc/hosts 2>/dev/null | grep -v "^#" | awk '{print $2}')$'\n'

    # Count unique
    total_domains=$(echo "$all_domains" | grep -v "^$" | sort -u | wc -l)

    echo ""
    print_info "Total .local domains discovered: ${total_domains}"

    if [ "$total_domains" -eq 0 ]; then
        echo ""
        print_warning "No .local domains found!"
        print_info "To set up .local domains, you can:"
        echo "    1. Use 'isle mdns install' to set up system-wide mDNS"
        echo "    2. Add entries to /etc/hosts manually"
        echo "    3. Deploy mesh-app services with domain labels"
    fi
}

# Main discovery function
discover_all() {
    print_header "Isle-Mesh .local Domain Discovery"

    discover_docker_labels
    discover_nginx_configs
    discover_etc_hosts
    discover_mdns

    if [ "$1" == "--test" ] || [ "$1" == "-t" ]; then
        test_discovered_urls
    fi

    show_summary
}

# Export function for JSON output
export_json() {
    local output_file="${1:-discovered-domains.json}"

    echo "{" > "$output_file"
    echo '  "timestamp": "'$(date -Iseconds)'",' >> "$output_file"
    echo '  "sources": {' >> "$output_file"

    # Docker containers
    echo '    "docker": [' >> "$output_file"
    if command -v docker &> /dev/null && docker ps &> /dev/null; then
        docker ps --format "{{.ID}}" 2>/dev/null | while read -r id; do
            local domain=$(docker inspect "$id" 2>/dev/null | jq -r '.[0].Config.Labels["mesh.domain"] // empty')
            local name=$(docker inspect "$id" 2>/dev/null | jq -r '.[0].Name' | sed 's/^\///')

            if [ -n "$domain" ]; then
                echo "      {\"container\": \"$name\", \"domain\": \"$domain\"}," >> "$output_file"
            fi
        done
    fi
    echo '      {}' >> "$output_file"
    echo '    ],' >> "$output_file"

    # /etc/hosts
    echo '    "hosts": [' >> "$output_file"
    grep "\.local" /etc/hosts 2>/dev/null | grep -v "^#" | while read -r line; do
        local ip=$(echo "$line" | awk '{print $1}')
        local domain=$(echo "$line" | awk '{print $2}')
        echo "      {\"ip\": \"$ip\", \"domain\": \"$domain\"}," >> "$output_file"
    done
    echo '      {}' >> "$output_file"
    echo '    ]' >> "$output_file"

    echo '  }' >> "$output_file"
    echo '}' >> "$output_file"

    print_info "Exported to $output_file"
}

# Main command handling
case "${1:-all}" in
    all|--all)
        discover_all "${@:2}"
        ;;
    docker|--docker)
        print_header "Isle-Mesh .local Domain Discovery (Docker)"
        discover_docker_labels
        ;;
    nginx|--nginx)
        print_header "Isle-Mesh .local Domain Discovery (Nginx)"
        discover_nginx_configs
        ;;
    hosts|--hosts)
        print_header "Isle-Mesh .local Domain Discovery (/etc/hosts)"
        discover_etc_hosts
        ;;
    mdns|--mdns)
        print_header "Isle-Mesh .local Domain Discovery (mDNS)"
        discover_mdns
        ;;
    test|--test)
        discover_all --test
        ;;
    export|--export)
        export_json "${2:-discovered-domains.json}"
        ;;
    help|--help|-h)
        echo "Usage: isle discover [command] [options]"
        echo ""
        echo "Commands:"
        echo "  all             - Discover from all sources (default)"
        echo "  docker          - Discover from Docker container labels"
        echo "  nginx           - Discover from Nginx configurations"
        echo "  hosts           - Discover from /etc/hosts"
        echo "  mdns            - Discover from mDNS/Avahi"
        echo "  test            - Discover and test URL accessibility"
        echo "  export [file]   - Export discovered domains to JSON"
        echo ""
        echo "Options:"
        echo "  --test, -t      - Test URL accessibility after discovery"
        echo "  --help, -h      - Show this help message"
        echo ""
        echo "Examples:"
        echo "  isle discover                    # Discover all .local domains"
        echo "  isle discover docker             # Only check Docker labels"
        echo "  isle discover --test             # Discover and test URLs"
        echo "  isle discover export domains.json # Export to JSON"
        ;;
    *)
        echo "Unknown command: $1"
        echo "Use 'isle discover help' for usage information"
        exit 1
        ;;
esac
