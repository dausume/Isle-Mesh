#!/bin/bash
# trust.sh — CA trust as an INSTALL-STEP capability (isle trust).
#
# Trust lifecycle (Dustin 2026-08-08): established AT JOIN (the
# manager app shows the fingerprint, the user consents — the app
# elevates via pkexec to run the install), then MAINTAINED
# AUTOMATICALLY under the SIGNED-CHANNEL RULE: a new root is only
# ever accepted over a TLS channel the CURRENT root authenticates
# (rotation rides existing trust); anything else needs explicit
# re-consent (fetch).
#
#   isle trust status                    where is the root trusted?
#   isle trust install [--yes]           import into system + NSS
#   isle trust cert                      print path + fingerprint
#   isle trust fetch [url] [--fingerprint <sha256>]
#                                        FIRST-JOIN acquisition: download
#                                        the root, verify fp (interactive
#                                        or scripted), then install
#   isle trust update [--auto]           signed-channel refresh from
#                                        trust.isle (timer-driven)
set -u
CA=/etc/isle-mesh/ca/isle-root.crt
NSSDB="sql:$HOME/.pki/nssdb"
NICK="Isle Root CA"
TRUST_URL_DEFAULT="https://trust.isle/ca/isle-root.crt"
G="\033[0;32m"; Y="\033[1;33m"; R="\033[0;31m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }
warn(){ echo -e "${Y}[WARN]${N} $*"; }
fail(){ echo -e "${R}[FAIL]${N} $*"; }
fp_of(){ openssl x509 -in "$1" -noout -fingerprint -sha256 | cut -d= -f2; }

curl_trust(){ # $1 out; authenticated by CURRENT root, core-local fallbacks
    curl -s --max-time 6 --cacert "$CA" -o "$1" "$TRUST_URL_DEFAULT" 2>/dev/null \
    || curl -s --max-time 6 --cacert "$CA" --resolve trust.isle:443:127.0.0.1 -o "$1" "$TRUST_URL_DEFAULT" 2>/dev/null \
    || curl -s --max-time 6 --cacert "$CA" --resolve trust.isle:443:10.10.0.2 -o "$1" "$TRUST_URL_DEFAULT" 2>/dev/null
}

status() {
    [ -f "$CA" ] || { fail "no isle root at $CA"; exit 1; }
    echo "isle root CA: $CA"; echo "SHA-256:      $(fp_of "$CA")"; echo
    if [ -f /usr/local/share/ca-certificates/isle-root.crt ] \
       && cmp -s "$CA" /usr/local/share/ca-certificates/isle-root.crt; then
        ok "system store: trusted (curl/CLI tools)"
    else warn "system store: NOT trusted — isle trust install"; fi
    if command -v certutil >/dev/null 2>&1; then
        certutil -d "$NSSDB" -L 2>/dev/null | grep -q "$NICK" \
            && ok "Chrome/NSS ($USER): trusted" \
            || warn "Chrome/NSS ($USER): NOT trusted — isle trust install"
    else warn "certutil missing (apt install libnss3-tools)"; fi
    ls "$HOME"/.mozilla/firefox/*/cert9.db >/dev/null 2>&1 \
        && warn "Firefox present: separate store (Settings > Certificates, or policies.json)"
    local probe; probe=$(mktemp)
    if curl_trust "$probe" && [ -s "$probe" ]; then
        ok "live probe: trust.isle answered over a channel this root authenticates"
    else warn "live probe: trust.isle not reachable/authenticated from here"; fi
    rm -f "$probe"
}

do_import() {
    sudo cp "$CA" /usr/local/share/ca-certificates/isle-root.crt \
        && sudo update-ca-certificates >/dev/null 2>&1 \
        && ok "system store: imported" || fail "system store import failed"
    command -v certutil >/dev/null 2>&1 || sudo apt-get install -y libnss3-tools >/dev/null 2>&1 || true
    if command -v certutil >/dev/null 2>&1; then
        mkdir -p "$HOME/.pki/nssdb"; [ -f "$HOME/.pki/nssdb/cert9.db" ] || certutil -d "$NSSDB" -N --empty-password 2>/dev/null
        certutil -d "$NSSDB" -D -n "$NICK" 2>/dev/null || true
        certutil -d "$NSSDB" -A -t "C,," -n "$NICK" -i "$CA" \
            && ok "Chrome/NSS: imported (restart Chrome)" || fail "Chrome/NSS import failed"
    fi
}

install_trust() {
    [ -f "$CA" ] || { fail "no isle root at $CA — run: isle trust fetch"; exit 1; }
    echo "About to trust this CA for THIS machine/user:"
    echo "  $CA"; echo "  SHA-256: $(fp_of "$CA")"
    echo "Verify the fingerprint matches the isle you intend to join."
    if [ "${1:-}" != "--yes" ]; then
        read -r -p "Proceed? [y/N] " a; [ "$a" = y ] || [ "$a" = Y ] || { echo "aborted"; exit 1; }
    fi
    do_import; echo; status
}

fetch_root() { # first-join: no existing trust — fingerprint IS the consent
    local url="$TRUST_URL_DEFAULT" expect="" a
    while [ $# -gt 0 ]; do case "$1" in
        --fingerprint) expect="$2"; shift 2 ;;
        http*) url="$1"; shift ;;
        *) shift ;;
    esac; done
    local tmp; tmp=$(mktemp)
    curl -sk --max-time 10 -o "$tmp" "$url" || { fail "download failed: $url"; exit 1; }
    openssl x509 -in "$tmp" -noout 2>/dev/null || { fail "not a certificate: $url"; exit 1; }
    local got; got=$(fp_of "$tmp")
    echo "Fetched root from $url"; echo "  SHA-256: $got"
    if [ -n "$expect" ]; then
        [ "${got//:/}" = "${expect^^//:/}" ] || [ "$got" = "${expect^^}" ] || { fail "fingerprint MISMATCH (expected $expect) — refusing"; exit 1; }
        ok "fingerprint matches the expected value"
    else
        echo "Compare against the operator's value (isle trust cert on the core)."
        read -r -p "Fingerprint verified? [y/N] " a; [ "$a" = y ] || [ "$a" = Y ] || { echo "aborted"; exit 1; }
    fi
    sudo mkdir -p /etc/isle-mesh/ca && sudo cp "$tmp" "$CA" && sudo chmod 644 "$CA"; rm -f "$tmp"
    do_import; echo; status
}

update_trust() { # signed-channel rule: only over TLS the CURRENT root vouches
    [ -f "$CA" ] || { fail "no current root — first join uses: isle trust fetch"; exit 1; }
    local tmp; tmp=$(mktemp)
    if ! curl_trust "$tmp" || ! openssl x509 -in "$tmp" -noout 2>/dev/null; then
        rm -f "$tmp"
        [ "${1:-}" = "--auto" ] && exit 0   # quiet when core unreachable
        fail "could not fetch a root over a channel the current root authenticates"
        echo "If the isle re-keyed, re-consent explicitly: isle trust fetch --fingerprint <new fp>"
        exit 1
    fi
    if cmp -s "$tmp" "$CA"; then
        [ "${1:-}" = "--auto" ] || ok "up to date ($(fp_of "$CA"))"
    else
        warn "root ROTATED (authenticated by the current root) — applying"
        echo "  old: $(fp_of "$CA")"; echo "  new: $(fp_of "$tmp")"
        sudo cp "$tmp" "$CA" && do_import
        [ -d /etc/isle-mesh/trust-page/ca ] && sudo cp "$CA" /etc/isle-mesh/trust-page/ca/isle-root.crt
    fi
    rm -f "$tmp"
}

case "${1:-status}" in
    status) status ;;
    install) install_trust "${2:-}" ;;
    cert) echo "$CA"; echo "SHA-256: $(fp_of "$CA")" ;;
    fetch) shift; fetch_root "$@" ;;
    update) update_trust "${2:-}" ;;
    *) echo "usage: isle trust [status|install [--yes]|cert|fetch [url] [--fingerprint <fp>]|update [--auto]]"; exit 1 ;;
esac
