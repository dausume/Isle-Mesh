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
# polari-isle = the polari sub-project (seeds ~/polari-isle on deploy).
# Router IMAGES are excluded: they are fetch-or-build artifacts living
# untracked in a working tree — a built qcow2 can carry credentials and
# once shipped 30MB rides every deb (found 2026-08-17: hand-staged debs
# were silently packaging them).
for d in isle-cli isle-agent mdns mesh-app-scaffolding openwrt-router polari-isle; do
    [ -d "$REPO/$d" ] || { echo "missing $REPO/$d" >&2; exit 1; }
    tar -C "$REPO" --exclude=.git --exclude=node_modules \
        --exclude='*.backup' --exclude='*.qcow2' --exclude='*.img' \
        --exclude='*.img.gz' --exclude='router-setup/images' \
        -cf - "$d" | tar -C "$STAGE/$SHARE" -xf -
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
# security material is DEPLOY-TIME input, never shipped in the deb —
# point at the walkthrough instead of installing any default
if [ "$(cat /etc/isle-mesh/agent/agent.mode 2>/dev/null)" = remote ]; then
    bash /usr/share/isle-mesh/isle-cli/scripts/watch.sh enable >/dev/null 2>&1 || true
    echo "isle-watch enabled (member device: listens for the core's ISLE-ENDING)"
fi
echo "isle CLI installed. On a core: sudo isle core-install"
echo "  (ends with the production-security walkthrough; any time:"
echo "   isle security setup — passwords/domain/certs put in at deploy)"
exit 0
EOF
# prerm (unin-2): plain `apt remove` must leave NO running mesh behind —
# the standard route does the same steps as the terminal (stop timers,
# full runtime teardown, network ownership handback), DATA PRESERVED
# (/etc/isle-mesh + docker volumes survive for a reinstall). Guarded to
# `remove` only (never fires on upgrade), and every step tolerates
# failure — an uninstall must never wedge dpkg.
cat > "$STAGE/DEBIAN/prerm" <<'EOF'
#!/bin/sh
if [ "$1" = remove ]; then
    SCRIPTS=/usr/share/isle-mesh/isle-cli/scripts
    # CORE CASCADE (unin-7): removing the core ends the isle for every
    # member — send the last-gasp ISLE-ENDING broadcast FIRST so member
    # watchers stop their apps and prompt their humans. (apt cannot
    # prompt here; the interactive warning lives in `isle uninstall`.)
    if [ "$(cat /etc/isle-mesh/agent/agent.mode 2>/dev/null)" = core ]; then
        echo "isle-mesh-cli: THIS IS AN ISLE CORE — broadcasting ISLE-ENDING to members"
        bash "$SCRIPTS/watch.sh" broadcast-ending || true
    fi
    systemctl disable --now isle-watch >/dev/null 2>&1 || true
    echo "isle-mesh-cli: stopping the mesh (data is preserved; purge erases it)"
    bash "$SCRIPTS/destroy.sh" --force >/dev/null 2>&1 || true
    bash "$SCRIPTS/network-handback.sh" || true
    echo "isle-mesh-cli: mesh stopped, networking handed back to the OS."
    echo "  data kept: /etc/isle-mesh + docker volumes (apt purge erases them)"
fi
exit 0
EOF

# postrm (unin-2): `purge` additionally erases config/state. It runs
# AFTER package files are gone, so it is fully self-contained. Volumes:
# backup-then-delete (accepted Q1) into /var/backups/isle-mesh-<date>.
cat > "$STAGE/DEBIAN/postrm" <<'EOF'
#!/bin/sh
[ "$1" = remove ] && rm -f /usr/local/bin/isle
if [ "$1" = purge ]; then
    BK="/var/backups/isle-mesh-purge-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$BK"
    [ -d /etc/isle-mesh ] && tar czf "$BK/etc-isle-mesh.tgz" -C /etc isle-mesh 2>/dev/null
    if command -v docker >/dev/null 2>&1; then
        for v in $(docker volume ls --format '{{.Name}}' 2>/dev/null             | grep -E '^(isle-|polari-isle_|prf-isle-|prf-polari-)'); do
            docker run --rm -v "$v":/v:ro -v "$BK":/b alpine                 tar czf "/b/$v.tgz" -C /v . >/dev/null 2>&1                 && docker volume rm "$v" >/dev/null 2>&1                 && echo "isle-mesh-cli purge: volume $v backed up + removed"
        done
    fi
    for unit in polari-isle-push isle-trust-update isle-host-agent isle-mesh-boot isle-watch; do
        systemctl stop "$unit.timer" "$unit.service" >/dev/null 2>&1 || true
        systemctl disable "$unit.timer" "$unit.service" >/dev/null 2>&1 || true
        rm -f "/etc/systemd/system/$unit.service" "/etc/systemd/system/$unit.timer"
    done
    systemctl daemon-reload >/dev/null 2>&1 || true
    rm -rf /etc/isle-mesh /usr/share/isle-mesh /var/lib/isle-mesh /var/log/isle-mesh
    rm -f /etc/apt/sources.list.d/isle-mesh.list /etc/dnsmasq.d/split-dns.conf
    sed -i '/\.isle$/d;/\.isle /d' /etc/hosts 2>/dev/null || true
    echo "isle-mesh-cli purge: config/state erased; backups at $BK"
fi
exit 0
EOF
chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/prerm" "$STAGE/DEBIAN/postrm"

INSTALLED_KB=$(du -sk "$STAGE/usr" | cut -f1)
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: admin
Priority: optional
Architecture: all
Depends: nodejs, jq, openssl, curl, iw, hostapd, socat
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
