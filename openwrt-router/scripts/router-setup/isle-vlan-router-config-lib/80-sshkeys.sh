#!/usr/bin/env bash
if [[ -n "${_SSHKEYS_SH:-}" ]]; then return; fi; _SSHKEYS_SH=1

setup_ssh_keys_if_any(){
  local PUB="${HOME}/.ssh/id_rsa.pub"
  [[ -f "$PUB" ]] || { warn "No SSH public key at $PUB; skipping key install"; return 0; }
  info "Installing SSH public key to OpenWRT authorized_keys…"
  # Install to Dropbear location (OpenWRT uses Dropbear, not OpenSSH)
  exec_ssh "mkdir -p /etc/dropbear && grep -qxF \"$(cat "$PUB")\" /etc/dropbear/authorized_keys 2>/dev/null || echo \"$(cat "$PUB")\" >> /etc/dropbear/authorized_keys && chmod 600 /etc/dropbear/authorized_keys" \
    || warn "Failed to install SSH key"
  ok "SSH key setup attempt complete"
}
