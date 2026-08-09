#!/bin/bash
# build-cli-deb.sh — build the isle-mesh-cli .deb FROM THE REPO
# (retires the /tmp hand-build that produced 0.1.0/0.1.1). Packages
# /usr/share/isle-mesh (the CLI + agent + scaffolding + shell tools
# + ICONS) and symlinks the `isle` command in postinst.
#
#   isle-cli/shells/build-cli-deb.sh [--version 0.1.2] \
#       [--output ~/polari-shells]
#
# Ships (handoff ISLE_ONBOARDING §4):
#   - shells/ tools + icons  → branded launchers on every device
#   - shells/debs/           → the SYSTEM stage a pkexec (root)
#                              install reads (onboard fills it)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
VERSION=0.1.2
OUTPUT="$HOME/polari-shells"
while [ $# -gt 0 ]; do case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
esac; done

PKG=isle-mesh-cli
SHARE=usr/share/isle-mesh
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/DEBIAN" "$STAGE/$SHARE" "$OUTPUT"

# ---- the CLI + its supporting trees (junk excluded) ----
for d in isle-cli isle-agent mdns mesh-app-scaffolding openwrt-router; do
    [ -d "$REPO/$d" ] || { echo "missing $REPO/$d" >&2; exit 1; }
    tar -C "$REPO" --exclude=.git --exclude=node_modules \
        --exclude='*.backup' -cf - "$d" | tar -C "$STAGE/$SHARE" -xf -
done

# ---- shell tools + icons (synced from polari-app-shell — see
# isle-cli/shells/tools/README) staged at shells/ so
# build-launcher-deb.sh finds $ROOT/shells/icons/polari-mark.png ----
TOOLS="$REPO/isle-cli/shells/tools"
[ -f "$TOOLS/build-launcher-deb.sh" ] || { echo "missing $TOOLS (sync from polari-app-shell/shells)" >&2; exit 1; }
mkdir -p "$STAGE/$SHARE/shells/icons" "$STAGE/$SHARE/shells/debs"
cp "$TOOLS"/*.sh "$STAGE/$SHARE/shells/"
cp "$TOOLS"/icons/* "$STAGE/$SHARE/shells/icons/"
cat > "$STAGE/$SHARE/shells/debs/README" <<'EOF'
System-wide stage for polari shell debs. `isle onboard` copies the
polari-shell-core runtime deb here so a pkexec-as-root install from
the store UI (whose $HOME is /root) can find it.
EOF

cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
# the old hand-built debs PACKAGED /usr/local/bin/isle — upgrading
# from them makes dpkg delete the file and prune the then-empty
# /usr/local/bin, so recreate it before linking
mkdir -p /usr/local/bin
ln -sf /usr/share/isle-mesh/isle-cli/index.js /usr/local/bin/isle
chmod +x /usr/share/isle-mesh/isle-cli/index.js 2>/dev/null || true
exit 0
EOF
cat > "$STAGE/DEBIAN/postrm" <<'EOF'
#!/bin/sh
[ "$1" = remove ] && rm -f /usr/local/bin/isle
exit 0
EOF
chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

INSTALLED_KB=$(du -sk "$STAGE/usr" | cut -f1)
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: admin
Priority: optional
Architecture: all
Depends: nodejs, jq, openssl, curl
Replaces: isle-manager-app (<< 0.2)
Installed-Size: $INSTALLED_KB
Maintainer: Isle-Mesh <isle@localhost>
Description: Isle-Mesh CLI
 isle command: trust, agent, store, shell launchers, onboard,
 app deploy. Ships the shell build tools + icons and the system
 deb stage under /usr/share/isle-mesh/shells.
EOF

DEB="$OUTPUT/${PKG}_${VERSION}_all.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$DEB" >/dev/null
echo "built: $DEB ($(du -h "$DEB" | cut -f1))"
echo "install: sudo apt install $DEB"
