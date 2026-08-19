#!/bin/bash
# isle-polari-deploy.sh — deploy/redeploy polari ON the isle
# (handoff §25.3: the isle-oriented dev route as the MAIN
# deployment path). Run ON isle-core. Idempotent: brings prf-isle
# up behind the agent, (re)registers polari.isle + api.polari.isle,
# issues leaves, DNS, restarts the self-feed pusher, verifies.
#
# The dev LOOP (from pol-core, where the code lives) — now via the
# MESH-LOCAL REGISTRY (fast push/pull, no 935MB save|ssh|load):
#   pol node build backend
#   docker tag prf-backend:staging registry.isle:5000/prf-backend:staging
#   docker push registry.isle:5000/prf-backend:staging
#   ssh <core> isle-polari-deploy --pull      # pull + retag + deploy
#
#   isle-polari-deploy.sh [--modules <csv>] [--pull]
set -u
MODULES="${POLARI_ISLE_MODULES:-islemesh}"
REGISTRY="${ISLE_REGISTRY:-registry.isle:5000}"
PULL=0
while [ $# -gt 0 ]; do case "$1" in
    --modules) MODULES="$2"; shift 2 ;;
    --pull) PULL=1; shift ;;
    *) shift ;;
esac; done
# the deployment lives in the INVOKING user's home (core-install runs
# under sudo/pkexec where $HOME is /root — the deployment must not)
DEPLOY_USER="${SUDO_USER:-$USER}"
USER_HOME="$(getent passwd "$DEPLOY_USER" | cut -d: -f6)"
DIR="${USER_HOME:-$HOME}/polari-isle"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
G="\033[0;32m"; Y="\033[1;33m"; R="\033[0;31m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }
step(){ echo -e "${Y}==>${N} $*"; }
die(){ echo -e "${R}[FAIL]${N} $*"; exit 1; }
# polari is a SUB-PROJECT nested in the repo (polari-isle/ — everything
# lives inside the suite): first run SEEDS ~/polari-isle from the
# versioned copy instead of requiring hand-made setup. The deployed
# copy stays the working instance (rebase edits it in place); the
# versioned dir is the source of truth for NEW deployments only.
if [ ! -d "$DIR" ]; then
    SEED=""
    for s in /usr/share/isle-mesh/polari-isle "$SCRIPT_DIR/../../polari-isle"; do
        [ -f "$s/docker-compose.yml" ] && { SEED="$s"; break; }
    done
    [ -n "$SEED" ] || die "no $DIR and no versioned polari-isle/ seed found (deb: /usr/share/isle-mesh/polari-isle)"
    mkdir -p "$DIR"
    cp "$SEED"/docker-compose.yml "$SEED"/runtime-config.json "$SEED"/push-to-polari.sh "$DIR/"
    chmod +x "$DIR/push-to-polari.sh"
    ok "seeded $DIR from the versioned polari sub-project ($SEED)"
fi

if [ "$PULL" = 1 ]; then
    step "0/5 pull the pushed image from the mesh registry"
    docker pull "$REGISTRY/prf-backend:staging" \
        || die "pull failed — is $REGISTRY reachable + trusted? (isle-registry-setup on the CA host; certs.d here)"
    docker tag "$REGISTRY/prf-backend:staging" prf-backend:staging
    docker pull "$REGISTRY/prf-frontend:staging" 2>/dev/null \
        && docker tag "$REGISTRY/prf-frontend:staging" prf-frontend:staging || true
    ok "images pulled from $REGISTRY + retagged local"
fi

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

# record the CORE's modules in its registry entry (modes are
# schema-tolerant) — the store/coherence surface them per instance
sudo python3 - "$MODULES" <<'PYMOD' 2>/dev/null && ok "modules recorded: $MODULES" || true
import json, sys
path = "/etc/isle-mesh/agent/registry.json"
reg = json.load(open(path))
app = reg.get("apps", {}).get("polari")
if app is None:
    raise SystemExit(1)
modes = [m for m in (app.get("modes") or [])
         if not str(m).startswith("modules:")]
modes.append("modules:" + sys.argv[1])
app["modes"] = modes
json.dump(reg, open(path, "w"), indent=2)
PYMOD

step "3/5 .isle DNS"
AGENT_IP=$(sudo docker inspect isle-vlan-agent --format '{{(index .NetworkSettings.Networks "isle-br-0").IPAddress}}' 2>/dev/null)
for dom in polari.isle api.polari.isle; do
    sudo isle dns register "$dom" "${AGENT_IP:-10.10.0.2}" >/dev/null 2>&1 && ok "$dom -> ${AGENT_IP:-10.10.0.2}"
done
docker exec isle-vlan-agent sh -c "nginx -t >/dev/null 2>&1 && kill -HUP 1" 2>/dev/null

step "4/5 self-feed pusher"
# units are written here (templated per user/home) so a fresh device
# needs no hand-made systemd files — the sub-project carries itself
if [ ! -f /etc/systemd/system/polari-isle-push.timer ]; then
    sudo tee /etc/systemd/system/polari-isle-push.service >/dev/null <<UNIT
[Unit]
Description=Feed isle state into prf-isle (push-to-polari)
[Service]
Type=oneshot
User=$USER
ExecStart=$DIR/push-to-polari.sh
UNIT
    sudo tee /etc/systemd/system/polari-isle-push.timer >/dev/null <<'UNIT'
[Unit]
Description=Feed isle state into prf-isle every 2 minutes
[Timer]
OnBootSec=90
OnUnitActiveSec=120
[Install]
WantedBy=timers.target
UNIT
    sudo systemctl daemon-reload
    ok "pusher units written (/etc/systemd/system/polari-isle-push.*)"
fi
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
