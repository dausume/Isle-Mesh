#!/usr/bin/env bash
# BEGIN: 10-args.sh
if [[ -n "${_ARGS_SH_SOURCED:-}" ]]; then return 0; fi; _ARGS_SH_SOURCED=1

parse_args() {
  while [[ $# -gt 0 ]]; do
    case $1 in
      --vm-name) VM_NAME="$2"; shift 2 ;;
      --memory)  MEMORY="$2"; shift 2 ;;
      --vcpus)   VCPUS="$2";  shift 2 ;;
      --image)   CUSTOM_IMAGE="$2"; shift 2 ;;
      --no-start) NO_START=true; shift ;;
      -h|--help)
        grep "^#" "${BASH_SOURCE[0]%/*}/../router-init.main.sh" | grep -v "#!/bin/bash" | sed 's/^# \?//'
        exit 0 ;;
      *) log_error "Unknown option: $1"; echo "Use --help for usage"; exit 1 ;;
    esac
  done
}
# END: 10-args.sh
