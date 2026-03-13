#!/usr/bin/env bash
if [[ -n "${_REQ_SH:-}" ]]; then return; fi; _REQ_SH=1

check_prerequisites_or_prompt(){
  require_bin ssh
  require_bin scp

  # Check for sshpass (needed for password authentication)
  if ! command -v sshpass >/dev/null 2>&1; then
    warn "sshpass is not installed - password authentication will not work"
    warn "Install with: sudo apt-get install -y sshpass"
  fi

  info "Testing SSH to ${OPENWRT_USER}@${OPENWRT_IP}…"
  # Try key-based first (BatchMode prevents password prompt)
  if ssh -o BatchMode=yes $SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" "echo SSHOK" >/dev/null 2>&1; then
    ok "SSH connectivity OK"
  elif [[ -n "${OPENWRT_PASSWORD:-}" ]] && [[ "$HAS_SSHPASS" == "true" ]]; then
    if sshpass -p "$OPENWRT_PASSWORD" ssh $SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" "echo SSHOK" >/dev/null 2>&1; then
      ok "SSH connectivity OK"
    else
      warn "SSH connection test failed"
      info "Attempting to continue with configured authentication..."
    fi
  else
    warn "SSH key auth failed, will try password auth in next step..."
  fi

  # Optional config file check (only if CONFIG_FILE is defined)
  if [[ -n "${CONFIG_FILE:-}" ]] && [[ ! -f "$CONFIG_FILE" ]]; then
    warn "VLAN config not found: $CONFIG_FILE (using defaults)"
  fi
}
