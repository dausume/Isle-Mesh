#!/usr/bin/env bash
if [[ -n "${_SSH_SH:-}" ]]; then return; fi; _SSH_SH=1

# Global variable to store password (if needed)
OPENWRT_PASSWORD=""

# Check if sshpass is available
HAS_SSHPASS=false
if command -v sshpass >/dev/null 2>&1; then
  HAS_SSHPASS=true
fi

exec_ssh(){
  if [[ -n "$OPENWRT_PASSWORD" ]] && [[ "$HAS_SSHPASS" == "true" ]]; then
    sshpass -p "$OPENWRT_PASSWORD" ssh $SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" "$@"
  else
    ssh $SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" "$@"
  fi
}

copy_to_openwrt(){
  if [[ -n "$OPENWRT_PASSWORD" ]] && [[ "$HAS_SSHPASS" == "true" ]]; then
    sshpass -p "$OPENWRT_PASSWORD" scp $SSH_OPTS "$1" "${OPENWRT_USER}@${OPENWRT_IP}:$2"
  else
    scp $SSH_OPTS "$1" "${OPENWRT_USER}@${OPENWRT_IP}:$2"
  fi
}

init_ssh_auth(){
  info "Testing SSH connection to ${OPENWRT_USER}@${OPENWRT_IP}…"

  # First try passwordless SSH
  if ssh $SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" "echo SSHOK" >/dev/null 2>&1; then
    ok "SSH connection successful (passwordless auth)"
    return 0
  fi

  # Passwordless failed - check if sshpass is available
  if [[ "$HAS_SSHPASS" != "true" ]]; then
    warn "SSH requires password but 'sshpass' is not installed"
    warn "Install it with: sudo apt-get install sshpass"
    warn "Or use passwordless SSH by connecting manually and pressing Enter at password prompt"
    return 1
  fi

  # Prompt for password
  warn "Passwordless SSH failed - password required"
  echo -n "Enter password for root@${OPENWRT_IP} (or press Enter if no password): "
  read -s OPENWRT_PASSWORD
  echo ""

  # Test with password
  if [[ -n "$OPENWRT_PASSWORD" ]]; then
    if sshpass -p "$OPENWRT_PASSWORD" ssh $SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" "echo SSHOK" >/dev/null 2>&1; then
      ok "SSH connection successful (password auth)"
      return 0
    else
      err "SSH authentication failed with provided password"
      OPENWRT_PASSWORD=""
      return 1
    fi
  else
    # Empty password entered - try one more time
    if ssh $SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" "echo SSHOK" >/dev/null 2>&1; then
      ok "SSH connection successful (no password)"
      return 0
    else
      err "SSH connection failed"
      return 1
    fi
  fi
}

maybe_set_root_password(){
  # This function is now replaced by init_ssh_auth
  # Keeping for backwards compatibility
  init_ssh_auth
}
