#!/usr/bin/env bash
# build-store-deb.sh — the ISLE APP STORE launcher .deb (handoff
# §32). Unlike a plain app launcher, installing the store ALWAYS
# brings up an isle AGENT on the device (Dustin: "the app store
# shell at minimum should always come along with an agent being
# installed"), so a user can use isle-mesh to any extent on any
# device — host apps, not just reach them. The postinst:
#   1. trusts the isle CA (isle trust install, if the CLI is there)
#   2. ensures an isle agent is running (isle agent ensure)
#   3. registers the store launcher (opens /isle — the tabbed hub)
# Depends: polari-shell-core, policykit-1, and (recommended) the
# isle CLI. Idempotent postinst; degrades honestly if the isle CLI
# is absent (prints what to run).
#
#   build-store-deb.sh --url https://polari.isle/isle [--ca <root>]
#       [--version 0.1.0] [--output dist]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
URL="https://polari.isle/isle"
CA=""; VERSION=0.1.0; OUTPUT="$ROOT/dist"
while [ $# -gt 0 ]; do case "$1" in
    --url) URL="$2"; shift 2 ;;
    --ca) CA="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
esac; done

PKG="isle-app-store"
SHARE="/usr/share/$PKG"
STAGE="$ROOT/build/store-deb"
rm -rf "$STAGE"; mkdir -p "$STAGE/DEBIAN" "$STAGE$SHARE" \
    "$STAGE/usr/share/applications" \
    "$STAGE/usr/share/icons/hicolor/256x256/apps" "$OUTPUT"

# the ISLAND mark (CC0, shells/icons) is the store's icon
ISLAND="$ROOT/shells/icons/isle-island.png"
if [ -f "$ISLAND" ]; then
    cp "$ISLAND" \
        "$STAGE/usr/share/icons/hicolor/256x256/apps/$PKG.png"
    STORE_ICON="$PKG"
else
    STORE_ICON="applications-internet"
fi

# ---- shell config (ShellConfig pointed at the tabbed hub) ----
CA_ARG="${CA:-}"
python3 - "$URL" "$CA_ARG" > "$STAGE$SHARE/polari-shell.json" <<'PYEOF'
import json, sys
url, ca = sys.argv[1], sys.argv[2]
ca_pem = [open(ca).read()] if ca else []
print(json.dumps({
    "kind": "polari-shell-registration", "schemaVersion": 1,
    "app": {"name": "isle-app-store", "title": "Isle App Store",
            "scope": "instance", "appName": "", "startRoute": "",
            "brandColor": "", "icon": ""},
    "instances": [{
        "id": "isle-store", "displayName": "Isle App Store",
        "webUrl": url, "apiUrl": url,
        # blank identity/instanceId → the shell treats this as a
        # plain mesh web view (no appstore-enrollment probe); prf-isle
        # doesn't run the appstore module, so a strict identity check
        # would false-flag on the SPA fallback.
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
                         "hint": {"cidrs": [], "probeUrl": url}},
    }], "enrollment": None}, indent=2))
PYEOF
[ -n "$CA_ARG" ] && [ -f "$CA_ARG" ] && cp "$CA_ARG" "$STAGE$SHARE/isle-root.crt"

# ---- .desktop ----
cat > "$STAGE/usr/share/applications/$PKG.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Isle App Store
Comment=Install and manage isle-mesh apps on this device
Exec=/usr/bin/polari-app-shell --config $SHARE/polari-shell.json
Icon=$STORE_ICON
Terminal=false
Categories=Network;System;
EOF

# ---- postinst: trust seed ONLY. The agent tier is an EXPLICIT
# user step — `isle agent ensure` on an agent-less device launches
# an interactive installer that REWRITES HOST NETWORKING
# (wpa_supplicant/networkd handoff; it took econ-core's wifi down
# mid-install). A maintainer script must never do that. ----
cat > "$STAGE/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
echo "==> Isle App Store: trusting the isle on this device"
CA=/usr/share/isle-app-store/isle-root.crt
# apt runs us with a sanitized PATH that skips /usr/local/bin —
# resolve the CLI symlink explicitly
ISLE=$(command -v isle 2>/dev/null || true)
[ -n "$ISLE" ] || { [ -x /usr/local/bin/isle ] && ISLE=/usr/local/bin/isle || true; }
if [ -n "$ISLE" ]; then
    if [ -f "$CA" ]; then
        # isle trust reads /etc/isle-mesh/ca/isle-root.crt, not our
        # share dir — seed it so the trust step actually takes (§34)
        mkdir -p /etc/isle-mesh/ca
        [ -f /etc/isle-mesh/ca/isle-root.crt ] || cp "$CA" /etc/isle-mesh/ca/isle-root.crt
        "$ISLE" trust install --yes 2>/dev/null || true
    fi
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^isle-vlan-agent$'; then
        echo "==> agent already running — this device can host apps"
    else
        echo "   to HOST mesh-apps here (brings up an agent — touches"
        echo "   network config, asks first):  sudo isle onboard --host"
    fi
    echo "==> ready — open 'Isle App Store' from your menu"
else
    echo "   NOTE: the isle CLI is not installed on this device."
    echo "   Install it to host apps here (reach still works):"
    echo "     see isle-core:~/Isle-Mesh (appInstall.sh / isle CLI)"
fi
exit 0
POSTINST
chmod 755 "$STAGE/DEBIAN/postinst"

INSTALLED_KB=$(du -sk "$STAGE$SHARE" | cut -f1)
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: web
Priority: optional
Architecture: all
Depends: polari-shell-core, policykit-1
Recommends: isle-mesh-cli
Installed-Size: $INSTALLED_KB
Maintainer: Polari <polari@localhost>
Description: Isle App Store — install & manage isle-mesh apps
 The genuine isle-mesh app store as a native window: browse the
 catalog + live topology, install apps (with a password prompt),
 and project the polari app store when a polari instance is up.
 Installing this ALWAYS brings up an isle agent so this device can
 host apps, not only reach them.
EOF

DEB="$OUTPUT/${PKG}_${VERSION}_all.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$DEB" >/dev/null
echo "built: $DEB ($(du -h "$DEB" | cut -f1))"
echo "install: sudo apt install ./$DEB   (installs the agent too)"
