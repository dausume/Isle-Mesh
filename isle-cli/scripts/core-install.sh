#!/bin/bash
# core-install.sh — `isle core-install`: ONE smooth flow from a
# fresh core box to a working isle core (handoff ISLE_ONBOARDING
# §5a — Dustin's §33 goal). Everything it runs already exists as a
# verb; this SEQUENCES them idempotently and ends with the JOIN
# INFO a remote needs.
#
#   isle core-install [--modules <csv>]
#
# Steps (each skipped when already done):
#   1. isle create        router + agent + bridges
#   2. isle CA present + trusted locally (isle trust install)
#   3. prf-isle lean      isle-polari-deploy (topology + store pages)
#   4. store shell        isle-app-store deb installed locally +
#                         runtime deb staged system-wide
#   5. apt-on-mesh        isle apt-repo publish (+ the remote
#                         bootstrap script served at apt.isle)
#   6. verify + JOIN INFO (CA fingerprint, bootstrap one-liner)
#   7. production security (deploy-time credentials): status always;
#      offers the interactive walkthrough (isle security setup) when
#      gaps exist — passwords/domain/certs are put in AT DEPLOY TIME,
#      never shipped (--skip-security to defer)
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
G="\033[0;32m"; Y="\033[1;33m"; C="\033[0;36m"; R="\033[0;31m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }
warn(){ echo -e "${Y}[WARN]${N} $*"; }
step(){ echo; echo -e "${C}==> $*${N}"; }
die(){ echo -e "${R}[FAIL]${N} $*"; exit 1; }

CA=/etc/isle-mesh/ca/isle-root.crt
MODULES="islemesh"
SKIP_SECURITY=0
while [ $# -gt 0 ]; do case "$1" in
    --modules) MODULES="$2"; shift 2 ;;
    --skip-security) SKIP_SECURITY=1; shift ;;
    *) shift ;;
esac; done

echo "Isle core install — one flow to a working isle core on $(hostname)"

# ---- 1. the isle itself (router + agent) ----
step "1/7 isle networking (router + agent)"
if docker ps --format '{{.Names}}' | grep -q '^isle-vlan-agent$'; then
    ok "isle agent already running"
else
    warn "no agent — running isle create (router VM + agent + bridges)"
    isle create || die "isle create failed"
fi

# ---- 2. the isle CA, trusted locally ----
step "2/7 isle CA"
[ -f "$CA" ] || die "no isle root CA at $CA — the CA is issued during isle setup (see security/); core-install expects it"
isle trust install --yes >/dev/null 2>&1 && ok "CA trusted locally ($(openssl x509 -in "$CA" -noout -fingerprint -sha256 | cut -d= -f2 | cut -c1-23)…)" \
    || warn "local trust import had warnings (isle trust status)"

# ---- 3. prf-isle: the lean polari (topology + store pages) ----
step "3/7 prf-isle (lean polari: $MODULES)"
if docker ps --format '{{.Names}}' | grep -q '^prf-isle-backend$'; then
    ok "prf-isle already up"
else
    [ -d "$HOME/polari-isle" ] || die "no ~/polari-isle — first-time compose comes from the §17 setup (isle-polari-deploy needs it)"
    isle-polari-deploy --modules "$MODULES" || die "prf-isle deploy failed"
fi

# ---- 4. the store shell on this box + staged runtime ----
step "4/7 store shell (native window + system stage)"
if dpkg -s isle-app-store >/dev/null 2>&1; then
    ok "isle-app-store installed"
else
    DEB=$(ls "$HOME"/polari-shells/isle-app-store_*_all.deb 2>/dev/null | sort -V | tail -1)
    [ -n "$DEB" ] && sudo apt-get install -y "$DEB" >/dev/null 2>&1 && ok "isle-app-store installed" \
        || warn "no isle-app-store deb staged (~/polari-shells) — build it from polari-app-shell/shells"
fi
CORE_DEB=$(ls "$HOME"/polari-shells/polari-shell-core_*_amd64.deb 2>/dev/null | sort -V | tail -1)
if [ -n "$CORE_DEB" ]; then
    sudo mkdir -p /usr/share/isle-mesh/shells/debs \
        && sudo cp -u "$CORE_DEB" /usr/share/isle-mesh/shells/debs/ \
        && ok "runtime deb staged system-wide"
fi

# ---- 5. apt-on-mesh (deb supply for remotes) ----
step "5/7 apt-on-mesh"
isle apt-repo publish | tail -2

# ---- 6. verify + JOIN INFO ----
step "6/7 verify"
CACURL="curl -skf --max-time 8"
HUB=$($CACURL -o /dev/null -w "%{http_code}" https://polari.isle/isle 2>/dev/null) || HUB=000
[ "$HUB" = 200 ] || HUB=$($CACURL --resolve polari.isle:443:127.0.0.1 -o /dev/null -w "%{http_code}" https://polari.isle/isle 2>/dev/null) || HUB=000
[ "$HUB" = 200 ] && ok "https://polari.isle/isle answers (the tabbed hub)" || warn "hub did not answer ($HUB)"
STORE_N=$(isle store list 2>/dev/null | head -1) && ok "store: ${STORE_N:-?}"

FP=$(openssl x509 -in "$CA" -noout -fingerprint -sha256 | cut -d= -f2)
BOOT=/usr/share/isle-mesh/isle-cli/scripts/isle-bootstrap.sh
[ -f "$BOOT" ] || BOOT="$SCRIPT_DIR/isle-bootstrap.sh"
BOOT_SHA=$([ -f "$BOOT" ] && sha256sum "$BOOT" | cut -d" " -f1 || echo "?")
CORE_IP=$(hostname -I | awk '{print $1}')

# core hairpin pins: every core-served .isle domain must pin to
# loopback on THIS host (macvlan host isolation) — reconciled
# here once and by the self-feed timer forever after
"$SCRIPT_DIR/hosts-reconcile.sh" || true

# ---- 7. production security (deploy-time credentials) ----
step "7/7 production security"
if [ "$SKIP_SECURITY" = 1 ]; then
    warn "security walkthrough skipped (--skip-security) — run it before"
    warn "any web exposure: isle security setup"
elif bash "$SCRIPT_DIR/secure-creds.sh" creds; then
    ok "deployment credentials look real (no placeholders)"
else
    warn "credential gaps found (above)"
    if [ -t 0 ]; then
        read -p "Run the production-security walkthrough now? (Y/n): " SEC_A
        [[ "$SEC_A" =~ ^[Nn] ]] || bash "$SCRIPT_DIR/secure-creds.sh" setup
    else
        warn "non-interactive run — walk through later: isle security setup"
    fi
fi

echo
echo -e "${G}════════ ISLE CORE READY — JOIN INFO for remotes ════════${N}"
echo "CA fingerprint (verify on every joining device):"
echo "  $FP"
echo
echo "On a fresh remote (connected to this isle's network):"
echo "  curl -ko isle-bootstrap.sh https://apt.isle/isle-bootstrap.sh \\"
echo "      || curl -ko isle-bootstrap.sh --resolve apt.isle:443:$CORE_IP https://apt.isle/isle-bootstrap.sh"
echo "  sha256sum isle-bootstrap.sh   # expect $BOOT_SHA"
echo "  sudo bash isle-bootstrap.sh --fingerprint '$FP' [--core $CORE_IP] [--host]"
echo
echo "That one flow: CA (fingerprint-verified) → apt-on-mesh →"
echo "isle CLI + store shell installed → isle onboard (member)."
