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
/etc/init.d/avahi-daemon restart || true
SH

  copy_to_openwrt "$tmp" "/tmp/mdns-config.sh"
  exec_ssh "chmod +x /tmp/mdns-config.sh && /tmp/mdns-config.sh" \
    || { warn "mDNS config failed (avahi likely missing)"; return 0; }
  ok "mDNS reflector configured"
}
