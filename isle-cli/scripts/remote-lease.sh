#!/usr/bin/env bash
# Configure a remote device's dedicated Isle Ethernet connection.
# Reuse an active/bound NetworkManager profile; never guess between ports.
# Usage: sudo isle remote-lease [interface]
set -uo pipefail
log(){ printf '[isle-remote-lease] %s\n' "$*"; }
err(){ log "$*" >&2; }

is_candidate(){
  local nic="$1" routes4 routes6
  [[ -d "/sys/class/net/$nic/device" && ! -d "/sys/class/net/$nic/wireless" ]] || return 1
  [[ ! -e "/sys/class/net/$nic/master" ]] || return 1
  [[ "$(cat "/sys/class/net/$nic/type" 2>/dev/null)" == 1 ]] || return 1
  [[ "$(cat "/sys/class/net/$nic/carrier" 2>/dev/null)" == 1 ]] || return 1
  # A wired uplink is protected too. Never remove its default route.
  routes4="$(ip -4 route show default dev "$nic")" || return 1
  routes6="$(ip -6 route show default dev "$nic")" || return 1
  [[ -z "$routes4" && -z "$routes6" ]]
}

pick_iface(){
  local path nic found=''
  for path in /sys/class/net/*; do
    nic="${path##*/}"
    is_candidate "$nic" || continue
    [[ -z "$found" ]] || { err 'Multiple candidate Ethernet ports; specify the Isle interface.'; return 1; }
    found="$nic"
  done
  [[ -n "$found" ]] || { err 'No dedicated Ethernet port with carrier found. Connect the Isle cable first.'; return 1; }
  printf '%s\n' "$found"
}

# Return the UUID (not display name: names can contain colons or be duplicated).
select_profile(){
  local nic="$1" uuid bound kind found='' unbound=false
  uuid="$(nmcli -g GENERAL.CON-UUID device show "$nic")" || return 1
  if [[ -n "$uuid" && "$uuid" != -- ]]; then
    kind="$(nmcli -g connection.type connection show uuid "$uuid")" || return 1
    [[ "$kind" == 802-3-ethernet ]] || { err 'Active profile is not Ethernet.'; return 1; }
    printf '%s\n' "$uuid"; return 0
  fi
  local uuids
  uuids="$(nmcli -g UUID connection show)" || return 1
  while IFS= read -r uuid; do
    [[ -n "$uuid" ]] || continue
    bound="$(nmcli -g connection.interface-name connection show uuid "$uuid")" || return 1
    kind="$(nmcli -g connection.type connection show uuid "$uuid")" || return 1
    [[ "$kind" == 802-3-ethernet ]] || continue
    if [[ -z "$bound" || "$bound" == -- ]]; then unbound=true; continue; fi
    [[ "$bound" == "$nic" ]] || continue
    [[ -z "$found" ]] || { err 'Multiple profiles are bound to this port; activate the intended Isle profile first.'; return 1; }
    found="$uuid"
  done <<< "$uuids"
  if [[ -z "$found" && "$unbound" == true ]]; then
    err 'An unbound Ethernet profile exists; activate the intended profile on this port first to avoid duplicates.'
    return 1
  fi
  printf '%s' "$found"
}

configure_nm(){
  local nic="$1" uuid addr
  uuid="$(select_profile "$nic")" || return 1
  if [[ -z "$uuid" ]]; then
    nmcli connection add type ethernet ifname "$nic" con-name "isle-cable-$nic" \
      ipv4.method auto ipv4.never-default yes ipv6.method disabled ipv6.never-default yes \
      connection.autoconnect yes >/dev/null || { err 'Could not create Ethernet profile.'; return 1; }
    uuid="$(select_profile "$nic")" || return 1
    [[ -n "$uuid" ]] || { err 'Created profile could not be identified.'; return 1; }
  fi
  # Static/bridged profiles need an explicit review, not an implicit conversion.
  [[ "$(nmcli -g ipv4.method connection show uuid "$uuid")" == auto ]] || {
    err 'Existing profile is not DHCP; review it before onboarding.'; return 1;
  }
  nmcli connection modify uuid "$uuid" ipv4.never-default yes ipv6.never-default yes \
    connection.autoconnect yes || { err 'Could not configure Ethernet profile.'; return 1; }
  log "Using Ethernet profile $uuid on $nic"
  nmcli --wait 30 connection up uuid "$uuid" ifname "$nic" >/dev/null || {
    err 'Ethernet activation failed; no lease confirmed.'; return 1;
  }
  addr="$(ip -4 -o addr show dev "$nic" scope global | awk '{print $4; exit}')"
  [[ -n "$addr" ]] || { err 'No IPv4 lease. Check the cable and Isle router DHCP.'; return 1; }
  log "Ethernet address: $addr. Isle identity and trusted service access still require verification."
}

main(){
  if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
    echo 'Usage: sudo isle remote-lease [interface]'; return 0
  fi
  [[ $# -le 1 && "${1:-}" != -* ]] || { err 'Usage: sudo isle remote-lease [interface]'; return 1; }
  [[ ${EUID:-$(id -u)} -eq 0 ]] || { err 'Run with sudo isle remote-lease.'; return 1; }
  local nic="${1:-}"
  [[ -n "$nic" ]] || nic="$(pick_iface)" || return 1
  is_candidate "$nic" || { err 'Refusing port: select physical Ethernet with carrier, outside a bridge and without a default route.'; return 1; }
  command -v nmcli >/dev/null && [[ "$(nmcli -t -f RUNNING general)" == running ]] || {
    err 'NetworkManager is required for persistent, scoped Ethernet setup. Configure other managers explicitly.'; return 1;
  }
  configure_nm "$nic" || return 1
  log 'Next: verify the Isle router, then configure scoped DNS with isle dns use-router and verify trusted HTTPS.'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
