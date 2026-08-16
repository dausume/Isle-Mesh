#!/bin/bash
# secure-creds.sh — the CREDENTIAL half of `isle security` (the network-
# hardening half lives in security.sh; both surface under one verb).
#
# All security material is put in AT DEPLOY TIME. This walkthrough runs in
# the isle SETUP phase (core-install's final step, or any time as
# `isle security setup`) and asks for the different passwords/processes as
# needed instead of ever shipping defaults:
#
#   isle security creds     inventory: what exists, placeholder values,
#                           last-updated ages, STALE flags (>SEC_STALE_DAYS)
#   isle security gate      non-interactive deploy check — exit 1 on
#                           missing/placeholder material (url.sh refuses
#                           to open outside doors while this fails)
#   isle security setup     the interactive walkthrough: runs the polari
#                           production setup shells where a polari node
#                           lives on this device, rotates stale door
#                           credentials, reports CA expiry, and walks the
#                           .isle -> web-site upgrade questions
#
# Privilege: runs unprivileged; root-needed writes go through polkit
# (pkexec) on a desktop, sudo otherwise (lib/security-ledger.sh sec_esc).
# Suggestions only — nothing rotates without a yes.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/security-ledger.sh"
G="\033[0;32m"; Y="\033[1;33m"; R="\033[0;31m"; C="\033[0;36m"; B="\033[1m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }
warn(){ echo -e "${Y}[WARN]${N} $*"; }
bad(){ echo -e "${R}[!!]${N} $*"; }
step(){ echo; echo -e "${C}==> $*${N}"; }

CA=/etc/isle-mesh/ca/isle-root.crt
EXPDIR=/etc/isle-mesh/exposures
LEDGER_JSON=/etc/isle-mesh/exposures.json

# ---- discovery -------------------------------------------------------------

# polari-rf-node checkouts on this device (the merged polari+isle box —
# e.g. the public droplet): where the production setup shells live.
polari_node_dirs() {
    local d
    for d in "${POLARI_NODE_DIR:-}" \
             "$HOME/Desktop/polari-suite/polari-rf-node" \
             "$HOME/polari-suite/polari-rf-node" \
             "$HOME/polari-rf-node" \
             /opt/polari-suite/polari-rf-node; do
        [ -n "$d" ] && [ -f "$d/prod-setup.sh" ] && echo "$d"
    done | sort -u
}

# every secret-bearing env file this device carries, as "name|path" rows
env_targets() {
    local d f
    for d in $(polari_node_dirs); do
        echo "kc-admin-env|$d/prf-keycloak/prf-keycloak-admin.env"
        echo "mariadb-env|$d/prf-mariadb/mariadb.env"
    done
    for f in "$HOME"/polari-isle/*.env /etc/isle-mesh/polari/*/*.env; do
        [ -f "$f" ] || continue
        grep -qE '^[A-Za-z_]*(PASSWORD|SECRET|PASS)[A-Za-z_]*=' "$f" 2>/dev/null || continue
        echo "isle-$(basename "$(dirname "$f")")-$(basename "$f")|$f"
    done
}

# ---- creds (inventory) -----------------------------------------------------

creds() {
    echo -e "${B}Deployment credential status on $(hostname) (stale > ${SEC_STALE_DAYS}d)${N}"
    local bad=0 found=0 entry name path ph age flag
    for entry in $(env_targets); do
        name="${entry%%|*}"; path="${entry#*|}"; found=1
        if [ ! -f "$path" ]; then
            warn "$path MISSING — generated at deploy time (isle security setup)"
            bad=1; continue
        fi
        ph=$(sec_placeholders "$path")
        age=$(ledger_age_days "$name" "$path")
        flag=""; sec_is_stale "$name" "$path" && flag=" ${Y}STALE${N}"
        if [ "${ph:-0}" -gt 0 ]; then
            bad "$path — $ph placeholder/default secret value(s), last updated ${age:-?}d ago (NOT deployable)"
            bad=1
        else
            echo -e "  ${G}ok${N} $path — last updated ${age:-?}d ago$flag"
        fi
    done
    [ "$found" = 0 ] && echo "  (no polari credential files on this device)"

    # outside doors: each htpasswd is a live credential with an age
    local dir port age2
    for dir in "$EXPDIR"/*/; do
        [ -f "$dir/htpasswd" ] || continue
        port=$(basename "$dir")
        age2=$(ledger_age_days "door-$port" "$dir/htpasswd")
        if sec_is_stale "door-$port" "$dir/htpasswd"; then
            warn "door :$port credential is ${age2}d old — rotate via isle security setup (or re-expose)"
        else
            echo -e "  ${G}ok${N} door :$port credential — ${age2:-?}d old"
        fi
    done

    # the isle CA itself
    if [ -f "$CA" ]; then
        local end days
        end=$(openssl x509 -in "$CA" -noout -enddate 2>/dev/null | cut -d= -f2)
        days=$(( ( $(date -d "$end" +%s) - $(date +%s) ) / 86400 ))
        if [ "$days" -lt 0 ]; then bad "isle CA EXPIRED ${days#-}d ago"; bad=1
        elif [ "$days" -lt 90 ]; then warn "isle CA expires in ${days}d"
        else echo -e "  ${G}ok${N} isle CA — ${days}d to expiry"; fi
    fi
    return $bad
}

gate() {
    if creds >/dev/null 2>&1; then
        ok "security gate clean — deployable material only"
    else
        creds || true
        bad "security gate FAILED — placeholder or missing credentials (fix: isle security setup)"
        return 1
    fi
}

# ---- setup (the walkthrough) ----------------------------------------------

rotate_door() {  # PORT — new one-person credential for an outside door
    local port=$1 dir="$EXPDIR/$port" user pass hash
    user=$(cut -d: -f1 "$dir/htpasswd" 2>/dev/null | head -1)
    [ -n "$user" ] || { warn "door :$port has no readable htpasswd"; return 1; }
    pass=$(openssl rand -base64 12 | tr -d "=+/" | cut -c1-16)
    hash=$(openssl passwd -apr1 "$pass")
    printf '%s:%s\n' "$user" "$hash" | sec_esc tee "$dir/htpasswd" >/dev/null
    ledger_stamp "door-$port" "$dir/htpasswd" "isle security setup"
    ok "door :$port credential rotated for '$user'"
    echo "   new password (shown ONCE — hand it to that one person): $pass"
}

setup() {
    echo -e "${B}Isle production-security walkthrough — $(hostname)${N}"
    echo "Security material is put in AT DEPLOY TIME: this walkthrough asks"
    echo "for what a production deployment needs and never ships defaults."

    step "1/4 current state"
    creds || true

    step "2/4 polari production credentials"
    local d ran=0
    for d in $(polari_node_dirs); do
        ran=1
        echo "polari node found: $d"
        echo "Its production setup prompts for the DOMAIN and each PASSWORD"
        echo "(Enter = auto-generate), keeps fresh real credentials, and"
        echo "refuses to keep placeholders (fail closed)."
        read -p "Run it now? (Y/n): " A
        if [[ ! "$A" =~ ^[Nn] ]]; then
            bash "$d/prod-setup.sh" || warn "prod-setup did not complete"
            echo "If this polari is RUNNING, roll the new credentials out in"
            echo "order (Keycloak first, then backend):  pol security rotate prod"
        fi
    done
    [ "$ran" = 0 ] && echo "  (no polari-rf-node checkout here — nothing to run; the lean"  \
        && echo "   isle polari carries no local KC/DB credentials)"

    step "3/4 outside doors (the .isle -> web upgrade)"
    local dir port any=0
    for dir in "$EXPDIR"/*/; do
        [ -f "$dir/htpasswd" ] || continue
        any=1; port=$(basename "$dir")
        local age; age=$(ledger_age_days "door-$port" "$dir/htpasswd")
        echo "door :$port — credential ${age:-?}d old"
        if sec_is_stale "door-$port" "$dir/htpasswd"; then
            read -p "  Rotate this door's credential now? (Y/n): " A
            [[ "$A" =~ ^[Nn] ]] || rotate_door "$port"
        fi
    done
    if [ "$any" = 0 ]; then
        echo "  no outside doors open — the isle is fully contained."
        read -p "  Will this isle be exposed as a WEB SITE from this device? (y/N): " A
        if [[ "$A" =~ ^[Yy] ]]; then
            if [ -f /etc/isle-mesh/entrypoint.enabled ]; then
                ok "this device is already a designated entrypoint"
            else
                echo "  1) designate it:        isle url entrypoint enable"
            fi
            echo "  2) the gate must be clean first (isle security gate) —"
            echo "     doors refuse to open over placeholder credentials"
            echo "  3) open a door:          isle url expose <name.isle> --port <p> --user <who>"
            echo "  4) a REAL domain + browser-trusted cert (Let's Encrypt via"
            echo "     DNS-01) is the polari node's cert walkthrough:"
            echo "       pol cert prod letsencrypt      (needs LE_DOMAIN, LE_EMAIL,"
            echo "       DO_API_TOKEN; wildcard covers the subdomains)"
        fi
    fi

    step "4/4 verdict"
    gate || true
}

case "${1:-creds}" in
    creds)  creds ;;
    gate)   gate ;;
    setup)  setup ;;
    *) echo "usage: isle security creds|gate|setup (credential half) — see also: isle security [status|harden]" ;;
esac
