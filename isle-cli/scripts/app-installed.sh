#!/usr/bin/env bash
# app-installed.sh — list isle-apps installed on THIS node.
#
# The per-app .deb postinst records each installed app under installed-apps/ (down by
# default). "Installed" = the .deb is present on this node; run-state (up/down) is a
# separate question answered by the isle agent. The management app reads this to offer
# "bring up" on installed-but-not-running apps.
#
#   isle app installed [--json]
set -euo pipefail

DIR="/etc/isle-mesh/agent/installed-apps"
JSON=0; [[ "${1:-}" == "--json" ]] && JSON=1

shopt -s nullglob
files=("$DIR"/*.env)
if [[ ${#files[@]} -eq 0 ]]; then
  [[ $JSON == 1 ]] && echo "[]" || echo "No isle-apps installed on this node."
  exit 0
fi

if [[ $JSON == 1 ]]; then
  first=1; printf '['
  for f in "${files[@]}"; do
    NAME=; DOMAIN=; PORT=; PROTOCOL=; . "$f"
    [[ $first == 1 ]] || printf ','
    printf '{"name":"%s","domain":"%s","port":"%s","protocol":"%s"}' \
      "$NAME" "$DOMAIN" "$PORT" "$PROTOCOL"
    first=0
  done
  printf ']\n'
else
  printf '%-20s %-26s %-6s %s\n' NAME DOMAIN PORT PROTO
  for f in "${files[@]}"; do
    NAME=; DOMAIN=; PORT=; PROTOCOL=; . "$f"
    printf '%-20s %-26s %-6s %s\n' "$NAME" "$DOMAIN" "$PORT" "$PROTOCOL"
  done
fi
