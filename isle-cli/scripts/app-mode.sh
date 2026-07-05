#!/usr/bin/env bash
# app-mode.sh — get or set an installed isle-app's availability_mode.
#   isle app mode <name>            show current mode
#   isle app mode <name> <mode>     set mode (needs sudo — marker is root-owned)
#   isle app mode --list            list every installed app + its mode
# Modes: always-available (default) | on-demand | scheduled | presence-gated | replicated | manual
# The management app drives this same verb (CLI + app parity).
set -uo pipefail
DIR="/etc/isle-mesh/agent/installed-apps"
VALID="always-available on-demand scheduled presence-gated replicated manual"

read_mode(){ local m; m="$(grep -E '^AVAILABILITY_MODE=' "$1" 2>/dev/null | cut -d'"' -f2)"; echo "${m:-always-available}"; }

if [[ "${1:-}" == "--list" ]]; then
  shopt -s nullglob
  files=("$DIR"/*.env)
  [[ ${#files[@]} -gt 0 ]] || { echo "No isle-apps installed."; exit 0; }
  printf '%-22s %s\n' NAME MODE
  for f in "${files[@]}"; do NAME=; . "$f" 2>/dev/null; printf '%-22s %s\n' "$NAME" "$(read_mode "$f")"; done
  exit 0
fi

NAME="${1:-}"; MODE="${2:-}"
[[ -n "$NAME" ]] || { echo "usage: isle app mode <name> [<mode>]   (modes: $VALID)"; exit 1; }
ENVF="$DIR/${NAME}.env"
[[ -f "$ENVF" ]] || { echo "no installed isle-app '$NAME' (looked in $DIR)"; exit 1; }

cur="$(read_mode "$ENVF")"
if [[ -z "$MODE" ]]; then echo "$NAME: $cur"; exit 0; fi

case " $VALID " in *" $MODE "*) ;; *) echo "invalid mode '$MODE' (valid: $VALID)"; exit 1;; esac
if [[ ! -w "$ENVF" ]]; then echo "cannot write $ENVF — re-run with sudo (isle app mode is root-owned)"; exit 1; fi
if grep -qE '^AVAILABILITY_MODE=' "$ENVF"; then
  sed -i "s/^AVAILABILITY_MODE=.*/AVAILABILITY_MODE=\"$MODE\"/" "$ENVF"
else
  printf 'AVAILABILITY_MODE="%s"\n' "$MODE" >> "$ENVF"
fi
echo "$NAME: $cur -> $MODE"
# Enforcement (bring up/down to match the new mode) is done by boot-bringup reconcile
# and the management app; on-demand/scheduled/etc. need the wake control-plane (planned).
