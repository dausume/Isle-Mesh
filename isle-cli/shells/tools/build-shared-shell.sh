#!/usr/bin/env bash
# build-shared-shell.sh — the SHARED runtime, packaged ONCE
# (handoff §21). Produces `polari-shell-core_<ver>_amd64.deb`:
# a jpackage app-image (bundled JRE + all jars + JCEF natives) at
# /opt/polari-shell, plus a launcher on PATH. Every "native" polari
# or isle app is a THIN launcher .deb that Depends on this and only
# passes `--config` — so N apps cost ONE runtime on disk, not N.
#
#   shells/build-shared-shell.sh [--version 0.1.0] [--output dist]
#
# Isolated shell (no host pollution): all work under build/.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION=0.1.0
OUTPUT="$ROOT/dist"
while [ $# -gt 0 ]; do case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
esac; done
command -v jpackage >/dev/null || { echo "jpackage not found (need JDK 17+)"; exit 1; }

PKG=polari-shell-core
APPDIR=/opt/polari-shell
STAGE="$ROOT/build/shared-shell"
IMG="$STAGE/image"
rm -rf "$STAGE"; mkdir -p "$IMG" "$OUTPUT"

echo "==> 1/4 installDist (all jars + deps into one dir)"
(cd "$ROOT" && ./gradlew :desktop:installDist -q)
DIST="$ROOT/desktop/build/install/desktop"
[ -d "$DIST/lib" ] || { echo "installDist produced no lib/ — build failed"; exit 1; }

echo "==> 2/4 jpackage app-image (bundled runtime, shared by all launchers)"
# jlink a minimal runtime from the modules the app + JavaFX need.
MAINJAR=$(cd "$DIST/lib" && ls desktop*.jar | head -1)
jpackage \
    --type app-image \
    --name polari-shell \
    --app-version "$VERSION" \
    --input "$DIST/lib" \
    --main-jar "$MAINJAR" \
    --main-class org.polari.shell.desktop.DesktopMain \
    --java-options '-DGDK_BACKEND=x11' \
    --java-options '--add-exports=java.desktop/sun.awt=ALL-UNNAMED' \
    --java-options '--add-opens=java.desktop/sun.awt.X11=ALL-UNNAMED' \
    --dest "$IMG"
# The app-image is $IMG/polari-shell/ (bin/polari-shell + lib/runtime)

echo "==> 3/4 lay out the .deb tree ($APPDIR + PATH launcher)"
DEBROOT="$STAGE/deb"
mkdir -p "$DEBROOT$APPDIR" "$DEBROOT/usr/bin" "$DEBROOT/DEBIAN"
cp -a "$IMG/polari-shell/." "$DEBROOT$APPDIR/"
# a stable launcher name every per-app .deb calls
cat > "$DEBROOT/usr/bin/polari-app-shell" <<EOF
#!/bin/sh
exec $APPDIR/bin/polari-shell "\$@"
EOF
chmod 755 "$DEBROOT/usr/bin/polari-app-shell"
INSTALLED_KB=$(du -sk "$DEBROOT$APPDIR" | cut -f1)
cat > "$DEBROOT/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: web
Priority: optional
Architecture: amd64
Depends: libnss3-tools
Installed-Size: $INSTALLED_KB
Maintainer: Polari <polari@localhost>
Description: Polari/Isle app shell — SHARED runtime
 The JavaFX/JCEF runtime and all common shell code, installed once.
 Per-app launcher packages (polari-app-*, isle-app-*) depend on this
 and only carry a config pointing it at their app URL, so many
 native-feeling apps share one runtime on disk.
EOF

echo "==> 4/4 dpkg-deb build"
DEB="$OUTPUT/${PKG}_${VERSION}_amd64.deb"
dpkg-deb --build --root-owner-group "$DEBROOT" "$DEB" >/dev/null
echo "built: $DEB ($(du -h "$DEB" | cut -f1), runtime ${INSTALLED_KB}KB on disk once)"
echo "install: sudo dpkg -i $DEB   (then per-app launchers via build-launcher-deb.sh)"
