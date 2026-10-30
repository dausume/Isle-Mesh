#!/bin/bash
# isle-registry-setup.sh — the MESH-LOCAL docker registry (handoff
# §10 offline-complete gap #8 + mac-3 + the dev-loop accelerator).
# Runs registry:2 on isle-core with an isle-CA-signed cert, so
# docker daemons trust it via certs.d (no insecure-registries).
# Makes image moves a fast push/pull instead of 935MB save|ssh|load,
# AND lets the mesh run offline / swarm place images cluster-wide.
#
# Run on isle-core (has the signing material). Idempotent.
#   isle-registry-setup.sh [--port 5000] [--address <core IPv4>]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/offline-runtime.sh"
SIGN=/etc/isle-mesh/ca/signing
REGDIR=/etc/isle-mesh/registry
CERTS="$REGDIR/certs"
G="\033[0;32m"; Y="\033[1;33m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }
step(){ echo -e "${Y}==>${N} $*"; }
die(){ echo "registry setup: $*" >&2; return 1; }

registry_configure() {
    PORT=5000; ISLE_IP=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --port|--address)
                [ $# -ge 2 ] || { die "$1 requires a value"; return 1; }
                if [ "$1" = --port ]; then PORT="$2"; else ISLE_IP="$2"; fi
                shift 2 ;;
            *) die "unknown argument: $1"; return 1 ;;
        esac
    done
    [[ "$PORT" =~ ^[1-9][0-9]{0,4}$ ]] && [ "$PORT" -le 65535 ] \
        || { die "port must be 1..65535"; return 1; }
    # The registry publishes a HOST port, unlike apps behind the agent.
    # Use the actual management bridge address, never an invented fallback
    # or a Wi-Fi interface name specific to the original developer machine.
    if [ -z "$ISLE_IP" ]; then
        ISLE_IP=$(ip -4 -o addr show dev br-mgmt scope global 2>/dev/null | awk '{split($4,a,"/"); print a[1]}') || ISLE_IP=""
    fi
    python3 - "$ISLE_IP" <<'IPCHECK' || return 1
import ipaddress, sys
try:
    address = ipaddress.IPv4Address(sys.argv[1])
    if address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local or int(address) == 0xffffffff:
        raise ValueError()
except ValueError:
    sys.exit('registry setup: need one routable core IPv4 address; select --address explicitly if br-mgmt is absent or ambiguous')
IPCHECK
    # An explicitly selected address must belong to THIS host.
    ip -4 -o addr show | awk '{split($4,a,"/"); print a[1]}' | grep -Fxq "$ISLE_IP" \
        || { die "selected address $ISLE_IP is not assigned to this host"; return 1; }
}

registry_ensure_image() {
    if isle_offline; then
        isle_offline_image registry:2 || return 1
    else
        docker image inspect registry:2 >/dev/null 2>&1 || docker pull registry:2 || return 1
    fi
    ok "registry:2 present"
}

registry_publish_dns() {
    sudo isle dns register registry.isle "$ISLE_IP" \
        || { die "registry is running, but DNS registration failed; setup incomplete"; return 1; }
    registry_verify "$PORT"
}

registry_verify() {
    local port="${1:-5000}"
    [[ "$port" =~ ^[1-9][0-9]{0,4}$ ]] && [ "$port" -le 65535 ] \
        || { die "port must be 1..65535"; return 1; }
    local code attempt
    for attempt in 1 2 3; do
        code=$(curl -sS --cacert /etc/isle-mesh/ca/isle-root.crt --max-time 5 \
            -o /dev/null -w '%{http_code}' "https://registry.isle:$port/v2/") || code="transport/DNS/TLS failure"
        if [ "$code" = 200 ]; then
            ok "registry.isle:$port resolves and serves the registry API with trusted TLS from this host"
            return 0
        fi
        [ "$attempt" = 3 ] || sleep 2
    done
    die "registry API verification failed ($code); setup incomplete"
}

main() {
registry_configure "$@" || return 1
sudo test -f "$SIGN/intermediate_ca_key" || { die "no isle signing material — run on the isle CA host"; return 1; }
sudo test -f /etc/isle-mesh/ca/isle-root.crt || { die "missing Isle public root certificate"; return 1; }

step "1/4 registry image"
registry_ensure_image || return 1

step "2/4 TLS cert for registry.isle (isle-CA signed, multi-SAN)"
sudo mkdir -p "$CERTS" "$REGDIR/data"
TMP=$(mktemp -d); chmod 700 "$TMP"
trap 'rm -rf -- "$TMP"' EXIT
openssl ecparam -name prime256v1 -genkey -noout -out "$TMP/key" 2>/dev/null
openssl req -new -key "$TMP/key" -subj "/CN=registry.isle" -out "$TMP/csr" 2>/dev/null
# every way a docker daemon might address it → one cert
printf 'subjectAltName=DNS:registry.isle,DNS:localhost,IP:%s,IP:127.0.0.1\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature\nextendedKeyUsage=serverAuth\n' \
    "$ISLE_IP" > "$TMP/ext"
sudo openssl x509 -req -in "$TMP/csr" \
    -CA "$SIGN/intermediate_ca.crt" -CAkey "$SIGN/intermediate_ca_key" \
    -passin "file:$SIGN/password" -CAcreateserial -days 365 -sha256 \
    -extfile "$TMP/ext" -out "$TMP/crt" 2>/dev/null
sudo sh -c "cat '$TMP/crt' '$SIGN/intermediate_ca.crt' > '$CERTS/registry.crt'"
sudo cp "$TMP/key" "$CERTS/registry.key"
sudo chmod 644 "$CERTS/registry.crt"; sudo chmod 640 "$CERTS/registry.key"
rm -rf "$TMP"
ok "cert issued (SANs: registry.isle, localhost, $ISLE_IP)"

step "3/4 run registry:2 with TLS on :$PORT"
docker rm -f isle-registry >/dev/null 2>&1 || true
docker run -d --name isle-registry --restart unless-stopped \
    -p "$PORT:5000" \
    -v "$REGDIR/data:/var/lib/registry" \
    -v "$CERTS:/certs:ro" \
    -e REGISTRY_HTTP_TLS_CERTIFICATE=/certs/registry.crt \
    -e REGISTRY_HTTP_TLS_KEY=/certs/registry.key \
    registry:2 >/dev/null
ok "isle-registry up on :$PORT"

step "4/4 trust it on THIS host (certs.d) + DNS"
for addr in "registry.isle:$PORT" "$ISLE_IP:$PORT" "localhost:$PORT"; do
    sudo mkdir -p "/etc/docker/certs.d/$addr"
    sudo cp /etc/isle-mesh/ca/isle-root.crt "/etc/docker/certs.d/$addr/ca.crt"
done
registry_publish_dns || return 1
echo
echo "push:  docker tag <img> registry.isle:$PORT/<img> && docker push registry.isle:$PORT/<img>"
echo "OTHER hosts must trust it once:"
echo "  sudo mkdir -p /etc/docker/certs.d/<addr>:$PORT && \\"
echo "  sudo cp isle-root.crt /etc/docker/certs.d/<addr>:$PORT/ca.crt"

}

if [[ "${BASH_SOURCE[0]}" = "$0" ]]; then
    main "$@"
fi
