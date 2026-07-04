#!/usr/bin/env bash
# install-boot-persistence.sh — install + enable the isle-mesh boot-recovery unit.
# Run once, with sudo. Idempotent.
set -euo pipefail
[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo "Run with sudo: sudo bash $0" >&2; exit 1; }

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_SRC="$SRC/isle-mesh-boot.service"
UNIT_DST="/etc/systemd/system/isle-mesh-boot.service"
BRINGUP="$SRC/isle-cli/scripts/boot-bringup.sh"

[[ -f "$UNIT_SRC" ]] || { echo "Missing $UNIT_SRC" >&2; exit 1; }
[[ -f "$BRINGUP"  ]] || { echo "Missing $BRINGUP"  >&2; exit 1; }

chmod +x "$BRINGUP"
install -m 0644 "$UNIT_SRC" "$UNIT_DST"
systemctl daemon-reload
systemctl enable isle-mesh-boot.service

echo ""
echo "✓ Installed and enabled isle-mesh-boot.service"
echo "  It runs 'isle recover --boot' on every boot (after docker + libvirt)."
echo ""
echo "Test now (safe / idempotent — it is exactly what runs at boot):"
echo "    sudo systemctl start isle-mesh-boot.service"
echo "    journalctl -u isle-mesh-boot.service -n 60 --no-pager"
