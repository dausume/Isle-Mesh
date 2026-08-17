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
#   isle security setup     the interactive walkthrough: the GENERIC
#                           self-hosting preparation first (credentials,
#                           doors, certs — provider-agnostic), then a
#                           POST STEP asks whether a particular hosting
#                           provider is in use and runs that provider's
#                           additional steps (DigitalOcean today)
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

# ---- provider post-steps ---------------------------------------------------
# The walkthrough is GENERIC self-hosting first; providers are a POST step.
# Each provider function states what is BUILT vs what is guidance — honest,
# never pretending automation exists where it doesn't.

provider_digitalocean() {
    echo "DigitalOcean — additional steps (real machinery exists for these):"
    echo "  1) DNS: host your domain's DNS at DO (or delegate it there) —"
    echo "     the built cert path uses certbot's DO DNS-01 plugin."
    echo "  2) Mint a DO API token (read+write) and have it ready as"
    echo "     DO_API_TOKEN — it is asked for AT CERT TIME, never stored"
    echo "     in a repo."
    echo "  3) On the polari node, run the cert walkthrough:"
    echo "       pol cert prod letsencrypt    (LE_DOMAIN, LE_EMAIL,"
    echo "       DO_API_TOKEN; DNS-01 grants a WILDCARD — one cert covers"
    echo "       every subdomain)"
    echo "     then:  pol cert auto-renew install   (weekly cron)"
    echo "  4) Droplet sizing note: a plain droplet stops billing when"
    echo "     destroyed, not when powered off — destroy/renew deliberately."
}

provider_generic_vps() {
    echo "Generic VPS / other provider — guidance (no provider automation built):"
    echo "  - Point your domain's A/AAAA records at this host at your DNS host."
    echo "  - Browser-trusted certs: certbot works everywhere — HTTP-01 needs"
    echo "    port 80 reachable; DNS-01 needs your DNS host's certbot plugin"
    echo "    (only the DigitalOcean plugin is wired into pol cert today)."
    echo "  - Self-signed/internal CA stays fully supported: pol cert prod"
    echo "    self-signed (clients import the root)."
}

provider_home() {
    echo "Home / own hardware — guidance:"
    echo "  - Your router must forward the door ports (or 80/443) to this"
    echo "    device; your ISP may block inbound 80/443 or rotate your IP"
    echo "    (dynamic DNS helps)."
    echo "  - The isle's containment posture still applies: only designated"
    echo "    entrypoints open doors (isle url entrypoint / isle url expose)."
    echo "  - isle security harden covers the ISP-visibility side."
}

provider_post_step() {
    echo "The general setup above is provider-agnostic. Particular providers"
    echo "have extra steps (DNS control, API tokens, cert automation)."
    read -p "Are you hosting with a particular provider? [do/vps/home/none] (none): " P
    case "${P,,}" in
        do|digitalocean|digital-ocean) provider_digitalocean ;;
        vps|other)                     provider_generic_vps ;;
        home|own|self)                 provider_home ;;
        *) echo "  none selected — the generic preparation is complete." ;;
    esac
}

setup() {
    echo -e "${B}Self-hosting security walkthrough — $(hostname)${N}"
    echo "Security material is put in AT DEPLOY TIME: this walkthrough asks"
    echo "for what a production deployment needs and never ships defaults."
    echo "It prepares the GENERAL self-hosting case first; provider-specific"
    echo "steps (DigitalOcean etc.) come as a post step at the end."

    step "1/5 current state"
    creds || true

    step "2/5 credentials (deploy-time input)"
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

    step "3/5 outside doors + certificates (the .isle -> web upgrade)"
    local dir port any=0 web=0
    for dir in "$EXPDIR"/*/; do
        [ -f "$dir/htpasswd" ] || continue
        any=1; web=1; port=$(basename "$dir")
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
        [[ "$A" =~ ^[Yy] ]] && web=1
    fi
    if [ "$web" = 1 ]; then
        if [ -f /etc/isle-mesh/entrypoint.enabled ]; then
            ok "this device is a designated entrypoint"
        else
            echo "  1) designate it:        isle url entrypoint enable"
        fi
        echo "  2) the gate must be clean first (isle security gate) —"
        echo "     doors refuse to open over placeholder credentials"
        echo "  3) open a door:          isle url expose <name.isle> --port <p> --user <who>"
        echo "  4) certificates, generically: internal/self-signed CA works"
        echo "     everywhere (clients import the root); a browser-trusted"
        echo "     cert needs a REAL domain you control — how, depends on"
        echo "     your provider (the post step below)."
    fi

    step "4/5 hosting provider (post step)"
    provider_post_step

    step "5/5 verdict"
    gate || true
}

case "${1:-creds}" in
    creds)  creds ;;
    gate)   gate ;;
    setup)  setup ;;
    *) echo "usage: isle security creds|gate|setup (credential half) — see also: isle security [status|harden]" ;;
esac
