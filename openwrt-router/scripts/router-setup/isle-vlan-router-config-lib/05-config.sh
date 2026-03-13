#!/usr/bin/env bash
if [[ -n "${_CFG_SH:-}" ]]; then return; fi; _CFG_SH=1

SCRIPT_DIR="${SCRIPT_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"

# Management access to OpenWRT
OPENWRT_IP="${OPENWRT_IP:-192.168.1.1}"
OPENWRT_USER="${OPENWRT_USER:-root}"

# Single-isle name (default: "my-isle"; UCI section derived by removing hyphens)
ISLE_NAME="${ISLE_NAME:-my-isle}"
MY_ISLE_NAME="$ISLE_NAME"
MY_ISLE_UCI="$(echo "$ISLE_NAME" | tr -d '-')"

# Network defaults for the isle
VLAN_ID="${VLAN_ID:-10}"
MY_ISLE_IF_BASE="${MY_ISLE_IF_BASE:-eth1}"               # base NIC inside OpenWRT
MY_ISLE_IF_DEV="${MY_ISLE_IF_BASE}.${VLAN_ID}"           # tagged subinterface
MY_ISLE_IP="${MY_ISLE_IP:-10.${VLAN_ID}.0.1}"
MY_ISLE_NETMASK="${MY_ISLE_NETMASK:-255.255.255.0}"

# Use dedicated isle SSH key if available (installed during router-init)
ISLE_SSH_KEY_PATH="${ISLE_SSH_KEY:-/etc/isle-mesh/router/ssh/isle_router_key}"
if [[ -f "$ISLE_SSH_KEY_PATH" ]]; then
  SSH_OPTS="-i $ISLE_SSH_KEY_PATH -o ConnectTimeout=10 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
else
  SSH_OPTS="-o ConnectTimeout=10 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
fi
STATE_DIR="${STATE_DIR:-/tmp/isle-mdns-config}"; mkdir -p "$STATE_DIR"

require_bin(){ command -v "$1" >/dev/null 2>&1 || { err "Missing: $1"; exit 1; }; }
