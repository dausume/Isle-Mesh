#!/bin/bash
# certs.sh — per-domain leaf issuance from the isle CA (isle certs).
#
# EXPLICIT SANs ONLY (Dustin 2026-08-08): no wildcards — OpenSSL
# rejects single-label wildcards (*.isle matches nothing), and
# explicit is preferable anyway. Every registered app domain gets
# its OWN leaf, signed by the isle INTERMEDIATE (the root key never
# leaves the suite CA), fullchain installed into the agent slots.
# Registration triggers issuance (agent-manager register hook);
# `sync` reconciles everything in registry.json (idempotent, boot-
# safe). Devices trust it all via ONE root import (isle trust).
#
#   isle certs status           every registered domain: cert state
#   isle certs sync             issue any missing/stale/self-signed
#   isle certs issue <domain>   (re)issue one leaf now
#   isle certs init-ca          mint a SELF-CONTAINED isle CA (root +
#                               encrypted intermediate) when none
#                               exists — the fresh-core path (finding
#                               #4, 2026-08-19): trust material is
#                               DEPLOY-TIME input like every other
#                               credential, created on the device at
#                               install, never shipped. A suite-managed
#                               CA (polari-rf-node/ca) may replace it.
set -u
SIGN=/etc/isle-mesh/ca/signing
CRTD=/etc/isle-mesh/agent/ssl/certs
KEYD=/etc/isle-mesh/agent/ssl/keys
REG=/etc/isle-mesh/agent/registry.json
DAYS=365
RENEW_UNDER=30
G="\033[0;32m"; Y="\033[1;33m"; R="\033[0;31m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }
warn(){ echo -e "${Y}[WARN]${N} $*"; }
fail(){ echo -e "${R}[FAIL]${N} $*"; }

require_signing() {
    sudo test -f "$SIGN/intermediate_ca_key" || {
        fail "no signing material at $SIGN — this device is not the isle cert authority"
        fail "on a NEW core: isle certs init-ca (mints a self-contained isle CA)"
        exit 1
    }
}

init_ca() {
    local CAD=/etc/isle-mesh/ca
    if sudo test -f "$CAD/isle-root.crt" && sudo test -f "$SIGN/intermediate_ca_key"; then
        ok "isle CA already present ($CAD/isle-root.crt) — nothing to do"
        return 0
    fi
    if sudo test -f "$CAD/isle-root.crt"; then
        warn "root exists but signing material is absent — refusing to overwrite an"
        warn "existing root (this device may trust a SUITE-managed CA); place the"
        warn "intermediate at $SIGN or remove the root deliberately first"
        return 1
    fi
    echo "Minting a self-contained isle CA on $(hostname) (root + intermediate)."
    echo "The root PRIVATE key stays on this device ($CAD, root-only); members"
    echo "trust the PUBLIC root via the fingerprint-verified bootstrap."
    local tmp; tmp=$(mktemp -d); chmod 700 "$tmp"
    # issuer substring "Polari Root CA" is LOAD-BEARING (cert_state checks
    # it on every leaf) — keep it in the INTERMEDIATE's CN too, since a
    # leaf's issuer is the intermediate's subject.
    local HOST; HOST=$(hostname)
    openssl ecparam -name prime256v1 -genkey -noout -out "$tmp/root.key" 2>/dev/null
    openssl req -x509 -new -key "$tmp/root.key" -sha256 -days 3650 \
        -subj "/CN=Polari Root CA ($HOST isle)" \
        -addext 'basicConstraints=critical,CA:TRUE,pathlen:1' \
        -addext 'keyUsage=critical,keyCertSign,cRLSign' \
        -out "$tmp/root.crt" 2>/dev/null || { rm -rf "$tmp"; fail "root generation failed"; return 1; }
    # intermediate key is ENCRYPTED; the password lives beside it root-only
    # (the shape certs.sh already signs with: -passin file:$SIGN/password)
    openssl rand -base64 32 > "$tmp/password"
    openssl ecparam -name prime256v1 -genkey -noout 2>/dev/null \
        | openssl ec -aes256 -passout "file:$tmp/password" -out "$tmp/int.key" 2>/dev/null
    openssl req -new -key "$tmp/int.key" -passin "file:$tmp/password" \
        -subj "/CN=Polari Root CA — isle intermediate ($HOST)" -out "$tmp/int.csr" 2>/dev/null
    printf 'basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n' > "$tmp/int.ext"
    openssl x509 -req -in "$tmp/int.csr" -CA "$tmp/root.crt" -CAkey "$tmp/root.key" \
        -CAcreateserial -days 1825 -sha256 -extfile "$tmp/int.ext" \
        -out "$tmp/int.crt" 2>/dev/null || { rm -rf "$tmp"; fail "intermediate signing failed"; return 1; }
    sudo mkdir -p "$SIGN" "$CRTD" "$KEYD"
    sudo install -m 644 "$tmp/root.crt" "$CAD/isle-root.crt"
    sudo install -m 600 "$tmp/root.key" "$CAD/root_ca_key"
    sudo install -m 644 "$tmp/int.crt" "$SIGN/intermediate_ca.crt"
    sudo install -m 600 "$tmp/int.key" "$SIGN/intermediate_ca_key"
    sudo install -m 600 "$tmp/password" "$SIGN/password"
    rm -rf "$tmp"
    ok "isle CA minted: $CAD/isle-root.crt (root 10y, intermediate 5y)"
    ok "fingerprint: $(openssl x509 -in "$CAD/isle-root.crt" -noout -fingerprint -sha256 | cut -d= -f2 | cut -c1-32)…"
}

domains_from_registry() {
    sudo cat "$REG" 2>/dev/null | python3 -c '
import json,sys
d=json.load(sys.stdin)
seen=[]
for app in (d.get("apps") or {}).values():
    dom=app.get("domain","")
    if dom and dom not in seen: seen.append(dom)
    for svc in (app.get("services") or []):
        sub=svc.get("subdomain","")
        full=(sub+"."+dom) if sub else ""
        if full and full not in seen: seen.append(full)
print("\n".join(seen))'
}

# cert_state <domain>: missing | self-signed | wrong-san | expiring | ok
cert_state() {
    local dom=$1 crt="$CRTD/$dom.crt"
    sudo test -f "$crt" || { echo missing; return; }
    local issuer
    issuer=$(sudo openssl x509 -in "$crt" -noout -issuer 2>/dev/null)
    case "$issuer" in
        *"Polari Root CA"*) ;;
        *) echo self-signed; return ;;
    esac
    sudo openssl x509 -in "$crt" -noout -ext subjectAltName 2>/dev/null | grep -q "DNS:$dom" \
        || { echo wrong-san; return; }
    sudo openssl x509 -in "$crt" -noout -checkend $((RENEW_UNDER*86400)) >/dev/null 2>&1 \
        || { echo expiring; return; }
    echo ok
}

issue_leaf() {
    local dom=$1
    require_signing
    # tmp is USER-owned (0700) so shell redirects work; sudo only
    # where the signing key is actually read or agent dirs written
    local tmp; tmp=$(mktemp -d); chmod 700 "$tmp"
    openssl ecparam -name prime256v1 -genkey -noout -out "$tmp/key" 2>/dev/null
    openssl req -new -key "$tmp/key" -subj "/CN=$dom" -out "$tmp/csr" 2>/dev/null
    # extfile must be a REAL file — process substitution does not
    # survive sudo fd handling (live-caught 2026-08-08)
    printf 'subjectAltName=DNS:%s\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature\nextendedKeyUsage=serverAuth\n' "$dom" > "$tmp/ext"
    if ! sudo openssl x509 -req -in "$tmp/csr" \
        -CA "$SIGN/intermediate_ca.crt" -CAkey "$SIGN/intermediate_ca_key" \
        -passin "file:$SIGN/password" -CAcreateserial \
        -days $DAYS -sha256 -extfile "$tmp/ext" \
        -out "$tmp/crt" 2> "$tmp/err"; then
        tail -2 "$tmp/err"
        rm -rf "$tmp"; fail "signing failed for $dom"; return 1
    fi
    sudo sh -c "cat '$tmp/crt' '$SIGN/intermediate_ca.crt' > '$CRTD/$dom.crt'"
    sudo cp "$tmp/key" "$KEYD/$dom.key"
    sudo chmod 644 "$CRTD/$dom.crt"; sudo chmod 640 "$KEYD/$dom.key"
    rm -rf "$tmp"
    ok "issued $dom (explicit SAN, ${DAYS}d, chained to the isle intermediate)"
}

reload_agent() {
    docker ps --format "{{.Names}}" | grep -q "^isle-vlan-agent$" || return 0
    docker exec isle-vlan-agent sh -c "nginx -t >/dev/null 2>&1 && kill -HUP 1" \
        && ok "agent reloaded" || warn "agent reload failed (nginx -t)"
}

case "${1:-status}" in
    status)
        printf "%-28s %s\n" DOMAIN STATE
        while read -r dom; do
            [ -n "$dom" ] || continue
            printf "%-28s %s\n" "$dom" "$(cert_state "$dom")"
        done < <(domains_from_registry) ;;
    sync)
        require_signing
        changed=0
        while read -r dom; do
            [ -n "$dom" ] || continue
            state=$(cert_state "$dom")
            if [ "$state" != ok ]; then
                echo "-> $dom: $state — issuing"
                issue_leaf "$dom" && changed=1
            fi
        done < <(domains_from_registry)
        [ $changed -eq 1 ] && reload_agent || ok "all leaves current" ;;
    issue)
        dom=${2:?usage: isle certs issue <domain>}
        issue_leaf "$dom" && reload_agent ;;
    init-ca)
        init_ca ;;
    *) echo "usage: isle certs [status|sync|issue <domain>|init-ca]"; exit 1 ;;
esac
