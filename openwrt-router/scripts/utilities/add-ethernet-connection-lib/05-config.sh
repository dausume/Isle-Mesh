#!/usr/bin/env bash
if [[ -n "${_CFG_SH:-}" ]]; then return; fi; _CFG_SH=1
STATE_DIR="/var/lib/isle-mesh"; mkdir -p "$STATE_DIR"
RESERVED="$STATE_DIR/reserved-ports.conf"; touch "$RESERVED"
ROUTER_VM="${ROUTER_VM:-openwrt-isle-router}"
ROLE="${ROLE:-lan}"         # lan|wan
ISLE="${ISLE:-my-isle}"
VLAN_ID="${VLAN_ID:-10}"
ETH_IFACE=""

cleanup_tmp(){ :; }
require_root(){ if [[ ${EUID:-$(id -u)} -ne 0 ]]; then err "Run as root (sudo)"; exit 1; fi; }
reserved(){ grep -q "^$1$" "$RESERVED" 2>/dev/null; }
reserve(){ echo "$1" >> "$RESERVED"; }
assert_router_exists(){ virsh list --all | grep -qw "$ROUTER_VM" || { err "VM $ROUTER_VM not found"; exit 1; }; }
