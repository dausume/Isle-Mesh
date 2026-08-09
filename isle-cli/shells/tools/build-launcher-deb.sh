#!/usr/bin/env bash
# build-launcher-deb.sh — a THIN per-app launcher .deb (handoff
# §21). Carries only a .desktop entry, an icon, and a
# polari-shell.json config; Depends on polari-shell-core for the
# runtime. Installing it makes one polari or isle app appear as a
# native application in the launcher — sharing the one shared
# runtime, adding only kilobytes.
#
#   build-launcher-deb.sh --name <app> --title "<Title>" \
#       --url https://<app>.isle [--kind polari|isle] \
#       [--instance-name <n>] [--ca <root.pem>] [--icon <png>] \
#       [--version 0.1.0] [--output dist]
#
# The config is the shell's registration document (ShellConfig):
# one instance, its web UI URL, optional pinned CA. No runtime, no
# jars — that all lives in polari-shell-core.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME=""; TITLE=""; URL=""; KIND="isle"; INSTANCE=""; CA=""; ICON=""
VERSION=0.1.0; OUTPUT="$ROOT/dist"
while [ $# -gt 0 ]; do case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --title) TITLE="$2"; shift 2 ;;
    --url) URL="$2"; shift 2 ;;
    --kind) KIND="$2"; shift 2 ;;
    --instance-name) INSTANCE="$2"; shift 2 ;;
    --ca) CA="$2"; shift 2 ;;
    --icon) ICON="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
esac; done
[ -n "$NAME" ] || { echo "--name required"; exit 1; }
[ -n "$URL" ]  || { echo "--url required"; exit 1; }
[ -n "$TITLE" ] || TITLE="$NAME"
[ -n "$INSTANCE" ] || INSTANCE="$NAME"

PKG="${KIND}-app-$(printf '%s' "$NAME" | tr '[:upper:]_' '[:lower:]-' | tr -cd 'a-z0-9.-')"
SHARE="/usr/share/$PKG"
# staging: $ROOT/build is root-owned once the tools ship in the CLI
# deb — POLARI_SHELL_BUILD lets the caller point at a writable dir
STAGE="${POLARI_SHELL_BUILD:-$ROOT/build}/launcher-$NAME"
rm -rf "$STAGE"; mkdir -p "$STAGE/DEBIAN" "$STAGE$SHARE" \
    "$STAGE/usr/share/applications" "$STAGE/usr/share/icons/hicolor/256x256/apps"
mkdir -p "$OUTPUT"

# ---- the shell config: a REAL ShellConfig registration document
# (kind/schemaVersion/app/instances — validated by core's
# ShellConfig.looksValid). CA PEM is EMBEDDED (tls.caPem list), the
# shell's own pinning model — not a path. Built with python so the
# multi-line PEM escapes correctly.
API_URL="$URL"
case "$NAME" in
    # convention: an <app>.isle web app's API is api.<app>.isle when
    # that's how it was deployed; caller can override via --api later
    *) API_URL="$URL" ;;
esac
python3 - "$NAME" "$TITLE" "$URL" "$INSTANCE" "$KIND" "${CA:-}" > "$STAGE$SHARE/polari-shell.json" <<'PYEOF'
import json, sys
name, title, url, instance, kind, ca = sys.argv[1:7]
ca_pem = []
if ca:
    try:
        ca_pem = [open(ca).read()]
    except OSError:
        pass
doc = {
    "kind": "polari-shell-registration",
    "schemaVersion": 1,
    "app": {"name": name, "title": title, "scope": "instance",
            "appName": "", "startRoute": "", "brandColor": "",
            "icon": ""},
    "instances": [{
        "id": instance,
        "displayName": title,
        "webUrl": url,
        "apiUrl": url,
        "identityUrl": "",
        "instanceId": "",
        "auth": {"authority": "", "realm": "Polari",
                 "clientId": "polari-shell", "pkce": "S256",
                 "scope": "openid profile email roles",
                 "shellRedirectUri": "polari://oauth/callback",
                 "loginHint": ""},
        "tls": {"caPem": ca_pem, "caSha256": ""},
        "reachability": {"scope": "mesh", "networkKind": "",
                         "networkName": "isle mesh",
                         "hint": {"cidrs": [],
                                  "probeUrl": url}},
    }],
    "enrollment": None,
}
print(json.dumps(doc, indent=2))
PYEOF

# ---- icon: --icon wins; else the POLARI MARK is the default for
# every isle/polari app (Dustin) ----
DEFAULT_ICON="$ROOT/shells/icons/polari-mark.png"
[ -n "$ICON" ] && [ -f "$ICON" ] || ICON="$DEFAULT_ICON"
if [ -f "$ICON" ]; then
    cp "$ICON" "$STAGE/usr/share/icons/hicolor/256x256/apps/$PKG.png"
    ICON_LINE="Icon=$PKG"
else
    ICON_LINE="Icon=applications-internet"
fi

# ---- .desktop: runs the SHARED runtime pointed at this config ----
cat > "$STAGE/usr/share/applications/$PKG.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$TITLE
Comment=$TITLE (Polari/Isle app)
Exec=/usr/bin/polari-app-shell --config $SHARE/polari-shell.json
$ICON_LINE
Terminal=false
Categories=Network;
EOF

# ---- control: Depends on the shared runtime ----
INSTALLED_KB=$(du -sk "$STAGE$SHARE" | cut -f1)
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: web
Priority: optional
Architecture: all
Depends: polari-shell-core
Installed-Size: $INSTALLED_KB
Maintainer: Polari <polari@localhost>
Description: $TITLE — Polari/Isle app launcher
 A thin launcher for the "$TITLE" app. Carries only its config and
 icon; the JavaFX/JCEF runtime is shared via polari-shell-core.
 Opens $URL in its own window, feeling like a native app.
EOF

DEB="$OUTPUT/${PKG}_${VERSION}_all.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$DEB" >/dev/null
echo "built: $DEB ($(du -h "$DEB" | cut -f1) — no runtime, shares polari-shell-core)"
echo "install: sudo apt install ./$DEB   (pulls polari-shell-core once)"
