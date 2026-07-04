#!/usr/bin/env bash
# 45-openwrt-network.sh — auto-configure the OpenWRT side after a cable is attached.
#
# Brings the newly-attached NIC into the isle network + DHCP scope so a remote node on
# the cable leases an isle address automatically. This is the step that used to be a
# printed "do this manually" suggestion. Idempotent; safe to re-run.
if [[ -n "${_OWRTNET_SH:-}" ]]; then return; fi; _OWRTNET_SH=1

OPENWRT_IP="${OPENWRT_IP:-192.168.1.1}"
OPENWRT_USER="${OPENWRT_USER:-root}"
ISLE_SSH_KEY="${ISLE_SSH_KEY:-/etc/isle-mesh/router/ssh/isle_router_key}"
_ROUTER_PASS_FILE="/etc/isle-mesh/router/ssh/.cached_password"
_ROUTER_SSH_OPTS="-o ConnectTimeout=10 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
[[ -f "$ISLE_SSH_KEY" ]] && _ROUTER_SSH_OPTS="-i $ISLE_SSH_KEY $_ROUTER_SSH_OPTS"
_ROUTER_PASS=""; [[ -f "$_ROUTER_PASS_FILE" ]] && _ROUTER_PASS="$(cat "$_ROUTER_PASS_FILE" 2>/dev/null)"
_ROUTER_AUTH=""

# Probe once, then use the working method (so heredoc stdin isn't consumed by a
# failed first attempt).
_router_probe(){
  if ssh -o BatchMode=yes $_ROUTER_SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" true 2>/dev/null; then
    _ROUTER_AUTH="key"; return 0; fi
  if [[ -n "$_ROUTER_PASS" ]] && command -v sshpass >/dev/null 2>&1 \
     && sshpass -p "$_ROUTER_PASS" ssh $_ROUTER_SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" true 2>/dev/null; then
    _ROUTER_AUTH="pass"; return 0; fi
  return 1
}
_router_ssh(){
  case "$_ROUTER_AUTH" in
    key)  ssh -o BatchMode=yes $_ROUTER_SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" "$@" ;;
    pass) sshpass -p "$_ROUTER_PASS" ssh $_ROUTER_SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" "$@" ;;
    *)    return 1 ;;
  esac
}

# network.<ISLE_UCI> is the OpenWRT isle interface (ISLE=my-isle -> myisle).
ISLE_UCI="${ISLE_UCI:-$(printf '%s' "${ISLE:-my-isle}" | tr -d '-')}"
ISLE_BRIDGE_DEV="${ISLE_BRIDGE_DEV:-br-isle}"

configure_openwrt_network(){
  if [[ "${ROLE:-lan}" != "lan" ]]; then
    warn "role=$ROLE — auto OpenWRT config currently handles role=lan only; skipping."
    return 0
  fi
  log "Configuring OpenWRT so '$ISLE_UCI' serves DHCP over the new cable…"

  if ! _router_probe; then
    warn "Could not reach OpenWRT non-interactively (no working key + no cached password)."
    warn "Cable is attached, but OpenWRT was NOT auto-configured. Fix router SSH auth and re-run."
    return 1
  fi

  # Identify the new NIC inside OpenWRT by the MAC libvirt gave the bridge interface.
  if [[ -z "${ATTACHED_MAC:-}" ]]; then
    ATTACHED_MAC="$(virsh domiflist "$ROUTER_VM" 2>/dev/null | awk -v b="$BR" '$3==b{print $5}' | tail -1)"
  fi
  [[ -n "$ATTACHED_MAC" ]] || { err "Could not determine the attached NIC MAC for bridge $BR"; return 1; }

  local NEW_ETH
  NEW_ETH="$(_router_ssh "for d in /sys/class/net/eth*; do [ \"\$(cat \$d/address 2>/dev/null)\" = \"$ATTACHED_MAC\" ] && basename \$d && break; done" 2>/dev/null | tr -d '\r')"
  [[ -n "$NEW_ETH" ]] || { err "OpenWRT sees no NIC with MAC $ATTACHED_MAC yet"; return 1; }
  ok "New NIC inside OpenWRT: $NEW_ETH (mac $ATTACHED_MAC)"

  # Idempotently add NEW_ETH to the isle interface as a bridge port. If the isle
  # interface is a plain device, promote it to a bridge (original device + new NIC).
  local out
  out="$(_router_ssh "sh" <<EOF
CUR=\$(uci -q get network.${ISLE_UCI}.device || echo '')
if [ "\$CUR" = "${ISLE_BRIDGE_DEV}" ]; then
  uci -q get network.islebr.ports 2>/dev/null | grep -qw "${NEW_ETH}" || uci add_list network.islebr.ports='${NEW_ETH}'
elif [ -n "\$CUR" ]; then
  uci set network.islebr=device
  uci set network.islebr.name='${ISLE_BRIDGE_DEV}'
  uci set network.islebr.type='bridge'
  uci -q get network.islebr.ports 2>/dev/null | grep -qw "\$CUR" || uci add_list network.islebr.ports="\$CUR"
  uci -q get network.islebr.ports 2>/dev/null | grep -qw "${NEW_ETH}" || uci add_list network.islebr.ports='${NEW_ETH}'
  uci set network.${ISLE_UCI}.device='${ISLE_BRIDGE_DEV}'
else
  echo "ERR:no-isle-iface"; exit 2
fi
uci commit network
/etc/init.d/network reload >/dev/null 2>&1
/etc/init.d/dnsmasq restart >/dev/null 2>&1
echo OK
EOF
)"
  if printf '%s' "$out" | grep -q OK; then
    ok "OpenWRT now bridges ${NEW_ETH} into '${ISLE_UCI}'; DHCP serves over the cable."
    return 0
  fi
  err "OpenWRT auto-config failed: ${out:-no output}"
  return 1
}
# End: 45-openwrt-network.sh
