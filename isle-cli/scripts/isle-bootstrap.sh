#!/bin/bash
# isle-bootstrap.sh — the REMOTE INSTALLER (handoff ISLE_ONBOARDING
# §5b — Dustin's §33 goal): ONE flow on a fresh device → a mesh
# member with the Isle App Store, from which the user installs
# whatever else the device needs.
#
# STANDALONE — runs before the isle CLI exists here. Served at
# https://apt.isle/isle-bootstrap.sh; `isle core-install` prints its
# SHA-256 so a remote can verify what it fetched.
#
#   sudo bash isle-bootstrap.sh --fingerprint <CA sha256>
#        [--core <ip>] [--host]
#
# Flow:
#   1. fetch the isle root CA and VERIFY THE FINGERPRINT (the trust
#      decision — abort on mismatch); trust it system-wide
#   2. enable apt-on-mesh (archive key over the now-CA-authenticated
#      channel, signed-by source)
#   3. apt install isle-mesh-cli + isle-app-store (pulls the
#      polari-shell-core runtime via Depends)
#   4. isle onboard [--host]  (trust/reach/register/stage tiers)
set -u
G="\033[0;32m"; Y="\033[1;33m"; R="\033[0;31m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }
warn(){ echo -e "${Y}[WARN]${N} $*"; }
die(){ echo -e "${R}[FAIL]${N} $*"; exit 1; }

FP=""; CORE=""; WANT_HOST=0; INSTALL_MODE=$(cat /etc/polari/install-mode 2>/dev/null || echo online)
while [ $# -gt 0 ]; do case "$1" in
    --fingerprint) FP="$2"; shift 2 ;;
    --core) CORE="$2"; shift 2 ;;
    --host) WANT_HOST=1; shift ;;
    --offline) INSTALL_MODE="offline"; shift ;;
    --online) INSTALL_MODE="online"; shift ;;
    *) shift ;;
esac; done
[ -n "$FP" ] || die "usage: sudo bash isle-bootstrap.sh --fingerprint <CA sha256> [--core <ip>] [--host]
(the fingerprint comes from the core's 'isle core-install' output — verify it out-of-band)"
[ "$(id -u)" = 0 ] || die "run with sudo (installs packages + trust)"
command -v openssl >/dev/null || die "openssl required"
command -v curl >/dev/null || command -v wget >/dev/null \
    || die "curl or wget required"

norm(){ echo "$1" | tr -d ': ' | tr '[:lower:]' '[:upper:]'; }

# fetch INSECURELY (pre-trust — the CA we fetch is what gets
# fingerprint-verified); curl preferred, wget on fresh boxes
fetch_k(){ # $1 url, $2 out
    if command -v curl >/dev/null; then
        curl -sk --max-time 8 -o "$2" "$1"
    else
        wget -q --timeout=8 --no-check-certificate -O "$2" "$1"
    fi
}
# fetch AUTHENTICATED by the isle root CA
fetch_ca(){ # $1 url, $2 out
    if command -v curl >/dev/null; then
        curl -sf --max-time 8 --cacert /etc/isle-mesh/ca/isle-root.crt -o "$2" "$1"
    else
        wget -q --timeout=8 --ca-certificate=/etc/isle-mesh/ca/isle-root.crt -O "$2" "$1"
    fi
}

echo "Isle bootstrap — making $(hostname) a mesh member (one flow)"

# no .isle DNS yet (not attached to the isle's network path)? pin
# the service names to --core so every later step (incl. apt, which
# cannot --resolve) works; isle DNS supersedes this once joined
for d in trust.isle apt.isle; do
    if ! getent hosts "$d" >/dev/null 2>&1; then
        [ -n "$CORE" ] || die ".isle DNS does not resolve here and no --core given"
        grep -q " $d\$" /etc/hosts || echo "$CORE $d" >> /etc/hosts
        warn ".isle DNS absent — pinned $d → $CORE in /etc/hosts"
    fi
done

# ---- 1. the isle root CA, fingerprint-verified ----
echo
echo "==> 1/4 isle CA (fingerprint-verified)"
TMP=$(mktemp)
fetch_k https://trust.isle/ca/isle-root.crt "$TMP" \
    || fetch_k https://apt.isle/isle-root.crt "$TMP" \
    || die "cannot fetch the isle CA (is this device on the isle's network? try --core <ip>)"
GOT=$(openssl x509 -in "$TMP" -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2)
[ -n "$GOT" ] || die "fetched file is not a certificate"
if [ "$(norm "$GOT")" != "$(norm "$FP")" ]; then
    echo "   expected: $FP"
    echo "   got:      $GOT"
    die "CA FINGERPRINT MISMATCH — refusing to trust. Verify the fingerprint with the isle's operator."
fi
ok "fingerprint matches — trusting this isle"
mkdir -p /etc/isle-mesh/ca /usr/local/share/ca-certificates
# install -m: a plain cp of the mktemp file keeps mode 600 —
# root-only, which broke every later unprivileged CA read (econ-core)
install -m 644 "$TMP" /etc/isle-mesh/ca/isle-root.crt
install -m 644 "$TMP" /usr/local/share/ca-certificates/isle-root.crt
update-ca-certificates >/dev/null 2>&1 && ok "system store: trusted" || warn "update-ca-certificates failed"
rm -f "$TMP"

# ---- 2. apt-on-mesh ----
echo
echo "==> 2/4 apt-on-mesh"
KEYRING=/usr/share/keyrings/isle-archive-keyring.gpg
fetch_ca https://apt.isle/isle-archive-keyring.gpg "$KEYRING" \
    || die "cannot fetch the archive key from https://apt.isle (core published? isle apt-repo publish)"
ok "archive key trusted over the authenticated Isle channel"

# ---- 3. the CLI + the store (runtime rides Depends) ----
echo
echo "==> 3/4 install isle-mesh-cli + Isle App Store"
# Fetch the common transaction helper through the already authenticated Isle
# CA channel. Its package indexes are additionally checked by apt signatures.
APT_INSTALLER=$(mktemp)
trap 'rm -f "$APT_INSTALLER"' EXIT
fetch_ca https://apt.isle/apt-install.sh "$APT_INSTALLER" \
    || die "core has no dependency installer; update/re-publish its local repository"
mkdir -p /etc/polari
printf '%s\n' "$INSTALL_MODE" > /etc/polari/install-mode
bash "$APT_INSTALLER" isle-mesh-cli isle-app-store polari-shell-core \
    || die "install failed from configured local-first package supply"
rm -f "$APT_INSTALLER"
trap - EXIT
ok "CLI + store + shared runtime installed"
isle apt-repo enable || die "could not enable this Isle's signed local source"

# ---- 4. onboard (trust/reach/register/stage) ----
echo
echo "==> 4/4 isle onboard"
if [ "$WANT_HOST" = 1 ]; then
    isle onboard --host
else
    isle onboard
    echo
    echo "   to HOST mesh-apps here later (containers on this device):"
    echo "     sudo isle onboard --host    # agent tier; real join = 'isle join'"
fi

echo
ok "bootstrap done — open 'Isle App Store' from the applications menu"
