#!/bin/bash
# shell.sh — `isle shell`: install polari/isle apps as NATIVE-feeling
# desktop shells (handoff §32). Makes a catalog polari-app install
# produce a real launcher .deb sharing the one polari-shell-core
# runtime, so polari apps feel native. Used by `isle store install`
# for polari-app entries.
#
#   isle shell launcher --name <app> --title "<T>" --url <.isle url>
#       [--ca <root>] [--install]
#   isle shell ensure-core         install polari-shell-core if absent
#
# The launcher builder lives in the polari-app-shell repo; on a
# device it is staged under /usr/share/isle-mesh/shells or found via
# POLARI_SHELL_TOOLS. If absent, we fall back to a stub launcher.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
G="\033[0;32m"; Y="\033[1;33m"; R="\033[0;31m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }
warn(){ echo -e "${Y}[WARN]${N} $*"; }
die(){ echo -e "${R}[FAIL]${N} $*"; exit 1; }

CA_DEFAULT=/etc/isle-mesh/ca/isle-root.crt
SHELLS_DIR="${POLARI_SHELL_TOOLS:-/usr/share/isle-mesh/shells}"
STAGE="${POLARI_SHELL_STAGE:-$HOME/polari-shells}"

ensure_core() {
    if dpkg -s polari-shell-core >/dev/null 2>&1; then
        ok "polari-shell-core already installed"; return 0
    fi
    local deb
    deb=$(ls "$STAGE"/polari-shell-core_*_amd64.deb 2>/dev/null | sort -V | tail -1)
    [ -n "$deb" ] || die "polari-shell-core not staged in $STAGE (build-shared-shell.sh, or copy the deb here)"
    warn "installing the shared shell runtime (once): $deb"
    sudo dpkg -i "$deb" || sudo apt-get -f install -y
    ok "polari-shell-core installed"
}

case "${1:-help}" in
    ensure-core) ensure_core ;;

    launcher)
        shift
        NAME=""; TITLE=""; URL=""; CA="$CA_DEFAULT"; DO_INSTALL=0
        while [ $# -gt 0 ]; do case "$1" in
            --name) NAME="$2"; shift 2 ;;
            --title) TITLE="$2"; shift 2 ;;
            --url) URL="$2"; shift 2 ;;
            --ca) CA="$2"; shift 2 ;;
            --install) DO_INSTALL=1; shift ;;
            *) shift ;;
        esac; done
        [ -n "$NAME" ] && [ -n "$URL" ] || die "usage: isle shell launcher --name <app> --url <url> [--title T] [--install]"
        [ -n "$TITLE" ] || TITLE="$NAME"
        ensure_core
        # build the launcher deb from the shared-shell tools if present
        BUILD="$SHELLS_DIR/build-launcher-deb.sh"
        OUT="$STAGE"
        if [ -f "$BUILD" ]; then
            bash "$BUILD" --name "$NAME" --title "$TITLE" --url "$URL" \
                --kind isle --ca "$CA" --output "$OUT" || die "launcher build failed"
        else
            warn "shell build tools absent ($BUILD) — using a pre-staged launcher if present"
        fi
        DEB=$(ls "$OUT"/isle-app-"$NAME"_*_all.deb 2>/dev/null | sort -V | tail -1)
        [ -n "$DEB" ] || die "no launcher deb for $NAME in $OUT"
        ok "launcher built: $DEB"
        if [ "$DO_INSTALL" = 1 ]; then
            sudo apt-get install -y "$DEB" || sudo dpkg -i "$DEB" || die "install failed"
            ok "'$TITLE' installed — it's in your applications menu (opens $URL)"
        else
            echo "install: sudo apt install $DEB"
        fi
        ;;

    help|*)
        echo "usage: isle shell [launcher --name <a> --url <u> [--install]|ensure-core]"
        ;;
esac
