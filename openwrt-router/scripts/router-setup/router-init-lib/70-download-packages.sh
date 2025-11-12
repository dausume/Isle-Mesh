#!/usr/bin/env bash
# BEGIN: 70-download-packages.sh
if [[ -n "${_DOWNLOAD_PACKAGES_SH_SOURCED:-}" ]]; then return 0; fi; _DOWNLOAD_PACKAGES_SH_SOURCED=1

# Download required OpenWRT packages for offline installation
OPENWRT_VERSION="${OPENWRT_VERSION:-23.05.3}"
ARCH="x86_64"
TARGET="x86/64"

# Required packages
# Note: kmod-8021q is built into the kernel for x86/64, no need to install
# Note: avahi-dbus-daemon provides the daemon functionality
REQUIRED_PACKAGES=(
  "avahi-dbus-daemon"
  "avahi-utils"
  "ip-full"
  "tcpdump"
)

# Function to download a single package and its dependencies
_download_package() {
  local pkg_name="$1"
  local found=false
  local packages_dir="$2"

  log_info "Searching for package: $pkg_name"

  # Base URLs for package repositories
  local base_url="https://downloads.openwrt.org/releases/${OPENWRT_VERSION}"
  local repos=(
    "$base_url/targets/${TARGET}/packages"
    "$base_url/packages/${ARCH}/base"
    "$base_url/packages/${ARCH}/packages"
    "$base_url/packages/${ARCH}/luci"
    "$base_url/packages/${ARCH}/routing"
  )

  for repo in "${repos[@]}"; do
    # Download Packages.gz to get package list
    local temp_pkg_list=$(mktemp)
    if curl -sf "${repo}/Packages.gz" | gunzip > "$temp_pkg_list" 2>/dev/null; then
      # Find package in the list
      local pkg_file=$(awk -v pkg="$pkg_name" '
        /^Package:/ { current_pkg = $2 }
        /^Filename:/ && current_pkg == pkg { print $2; exit }
      ' "$temp_pkg_list")

      if [[ -n "$pkg_file" ]]; then
        local pkg_filename=$(basename "$pkg_file")
        local download_url="${repo}/${pkg_file}"

        # Check if already downloaded
        if [[ -f "$packages_dir/$pkg_filename" ]]; then
          log_info "Already exists: $pkg_filename"
          found=true
        else
          log_info "Downloading from $repo"
          if curl -fL -o "$packages_dir/$pkg_filename" "$download_url"; then
            log_info "Downloaded: $pkg_filename"
            found=true
          else
            log_error "Failed to download: $download_url"
          fi
        fi

        # Get dependencies
        local deps=$(awk -v pkg="$pkg_name" '
          /^Package:/ { current_pkg = $2 }
          /^Depends:/ && current_pkg == pkg {
            gsub(/,/, " ", $0)
            sub(/^Depends: /, "", $0)
            print $0
          }
        ' "$temp_pkg_list")

        if [[ -n "$deps" ]]; then
          log_info "Dependencies for $pkg_name: $deps"
          # Download dependencies recursively (simple version - no version checks)
          for dep in $deps; do
            # Remove version constraints
            dep=$(echo "$dep" | sed 's/[>=<].*//; s/ //g')
            # Skip already processed or meta packages
            if [[ ! -f "$packages_dir"/*"${dep}"*.ipk ]] && [[ "$dep" != "libc" ]]; then
              _download_package "$dep" "$packages_dir"
            fi
          done
        fi

        rm -f "$temp_pkg_list"
        break
      fi
      rm -f "$temp_pkg_list"
    fi
  done

  if [[ "$found" == false ]]; then
    log_warning "Package not found: $pkg_name"
  fi
}

download_openwrt_packages() {
  log_step "Step 7: Downloading OpenWRT Packages"

  # Set packages directory relative to router-setup
  local script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  local packages_dir="${script_dir}/../packages"

  # Create packages directory
  mkdir -p "$packages_dir"

  log_info "OpenWRT Version: $OPENWRT_VERSION"
  log_info "Architecture: $ARCH"
  log_info "Packages directory: $packages_dir"

  for pkg in "${REQUIRED_PACKAGES[@]}"; do
    _download_package "$pkg" "$packages_dir"
  done

  log_success "Package download complete!"
  log_info "Downloaded packages are in: $packages_dir"

  # List downloaded packages
  local pkg_count=$(ls -1 "$packages_dir"/*.ipk 2>/dev/null | wc -l)
  if [[ $pkg_count -gt 0 ]]; then
    log_info "Downloaded $pkg_count package file(s)"
  else
    log_warning "No .ipk files found"
  fi
}
# END: 70-download-packages.sh
