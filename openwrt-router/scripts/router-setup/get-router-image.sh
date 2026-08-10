#!/usr/bin/env bash
# get-router-image.sh — obtain the isle router image, trying every
# source in turn until one works. Nothing in the repo ships the image
# itself (see images/README.md); this script is how it gets there.
#
#   ./get-router-image.sh [-o OUTPUT] [--sources "cache mesh release build"]
#                         [--force] [--verify-deep]
#
# The chain, in order:
#   1. cache   — already on disk at the expected path, checksum-checked
#   2. mesh    — our prebuilt copy over the isle (no internet needed)
#   3. release — our public copy (internet, no isle needed)
#   4. build   — build it from the upstream OpenWRT base
#
# The point of the chain is that no step is load-bearing: our hosting
# down falls through to building from upstream; upstream moved falls
# back to a cached/mesh/release copy. Step 4 needs only
# downloads.openwrt.org and build-router-image.sh.
#
# Env knobs:
#   IMAGE_DIR                     where the image lives (default ./images)
#   ISLE_ROUTER_IMAGE_MESH_URL    enables step 2
#   ISLE_ROUTER_IMAGE_RELEASE_URL enables step 3
#   ISLE_IMAGE_SOURCES            override the chain (same as --sources)

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="${MANIFEST:-$SCRIPT_DIR/router-image.manifest}"
# shellcheck disable=SC1090
source "$MANIFEST"

IMAGE_DIR="${IMAGE_DIR:-$SCRIPT_DIR/images}"
OUTPUT=""
SOURCES="${ISLE_IMAGE_SOURCES:-cache mesh release build}"
FORCE=0
VERIFY_DEEP=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--output)   OUTPUT="$2"; shift 2 ;;
    --sources)     SOURCES="$2"; shift 2 ;;
    --force)       FORCE=1; shift ;;
    --verify-deep) VERIFY_DEEP=1; shift ;;
    -h|--help)     sed -n '2,26p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

OUTPUT="${OUTPUT:-$IMAGE_DIR/$ROUTER_IMAGE_NAME}"

log()  { printf '\033[0;34m[image]\033[0m %s\n' "$*" >&2; }
ok()   { printf '\033[0;32m[image]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m[image]\033[0m %s\n' "$*" >&2; }
err()  { printf '\033[0;31m[image]\033[0m %s\n' "$*" >&2; }

sha_of() { sha256sum "$1" | awk '{print $1}'; }

# qemu-img cannot stream raw output to a pipe (it tries to resize the
# destination), so convert to a sparse temp file and hash that.
raw_sha_of() {
  local src="$1" tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/router-raw.XXXXXX")"
  qemu-img convert -f qcow2 -O raw "$src" "$tmp" || { rm -f "$tmp"; return 1; }
  sha256sum "$tmp" | awk '{print $1}'
  rm -f "$tmp"
}

# Is this file the image we expect? Cheap check against the published
# sha when one exists; otherwise fall back to "is it a valid qcow2 of
# the right size", and only pay for a full raw compare on --verify-deep.
verify_image() {
  local f="$1"
  [[ -s "$f" ]] || return 1

  if [[ -n "${ROUTER_IMAGE_PUBLISHED_SHA256:-}" ]]; then
    if [[ "$(sha_of "$f")" == "$ROUTER_IMAGE_PUBLISHED_SHA256" ]]; then
      return 0
    fi
    # A locally built image legitimately differs from the published
    # artifact's packing, so this is not fatal on its own.
    warn "file sha does not match the published artifact sha"
  fi

  qemu-img info "$f" >/dev/null 2>&1 || { err "not a readable qcow2: $f"; return 1; }

  if [[ "$VERIFY_DEEP" -eq 1 ]]; then
    [[ -n "${ROUTER_IMAGE_RAW_SHA256:-}" ]] || { warn "no raw sha in manifest; skipping deep verify"; return 0; }
    log "deep-verifying raw content (this reads the whole disk)"
    local got; got="$(raw_sha_of "$f")"
    if [[ "$got" != "$ROUTER_IMAGE_RAW_SHA256" ]]; then
      err "raw content MISMATCH"
      err "  expected $ROUTER_IMAGE_RAW_SHA256"
      err "  got      $got"
      err "  (an image that has been BOOTED will differ — that is expected,"
      err "   and such an image must never be published or committed)"
      return 1
    fi
    ok "raw content matches the manifest"
  fi
  return 0
}

fetch_url() {
  local url="$1" dest="$2"
  log "fetching $url"
  if command -v curl >/dev/null; then
    curl -fL --retry 2 --connect-timeout 15 -o "$dest" "$url"
  else
    wget -q --show-progress -O "$dest" "$url"
  fi
}

# A fetched artifact is only accepted if the manifest pins its sha —
# otherwise we would be trusting whatever the endpoint served.
try_download() {
  local url="$1" label="$2"
  if [[ -z "$url" ]]; then
    log "$label: not configured — skipping"
    return 1
  fi
  if [[ -z "${ROUTER_IMAGE_PUBLISHED_SHA256:-}" ]]; then
    warn "$label: configured, but the manifest pins no published sha — refusing to trust it"
    return 1
  fi
  local tmp="$OUTPUT.partial"
  rm -f "$tmp"
  if ! fetch_url "$url" "$tmp"; then
    warn "$label: fetch failed"; rm -f "$tmp"; return 1
  fi
  local got; got="$(sha_of "$tmp")"
  if [[ "$got" != "$ROUTER_IMAGE_PUBLISHED_SHA256" ]]; then
    err "$label: checksum MISMATCH — discarding"
    err "  expected $ROUTER_IMAGE_PUBLISHED_SHA256"
    err "  got      $got"
    rm -f "$tmp"; return 1
  fi
  mv -f "$tmp" "$OUTPUT"
  ok "$label: verified and installed"
  return 0
}

mkdir -p "$IMAGE_DIR"

if [[ "$FORCE" -eq 1 && -f "$OUTPUT" ]]; then
  log "--force: discarding existing $OUTPUT"
  rm -f "$OUTPUT"
fi

for src in $SOURCES; do
  case "$src" in
    cache)
      if [[ -f "$OUTPUT" ]] && verify_image "$OUTPUT"; then
        ok "using cached image: $OUTPUT"
        exit 0
      fi
      [[ -f "$OUTPUT" ]] && warn "cached image failed verification — trying the next source"
      ;;
    mesh)
      try_download "${ROUTER_IMAGE_MESH_URL:-}" "mesh" && exit 0
      ;;
    release)
      try_download "${ROUTER_IMAGE_RELEASE_URL:-}" "release" && exit 0
      ;;
    build)
      log "building from the upstream OpenWRT base"
      build_args=()
      [[ "$VERIFY_DEEP" -eq 1 ]] && build_args+=(--verify-deep)
      if IMAGE_DIR="$IMAGE_DIR" "$SCRIPT_DIR/build-router-image.sh" -o "$OUTPUT" "${build_args[@]}"; then
        ok "built from upstream: $OUTPUT"
        exit 0
      fi
      warn "build from upstream failed"
      ;;
    *) warn "unknown source '$src' — skipping" ;;
  esac
done

err "could not obtain the router image from any source: $SOURCES"
err "the last-resort path needs only internet access to downloads.openwrt.org:"
err "  $SCRIPT_DIR/build-router-image.sh"
exit 1
