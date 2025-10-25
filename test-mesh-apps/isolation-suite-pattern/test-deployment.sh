#!/bin/bash

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Base directory
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

print_header() {
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}========================================${NC}"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_info() {
    echo -e "${YELLOW}→ $1${NC}"
}

test_endpoint() {
    local url=$1
    local description=$2

    response=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null)

    if [ "$response" = "200" ]; then
        print_success "$description: $url (HTTP $response)"
        return 0
    else
        print_error "$description: $url (HTTP $response)"
        return 1
    fi
}

test_isolation_mode() {
    print_header "Testing Isolation Mode"

    print_info "Starting Service A in isolation mode..."
    cd "$BASE_DIR/service-a"
    docker-compose up -d > /dev/null 2>&1

    print_info "Starting Service B in isolation mode..."
    cd "$BASE_DIR/service-b"
    docker-compose up -d > /dev/null 2>&1

    print_info "Waiting for services to start..."
    sleep 5

    echo ""
    print_info "Testing Service A endpoints..."
    test_endpoint "http://localhost:5001/" "Service A root"
    test_endpoint "http://localhost:5001/health" "Service A health"
    test_endpoint "http://localhost:5001/api/data" "Service A data"

    echo ""
    print_info "Testing Service B endpoints..."
    test_endpoint "http://localhost:6001/" "Service B root"
    test_endpoint "http://localhost:6001/health" "Service B health"
    test_endpoint "http://localhost:6001/api/process" "Service B process"

    echo ""
    print_info "Checking isolation labels..."
    isolation_label_a=$(docker inspect service-a-isolation 2>/dev/null | jq -r '.[0].Config.Labels["mesh.isolation"]')
    isolation_label_b=$(docker inspect service-b-isolation 2>/dev/null | jq -r '.[0].Config.Labels["mesh.isolation"]')

    if [ "$isolation_label_a" = "true" ] && [ "$isolation_label_b" = "true" ]; then
        print_success "Isolation labels correctly set"
    else
        print_error "Isolation labels incorrect"
    fi

    echo ""
    print_info "Fetching process response to verify no integration..."
    process_response=$(curl -s "http://localhost:6001/api/process")
    integration_status=$(echo "$process_response" | jq -r '.integration')

    if [ "$integration_status" = "not-configured" ]; then
        print_success "Service B running in isolation (no Service A integration)"
    else
        print_error "Service B should not be integrated in isolation mode"
    fi

    echo ""
    print_info "Isolation mode test complete!"
}

test_suite_mode() {
    print_header "Testing Suite Mode"

    print_info "Starting services in suite mode..."
    cd "$BASE_DIR/suite"
    docker-compose up -d > /dev/null 2>&1

    print_info "Waiting for services to start and become healthy..."
    sleep 10

    echo ""
    print_info "Testing Service A endpoints..."
    test_endpoint "http://localhost:5001/" "Service A root"
    test_endpoint "http://localhost:5001/health" "Service A health"
    test_endpoint "http://localhost:5001/api/data" "Service A data"

    echo ""
    print_info "Testing Service B endpoints..."
    test_endpoint "http://localhost:6001/" "Service B root"
    test_endpoint "http://localhost:6001/health" "Service B health"
    test_endpoint "http://localhost:6001/api/process" "Service B process"
    test_endpoint "http://localhost:6001/api/status" "Service B status"

    echo ""
    print_info "Checking suite labels..."
    suite_label_a=$(docker inspect service-a-suite 2>/dev/null | jq -r '.[0].Config.Labels["mesh.suite"]')
    suite_label_b=$(docker inspect service-b-suite 2>/dev/null | jq -r '.[0].Config.Labels["mesh.suite"]')

    if [ "$suite_label_a" = "true" ] && [ "$suite_label_b" = "true" ]; then
        print_success "Suite labels correctly set"
    else
        print_error "Suite labels incorrect"
    fi

    echo ""
    print_info "Verifying Service B can reach Service A..."
    status_response=$(curl -s "http://localhost:6001/api/status")
    service_a_configured=$(echo "$status_response" | jq -r '.service_a_configured')

    if [ "$service_a_configured" = "true" ]; then
        print_success "Service A URL configured in Service B"
    else
        print_error "Service A URL not configured in Service B"
    fi

    echo ""
    print_info "Testing integration..."
    process_response=$(curl -s "http://localhost:6001/api/process")
    integration_status=$(echo "$process_response" | jq -r '.integration')

    if [ "$integration_status" = "success" ]; then
        print_success "Service B successfully integrated with Service A"
        echo "$process_response" | jq .
    else
        print_error "Integration failed: $integration_status"
        echo "$process_response" | jq .
    fi

    echo ""
    print_info "Testing internal network connectivity..."
    docker exec service-b-suite curl -s http://service-a:5000/health > /dev/null 2>&1
    if [ $? -eq 0 ]; then
        print_success "Service B can reach Service A via internal network"
    else
        print_error "Service B cannot reach Service A via internal network"
    fi

    echo ""
    print_info "Suite mode test complete!"
}

cleanup_isolation() {
    print_info "Cleaning up isolation mode..."
    cd "$BASE_DIR/service-a"
    docker-compose down > /dev/null 2>&1
    cd "$BASE_DIR/service-b"
    docker-compose down > /dev/null 2>&1
    print_success "Isolation mode cleaned up"
}

cleanup_suite() {
    print_info "Cleaning up suite mode..."
    cd "$BASE_DIR/suite"
    docker-compose down > /dev/null 2>&1
    print_success "Suite mode cleaned up"
}

cleanup_all() {
    print_header "Cleaning Up All Deployments"
    cleanup_isolation
    cleanup_suite
    echo ""
    print_success "All deployments cleaned up"
}

show_help() {
    echo "Usage: $0 [OPTION]"
    echo ""
    echo "Test and manage isolation vs suite deployment patterns"
    echo ""
    echo "Options:"
    echo "  isolation     Test isolation mode deployment"
    echo "  suite         Test suite mode deployment"
    echo "  both          Test both modes sequentially"
    echo "  cleanup       Clean up all deployments"
    echo "  help          Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 isolation    # Test only isolation mode"
    echo "  $0 suite        # Test only suite mode"
    echo "  $0 both         # Test both modes"
    echo "  $0 cleanup      # Clean up everything"
}

# Main script logic
case "$1" in
    isolation)
        cleanup_all
        echo ""
        test_isolation_mode
        ;;
    suite)
        cleanup_all
        echo ""
        test_suite_mode
        ;;
    both)
        cleanup_all
        echo ""
        test_isolation_mode
        echo ""
        cleanup_isolation
        echo ""
        test_suite_mode
        ;;
    cleanup)
        cleanup_all
        ;;
    help|--help|-h|"")
        show_help
        ;;
    *)
        print_error "Unknown option: $1"
        echo ""
        show_help
        exit 1
        ;;
esac

exit 0
