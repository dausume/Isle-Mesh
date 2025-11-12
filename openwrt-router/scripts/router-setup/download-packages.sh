#!/usr/bin/env bash
# Download required OpenWRT packages for offline installation
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PACKAGES_DIR="${PACKAGES_DIR:-$SCRIPT_DIR/packages}"
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

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info() { echo -e "${BLUE}[INFO]${NC} $*"; }
ok() { echo -e "${GREEN}[SUCCESS]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARNING]${NC} $*"; }
err() { echo -e "${RED}[ERROR]${NC} $*"; }

# Create packages directory
mkdir -p "$PACKAGES_DIR"

# Base URLs for package repositories
BASE_URL="https://downloads.openwrt.org/releases/${OPENWRT_VERSION}"
REPOS=(
  "$BASE_URL/targets/${TARGET}/packages"
  "$BASE_URL/packages/${ARCH}/base"
  "$BASE_URL/packages/${ARCH}/packages"
  "$BASE_URL/packages/${ARCH}/luci"
  "$BASE_URL/packages/${ARCH}/routing"
)

# Function to download a single package and its dependencies
download_package() {
  local pkg_name="$1"
  local found=false

  info "Searching for package: $pkg_name"

  for repo in "${REPOS[@]}"; do
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
        if [[ -f "$PACKAGES_DIR/$pkg_filename" ]]; then
          ok "Already exists: $pkg_filename"
          found=true
        else
          info "Downloading from $repo"
          if curl -fL -o "$PACKAGES_DIR/$pkg_filename" "$download_url"; then
            ok "Downloaded: $pkg_filename"
            found=true
          else
            err "Failed to download: $download_url"
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
          info "Dependencies for $pkg_name: $deps"
          # Download dependencies recursively (simple version - no version checks)
          for dep in $deps; do
            # Remove version constraints
            dep=$(echo "$dep" | sed 's/[>=<].*//; s/ //g')
            # Skip already processed or meta packages
            if [[ ! -f "$PACKAGES_DIR"/*"${dep}"*.ipk ]] && [[ "$dep" != "libc" ]]; then
              download_package "$dep"
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
    warn "Package not found: $pkg_name"
  fi
}

main() {
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  OpenWRT Package Downloader"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo
  info "OpenWRT Version: $OPENWRT_VERSION"
  info "Architecture: $ARCH"
  info "Packages directory: $PACKAGES_DIR"
  echo

  for pkg in "${REQUIRED_PACKAGES[@]}"; do
    download_package "$pkg"
  done

  echo
  ok "Package download complete!"
  info "Downloaded packages are in: $PACKAGES_DIR"

  # List downloaded packages
  echo
  info "Downloaded files:"
  ls -lh "$PACKAGES_DIR"/*.ipk 2>/dev/null || warn "No .ipk files found"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
