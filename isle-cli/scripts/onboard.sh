#!/bin/bash
# onboard.sh — `isle onboard`: make THIS device a mesh member in one
# flow (handoff §33 goal). After onboarding, the device's app store
# "Install on this device" completes for polari apps (native
# launchers) and it reaches every .isle app. Hosting mesh-apps here
# (running containers) is the optional agent tier.
#
#   isle onboard [--host]      --host also brings up an agent so the
#                              device can HOST apps (needs isle join)
#
# Idempotent, consent-first for the CA, narrates each tier.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHELLS_DIR="/usr/share/isle-mesh/shells"
SYS_STAGE="$SHELLS_DIR/debs"
WANT_HOST=0
[ "${1:-}" = "--host" ] && WANT_HOST=1
G="\033[0;32m"; Y="\033[1;33m"; C="\033[0;36m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }
warn(){ echo -e "${Y}[WARN]${N} $*"; }
step(){ echo; echo -e "${C}==> $*${N}"; }

echo "Isle onboarding — making $(hostname) a mesh member"

# ---- 1. trust the isle CA (system + browser NSS) ----
step "1/5 trust the isle CA"
if command -v isle >/dev/null 2>&1; then
    isle trust install --yes 2>&1 | sed 's/^/   /' || warn "trust step had warnings"
else
    warn "isle CLI missing (shouldn't happen — this IS the isle CLI)"
fi

# ---- 2. reach: .isle resolves + the store answers ----
step "2/5 verify mesh reach"
if getent hosts polari.isle >/dev/null 2>&1; then
    ok "polari.isle resolves ($(getent hosts polari.isle | awk '{print $1}'))"
else
    warn "polari.isle does NOT resolve here — isle DNS not reaching this device"
fi
CODE=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 6 https://api.polari.isle/api/islemesh 2>/dev/null) || CODE=000
# a core HOST can't reach its own agent's macvlan IP (host
# isolation) — fall back to the local proxy, like store.sh does
API_RESOLVE=""
if [ "$CODE" != 200 ]; then
    C2=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 6 --resolve api.polari.isle:443:127.0.0.1 https://api.polari.isle/api/islemesh 2>/dev/null) || C2=000
    if [ "$C2" = 200 ]; then
        CODE=200; API_RESOLVE="--resolve api.polari.isle:443:127.0.0.1"
    fi
fi
[ "$CODE" = 200 ] && ok "the isle store API answers (api.polari.isle 200)" \
    || warn "api.polari.isle did not answer ($CODE) — reach not established"

# ---- 3. register this device with polari (topology) ----
step "3/5 register this device"
LINKS=$(ip -br link | awk '$1!~/^(lo|veth|br-|docker|virbr|isle)/{print $1" "$2}')
LINKS="$LINKS" python3 - "$(hostname)" <<'PYEOF' 2>/dev/null | curl -skf --max-time 8 $API_RESOLVE -X POST -H "Content-Type: application/json" --data-binary @- https://api.polari.isle/api/islemesh/ingest/device >/dev/null 2>&1 \
    && ok "registered in the isle topology" || warn "topology registration skipped (API unreachable)"
import json, os, sys
host = sys.argv[1]
uplinks = []
for line in os.environ.get("LINKS", "").splitlines():
    p = line.split()
    if len(p) < 2: continue
    k = "ethernet" if p[0].startswith("e") else ("wifi" if p[0].startswith("w") else "")
    if k: uplinks.append({"interface": p[0], "kind": k, "link_up": p[1] == "UP"})
print(json.dumps({"device": host, "facts": {"machine_name": host,
    "connectivity_mode": "dual-home",
    "notes": "onboarded via isle onboard"}, "uplinks": uplinks}))
PYEOF

# ---- 4. stage the native-app install path ----
step "4/5 native-app install path"
if dpkg -s polari-shell-core >/dev/null 2>&1; then
    ok "polari-shell-core runtime installed"
else
    warn "polari-shell-core NOT installed — native launchers need it"
    echo "     install: sudo apt install <polari-shell-core deb>"
fi
# stage the shared-runtime deb SYSTEM-WIDE: the store UI's install
# bridge runs via pkexec AS ROOT, whose $HOME (/root) has no staged
# debs — shell.sh's root default is $SYS_STAGE, so fill it here.
USER_HOME=$(getent passwd "${SUDO_USER:-$USER}" | cut -d: -f6)
CORE_DEB=$(ls "$USER_HOME"/polari-shells/polari-shell-core_*_amd64.deb 2>/dev/null | sort -V | tail -1)
if [ -n "$CORE_DEB" ]; then
    if sudo mkdir -p "$SYS_STAGE" && sudo cp -u "$CORE_DEB" "$SYS_STAGE"/; then
        ok "staged $(basename "$CORE_DEB") → $SYS_STAGE (pkexec/root installs find it)"
    else
        warn "could not stage the runtime deb into $SYS_STAGE"
    fi
elif [ -z "$(ls "$SYS_STAGE"/polari-shell-core_*_amd64.deb 2>/dev/null)" ] \
     && ! dpkg -s polari-shell-core >/dev/null 2>&1; then
    warn "no polari-shell-core deb found to stage (looked in $USER_HOME/polari-shells)"
fi
if [ -f "$SHELLS_DIR/build-launcher-deb.sh" ]; then
    ok "shell launcher tools present ($SHELLS_DIR)"
else
    warn "launcher build tools missing at $SHELLS_DIR"
fi
if [ -f "$SHELLS_DIR/icons/polari-mark.png" ]; then
    ok "app icons present (branded launchers)"
else
    warn "icons missing at $SHELLS_DIR/icons — launchers fall back to a generic icon"
fi
echo "   → 'isle store install <polari-app>' now builds a native launcher here"

# ---- 5. host tier (optional) ----
step "5/5 host tier"
if [ "$WANT_HOST" = 1 ]; then
    warn "host tier brings up an isle agent — its installer can MODIFY"
    warn "host network config (wpa_supplicant/networkd handoff, mDNS)."
    warn "Do this at the machine, not over the connection it may reset."
    if isle agent ensure 2>/dev/null; then
        ok "agent up — this device can HOST mesh-apps"
    else
        warn "could not bring up an agent — hosting needs the isle join/router setup"
        echo "     (reach + native-app install still work; run 'isle create'/'isle join' to host)"
    fi
else
    echo "   skipped — but NOTE: installing apps from the store on this"
    echo "   device REQUIRES it (membership = a running agent)."
    echo "   Join when ready:  sudo isle onboard --host"
fi

echo
ok "onboarding done — open 'Isle App Store' and install apps on this device"
