#!/usr/bin/env bash
# install-hotplug.sh — install the isle cable-hotplug udev rule.
# Run once with sudo. Makes a plugged-in isle cable trigger 'isle hotplug' (role-aware,
# discovery-gated, wifi-safe). Idempotent.
set -euo pipefail
[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo "Run with sudo: sudo bash $0" >&2; exit 1; }

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RULE_SRC="$SRC/90-isle-hotplug.rules"
RULE_DST=/etc/udev/rules.d/90-isle-hotplug.rules
[[ -f "$RULE_SRC" ]] || { echo "Missing $RULE_SRC" >&2; exit 1; }

# Resolve the isle binary the rule should call (system install, else the invoking
# user's user-space install).
ISLE_BIN="$(command -v isle 2>/dev/null || true)"
if [[ -z "$ISLE_BIN" && -n "${SUDO_USER:-}" ]]; then
  cand="$(eval echo "~${SUDO_USER}/.local/bin/isle")"
  [[ -x "$cand" ]] && ISLE_BIN="$cand"
fi
[[ -n "$ISLE_BIN" ]] || ISLE_BIN=/usr/local/bin/isle

sed "s#/usr/local/bin/isle#${ISLE_BIN}#g" "$RULE_SRC" > "$RULE_DST"
udevadm control --reload-rules

echo "✓ Installed $RULE_DST (isle → ${ISLE_BIN})"
echo "  A plugged-in isle cable now triggers 'isle hotplug' automatically."
echo "  Watch it:  tail -f /var/log/isle-hotplug.log   (then plug/unplug the cable)"
