#!/usr/bin/env bash
# BEGIN: 40-image.sh
if [[ -n "${_IMAGE_SH_SOURCED:-}" ]]; then return 0; fi; _IMAGE_SH_SOURCED=1

download_image() {
  log_step "Step 3: Preparing OpenWRT Image"
  mkdir -p "$IMAGE_DIR"

  local QCOW2_IMAGE="$IMAGE_DIR/openwrt-isle-router.qcow2"

  if [[ -n "${CUSTOM_IMAGE:-}" ]]; then
    if [[ ! -f "$CUSTOM_IMAGE" ]]; then
      log_error "Custom image not found: $CUSTOM_IMAGE"; exit 1
    fi
    log_info "Using custom image: $CUSTOM_IMAGE"
    cp "$CUSTOM_IMAGE" "$QCOW2_IMAGE"
    log_success "Custom image copied"
    return
  fi

  if [[ -f "$QCOW2_IMAGE" ]]; then
    log_success "Image already exists: $QCOW2_IMAGE"
    return
  fi

  local OPENWRT_VERSION="23.05.3"
  local IMAGE_NAME="openwrt-${OPENWRT_VERSION}-x86-64-generic-ext4-combined.img.gz"
  local DOWNLOAD_URL="https://downloads.openwrt.org/releases/${OPENWRT_VERSION}/targets/x86/64/${IMAGE_NAME}"

  local COMPRESSED="$IMAGE_DIR/$IMAGE_NAME"
  local EXTRACTED="${COMPRESSED%.gz}"

  if [[ ! -f "$COMPRESSED" ]]; then
    log_info "Downloading OpenWRT ${OPENWRT_VERSION}..."
    wget -q --show-progress -O "$COMPRESSED" "$DOWNLOAD_URL" || { log_error "Failed to download"; exit 1; }
  fi

  if [[ ! -f "$EXTRACTED" ]]; then
    log_info "Extracting image..."
    gunzip -k "$COMPRESSED" || { [[ -f "$EXTRACTED" ]] || { log_error "Failed to extract image"; exit 1; }; }
  fi

  log_info "Converting to qcow2..."
  qemu-img convert -f raw -O qcow2 "$EXTRACTED" "$QCOW2_IMAGE"
  qemu-img resize "$QCOW2_IMAGE" 4G
  rm -f "$EXTRACTED"
  log_success "Image ready: $QCOW2_IMAGE"
}
# END: 40-image.sh
