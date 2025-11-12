#!/usr/bin/env bash
# BEGIN: 80-copy-packages.sh
if [[ -n "${_COPY_PACKAGES_SH_SOURCED:-}" ]]; then return 0; fi; _COPY_PACKAGES_SH_SOURCED=1

copy_packages_to_router() {
  log_step "Step 8: Copying Packages to Router VM"

  # Set packages directory relative to router-setup
  local script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  local packages_dir="${script_dir}/../packages"

  # Router connection details
  local router_ip="${ROUTER_IP:-192.168.1.1}"
  local router_user="${ROUTER_USER:-root}"
  local router_dest="${ROUTER_PACKAGE_DIR:-/tmp/packages}"

  # Check if packages directory exists and has files
  if [[ ! -d "$packages_dir" ]]; then
    log_error "Packages directory not found: $packages_dir"
    exit 1
  fi

  local pkg_count=$(ls -1 "$packages_dir"/*.ipk 2>/dev/null | wc -l)
  if [[ $pkg_count -eq 0 ]]; then
    log_warning "No .ipk files found in $packages_dir"
    log_info "Skipping package copy"
    return 0
  fi

  log_info "Found $pkg_count package file(s) to copy"
  log_info "Router IP: $router_ip"
  log_info "Destination: $router_dest"

  # Wait for SSH to be available (with timeout)
  log_info "Waiting for SSH connection to router..."
  local max_attempts=30
  local attempt=0
  while ! ssh -o ConnectTimeout=2 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            "${router_user}@${router_ip}" "exit" 2>/dev/null; do
    attempt=$((attempt + 1))
    if [[ $attempt -ge $max_attempts ]]; then
      log_error "Failed to connect to router via SSH after $max_attempts attempts"
      exit 1
    fi
    sleep 2
  done
  log_success "SSH connection established"

  # Create destination directory on router
  log_info "Creating destination directory on router"
  if ! ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
          "${router_user}@${router_ip}" "mkdir -p ${router_dest}" 2>/dev/null; then
    log_error "Failed to create directory on router"
    exit 1
  fi

  # Copy packages using SCP
  log_info "Copying packages to router..."
  if scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
         "$packages_dir"/*.ipk "${router_user}@${router_ip}:${router_dest}/" 2>/dev/null; then
    log_success "Successfully copied $pkg_count package(s) to router"

    # Verify files on router
    local remote_count=$(ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                            "${router_user}@${router_ip}" "ls -1 ${router_dest}/*.ipk 2>/dev/null | wc -l" 2>/dev/null)
    log_info "Verified $remote_count file(s) on router"
  else
    log_error "Failed to copy packages to router"
    exit 1
  fi
}
# END: 80-copy-packages.sh
