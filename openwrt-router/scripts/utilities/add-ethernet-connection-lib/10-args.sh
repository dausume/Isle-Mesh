#!/usr/bin/env bash
# 10-args.sh
if [[ -n "${_ARGS_SH:-}" ]]; then return; fi; _ARGS_SH=1
parse_args(){
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --iface) ETH_IFACE="$2"; shift 2 ;;
      --role)  ROLE="$2"; shift 2 ;;         # lan|wan
      --isle)  ISLE="$2"; shift 2 ;;
      --vlan)  VLAN_ID="$2"; shift 2 ;;
      -h|--help)
        cat <<EOF
Usage: add-ethernet-connection.sh [--iface ethX] [--role lan|wan] [--isle my-isle] [--vlan 10]
If --iface omitted, you'll select from detected Ethernet NICs (ISP iface excluded).
EOF
        exit 0 ;;
      *) err "Unknown arg: $1"; exit 1 ;;
    esac
  done
}

# End: 10-args.sh
