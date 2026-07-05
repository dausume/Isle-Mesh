#!/usr/bin/env bash
# app-package.sh — package a wrapped isle-app (docker-compose + shell) into a .deb.
#
# Turns a docker-compose app into a portable Debian package that feels like a normal
# installable app: a desktop icon + a per-app lifecycle wrapper (up/down/access/status)
# that wraps `docker compose` and registers/deregisters the app with the isle agent
# (proxy-terminated .local/.isle). Install it on any isle node with `dpkg -i`.
#
#   isle app package --name <n> --compose <file> [--domain <n>.local] [--container <c>]
#                    [--port 80] [--protocol http] [--version 0.1.0] [--output .]
#                    [--icon <png>] [--maintainer "you <you@host>"]
#
# The management app (AppsView) drives the installed apps; double-click → this wrapper.
set -euo pipefail

NAME=""; COMPOSE=""; DOMAIN=""; CONTAINER=""; PORT="80"; PROTOCOL="http"; AVAILABILITY_MODE="always-available"
VERSION="0.1.0"; OUTPUT="."; ICON=""; MAINTAINER="isle-mesh <isle@localhost>"

die(){ echo "ERROR: $*" >&2; exit 1; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --compose) COMPOSE="$2"; shift 2 ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    --container) CONTAINER="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --protocol) PROTOCOL="$2"; shift 2 ;;
    --mode) AVAILABILITY_MODE="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    --icon) ICON="$2"; shift 2 ;;
    --maintainer) MAINTAINER="$2"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) die "unknown arg: $1" ;;
  esac
done

[[ -n "$NAME" ]]    || die "--name is required"
[[ -n "$COMPOSE" && -f "$COMPOSE" ]] || die "--compose <existing docker-compose file> is required"
command -v dpkg-deb >/dev/null 2>&1 || die "dpkg-deb not found (install dpkg-dev)"
# sanitize name for package/paths (debian pkg names: lowercase, digits, - + .)
PKG="isle-app-$(printf '%s' "$NAME" | tr '[:upper:]_' '[:lower:]-' | tr -cd 'a-z0-9.-')"
[[ -z "$DOMAIN" ]] && DOMAIN="${NAME}.local"
[[ -z "$CONTAINER" ]] && CONTAINER="$NAME"
APPROOT="/usr/share/isle-mesh/apps/${NAME}"

STAGE="$(mktemp -d)/${PKG}_${VERSION}"
mkdir -p "$STAGE/DEBIAN" "$STAGE${APPROOT}" "$STAGE/usr/bin" "$STAGE/usr/share/applications"

# --- payload: the app's compose + its metadata --------------------------------
cp "$COMPOSE" "$STAGE${APPROOT}/docker-compose.yml"
cat > "$STAGE${APPROOT}/isle-app.env" <<EOF
NAME="${NAME}"
DOMAIN="${DOMAIN}"
CONTAINER="${CONTAINER}"
PORT="${PORT}"
PROTOCOL="${PROTOCOL}"
PKG="${PKG}"
AVAILABILITY_MODE="${AVAILABILITY_MODE}"
EOF

# --- lifecycle wrapper: wraps docker compose + isle agent registration --------
# (installed=this file present; down=containers stopped; up=compose up + registered)
cat > "$STAGE/usr/bin/${PKG}" <<'WRAP'
#!/bin/sh
# isle-app lifecycle wrapper (generated). Wraps docker compose for one isle-app +
# registers/deregisters it with the isle agent. Called by the management app.
set -e
SELF_DIR="__APPROOT__"
. "$SELF_DIR/isle-app.env"
DC="docker compose"; command -v docker >/dev/null 2>&1 || DC="docker-compose"
compose(){ $DC -f "$SELF_DIR/docker-compose.yml" -p "isle-app-$NAME" "$@"; }
case "${1:-}" in
  up)      compose up -d --build && isle agent register --name "$NAME" --domain "$DOMAIN" \
             --container "$CONTAINER" --port "$PORT" --protocol "$PROTOCOL"
           echo "up: $DOMAIN (and ${DOMAIN%.local}.isle) — access via the mesh proxy" ;;
  down)    isle agent unregister --name "$NAME" 2>/dev/null || true; compose down ;;
  status)  compose ps ;;
  access)  xdg-open "https://$DOMAIN" >/dev/null 2>&1 || echo "open https://$DOMAIN" ;;
  *)       echo "usage: ${0##*/} {up|down|status|access}"; exit 1 ;;
esac
WRAP
sed -i "s#__APPROOT__#${APPROOT}#" "$STAGE/usr/bin/${PKG}"

# --- desktop entry: double-click launches the app's up + access ---------------
cat > "$STAGE/usr/share/applications/${PKG}.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=${NAME} (isle-app)
Comment=Isle-Mesh app — ${DOMAIN}
Exec=${PKG} up
Terminal=false
Categories=Network;
EOF
if [[ -n "$ICON" && -f "$ICON" ]]; then
  mkdir -p "$STAGE/usr/share/icons/hicolor/256x256/apps"
  cp "$ICON" "$STAGE/usr/share/icons/hicolor/256x256/apps/${PKG}.png"
  echo "Icon=${PKG}" >> "$STAGE/usr/share/applications/${PKG}.desktop"
fi

# --- DEBIAN metadata ----------------------------------------------------------
cat > "$STAGE/DEBIAN/control" <<EOF
Package: ${PKG}
Version: ${VERSION}
Section: net
Priority: optional
Architecture: all
Depends: docker.io | docker-ce
Recommends: isle-manager-app
Maintainer: ${MAINTAINER}
Description: Isle-Mesh app: ${NAME}
 A docker-compose app wrapped as an installable isle-app. Managed by the Isle
 management app: bring up/down, access via the proxy-terminated ${DOMAIN} /
 ${DOMAIN%.local}.isle, de-register, or uninstall.
EOF

cat > "$STAGE/DEBIAN/postinst" <<EOF
#!/bin/sh
set -e
# Record as installed (down) so the management app can offer "bring up". Do NOT
# auto-start — the operator brings it up from the management app.
mkdir -p /etc/isle-mesh/agent/installed-apps 2>/dev/null || true
cp "${APPROOT}/isle-app.env" "/etc/isle-mesh/agent/installed-apps/${NAME}.env" 2>/dev/null || true
echo "Installed isle-app '${NAME}'. Bring it up:  ${PKG} up   (or from the management app)."
exit 0
EOF

cat > "$STAGE/DEBIAN/prerm" <<EOF
#!/bin/sh
set -e
# On remove: bring the app down + de-register + forget it.
${PKG} down 2>/dev/null || true
rm -f "/etc/isle-mesh/agent/installed-apps/${NAME}.env" 2>/dev/null || true
exit 0
EOF

chmod 755 "$STAGE/usr/bin/${PKG}" "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/prerm"

# --- build --------------------------------------------------------------------
OUT_DEB="${OUTPUT%/}/${PKG}_${VERSION}_all.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$OUT_DEB" >/dev/null
rm -rf "$(dirname "$STAGE")"
echo "✓ Built isle-app package: $OUT_DEB"
echo "  Install on any isle node:  sudo dpkg -i $(basename "$OUT_DEB")"
echo "  Then:  ${PKG} up   |   ${PKG} down   |   ${PKG} access   (or use the management app)"
