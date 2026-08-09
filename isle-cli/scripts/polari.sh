#!/bin/bash
# polari.sh — `isle polari`: POLARI INSTANCES as mesh-app installs
# (Dustin: polari topology manipulation should act like a mesh-app
# install of polari instances — the topology functionality works
# the same on an isle/mesh).
#
#   isle polari instance deploy [--name <n>] [--modules <csv>]
#   isle polari instance undeploy <name>
#   isle polari instances              what runs where (coherence)
#
# deploy: a NEW polari instance (backend + frontend, prf images from
# the mesh registry when absent locally) behind THIS device's agent,
# serving https://<name>.isle + https://api.<name>.isle. The name
# auto-suffixes off the isle-wide instance list (polari-2, ...).
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
G="\033[0;32m"; Y="\033[1;33m"; R="\033[0;31m"; C="\033[0;36m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }
warn(){ echo -e "${Y}[WARN]${N} $*"; }
step(){ echo -e "${C}==>${N} $*"; }
die(){ echo -e "${R}[FAIL]${N} $*"; exit 1; }

API="https://api.polari.isle"
CURL="curl -skf --max-time 8"
REGISTRY="${ISLE_REGISTRY:-registry.isle:5000}"
BASE=/etc/isle-mesh/polari

api_get(){ $CURL "$API$1" 2>/dev/null && return 0
    $CURL --resolve api.polari.isle:443:127.0.0.1 "$API$1" 2>/dev/null; }

require_member(){
    docker ps --format '{{.Names}}' 2>/dev/null | grep -qE '^isle-(vlan|remote)-agent$' \
        || die "this device is not an isle member (no agent) — sudo isle onboard --host / isle core-install"
}

next_name(){
    api_get /api/islemesh/catalog/polari 2>/dev/null | python3 -c '
import json, sys
try: e = json.load(sys.stdin)["entry"]
except Exception: print("polari-2"); raise SystemExit
taken = {i["app"] for i in e.get("instances", [])}
n = 2
while "polari-%d" % n in taken: n += 1
print("polari-%d" % n)' 2>/dev/null || echo "polari-2"
}

ensure_images(){
    if ! docker image inspect prf-backend:staging >/dev/null 2>&1; then
        step "pulling prf images from the mesh registry"
        docker pull "$REGISTRY/prf-backend:staging" 2>/dev/null \
            || docker pull 192.168.0.24:5000/prf-backend:staging 2>/dev/null \
            || die "no prf-backend:staging locally and no registry pull worked"
        docker tag "$(docker images -q "$REGISTRY/prf-backend:staging" 2>/dev/null || echo 192.168.0.24:5000/prf-backend:staging)" prf-backend:staging 2>/dev/null \
            || docker tag 192.168.0.24:5000/prf-backend:staging prf-backend:staging
    fi
    if ! docker image inspect prf-frontend:staging >/dev/null 2>&1; then
        docker pull "$REGISTRY/prf-frontend:staging" 2>/dev/null \
            || docker pull 192.168.0.24:5000/prf-frontend:staging 2>/dev/null \
            || die "no prf-frontend:staging locally and no registry pull worked"
        docker tag "$(docker images -q "$REGISTRY/prf-frontend:staging" 2>/dev/null || echo 192.168.0.24:5000/prf-frontend:staging)" prf-frontend:staging 2>/dev/null \
            || docker tag 192.168.0.24:5000/prf-frontend:staging prf-frontend:staging
    fi
    ok "prf images present"
}

write_configs(){ # $1 name $2 modules
    local NAME="$1" MODULES="$2" DIR="$BASE/$1"
    sudo mkdir -p "$DIR"
    sudo tee "$DIR/runtime-config.json" >/dev/null <<EOF
{
  "_comment": "$NAME: a polari instance deployed as a mesh-app (isle polari)",
  "backend": {
    "http":  { "protocol": "http",  "url": "api.$NAME.isle", "port": "80" },
    "https": { "protocol": "https", "url": "api.$NAME.isle", "port": "443" },
    "ws":    { "protocol": "wss",   "url": "api.$NAME.isle", "port": "443" },
    "preferHttps": true
  },
  "frontend": {
    "http":  { "protocol": "http",  "url": "$NAME.isle", "port": "80" },
    "https": { "protocol": "https", "url": "$NAME.isle", "port": "443" }
  }
}
EOF
    sudo tee "$DIR/docker-compose.yml" >/dev/null <<EOF
services:
  backend:
    image: prf-backend:staging
    container_name: prf-$NAME-backend
    restart: unless-stopped
    networks: [isle-agent-net]
    environment:
      - WEBSOCKET_ENABLED=true
      - WEBSOCKET_PORT=3001
      - POLARI_MODULES=$MODULES
      - POLARI_LAZY_BOOT=1
    volumes:
      - backend-data:/data
    mem_limit: 2g
  frontend:
    image: prf-frontend:staging
    container_name: prf-$NAME-frontend
    restart: unless-stopped
    networks: [isle-agent-net]
    volumes:
      - $DIR/runtime-config.json:/usr/share/nginx/html/assets/runtime-config.json:ro
    mem_limit: 128m
    depends_on: [backend]
volumes:
  backend-data:
networks:
  isle-agent-net:
    external: true
EOF
}

agent_ip(){
    local ip
    ip=$(sudo docker inspect isle-vlan-agent --format '{{(index .NetworkSettings.Networks "isle-br-0").IPAddress}}' 2>/dev/null)
    [ -n "$ip" ] || ip=$(sudo docker exec isle-remote-agent ip -4 -o addr 2>/dev/null \
        | awk '$2 != "lo" && $4 !~ /^172\.20\./ {split($4, a, "/"); print a[1]; exit}')
    echo "$ip"
}

self_report(){
    local REG=/etc/isle-mesh/agent/registry.json RPT HOSTN AGENT
    [ -f "$REG" ] || return 0
    # canonical device name: same normalization as the core pusher
    HOSTN=$(hostname | sed "s/dustin-etts-mesh-core/isle-core/")
    AGENT=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -cE '^isle-(vlan|remote)-agent$' || true)
    # device facts ride along so coherence knows the agent is up
    printf '{"device":"%s","facts":{"machine_name":"%s","agent_present":%s}}' \
        "$HOSTN" "$HOSTN" "$([ "${AGENT:-0}" -gt 0 ] && echo true || echo false)" \
        | { $CURL -X POST -H "Content-Type: application/json" --data-binary @- \
              "$API/api/islemesh/ingest/device" >/dev/null 2>&1 \
            || true; }
    RPT=$(mktemp)
    python3 - "$HOSTN" "$REG" > "$RPT" 2>/dev/null <<'PYEOF'
import json, sys
print(json.dumps({'device': sys.argv[1],
                  'registry': json.load(open(sys.argv[2]))}))
PYEOF
    { $CURL -X POST -H "Content-Type: application/json" --data-binary @"$RPT" \
        "$API/api/islemesh/ingest/registry" >/dev/null 2>&1 \
      || $CURL --resolve api.polari.isle:443:127.0.0.1 -X POST \
        -H "Content-Type: application/json" --data-binary @"$RPT" \
        "$API/api/islemesh/ingest/registry" >/dev/null 2>&1; } \
        && ok "instance reported to the isle topology" \
        || warn "topology report skipped (api unreachable)"
    rm -f "$RPT"
}

deploy(){
    local NAME="" MODULES="islemesh"
    while [ $# -gt 0 ]; do case "$1" in
        --name) NAME="$2"; shift 2 ;;
        --modules) MODULES="$2"; shift 2 ;;
        *) shift ;;
    esac; done
    require_member
    [ -n "$NAME" ] || NAME=$(next_name)
    echo "$NAME" | grep -qE '^[a-z0-9][a-z0-9-]{0,40}$' || die "bad instance name: $NAME"
    [ -d "$BASE/$NAME" ] && warn "$NAME already has a config dir — redeploying it"

    step "1/5 images"
    ensure_images
    step "2/5 config ($NAME, modules: $MODULES)"
    write_configs "$NAME" "$MODULES"
    ok "$BASE/$NAME"
    step "3/5 containers"
    (cd "$BASE/$NAME" && sudo docker compose -p "prf-$NAME" up -d) || die "compose up failed"
    ok "prf-$NAME-backend + prf-$NAME-frontend up"
    step "4/5 register $NAME.isle + api.$NAME.isle behind the agent"
    local AM=/usr/share/isle-mesh/isle-agent/scripts/agent-manager.sh
    sudo bash "$AM" register --name "$NAME" --domain "$NAME.isle" \
        --container "prf-$NAME-frontend" --port 4200 --protocol http >/dev/null 2>&1 \
        && ok "$NAME.isle" || warn "$NAME.isle registration failed"
    sudo bash "$AM" register --name "$NAME-api" --domain "api.$NAME.isle" \
        --container "prf-$NAME-backend" --port 3000 --protocol http >/dev/null 2>&1 \
        && ok "api.$NAME.isle" || warn "api.$NAME.isle registration failed"
    local IP; IP=$(agent_ip)
    for dom in "$NAME.isle" "api.$NAME.isle"; do
        sudo /usr/local/bin/isle dns register "$dom" "$IP" >/dev/null 2>&1 \
            && ok "$dom -> $IP" \
            || warn "$dom: router DNS not registrable here — the core's dns-reconcile maps it (~2min)"
    done
    # record the instance's MODULES in its registry entry (modes are
    # schema-tolerant strings) — the store/coherence surface them
    if [ -f /etc/isle-mesh/agent/registry.json ]; then
        sudo python3 - "$NAME" "$MODULES" <<'PYMOD' 2>/dev/null && ok "modules recorded: $MODULES" || warn "could not record modules in the registry"
import json, sys
name, modules = sys.argv[1], sys.argv[2]
path = "/etc/isle-mesh/agent/registry.json"
reg = json.load(open(path))
app = reg.get("apps", {}).get(name)
if app is None:
    raise SystemExit(1)
modes = [m for m in (app.get("modes") or [])
         if not str(m).startswith("modules:")]
modes.append("modules:" + modules)
app["modes"] = modes
json.dump(reg, open(path, "w"), indent=2)
PYMOD
    fi
    step "5/5 report + verify"
    self_report
    local code i
    for i in $(seq 1 25); do
        code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 6 \
            --resolve "api.$NAME.isle:443:127.0.0.1" "https://api.$NAME.isle/api/health" 2>/dev/null)
        [ "$code" = 200 ] && break; sleep 12
    done
    if [ "${code:-}" = 200 ]; then
        ok "api.$NAME.isle/api/health -> 200 (via the local agent)"
    else
        warn "backend not yet healthy via the local agent (last: ${code:-none}) — lazy boot can take ~1-2min; from another device: https://$NAME.isle"
    fi
    echo
    ok "polari instance '$NAME' deployed as a mesh-app: https://$NAME.isle"
    echo "   undeploy: isle polari instance undeploy $NAME"
}

undeploy(){
    local NAME="${1:?usage: isle polari instance undeploy <name>}"
    [ -d "$BASE/$NAME" ] || die "no such instance dir: $BASE/$NAME"
    (cd "$BASE/$NAME" && sudo docker compose -p "prf-$NAME" down) || warn "compose down had warnings"
    local AM=/usr/share/isle-mesh/isle-agent/scripts/agent-manager.sh
    sudo bash "$AM" unregister --name "$NAME" >/dev/null 2>&1
    sudo bash "$AM" unregister --name "$NAME-api" >/dev/null 2>&1
    self_report
    ok "undeployed $NAME (config kept at $BASE/$NAME; data volume kept)"
}

instances(){
    api_get /api/islemesh/coherence | python3 -c '
import json, sys
try: d = json.load(sys.stdin)
except Exception: print("coherence unavailable (is prf-isle up?)"); raise SystemExit(1)
p = d.get("polari", {})
print("polari instances across the isle:")
for i in p.get("instances", []):
    print("  %-12s on %-28s %s" % (i["app"], i["device"] or "?", i["domain"] or ""))
if p.get("candidates"):
    print("could also run on: " + ", ".join(p["candidates"]))
for a in d.get("assessments", []):
    print("[%s] %s" % (a["level"], a["message"]))'
}

case "${1:-help}" in
    instance)
        case "${2:-}" in
            deploy) shift 2; deploy "$@" ;;
            undeploy) shift 2; undeploy "$@" ;;
            *) echo "usage: isle polari instance [deploy [--name n] [--modules csv]|undeploy <name>]" ;;
        esac ;;
    instances) instances ;;
    *) echo "usage: isle polari [instance deploy|instance undeploy <n>|instances]" ;;
esac
