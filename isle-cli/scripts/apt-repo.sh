#!/bin/bash
# apt-repo.sh — `isle apt-repo`: the APT-ON-MESH repo (handoff
# ISLE_ONBOARDING §5b distribution). The registry (§28) is the
# docker-image half of offline supply; this is the DEB half: a
# SIGNED flat apt repo served at https://apt.isle so a remote pulls
# isle-mesh-cli / polari-shell-core / isle-app-store over the isle,
# no internet needed.
#
#   isle apt-repo publish [--from <dir>]   (core) collect newest debs,
#                                          sign, (re)serve at apt.isle
#   isle apt-repo enable                   (client) trust the repo key +
#                                          add the apt source here
#   isle apt-repo status                   what the repo serves
#
# Trust model: the archive-signing KEY is fetched over a TLS channel
# the isle root CA authenticates (tier-1 trust must already be
# done), then apt verifies every index against that key (signed-by).
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
G="\033[0;32m"; Y="\033[1;33m"; R="\033[0;31m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }
warn(){ echo -e "${Y}[WARN]${N} $*"; }
die(){ echo -e "${R}[FAIL]${N} $*"; exit 1; }

APT_DIR=/etc/isle-mesh/apt
REPO="$APT_DIR/repo"
GNUPG="$APT_DIR/gnupg"
KEYID="Isle Mesh Apt <apt@isle>"
KEYRING_NAME=isle-archive-keyring.gpg
URL=https://apt.isle
CA=/etc/isle-mesh/ca/isle-root.crt
CURL="curl -sf --max-time 8 --cacert $CA"

ensure_key() {
    sudo mkdir -p "$GNUPG"; sudo chmod 700 "$GNUPG"
    if ! sudo GNUPGHOME="$GNUPG" gpg --batch --list-secret-keys 2>/dev/null | grep -q "apt@isle"; then
        warn "generating the archive signing key (once)"
        sudo GNUPGHOME="$GNUPG" gpg --batch --pinentry-mode loopback --passphrase "" --quick-gen-key "$KEYID" ed25519 sign 0 \
            || die "key generation failed"
    fi
    ok "archive signing key present"
}

publish() {
    # deb sources, first hit wins: --from, the hand-staged dir (the
    # INVOKING user's home — under sudo $HOME is /root, finding #6),
    # then the from-code build output (.generated/debs of a suite
    # checkout) — the normal fresh-box world after build-polari-isle-deb
    local UH; UH="$(getent passwd "${SUDO_USER:-$USER}" | cut -d: -f6)"
    local FROM=""
    while [ $# -gt 0 ]; do case "$1" in
        --from) FROM="$2"; shift 2 ;;
        *) shift ;;
    esac; done
    if [ -z "$FROM" ]; then
        for c in "$UH/polari-shells" "$UH/polari-suite/.generated/debs" \
                 "$HOME/polari-shells"; do
            ls "$c"/*.deb >/dev/null 2>&1 && { FROM="$c"; break; }
        done
    fi
    [ -n "$FROM" ] && [ -d "$FROM" ] || die "no deb source dir found (looked: ~/polari-shells, ~/polari-suite/.generated/debs; or pass --from <dir>)"
    ensure_key
    sudo mkdir -p "$REPO"

    # newest version of each package only (sort -V per package name)
    local names
    names=$(ls "$FROM"/*.deb 2>/dev/null | xargs -rn1 basename | sed 's/_.*//' | sort -u)
    [ -n "$names" ] || die "no debs in $FROM"
    for p in $names; do
        local newest
        newest=$(ls "$FROM/${p}"_*.deb | sort -V | tail -1)
        sudo cp -u "$newest" "$REPO/"
        echo "   + $(basename "$newest")"
        # prune superseded versions from the served repo
        ls "$REPO/${p}"_*.deb 2>/dev/null | sort -V | head -n -1 \
            | xargs -r sudo rm -f
    done

    # flat-repo indices: Packages(.gz) → Release → signatures
    ( cd "$REPO" && \
      sudo sh -c 'dpkg-scanpackages --multiversion . /dev/null > Packages 2>/dev/null' && \
      sudo sh -c 'gzip -kf Packages' && \
      sudo sh -c 'apt-ftparchive -o APT::FTPArchive::Release::Origin=isle-mesh \
          -o APT::FTPArchive::Release::Label="Isle Mesh" \
          -o APT::FTPArchive::Release::Suite=isle \
          -o APT::FTPArchive::Release::Description="Isle-Mesh apt-on-mesh repo" \
          release . > Release' ) || die "index build failed"
    sudo GNUPGHOME="$GNUPG" gpg --batch --pinentry-mode loopback --passphrase "" --yes -abs -o "$REPO/Release.gpg" "$REPO/Release" \
        || die "Release signing failed"
    sudo GNUPGHOME="$GNUPG" gpg --batch --pinentry-mode loopback --passphrase "" --yes --clearsign -o "$REPO/InRelease" "$REPO/Release" \
        || die "InRelease signing failed"
    sudo GNUPGHOME="$GNUPG" gpg --batch --yes --export -o "$REPO/$KEYRING_NAME"
    # serve the remote bootstrap + the root CA next to the debs (the
    # §5b one-flow: a fresh remote fetches these first)
    [ -f "$SCRIPT_DIR/isle-bootstrap.sh" ] && sudo cp "$SCRIPT_DIR/isle-bootstrap.sh" "$REPO/"
    [ -f "$CA" ] && sudo cp "$CA" "$REPO/isle-root.crt"
    # ---- the HUMAN landing page (finding #9c, 2026-08-23):
    # https://apt.isle/ was a bare-nginx 403 — an average user sent
    # here (the store's join door, printed instructions) had no way
    # to discover the debs, the CA, or what to do. Regenerated on
    # every publish from what is actually served.
    local FPR
    FPR=$(openssl x509 -in "$CA" -noout -fingerprint -sha256 \
          2>/dev/null | cut -d= -f2 || echo "unavailable")
    DEB_ROWS=$(cd "$REPO" && for f in *.deb; do
        printf '<tr><td><a href="%s">%s</a></td><td>%s</td></tr>' \
            "$f" "$f" "$(du -h "$f" | cut -f1)"; done)
    sudo tee "$REPO/index.html" >/dev/null <<HTMLEOF
<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Isle apt repository</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:46rem;margin:2rem auto;
      padding:0 1rem;line-height:1.5;color:#222}
 code,pre{background:#f4f4f4;padding:.15rem .35rem;border-radius:4px}
 pre{padding:.6rem;overflow-x:auto}
 table{border-collapse:collapse;width:100%}
 td{padding:.3rem .6rem;border-bottom:1px solid #eee}
 .fpr{word-break:break-all;font-size:.85em}
</style></head><body>
<h1>This isle's software</h1>
<p>Signed apt repository served by this isle's core.
   Everything here is installable two ways:</p>
<h2>Join this isle (new device)</h2>
<pre>curl -ko isle-bootstrap.sh https://apt.isle/isle-bootstrap.sh
# VERIFY its sha256 against the core's printout, then:
sudo bash isle-bootstrap.sh --fingerprint '&lt;from the core&gt;'</pre>
<p>The CA fingerprint is the trust anchor — always compare it.
   This core's CA (<a href="isle-root.crt">isle-root.crt</a>)
   fingerprint:</p>
<p class="fpr"><code>$FPR</code></p>
<h2>Already a member — apt route</h2>
<pre>isle apt-repo enable
sudo apt update &amp;&amp; sudo apt install isle-app-store</pre>
<h2>Packages served</h2>
<table>$DEB_ROWS</table>
<p>Index files: <a href="Packages">Packages</a> ·
   <a href="Release">Release</a> ·
   <a href="isle-archive-keyring.gpg">archive keyring</a></p>
</body></html>
HTMLEOF
    sudo chmod -R a+rX "$REPO"
    ok "repo indexed + signed ($(ls "$REPO"/*.deb | wc -l) debs) + landing page"

    # serve: an nginx on the agent net at apt.isle (bind-mounted, so
    # re-publish is live without redeploy)
    if docker ps --format '{{.Names}}' | grep -q '^isle-apt-apt-1$\|^isle-apt\b'; then
        ok "apt.isle already serving (bind mount picks up the refresh)"
    else
        local COMPOSE="$APT_DIR/compose.yml"
        sudo tee "$COMPOSE" >/dev/null <<EOF
services:
  apt:
    image: nginx:alpine
    restart: unless-stopped
    volumes:
      - $REPO:/usr/share/nginx/html:ro
EOF
        isle app deploy apt --compose "$COMPOSE" --service apt --port 80 \
            --domain apt.isle || die "apt.isle deploy failed"
        ok "apt.isle deployed"
    fi
    # finding #9b: the CORE host itself cannot reach its own agent
    # (macvlan isolation) and the catalog-driven hairpin reconcile
    # only helps once the app reaches the catalog — pin here too,
    # same precedent as enable_client. Idempotent.
    if ! getent hosts apt.isle >/dev/null 2>&1; then
        echo "127.0.0.1 apt.isle" | sudo tee -a /etc/hosts >/dev/null
        ok "core hairpin: pinned apt.isle -> 127.0.0.1 in /etc/hosts"
    fi
    echo "clients: isle apt-repo enable && sudo apt update"
    echo "humans:  https://apt.isle/  (debs + CA + join instructions)"
}

fetch_repo() { # $1 path, $2 outfile
    $CURL -o "$2" "$URL/$1" 2>/dev/null && return 0
    $CURL --resolve apt.isle:443:127.0.0.1 -o "$2" "$URL/$1" 2>/dev/null
}

enable_client() {
    [ -f "$CA" ] || die "isle CA not trusted here — run: isle trust fetch / isle onboard"
    # apt cannot --resolve: on the CORE HOST (macvlan isolation — it
    # can't reach its own agent's IP) pin apt.isle to the local proxy
    if ! $CURL -o /dev/null "$URL/InRelease" 2>/dev/null; then
        if $CURL --resolve apt.isle:443:127.0.0.1 -o /dev/null "$URL/InRelease" 2>/dev/null \
           && ! grep -q "apt\.isle" /etc/hosts; then
            echo "127.0.0.1 apt.isle" | sudo tee -a /etc/hosts >/dev/null
            ok "host isolation detected — pinned apt.isle → 127.0.0.1 in /etc/hosts"
        fi
    fi
    local tmp; tmp=$(mktemp)
    fetch_repo "$KEYRING_NAME" "$tmp" || die "cannot fetch the repo key from $URL (is the core's apt repo published?)"
    sudo install -m 644 "$tmp" "/usr/share/keyrings/$KEYRING_NAME"; rm -f "$tmp"
    ok "archive key trusted (fetched over the CA-authenticated channel)"
    echo "deb [signed-by=/usr/share/keyrings/$KEYRING_NAME] $URL ./" \
        | sudo tee /etc/apt/sources.list.d/isle-mesh.list >/dev/null
    ok "apt source added: $URL (flat, signed)"
    echo "next: sudo apt update && sudo apt install isle-mesh-cli polari-shell-core isle-app-store"
}

status() {
    local tmp; tmp=$(mktemp)
    if fetch_repo Packages "$tmp"; then
        echo "apt-on-mesh @ $URL serves:"
        grep -E "^Package:|^Version:" "$tmp" | paste - - | sed 's/Package: /  /;s/\tVersion: / /'
    elif [ -d "$REPO" ]; then
        echo "local repo dir (not reachable via $URL):"
        ls "$REPO"/*.deb 2>/dev/null | sed 's|.*/|  |'
    else
        warn "no apt repo reachable and none published here"
    fi
    rm -f "$tmp"
}

case "${1:-help}" in
    publish) shift; publish "$@" ;;
    enable)  enable_client ;;
    status)  status ;;
    *) echo "usage: isle apt-repo [publish [--from <dir>]|enable|status]" ;;
esac
