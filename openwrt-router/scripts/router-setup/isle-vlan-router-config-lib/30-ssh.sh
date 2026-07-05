#!/usr/bin/env bash
if [[ -n "${_SSH_SH:-}" ]]; then return; fi; _SSH_SH=1

# Check if sshpass is available
HAS_SSHPASS=false
if command -v sshpass >/dev/null 2>&1; then
  HAS_SSHPASS=true
fi

# Load cached router password from initial setup (if available)
ISLE_ROUTER_PASS_FILE="${ISLE_ROUTER_PASS_FILE:-/etc/isle-mesh/router/ssh/.cached_password}"
if [[ -f "$ISLE_ROUTER_PASS_FILE" ]]; then
  OPENWRT_PASSWORD="$(cat "$ISLE_ROUTER_PASS_FILE" 2>/dev/null)"
else
  OPENWRT_PASSWORD=""
fi

exec_ssh(){
  # Use cached password via sshpass if available (key is already in SSH_OPTS -i)
  if [[ -n "$OPENWRT_PASSWORD" ]] && [[ "$HAS_SSHPASS" == "true" ]]; then
    sshpass -p "$OPENWRT_PASSWORD" ssh $SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" "$@"
  else
    ssh -o BatchMode=yes $SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" "$@"
  fi
}

copy_to_openwrt(){
  # -O: use legacy SCP protocol (OpenWRT Dropbear lacks sftp-server)
  if [[ -n "$OPENWRT_PASSWORD" ]] && [[ "$HAS_SSHPASS" == "true" ]]; then
    sshpass -p "$OPENWRT_PASSWORD" scp -O $SSH_OPTS "$1" "${OPENWRT_USER}@${OPENWRT_IP}:$2"
  else
    scp -O -o BatchMode=yes $SSH_OPTS "$1" "${OPENWRT_USER}@${OPENWRT_IP}:$2"
  fi
}

init_ssh_auth(){
  info "Testing SSH connection to ${OPENWRT_USER}@${OPENWRT_IP}…"

  # First try key-based SSH (BatchMode prevents /dev/tty password prompt)
  if ssh -o BatchMode=yes $SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" "echo SSHOK" >/dev/null 2>&1; then
    ok "SSH connection successful (key-based auth)"
    return 0
  fi

  # Try cached password (from router-init setup)
  if [[ -n "$OPENWRT_PASSWORD" ]] && [[ "$HAS_SSHPASS" == "true" ]]; then
    if sshpass -p "$OPENWRT_PASSWORD" ssh $SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" "echo SSHOK" >/dev/null 2>&1; then
      ok "SSH connection successful (cached password)"
      return 0
    fi
  fi

  # Passwordless and cached both failed - need to prompt
  if [[ "$HAS_SSHPASS" != "true" ]]; then
    warn "SSH requires password but 'sshpass' is not installed"
    warn "Install with: sudo apt-get install sshpass"
    return 1
  fi

  # Non-interactive first: try the known default router password(s).
  local _dp
  for _dp in "${ROUTER_DEFAULT_PASSWORD:-root}" "root" ""; do
    if sshpass -p "$_dp" ssh $SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" "echo SSHOK" >/dev/null 2>&1; then
      OPENWRT_PASSWORD="$_dp"; ok "SSH connection successful (default password)"; return 0
    fi
  done

  # Prompt only if a real terminal is present; unattended/app installs must not hang.
  if [[ -t 0 ]]; then
    warn "SSH key/cached/default password not working — password required"
    echo -n "Enter password for root@${OPENWRT_IP}: "
    read -s OPENWRT_PASSWORD
    echo ""
    if sshpass -p "$OPENWRT_PASSWORD" ssh $SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" "echo SSHOK" >/dev/null 2>&1; then
      ok "SSH connection successful (password auth)"
      return 0
    else
      err "SSH authentication failed with provided password"
      OPENWRT_PASSWORD=""
      return 1
    fi
  else
    err "Router SSH auth failed (default rejected) and no terminal to prompt"
    return 1
  fi
}

maybe_set_root_password(){
  # This function is now replaced by init_ssh_auth
  # Keeping for backwards compatibility
  init_ssh_auth
}
