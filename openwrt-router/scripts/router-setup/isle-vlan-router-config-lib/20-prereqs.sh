#!/usr/bin/env bash
if [[ -n "${_REQ_SH:-}" ]]; then return; fi; _REQ_SH=1

check_prerequisites_or_prompt(){
  require_bin ssh
  require_bin scp

  info "Testing SSH to ${OPENWRT_USER}@${OPENWRT_IP}…"
  if ! ssh $SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" "echo SSHOK" >/dev/null 2>&1; then
    warn "SSH with key auth failed. You may need to set a password or upload keys."
    echo -n "Continue anyway? (y/N) "; read -r ans
    [[ "$ans" =~ ^[Yy]$ ]] || exit 1
  else
    ok "SSH connectivity OK"
  fi

  [[ -f "$CONFIG_FILE" ]] || { warn "VLAN config not found: $CONFIG_FILE (using defaults)"; }
}
