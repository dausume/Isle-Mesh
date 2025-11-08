#!/bin/bash

#############################################################################
# OpenWRT Image Download Script
#
# Downloads the latest OpenWRT x86_64 image for use with KVM
#
# Usage: ./download-image.sh [options]
#
# Options:
#   -v, --version VERSION   OpenWRT version (default: 23.05.3)
#   -o, --output DIR        Output directory (default: ../images)
#   -h, --help              Show this help message
#############################################################################

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Default configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OPENWRT_VERSION="23.05.3"
OUTPUT_DIR="$PROJECT_ROOT/images"

# Log functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Show usage
show_usage() {
    cat << EOF
OpenWRT Image Download Script

Usage: $0 [options]

Options:
  -v, --version VERSION   OpenWRT version (default: 23.05.3)
  -o, --output DIR        Output directory (default: ../images)
  -h, --help              Show this help message

Available Versions:
  - 23.05.3 (Latest stable)
  - 23.05.2
  - 22.03.5

The script will download the x86_64 generic image suitable for KVM/QEMU.

EOF
}

# Parse arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -v|--version)
                OPENWRT_VERSION="$2"
                shift 2
                ;;
            -o|--output)
                OUTPUT_DIR="$2"
                shift 2
                ;;
            -h|--help)
                show_usage
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                show_usage
                exit 1
                ;;
        esac
    done
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."

    for cmd in wget qemu-img; do
        if ! command -v $cmd &> /dev/null; then
            log_error "Missing required command: $cmd"
            exit 1
        fi
    done

    log_success "Prerequisites met"
}

# Download and process image
download_image() {
    log_info "Downloading OpenWRT ${OPENWRT_VERSION}..."

    mkdir -p "$OUTPUT_DIR"

    # Construct download URL
    local IMAGE_NAME="openwrt-${OPENWRT_VERSION}-x86-64-generic-ext4-combined.img.gz"
    local DOWNLOAD_URL="https://downloads.openwrt.org/releases/${OPENWRT_VERSION}/targets/x86/64/${IMAGE_NAME}"

    local COMPRESSED_IMAGE="$OUTPUT_DIR/$IMAGE_NAME"
    local EXTRACTED_IMAGE="${COMPRESSED_IMAGE%.gz}"
    local QCOW2_IMAGE="$OUTPUT_DIR/openwrt-${OPENWRT_VERSION}.qcow2"

    # Download if not exists
    if [[ -f "$QCOW2_IMAGE" ]]; then
        log_success "Image already exists: $QCOW2_IMAGE"
        return 0
    fi

    if [[ ! -f "$COMPRESSED_IMAGE" ]]; then
        log_info "Downloading from: $DOWNLOAD_URL"
        wget --progress=bar:force -O "$COMPRESSED_IMAGE" "$DOWNLOAD_URL" || {
            log_error "Failed to download image"
            rm -f "$COMPRESSED_IMAGE"
            exit 1
        }
        log_success "Downloaded: $COMPRESSED_IMAGE"
    else
        log_info "Using cached download: $COMPRESSED_IMAGE"
    fi

    # Extract if needed
    if [[ ! -f "$EXTRACTED_IMAGE" ]]; then
        log_info "Extracting image..."
        gunzip -k "$COMPRESSED_IMAGE" || {
            log_error "Failed to extract image"
            exit 1
        }
        log_success "Extracted: $EXTRACTED_IMAGE"
    else
        log_info "Image already extracted: $EXTRACTED_IMAGE"
    fi

    # Convert to qcow2
    log_info "Converting to qcow2 format..."
    qemu-img convert -f raw -O qcow2 "$EXTRACTED_IMAGE" "$QCOW2_IMAGE" || {
        log_error "Failed to convert image"
        exit 1
    }
    log_success "Converted to qcow2: $QCOW2_IMAGE"

    # Resize to 2GB
    log_info "Resizing disk to 2GB..."
    qemu-img resize "$QCOW2_IMAGE" 2G || {
        log_warning "Failed to resize image"
    }

    # Show image info
    log_info "Image information:"
    qemu-img info "$QCOW2_IMAGE"

    # Cleanup extracted raw image
    if [[ -f "$EXTRACTED_IMAGE" ]]; then
        log_info "Cleaning up extracted image..."
        rm -f "$EXTRACTED_IMAGE"
    fi

    log_success "Final image: $QCOW2_IMAGE"
}

# Display summary
show_summary() {
    cat << EOF

${GREEN}╔═══════════════════════════════════════════════════════════════════════════╗
║                    OpenWRT Image Downloaded                               ║
╚═══════════════════════════════════════════════════════════════════════════╝${NC}

${BLUE}Image Details:${NC}
  Version:    ${OPENWRT_VERSION}
  Format:     qcow2
  Location:   $OUTPUT_DIR/openwrt-${OPENWRT_VERSION}.qcow2

${BLUE}Next Steps:${NC}
  1. Run provisioning script: sudo ./provision-vm.sh
  2. Or manually specify image: sudo ./provision-vm.sh -i "$OUTPUT_DIR/openwrt-${OPENWRT_VERSION}.qcow2"

${BLUE}Image Information:${NC}

EOF

    qemu-img info "$OUTPUT_DIR/openwrt-${OPENWRT_VERSION}.qcow2" 2>/dev/null || true

    echo ""
}

# Main execution
main() {
    log_info "Starting OpenWRT image download..."

    parse_args "$@"
    check_prerequisites
    download_image
    show_summary

    log_success "Download complete!"
}

# Run main function
main "$@"
