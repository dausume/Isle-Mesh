#!/usr/bin/env bash
if [[ -n "${_MDNS_SH:-}" ]]; then return; fi; _MDNS_SH=1

configure_mdns_reflector(){
  info "Configuring mDNS reflector (Avahi)…"
  local tmp="$STATE_DIR/mdns-config.sh"
  cat >"$tmp"<<'SH'
#!/bin/sh
set -e
cat >/etc/avahi/avahi-daemon.conf <<'EOF'
[server]
use-ipv4=yes
use-ipv6=no
enable-reflector=yes
reflect-ipv=yes

[publish]
publish-addresses=yes
publish-domain=yes

[reflector]
enable-reflector=yes
reflect-ipv=yes
EOF

/etc/init.d/avahi-daemon enable
# Schedule avahi restart in background to allow SSH to exit cleanly
( sleep 2 && /etc/init.d/avahi-daemon restart ) >/dev/null 2>&1 </dev/null &
echo "Avahi restart scheduled"
SH

  copy_to_openwrt "$tmp" "/tmp/mdns-config.sh"
  exec_ssh "chmod +x /tmp/mdns-config.sh && /tmp/mdns-config.sh" \
    || { warn "mDNS config failed (avahi likely missing)"; return 0; }

  # Wait for avahi to restart
  info "Waiting for Avahi to restart (5 seconds)..."
  sleep 5

  ok "mDNS reflector configured"
}
