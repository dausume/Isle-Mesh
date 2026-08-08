#!/bin/bash
# trust.sh — CA trust as an INSTALL-STEP capability (isle trust).
#
# The isle serves .isle apps with certs chained to the isle root CA
# (/etc/isle-mesh/ca/isle-root.crt). Browsers/tools trust it only
# after a ONE-TIME import — this verb DETECTS where trust is
# missing and performs/instructs the import. Consent-first: the
# fingerprint is always shown; nothing imports without an explicit
# yes (or --yes). Designed to be called by the .deb postinst
# (debconf-consented) and re-runnable forever after.
#
#   isle trust status     where is the root trusted? (system, NSS/
#                         Chrome, live probe)
#   isle trust install    import into system store + user NSS db
#   isle trust cert       print cert path + fingerprint (manual use)
set -u
CA=/etc/isle-mesh/ca/isle-root.crt
NSSDB="sql:$HOME/.pki/nssdb"
NICK="Isle Root CA"
G="\033[0;32m"; Y="\033[1;33m"; R="\033[0;31m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }
warn(){ echo -e "${Y}[WARN]${N} $*"; }
fail(){ echo -e "${R}[FAIL]${N} $*"; }

[ -f "$CA" ] || { fail "no isle root at $CA — is the isle installed on this device?"; exit 1; }
FP=$(openssl x509 -in "$CA" -noout -fingerprint -sha256 | cut -d= -f2)

status() {
    echo "isle root CA: $CA"
    echo "SHA-256:      $FP"
    echo
    if [ -f /usr/local/share/ca-certificates/isle-root.crt ] \
       && cmp -s "$CA" /usr/local/share/ca-certificates/isle-root.crt; then
        ok "system store: trusted (curl/CLI tools)"
    else
        warn "system store: NOT trusted — isle trust install"
    fi
    if command -v certutil >/dev/null 2>&1; then
        if certutil -d "$NSSDB" -L 2>/dev/null | grep -q "$NICK"; then
            ok "Chrome/NSS ($USER): trusted"
        else
            warn "Chrome/NSS ($USER): NOT trusted — isle trust install"
        fi
    else
        warn "certutil missing (apt install libnss3-tools) — cannot check/import Chrome trust"
    fi
    ls "$HOME"/.mozilla/firefox/*/cert9.db >/dev/null 2>&1 \
        && warn "Firefox present: separate store — import via Settings > Certificates (or enterprise policies.json)"
    if curl -s --max-time 4 --cacert "$CA" https://polari.isle/ -o /dev/null 2>/dev/null \
       || curl -s --max-time 4 --cacert "$CA" --resolve polari.isle:443:127.0.0.1 https://polari.isle/ -o /dev/null 2>/dev/null; then
        ok "live probe: an .isle app answered with a cert this root vouches for"
    else
        warn "live probe: no .isle app reachable from here (fine if none is served yet)"
    fi
}

install_trust() {
    local yes="${1:-}"
    echo "About to trust this CA for THIS machine/user:"
    echo "  $CA"
    echo "  SHA-256: $FP"
    echo "A trusted CA can vouch for the sites it is allowed to name — verify the"
    echo "fingerprint matches the isle you intend to join."
    if [ "$yes" != "--yes" ]; then
        read -r -p "Proceed? [y/N] " a; [ "$a" = y ] || [ "$a" = Y ] || { echo "aborted"; exit 1; }
    fi
    if sudo cp "$CA" /usr/local/share/ca-certificates/isle-root.crt \
       && sudo update-ca-certificates >/dev/null 2>&1; then
        ok "system store: imported"
    else
        fail "system store import failed (sudo needed)"
    fi
    if ! command -v certutil >/dev/null 2>&1; then
        warn "installing libnss3-tools for Chrome trust..."
        sudo apt-get install -y libnss3-tools >/dev/null 2>&1 || warn "could not install libnss3-tools — Chrome import skipped"
    fi
    if command -v certutil >/dev/null 2>&1; then
        mkdir -p "$HOME/.pki/nssdb"; [ -f "$HOME/.pki/nssdb/cert9.db" ] || certutil -d "$NSSDB" -N --empty-password 2>/dev/null
        certutil -d "$NSSDB" -D -n "$NICK" 2>/dev/null || true
        certutil -d "$NSSDB" -A -t "C,," -n "$NICK" -i "$CA" \
            && ok "Chrome/NSS: imported (restart Chrome to take effect)" \
            || fail "Chrome/NSS import failed"
    fi
    echo; status
}

case "${1:-status}" in
    status) status ;;
    install) install_trust "${2:-}" ;;
    cert) echo "$CA"; echo "SHA-256: $FP" ;;
    *) echo "usage: isle trust [status|install [--yes]|cert]"; exit 1 ;;
esac
