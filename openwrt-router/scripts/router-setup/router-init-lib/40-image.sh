#!/usr/bin/env bash
# BEGIN: 40-image.sh
if [[ -n "${_IMAGE_SH_SOURCED:-}" ]]; then return 0; fi; _IMAGE_SH_SOURCED=1

# The image is NOT shipped in git (see images/README.md). It is
# obtained by get-router-image.sh, which tries: local cache → the mesh
# → our public release → build from the upstream OpenWRT base.
#
# When running as the bundled router-init.sh, the sibling scripts may
# not be alongside us, so this keeps a self-contained build-from-
# upstream fallback — the same last resort the chain ends in.

_ROUTER_IMAGE_FALLBACK_VERSION="23.05.3"
_ROUTER_IMAGE_FALLBACK_SHA256="7643950cbf6bf5d785525f300efc4e2ceaad613b5d0725886e7f5fcc5a9d18f9"

# Inline last resort: fetch the upstream base, verify it, convert it.
_build_image_from_upstream() {
  local qcow2="$1"
  local version="$_ROUTER_IMAGE_FALLBACK_VERSION"
  local name="openwrt-${version}-x86-64-generic-ext4-combined.img.gz"
  local url="https://downloads.openwrt.org/releases/${version}/targets/x86/64/${name}"
  local compressed="$IMAGE_DIR/$name"
  local extracted="${compressed%.gz}"

  if [[ ! -f "$compressed" ]]; then
    log_info "Downloading OpenWRT ${version} base..."
    wget -q --show-progress -O "$compressed" "$url" || {
      log_error "Failed to download the upstream base"; rm -f "$compressed"; return 1; }
  fi

  # Refuse to build from a base we cannot vouch for.
  local got
  got="$(sha256sum "$compressed" | awk '{print $1}')"
  if [[ "$got" != "$_ROUTER_IMAGE_FALLBACK_SHA256" ]]; then
    log_error "Upstream base checksum mismatch — refusing to build"
    log_error "  expected $_ROUTER_IMAGE_FALLBACK_SHA256"
    log_error "  got      $got"
    return 1
  fi

  # Upstream's .gz has trailing padding: gunzip exits non-zero with
  # "trailing garbage ignored" on a good file, so judge by the result.
  log_info "Extracting base image..."
  gunzip -kf "$compressed" || true
  [[ -s "$extracted" ]] || { log_error "Extraction produced no image"; return 1; }

  log_info "Converting to qcow2..."
  qemu-img convert -f raw -O qcow2 "$extracted" "$qcow2" || return 1
  qemu-img resize "$qcow2" 4G >/dev/null || return 1
  rm -f "$extracted"
  return 0
}

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

  # Preferred: the full acquisition chain.
  local getter="${PROJECT_ROOT:-$SCRIPT_DIR}/get-router-image.sh"
  if [[ -x "$getter" ]]; then
    log_info "Obtaining router image (cache → mesh → release → build)..."
    if IMAGE_DIR="$IMAGE_DIR" "$getter" -o "$QCOW2_IMAGE"; then
      log_success "Image ready: $QCOW2_IMAGE"
      return
    fi
    log_warning "Acquisition chain failed — falling back to a direct upstream build"
  fi

  # Bundled/standalone: build from upstream directly.
  if _build_image_from_upstream "$QCOW2_IMAGE"; then
    log_success "Image ready: $QCOW2_IMAGE"
    return
  fi

  log_error "Could not obtain the router image"
  exit 1
}
# END: 40-image.sh
