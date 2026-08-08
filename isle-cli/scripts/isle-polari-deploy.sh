#!/bin/bash
# isle-polari-deploy.sh — deploy/redeploy polari ON the isle
# (handoff §25.3: the isle-oriented dev route as the MAIN
# deployment path). Run ON isle-core. Idempotent: brings prf-isle
# up behind the agent, (re)registers polari.isle + api.polari.isle,
# issues leaves, DNS, restarts the self-feed pusher, verifies.
#
# The dev LOOP (from pol-core, where the code lives):
#   pol node build backend            # build the image
#   docker save prf-backend:staging | ssh isle-core docker load
#   ssh isle-core isle-polari-deploy.sh   # deploy on the isle
# This script is the isle half — one command makes the isle serve
# the current polari image.
#
#   isle-polari-deploy.sh [--modules <csv>]
set -u
MODULES="${POLARI_ISLE_MODULES:-islemesh}"
[ "${1:-}" = "--modules" ] && MODULES="$2"
DIR="$HOME/polari-isle"
G="\033[0;32m"; Y="\033[1;33m"; R="\033[0;31m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }
step(){ echo -e "${Y}==>${N} $*"; }
die(){ echo -e "${R}[FAIL]${N} $*"; exit 1; }
[ -d "$DIR" ] || die "no $DIR — first-time setup writes the compose (see handoff §17)"

step "1/5 bring prf-isle up (current image, behind the agent)"
(cd "$DIR" && POLARI_ISLE_MODULES="$MODULES" docker compose up -d) \
    || die "compose up failed"
ok "prf-isle containers up (modules: $MODULES)"

step "2/5 register polari.isle + api.polari.isle (leaf issuance rides the hook)"
AM=/usr/share/isle-mesh/isle-agent/scripts/agent-manager.sh
sudo bash "$AM" register --name polari --domain polari.isle \
    --container prf-isle-frontend --port 4200 --protocol http >/dev/null 2>&1 && ok "polari.isle"
sudo bash "$AM" register --name polari-api --domain api.polari.isle \
    --container prf-isle-backend --port 3000 --protocol http >/dev/null 2>&1 && ok "api.polari.isle"

step "3/5 .isle DNS"
AGENT_IP=$(sudo docker inspect isle-vlan-agent --format '{{(index .NetworkSettings.Networks "isle-br-0").IPAddress}}' 2>/dev/null)
for dom in polari.isle api.polari.isle; do
    sudo isle dns register "$dom" "${AGENT_IP:-10.10.0.2}" >/dev/null 2>&1 && ok "$dom -> ${AGENT_IP:-10.10.0.2}"
done
docker exec isle-vlan-agent sh -c "nginx -t >/dev/null 2>&1 && kill -HUP 1" 2>/dev/null

step "4/5 self-feed pusher"
sudo systemctl enable --now polari-isle-push.timer 2>/dev/null && ok "pusher timer live (2min)"

step "5/5 verify"
for i in $(seq 1 25); do
    code=$(curl -sk -o /dev/null -w "%{http_code}" --resolve api.polari.isle:443:127.0.0.1 https://api.polari.isle/api/health 2>/dev/null)
    [ "$code" = 200 ] && break; sleep 12
done
[ "${code:-}" = 200 ] && ok "api.polari.isle/api/health -> 200" || die "backend did not become healthy (last: ${code:-none})"
WEB=$(curl -sk -o /dev/null -w "%{http_code}" --resolve polari.isle:443:127.0.0.1 https://polari.isle/ 2>/dev/null)
ok "polari.isle -> $WEB"
bash "$DIR/push-to-polari.sh" >/dev/null 2>&1 && ok "fed the graph"
echo
ok "polari is live on the isle: https://polari.isle  (store: /isle-store)"
