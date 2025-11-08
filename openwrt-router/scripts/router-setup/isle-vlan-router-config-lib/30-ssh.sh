#!/usr/bin/env bash
if [[ -n "${_SSH_SH:-}" ]]; then return; fi; _SSH_SH=1

exec_ssh(){ ssh $SSH_OPTS "${OPENWRT_USER}@${OPENWRT_IP}" "$@"; }
copy_to_openwrt(){ scp $SSH_OPTS "$1" "${OPENWRT_USER}@${OPENWRT_IP}:$2"; }

maybe_set_root_password(){
  echo -n "Do you want to set the root password now? (Y/n) "; read -r ans
  if [[ ! "$ans" =~ ^[Nn]$ ]]; then
    info "Setting root password (interactive on OpenWRT)…"
    exec_ssh "passwd" || warn "Couldn't set password non-interactively"
  fi
}
