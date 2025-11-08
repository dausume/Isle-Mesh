#!/usr/bin/env bash
if [[ -n "${_ARGS_SH:-}" ]]; then return; fi; _ARGS_SH=1

usage(){
  cat <<EOF
OpenWRT Router mDNS Passthrough (single isle: "my-isle")

Usage: ./configure-mdns-on-openwrt.sh [options]
  -i, --ip IP             OpenWRT management IP (default: ${OPENWRT_IP})
  -u, --user USER         SSH user (default: ${OPENWRT_USER})
      --vlan ID           VLAN ID for my-isle (default: ${VLAN_ID})
      --isle-if-base IF   Base iface inside OpenWRT (default: ${MY_ISLE_IF_BASE})
      --isle-ip IP        my-isle IP (default: ${MY_ISLE_IP})
      --isle-netmask NM   my-isle netmask (default: ${MY_ISLE_NETMASK})
  -h, --help              Show help

The isle name is fixed to "my-isle" (UCI section "myisle").
EOF
}

parse_args(){
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -i|--ip) OPENWRT_IP="$2"; shift 2 ;;
      -u|--user) OPENWRT_USER="$2"; shift 2 ;;
      --vlan) VLAN_ID="$2"; shift 2 ;;
      --isle-if-base) MY_ISLE_IF_BASE="$2"; shift 2 ;;
      --isle-ip) MY_ISLE_IP="$2"; shift 2 ;;
      --isle-netmask) MY_ISLE_NETMASK="$2"; shift 2 ;;
      -h|--help) usage; exit 0 ;;
      *) err "Unknown option: $1"; usage; exit 1 ;;
    esac
  done
  # recompute derived values if VLAN/base changed
  MY_ISLE_IF_DEV="${MY_ISLE_IF_BASE}.${VLAN_ID}"
  [[ "$MY_ISLE_IP" =~ ^10\.${VLAN_ID}\. ]] || true  # user may override; no strict coupling enforced
}
