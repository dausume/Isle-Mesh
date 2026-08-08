#!/bin/bash
# isle-registry-setup.sh — the MESH-LOCAL docker registry (handoff
# §10 offline-complete gap #8 + mac-3 + the dev-loop accelerator).
# Runs registry:2 on isle-core with an isle-CA-signed cert, so
# docker daemons trust it via certs.d (no insecure-registries).
# Makes image moves a fast push/pull instead of 935MB save|ssh|load,
# AND lets the mesh run offline / swarm place images cluster-wide.
#
# Run on isle-core (has the signing material). Idempotent.
#   isle-registry-setup.sh [--port 5000]
set -eu
PORT=5000
[ "${1:-}" = "--port" ] && PORT="$2"
SIGN=/etc/isle-mesh/ca/signing
REGDIR=/etc/isle-mesh/registry
CERTS="$REGDIR/certs"
HOST_IP=$(ip -br addr show wlp2s0 2>/dev/null | awk '{print $3}' | cut -d/ -f1)
# isle-core's address ON the isle (br-mgmt) — where in-mesh nodes +
# the router-DNS name resolve the registry (published on 0.0.0.0).
ISLE_IP=$(ip -br addr show br-mgmt 2>/dev/null | awk '{print $3}' | cut -d/ -f1)
ISLE_IP="${ISLE_IP:-192.168.1.254}"
G="\033[0;32m"; Y="\033[1;33m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }
step(){ echo -e "${Y}==>${N} $*"; }

sudo test -f "$SIGN/intermediate_ca_key" || { echo "no isle signing material — run on the isle CA host"; exit 1; }
command -v registry >/dev/null 2>&1 || true

step "1/4 registry image"
docker image inspect registry:2 >/dev/null 2>&1 || docker pull registry:2
ok "registry:2 present"

step "2/4 TLS cert for registry.isle (isle-CA signed, multi-SAN)"
sudo mkdir -p "$CERTS" "$REGDIR/data"
TMP=$(mktemp -d); chmod 700 "$TMP"
openssl ecparam -name prime256v1 -genkey -noout -out "$TMP/key" 2>/dev/null
openssl req -new -key "$TMP/key" -subj "/CN=registry.isle" -out "$TMP/csr" 2>/dev/null
# every way a docker daemon might address it → one cert
printf 'subjectAltName=DNS:registry.isle,DNS:%s,DNS:localhost,IP:%s,IP:%s,IP:127.0.0.1\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature\nextendedKeyUsage=serverAuth\n' \
    "$(hostname)" "$HOST_IP" "$ISLE_IP" > "$TMP/ext"
sudo openssl x509 -req -in "$TMP/csr" \
    -CA "$SIGN/intermediate_ca.crt" -CAkey "$SIGN/intermediate_ca_key" \
    -passin "file:$SIGN/password" -CAcreateserial -days 365 -sha256 \
    -extfile "$TMP/ext" -out "$TMP/crt" 2>/dev/null
sudo sh -c "cat '$TMP/crt' '$SIGN/intermediate_ca.crt' > '$CERTS/registry.crt'"
sudo cp "$TMP/key" "$CERTS/registry.key"
sudo chmod 644 "$CERTS/registry.crt"; sudo chmod 640 "$CERTS/registry.key"
rm -rf "$TMP"
ok "cert issued (SANs: registry.isle, $(hostname), $HOST_IP, $ISLE_IP)"

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
for addr in "registry.isle:$PORT" "$HOST_IP:$PORT" "$ISLE_IP:$PORT" "localhost:$PORT"; do
    sudo mkdir -p "/etc/docker/certs.d/$addr"
    sudo cp /etc/isle-mesh/ca/isle-root.crt "/etc/docker/certs.d/$addr/ca.crt"
done
sudo isle dns register registry.isle "$ISLE_IP" >/dev/null 2>&1 || true
ok "isle-core trusts registry.isle:$PORT + fallbacks; DNS registered"
echo
echo "push:  docker tag <img> registry.isle:$PORT/<img> && docker push registry.isle:$PORT/<img>"
echo "OTHER hosts must trust it once:"
echo "  sudo mkdir -p /etc/docker/certs.d/<addr>:$PORT && \\"
echo "  sudo cp isle-root.crt /etc/docker/certs.d/<addr>:$PORT/ca.crt"
