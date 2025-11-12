#!/usr/bin/env bash
if [[ -n "${_NET_SH:-}" ]]; then return; fi; _NET_SH=1

configure_single_isle_network(){
  info "Configuring network (single isle: ${MY_ISLE_NAME}, VLAN ${VLAN_ID}, dev ${MY_ISLE_IF_DEV})…"
  local tmp="$STATE_DIR/network-config.sh"
  cat >"$tmp"<<'SH'
#!/bin/sh
set -e
cp /etc/config/network /etc/config/network.bak 2>/dev/null || true

# Ensure 802.1q available
modprobe 8021q 2>/dev/null || true

# Create tagged subinterface for my-isle
# Using device syntax for DSA-less/virtio models
uci -q delete network.ISLE_UCI_PLACEHOLDER 2>/dev/null || true
uci set network.ISLE_UCI_PLACEHOLDER=interface
uci set network.ISLE_UCI_PLACEHOLDER.proto='static'
uci set network.ISLE_UCI_PLACEHOLDER.device='ISLE_IF_DEV_PLACEHOLDER'
uci set network.ISLE_UCI_PLACEHOLDER.ipaddr='ISLE_IP_PLACEHOLDER'
uci set network.ISLE_UCI_PLACEHOLDER.netmask='ISLE_NETMASK_PLACEHOLDER'

uci commit network
# Schedule network restart in background to allow SSH to exit cleanly
( sleep 2 && /etc/init.d/network restart ) >/dev/null 2>&1 </dev/null &
echo "Network restart scheduled"
# Exit successfully immediately to allow SSH to close
exit 0
SH

  # Replace placeholders with actual values
  sed -i "s/ISLE_UCI_PLACEHOLDER/${MY_ISLE_UCI}/g" "$tmp"
  sed -i "s/ISLE_IF_DEV_PLACEHOLDER/${MY_ISLE_IF_DEV}/g" "$tmp"
  sed -i "s/ISLE_IP_PLACEHOLDER/${MY_ISLE_IP}/g" "$tmp"
  sed -i "s/ISLE_NETMASK_PLACEHOLDER/${MY_ISLE_NETMASK}/g" "$tmp"

  copy_to_openwrt "$tmp" "/tmp/network-config.sh" || { err "Failed to copy network config script"; exit 1; }

  # Execute the script - it should exit immediately after scheduling the restart
  # Using explicit sh to avoid permission issues
  info "Executing network configuration script on router..."
  exec_ssh "sh /tmp/network-config.sh" || warn "Network configuration script returned non-zero exit (may be expected during network restart)"

  # Wait for network to restart
  info "Waiting for network to restart (15 seconds)..."
  sleep 15

  # Verify configuration was applied (with retries)
  local retries=3
  local verified=false
  for ((i=1; i<=retries; i++)); do
    if exec_ssh "uci show network.${MY_ISLE_UCI}" >/dev/null 2>&1; then
      ok "Network configured for ${MY_ISLE_NAME} on ${MY_ISLE_IF_DEV}"
      verified=true
      break
    else
      if [[ $i -lt $retries ]]; then
        info "Verification attempt $i failed, retrying in 5 seconds..."
        sleep 5
      fi
    fi
  done

  if [[ "$verified" != "true" ]]; then
    warn "Unable to verify network configuration after $retries attempts"
    warn "The router may be configured but needs more time to restart"
    warn "Continuing with setup - network may become available shortly"
  fi

  # Always return success - verification is informational only
  return 0
}
