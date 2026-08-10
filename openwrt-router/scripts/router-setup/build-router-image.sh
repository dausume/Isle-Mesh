#!/usr/bin/env bash
# build-router-image.sh — build the isle router image from the upstream
# OpenWRT base. This is step 4 of the acquisition chain (see
# get-router-image.sh) and the one that depends on nothing of ours:
# given downloads.openwrt.org and this script, anyone can produce the
# image themselves.
#
#   ./build-router-image.sh [-o OUTPUT] [--keep-base] [--verify-deep]
#
# The result is a PRISTINE image: upstream userland, no keys, no
# provisioning. Isle-specific setup (SSH key, packages, uci config)
# happens at runtime against the booted VM — see router-init.main.sh.
# That separation is deliberate: a built image must never carry
# credentials, because it is the thing we cache, publish and share.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="${MANIFEST:-$SCRIPT_DIR/router-image.manifest}"
# shellcheck disable=SC1090
source "$MANIFEST"

OUTPUT_DIR="${IMAGE_DIR:-$SCRIPT_DIR/images}"
OUTPUT=""
KEEP_BASE=0
VERIFY_DEEP=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--output)   OUTPUT="$2"; shift 2 ;;
    --keep-base)   KEEP_BASE=1; shift ;;
    --verify-deep) VERIFY_DEEP=1; shift ;;
    -h|--help)     sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

OUTPUT="${OUTPUT:-$OUTPUT_DIR/$ROUTER_IMAGE_NAME}"

log()  { printf '\033[0;34m[build]\033[0m %s\n' "$*" >&2; }
ok()   { printf '\033[0;32m[build]\033[0m %s\n' "$*" >&2; }
err()  { printf '\033[0;31m[build]\033[0m %s\n' "$*" >&2; }

for cmd in qemu-img sha256sum gunzip; do
  command -v "$cmd" >/dev/null || { err "missing required command: $cmd"; exit 1; }
done
command -v curl >/dev/null || command -v wget >/dev/null || {
  err "need curl or wget to fetch the upstream base"; exit 1; }

mkdir -p "$OUTPUT_DIR"
BASE="$OUTPUT_DIR/$OPENWRT_BASE_FILE"

sha_of() { sha256sum "$1" | awk '{print $1}'; }

# ── 1. Get the upstream base, checksum-gated ─────────────────────────
if [[ -f "$BASE" ]] && [[ "$(sha_of "$BASE")" == "$OPENWRT_BASE_SHA256" ]]; then
  log "upstream base already present and verified"
else
  [[ -f "$BASE" ]] && { log "cached base failed checksum — refetching"; rm -f "$BASE"; }
  log "downloading upstream base: $OPENWRT_BASE_URL"
  if command -v curl >/dev/null; then
    curl -fL --retry 3 --connect-timeout 20 -o "$BASE" "$OPENWRT_BASE_URL" || {
      err "download failed"; rm -f "$BASE"; exit 1; }
  else
    wget -q --show-progress -O "$BASE" "$OPENWRT_BASE_URL" || {
      err "download failed"; rm -f "$BASE"; exit 1; }
  fi
  got="$(sha_of "$BASE")"
  if [[ "$got" != "$OPENWRT_BASE_SHA256" ]]; then
    err "upstream base checksum MISMATCH"
    err "  expected $OPENWRT_BASE_SHA256"
    err "  got      $got"
    rm -f "$BASE"
    exit 1
  fi
  ok "upstream base verified against the manifest"
fi

# ── 2. Extract → qcow2 → resize ──────────────────────────────────────
EXTRACTED="${BASE%.gz}"
# Upstream's .gz carries trailing padding, so gunzip exits 2 with
# "trailing garbage ignored" on a perfectly good file. Judge the result
# by whether the image came out, not by the exit code.
log "extracting base image"
gunzip -kf "$BASE" || true
[[ -s "$EXTRACTED" ]] || { err "extraction produced no image"; exit 1; }

log "converting to qcow2 and resizing to $ROUTER_IMAGE_DISK_SIZE"
TMP_OUT="$OUTPUT.partial"
rm -f "$TMP_OUT"
qemu-img convert -f raw -O qcow2 "$EXTRACTED" "$TMP_OUT"
qemu-img resize "$TMP_OUT" "$ROUTER_IMAGE_DISK_SIZE" >/dev/null

[[ "$KEEP_BASE" -eq 1 ]] || rm -f "$EXTRACTED"

# ── 3. Verify what we built ──────────────────────────────────────────
if [[ "$VERIFY_DEEP" -eq 1 && -n "${ROUTER_IMAGE_RAW_SHA256:-}" ]]; then
  log "deep-verifying raw content against the manifest"
  # qemu-img cannot stream raw to a pipe (it tries to resize the
  # destination), so convert to a sparse temp file and hash that.
  raw_tmp="$(mktemp "${TMPDIR:-/tmp}/router-raw.XXXXXX")"
  qemu-img convert -f qcow2 -O raw "$TMP_OUT" "$raw_tmp"
  raw_sha="$(sha256sum "$raw_tmp" | awk '{print $1}')"
  rm -f "$raw_tmp"
  if [[ "$raw_sha" != "$ROUTER_IMAGE_RAW_SHA256" ]]; then
    err "built image raw content MISMATCH"
    err "  expected $ROUTER_IMAGE_RAW_SHA256"
    err "  got      $raw_sha"
    rm -f "$TMP_OUT"
    exit 1
  fi
  ok "raw content matches the manifest"
fi

mv -f "$TMP_OUT" "$OUTPUT"
ok "built: $OUTPUT"
qemu-img info "$OUTPUT" | sed 's/^/  /' >&2
