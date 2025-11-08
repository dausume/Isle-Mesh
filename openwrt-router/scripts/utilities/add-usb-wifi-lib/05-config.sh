#!/usr/bin/env bash
if [[ -n "${_CFG2_SH:-}" ]]; then return; fi; _CFG2_SH=1
STATE_DIR="/var/lib/isle-mesh"; mkdir -p "$STATE_DIR"
RESERVED="$STATE_DIR/reserved-ports.conf"; touch "$RESERVED"
ROUTER_VM="${ROUTER_VM:-openwrt-isle-router}"

SSID_DEFAULT="my-isle-WiFi"; WIFI_PSK_DEFAULT="ChangeMe12345"
OPENWRT_HOST="${OPENWRT_HOST:-}"  # if set, we'll try SSH (e.g., 192.168.50.1)
OPENWRT_SSH="${OPENWRT_SSH:-root@$OPENWRT_HOST}"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=4"

USB_PATH=""  # e.g., 1-3.2
USB_VID=""; USB_PID=""
cleanup_tmp(){ :; }
require_root(){ if [[ ${EUID:-$(id -u)} -ne 0 ]]; then err "Run as root (sudo)"; exit 1; fi; }
reserved(){ grep -q "^$1$" "$RESERVED" 2>/dev/null; }
reserve(){ echo "$1" >> "$RESERVED"; }
assert_router_exists(){ virsh list --all | grep -qw "$ROUTER_VM" || { err "VM $ROUTER_VM not found"; exit 1; }; }
