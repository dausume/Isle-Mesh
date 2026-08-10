#!/usr/bin/env bash
# fix-live-certs.sh — regenerate any empty/invalid isle app SSL cert+key.
# The 2026-07-03 power surge left 0-byte cert/key files; nginx cannot load them
# (PEM "no start line") so the vlan-agent crash-loops and NOTHING serves on 443.
# Self-signed, SAN <base>.local + <base>.isle (TLS terminates at the proxy).
#   Run:  sudo bash ~/Isle-Mesh/fix-live-certs.sh
set -uo pipefail
CERTS=/etc/isle-mesh/agent/ssl/certs
KEYS=/etc/isle-mesh/agent/ssl/keys
[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo "Run with sudo: sudo bash $0"; exit 1; }

shopt -s nullglob
fixed=0
echo "== scanning $CERTS =="
for crt in "$CERTS"/*.crt; do
  name="$(basename "$crt" .crt)"          # e.g. sample.local
  base="${name%.local}"; base="${base%.isle}"
  key="$KEYS/${name}.key"
  if [[ -s "$crt" ]] && openssl x509 -in "$crt" -noout >/dev/null 2>&1 && [[ -s "$key" ]]; then
    echo "  ok:  $name (valid)"; continue
  fi
  echo "  fix: $name (empty/invalid) — regenerating..."
  if openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
       -keyout "$key" -out "$crt" -subj "/CN=${name}" \
       -addext "subjectAltName=DNS:${base}.local,DNS:${base}.isle" >/dev/null 2>&1; then
    chmod 644 "$crt"; chmod 600 "$key"; echo "    OK ($base.local, $base.isle)"; fixed=$((fixed+1))
  else
    echo "    FAILED for $name"
  fi
done
echo "== regenerated $fixed cert(s) =="

if [[ $fixed -gt 0 ]]; then
  echo "== restarting vlan-agent so nginx loads the valid certs =="
  docker restart isle-vlan-agent >/dev/null 2>&1 \
    || ( cd /etc/isle-mesh/agent && docker compose up -d --force-recreate >/dev/null 2>&1 )
  sleep 5
  docker ps --filter name=isle-vlan-agent --format "  {{.Names}}: {{.Status}}"
fi
