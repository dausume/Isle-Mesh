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
#                                        apt.isle (timer-driven)
set -u
CA=/etc/isle-mesh/ca/isle-root.crt
# pkexec changes HOME/USER to root; browser trust belongs to its caller.
if [ -n "${PKEXEC_UID:-}" ]; then
    TRUST_USER="$(id -un "$PKEXEC_UID")" || exit 1
else
    TRUST_USER="${SUDO_USER:-$(id -un)}"
fi
TRUST_HOME="$(getent passwd "$TRUST_USER" | cut -d: -f6)"
[ -n "$TRUST_HOME" ] || { echo "Cannot resolve browser trust home" >&2; exit 1; }
NSSDB="sql:$TRUST_HOME/.pki/nssdb"
as_trust_user() {
    if [ "$(id -u)" = "$(id -u "$TRUST_USER")" ]; then
        "$@"
    else
        runuser -u "$TRUST_USER" -- "$@"
    fi
}
NICK="Isle Root CA"
TRUST_URL_DEFAULT="https://apt.isle/isle-root.crt"
G="\033[0;32m"; Y="\033[1;33m"; R="\033[0;31m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }
warn(){ echo -e "${Y}[WARN]${N} $*"; }
fail(){ echo -e "${R}[FAIL]${N} $*"; }
fp_of(){ openssl x509 -in "$1" -noout -fingerprint -sha256 | cut -d= -f2; }

curl_trust(){ # $1 out; authenticated by CURRENT root, core-local fallbacks
    curl --http1.1 --fail -s --max-time 6 --cacert "$CA" -o "$1" "$TRUST_URL_DEFAULT" 2>/dev/null \
    || curl --http1.1 --fail -s --max-time 6 --cacert "$CA" --resolve apt.isle:443:127.0.0.1 -o "$1" "$TRUST_URL_DEFAULT" 2>/dev/null \
    || curl --http1.1 --fail -s --max-time 6 --cacert "$CA" --resolve apt.isle:443:10.10.0.2 -o "$1" "$TRUST_URL_DEFAULT" 2>/dev/null
}

signed_rotation(){
    # The transport is untrusted here; ONLY the previously installed archive
    # key authenticates this public certificate. Never fetch a replacement key.
    local out="$1" sig key=/usr/share/keyrings/isle-archive-keyring.gpg
    [ -s "$key" ] || return 1
    sig=$(mktemp)
    if curl --http1.1 --fail --silent --insecure --max-time 10 -o "$out" "$TRUST_URL_DEFAULT" \
       && curl --http1.1 --fail --silent --insecure --max-time 10 -o "$sig" "$TRUST_URL_DEFAULT.asc" \
       && gpgv --keyring "$key" "$sig" "$out" >/dev/null 2>&1 \
       && openssl x509 -in "$out" -noout -checkend 0 >/dev/null 2>&1 \
       && openssl x509 -in "$out" -noout -text | grep -q 'CA:TRUE'; then
        # Reject replay of an older signed root. Equal validity starts are
        # accepted only for the same certificate; ambiguous rotations fail closed.
        if [ -s "$CA" ] && ! cmp -s "$CA" "$out"; then
            local old_start new_start
            old_start=$(date -d "$(openssl x509 -in "$CA" -noout -startdate | cut -d= -f2-)" +%s) || { rm -f "$sig"; return 1; }
            new_start=$(date -d "$(openssl x509 -in "$out" -noout -startdate | cut -d= -f2-)" +%s) || { rm -f "$sig"; return 1; }
            [ "$new_start" -gt "$old_start" ] || { rm -f "$sig"; return 1; }
        fi
        rm -f "$sig"; return 0
    fi
    rm -f "$sig"; return 1
}

status() {
    [ -f "$CA" ] || { fail "no isle root at $CA"; exit 1; }
    echo "isle root CA: $CA"; echo "SHA-256:      $(fp_of "$CA")"; echo
    if [ -f /usr/local/share/ca-certificates/isle-root.crt ] \
       && cmp -s "$CA" /usr/local/share/ca-certificates/isle-root.crt; then
        ok "system store: trusted (curl/CLI tools)"
    else warn "system store: NOT trusted — isle trust install"; fi
    if command -v certutil >/dev/null 2>&1; then
        local browser_fp
        browser_fp=$(as_trust_user certutil -d "$NSSDB" -L -n "$NICK" -a 2>/dev/null | openssl x509 -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2)
        [ -n "$browser_fp" ] && [ "$browser_fp" = "$(fp_of "$CA")" ] \
            && ok "Chrome/NSS ($TRUST_USER): current isle CA trusted" \
            || warn "Chrome/NSS ($TRUST_USER): current isle CA NOT trusted — isle trust install"
    else warn "certutil missing (apt install libnss3-tools)"; fi
    if [ -f /etc/firefox/policies/isle-root.crt ] && cmp -s "$CA" /etc/firefox/policies/isle-root.crt; then
        ok "Firefox: current public root policy present (restart browser to load it)"
    else warn "Firefox: current root policy missing — isle trust install"; fi
    local probe; probe=$(mktemp)
    if curl_trust "$probe" && [ -s "$probe" ]; then
        ok "live probe: apt.isle answered over a channel this root authenticates"
    else warn "live probe: apt.isle not reachable/authenticated from here"; fi
    rm -f "$probe"
}

valid_root(){
    openssl x509 -in "$1" -noout -checkend 0 >/dev/null 2>&1 \
      && openssl x509 -in "$1" -noout -text | grep -q 'CA:TRUE'
}

do_import() {
    valid_root "$CA" || { fail "Invalid, expired or non-CA trust material; refusing import"; return 1; }
    sudo cp "$CA" /usr/local/share/ca-certificates/isle-root.crt \
        && sudo update-ca-certificates --fresh >/dev/null 2>&1 \
        && ok "system store: imported" || { fail "system store import failed"; return 1; }
    if ! command -v certutil >/dev/null 2>&1; then
        sudo bash "$(dirname "${BASH_SOURCE[0]}")/apt-install.sh" libnss3-tools >/dev/null 2>&1 || true
    fi
    sudo python3 "$(dirname "${BASH_SOURCE[0]}")/firefox_trust.py" policy || return 1
    import_browser
}

import_browser() {
    if command -v certutil >/dev/null 2>&1; then
        as_trust_user mkdir -p "$TRUST_HOME/.pki/nssdb" || return 1
        if [ ! -f "$TRUST_HOME/.pki/nssdb/cert9.db" ]; then
            as_trust_user certutil -d "$NSSDB" -N --empty-password || return 1
        fi
        as_trust_user certutil -d "$NSSDB" -D -n "$NICK" 2>/dev/null || true
        as_trust_user certutil -d "$NSSDB" -A -t "C,," -n "$NICK" -i "$CA" \
            && ok "Chrome/NSS ($TRUST_USER): imported (restart an already-open browser)" \
            || { fail "Chrome/NSS import failed"; return 1; }
        as_trust_user python3 "$(dirname "${BASH_SOURCE[0]}")/firefox_trust.py" profiles || return 1
    else
        fail "Browser trust tools missing; install libnss3-tools, then retry certificate setup"
        return 1
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
    do_import || return 1; echo; status
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
        expect=${expect^^}; expect=${expect//:/}
        [ "${got//:/}" = "$expect" ] || { fail "fingerprint MISMATCH (expected $expect) — refusing"; exit 1; }
        ok "fingerprint matches the expected value"
    else
        echo "Compare against the operator's value (isle trust cert on the core)."
        read -r -p "Fingerprint verified? [y/N] " a; [ "$a" = y ] || [ "$a" = Y ] || { echo "aborted"; exit 1; }
    fi
    valid_root "$tmp" || { rm -f "$tmp"; fail "Not a valid CA root"; return 1; }
    sudo mkdir -p /etc/isle-mesh/ca && sudo install -m 644 "$tmp" "$CA" || { rm -f "$tmp"; return 1; }; rm -f "$tmp"
    do_import || return 1; echo; status
}

update_trust() { # signed-channel rule: only over TLS the CURRENT root vouches
    [ -f "$CA" ] || { fail "no current root — first join uses: isle trust fetch"; exit 1; }
    local tmp; tmp=$(mktemp)
    if ! { curl_trust "$tmp" || signed_rotation "$tmp"; } || ! valid_root "$tmp"; then
        rm -f "$tmp"
        [ "${1:-}" = "--auto" ] && exit 0   # quiet when core unreachable
        fail "could not authenticate a root using current TLS trust or the installed archive signing key"
        echo "If the isle re-keyed, re-consent explicitly: isle trust fetch --fingerprint <new fp>"
        exit 1
    fi
    if cmp -s "$tmp" "$CA"; then
        [ "${1:-}" = "--auto" ] || ok "up to date ($(fp_of "$CA"))"
    else
        warn "root ROTATED (authenticated by existing Isle trust) — applying"
        echo "  old: $(fp_of "$CA")"; echo "  new: $(fp_of "$tmp")"
        sudo install -m 644 "$tmp" "$CA" && do_import || { rm -f "$tmp"; return 1; }
        [ -d /etc/isle-mesh/trust-page/ca ] && sudo cp "$CA" /etc/isle-mesh/trust-page/ca/isle-root.crt
    fi
    rm -f "$tmp"
}

case "${1:-status}" in
    status) status ;;
    browser)
        # No elevation or new trust decision: mirror only the CA already
        # approved in the system store into this user's browser store.
        [ -f "$CA" ] && cmp -s "$CA" /usr/local/share/ca-certificates/isle-root.crt \
            || { fail "Isle CA is not installed in the system trust store"; exit 1; }
        command -v certutil >/dev/null 2>&1 || { fail "certutil unavailable"; exit 1; }
        import_browser ;;
    install) install_trust "${2:-}" ;;
    cert) echo "$CA"; echo "SHA-256: $(fp_of "$CA")" ;;
    fetch) shift; fetch_root "$@" ;;
    update) update_trust "${2:-}" ;;
    *) echo "usage: isle trust [status|browser|install [--yes]|cert|fetch [url] [--fingerprint <fp>]|update [--auto]]"; exit 1 ;;
esac
