#!/usr/bin/env bash
if [[ -n "${_SSHKEYS_SH:-}" ]]; then return; fi; _SSHKEYS_SH=1

setup_ssh_keys_if_any(){
  local PUB="${HOME}/.ssh/id_rsa.pub"
  [[ -f "$PUB" ]] || { warn "No SSH public key at $PUB; skipping key install"; return 0; }
  info "Installing SSH public key to OpenWRT authorized_keys…"
  exec_ssh "mkdir -p /root/.ssh && grep -qxF \"$(cat "$PUB")\" /root/.ssh/authorized_keys 2>/dev/null || echo \"$(cat "$PUB")\" >> /root/.ssh/authorized_keys" \
    || warn "Failed to install SSH key"
  ok "SSH key setup attempt complete"
}
