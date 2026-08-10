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
      # Without this the sqlite files land in ./data -> /app/data, in the
      # container's writable layer, and the volume below sits empty — so
      # every recreate (including `isle polari module move`, which
      # recreates both backends) threw the instance's data away.
      - DATABASE_PATH=/data/polari.db
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
    # resolve the health probe to the agent that actually serves
    # this instance: the CORE agent is on 127.0.0.1 (host-local
    # proxy), a REMOTE agent is its macvlan IP (127.0.0.1 would
    # never reach it — the old 5-minute 'hang'). Short, bounded loop.
    local IP2; IP2=$(agent_ip)
    local RES="--resolve api.$NAME.isle:443:127.0.0.1"
    [ -n "$IP2" ] && [ "$IP2" != "10.10.0.2" ] \
        && RES="--resolve api.$NAME.isle:443:$IP2"
    local code i
    for i in $(seq 1 6); do
        code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 6 \
            $RES "https://api.$NAME.isle/api/health" 2>/dev/null)
        [ "$code" = 200 ] && break; sleep 5
    done
    if [ "${code:-}" = 200 ]; then
        ok "api.$NAME.isle/api/health -> 200"
    else
        warn "backend still booting (last: ${code:-none}) — lazy boot takes ~1-2min; it will answer at https://$NAME.isle. Not a failure; the instance is up + reported."
    fi
    echo
    ok "polari instance '$NAME' deployed as a mesh-app: https://$NAME.isle"
    echo "   undeploy: isle polari instance undeploy $NAME"
}

# THE DYNAMIC-URL PROOF (Dustin #1): change a LIVE instance's URL
# and push the change through every reference — runtime-config
# (IN-PLACE: bind-mounted, mv breaks the inode), agent registry,
# fragments (watcher regen), DNS, leaf (rides the register hook).
rebase(){
    local NAME="${1:?usage: isle polari instance rebase <name> --domain <new.isle>}"; shift
    local NEWDOM=""
    while [ $# -gt 0 ]; do case "$1" in
        --domain) NEWDOM="$2"; shift 2 ;;
        *) shift ;;
    esac; done
    [ -n "$NEWDOM" ] || die "--domain <new.isle> required"
    echo "$NEWDOM" | grep -qE '^[a-z0-9][a-z0-9-]{0,40}\.isle$' || die "bad domain: $NEWDOM (want <name>.isle)"
    local DIR="$BASE/$NAME"
    [ -d "$DIR" ] || die "no such instance: $DIR"
    require_member
    local OLDDOM
    OLDDOM=$(python3 -c "import json;print(json.load(open('$DIR/runtime-config.json'))['frontend']['https']['url'])" 2>/dev/null)
    [ -n "$OLDDOM" ] || die "cannot read current domain from $DIR/runtime-config.json"
    step "1/4 rewrite URL references (runtime-config, in place)"
    sudo python3 - "$DIR/runtime-config.json" "$OLDDOM" "$NEWDOM" <<'PYRC'
import json, sys
path, old, new = sys.argv[1:4]
txt = open(path).read().replace("api." + old, "api." + new).replace(old, new)
open(path, "w").write(txt)
PYRC
    ok "$OLDDOM -> $NEWDOM (+ api.) in runtime-config.json"
    step "2/4 re-register behind the agent (fragment regen + leaf ride the hook)"
    local AM=/usr/share/isle-mesh/isle-agent/scripts/agent-manager.sh
    sudo bash "$AM" unregister --name "$NAME" >/dev/null 2>&1
    sudo bash "$AM" unregister --name "$NAME-api" >/dev/null 2>&1
    sudo bash "$AM" register --name "$NAME" --domain "$NEWDOM" \
        --container "prf-$NAME-frontend" --port 4200 --protocol http >/dev/null 2>&1 \
        && ok "$NEWDOM" || die "$NEWDOM registration failed"
    sudo bash "$AM" register --name "$NAME-api" --domain "api.$NEWDOM" \
        --container "prf-$NAME-backend" --port 3000 --protocol http >/dev/null 2>&1 \
        && ok "api.$NEWDOM" || warn "api.$NEWDOM registration failed"
    # keep the modules note on the fresh entry
    local MODS
    MODS=$(python3 -c "import json;reg=json.load(open('/etc/isle-mesh/agent/registry.json'));print(','.join(m.split(':',1)[1] for a in [reg['apps'].get('$NAME',{})] for m in (a.get('modes') or []) if str(m).startswith('modules:')))" 2>/dev/null)
    step "3/4 DNS: new rows in, old rows out"
    local IP; IP=$(agent_ip)
    for dom in "$NEWDOM" "api.$NEWDOM"; do
        sudo /usr/local/bin/isle dns register "$dom" "$IP" >/dev/null 2>&1 \
            && ok "$dom -> $IP" || warn "$dom: DNS via the core reconcile"
    done
    for dom in "$OLDDOM" "api.$OLDDOM"; do
        sudo /usr/local/bin/isle dns unregister "$dom" >/dev/null 2>&1 \
            && ok "retired $dom" || warn "$dom not unregistered (may already be gone)"
    done
    step "4/4 report + verify"
    self_report
    local code
    code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 8 \
        --resolve "$NEWDOM:443:127.0.0.1" "https://$NEWDOM/" 2>/dev/null)
    [ "$code" = 200 ] && ok "https://$NEWDOM -> 200 (references moved)" \
        || warn "https://$NEWDOM -> ${code:-none} (agent regen can lag a few seconds)"
    local oldcode
    oldcode=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 8 \
        --resolve "$OLDDOM:443:127.0.0.1" "https://$OLDDOM/" 2>/dev/null)
    echo "   old URL $OLDDOM now answers: ${oldcode:-none} (404/000 = correctly retired)"
    echo
    ok "instance '$NAME' rebased: $OLDDOM -> $NEWDOM (all references updated)"
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

# --- MODULE MOVEMENT between instances (the dynamic-placement proof)
# An instance's modules live in its compose POLARI_MODULES env; the
# backend lazy-boots the set. Moving a module = drop it from A's set
# + add to B's + recreate both backends + re-record. Data for a
# stateful module stays in its origin instance's volume (module
# LOADING relocates; data migration is a separate, declared step).
_modules_of_compose(){ # $1 instance dir
    grep -oE 'POLARI_MODULES=[^ ]*' "$1/docker-compose.yml" 2>/dev/null \
        | head -1 | cut -d= -f2
}
_set_modules(){ # $1 dir  $2 csv  — rewrite compose + recreate backend
    local DIR="$1" CSV="$2" NAME
    NAME=$(basename "$DIR")
    sudo sed -i "s|POLARI_MODULES=[^ ]*|POLARI_MODULES=$CSV|" \
        "$DIR/docker-compose.yml"
    (cd "$DIR" && sudo docker compose -p "prf-$NAME" up -d) >/dev/null 2>&1 \
        || warn "recreate of prf-$NAME had warnings"
    # re-record the modules note in the registry entry
    sudo python3 - "$NAME" "$CSV" <<'PYMOD' 2>/dev/null || true
import json, sys
name, csv = sys.argv[1], sys.argv[2]
path = "/etc/isle-mesh/agent/registry.json"
reg = json.load(open(path))
app = reg.get("apps", {}).get(name)
if app is None:
    raise SystemExit(0)
modes = [m for m in (app.get("modes") or [])
         if not str(m).startswith("modules:")]
modes.append("modules:" + csv)
app["modes"] = modes
json.dump(reg, open(path, "w"), indent=2)
PYMOD
}

# add a module to an instance (the resolver's primitive)
add_module(){
    local MOD="${1:?usage: isle polari module add <module> --to <instance>}"; shift
    local TO=""
    while [ $# -gt 0 ]; do case "$1" in
        --to) TO="$2"; shift 2 ;; *) shift ;;
    esac; done
    [ -n "$TO" ] || die "--to <instance> required"
    [ -d "$BASE/$TO" ] || die "target instance not found: $BASE/$TO"
    require_member
    local B NEWB
    B=$(_modules_of_compose "$BASE/$TO")
    if echo ",$B," | grep -q ",$MOD,"; then
        ok "$TO already has $MOD (${B})"; return 0
    fi
    NEWB="${B:+$B,}$MOD"
    step "add $MOD to $TO"
    _set_modules "$BASE/$TO" "$NEWB"
    self_report
    ok "$TO modules: $NEWB (lazy boot ~1-2min; https://$TO.isle)"
}

# APP PLACEMENT: a polari-app is a module collection — plan/ensure
# its modules are live across the isle (Dustin's convergence intent)
app_plan(){
    local APP="${1:?usage: isle polari app plan <app>}"
    api_get "/api/islemesh/appplan/$APP" | python3 -c '
import json, sys
try: d = json.load(sys.stdin)
except Exception: print("plan unavailable (is prf-isle up?)"); raise SystemExit(1)
if not d.get("ok"): print(d.get("error","no such app")); raise SystemExit(1)
print("app: %s (%s)" % (d["app"], d.get("title","")))
print("modules needed: %s" % ", ".join(d["modules_needed"]))
for s in d["satisfied"]:
    print("  [ok]      %-16s on %s" % (s["module"], ", ".join(s["instances"])))
for m in d["missing"]:
    print("  [MISSING] %s" % m)
if d["complete"]:
    print("\nCOMPLETE — every module is live; the app works.")
else:
    print("\nplan to ensure it:")
    for step in d["plan"]:
        print("  $ %s" % step["cmd"])'
}

app_ensure(){
    local APP="${1:?usage: isle polari app ensure <app> [--yes]}"
    local YES="${2:-}"
    local RESP; RESP=$(api_get "/api/islemesh/appplan/$APP")
    echo "$RESP" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("ok") else 1)' \
        || die "no such app: $APP"
    if echo "$RESP" | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin)["complete"] else 1)'; then
        ok "$APP already complete — every module is live"; return 0
    fi
    echo -e "${Y}Ensure '$APP' — will run on THIS host:${N}"
    echo "$RESP" | python3 -c 'import json,sys; [print("  $ "+s["cmd"]) for s in json.load(sys.stdin)["plan"]]'
    if [ "$YES" != "--yes" ]; then
        read -r -p "Proceed? [y/N] " a; [ "$a" = y ] || [ "$a" = Y ] || { echo aborted; exit 1; }
    fi
    # each plan step is an isle command targeting a DEPLOYED instance
    echo "$RESP" | python3 -c 'import json,sys; [print("%s|%s"%(s["action"],s["cmd"])) for s in json.load(sys.stdin)["plan"]]' \
      | while IFS='|' read -r action cmd; do
        echo -e "${C}==> $cmd${N}"
        eval "sudo ${cmd#sudo }" || warn "step had warnings: $cmd"
    done
    ok "ensured $APP (re-run 'isle polari app plan $APP' to confirm complete)"
}

move_module(){
    local MOD="${1:?usage: isle polari module move <module> --from <A> --to <B>}"; shift
    local FROM="" TO=""
    while [ $# -gt 0 ]; do case "$1" in
        --from) FROM="$2"; shift 2 ;;
        --to) TO="$2"; shift 2 ;;
        *) shift ;;
    esac; done
    [ -n "$FROM" ] && [ -n "$TO" ] || die "--from <A> --to <B> required"
    [ "$FROM" != "$TO" ] || die "--from and --to are the same instance"
    [ -d "$BASE/$FROM" ] || die "source instance not found: $BASE/$FROM (the core polari.isle is not movable this way)"
    [ -d "$BASE/$TO" ]   || die "target instance not found: $BASE/$TO"
    require_member
    local A B
    A=$(_modules_of_compose "$BASE/$FROM")
    B=$(_modules_of_compose "$BASE/$TO")
    echo ",$A," | grep -q ",$MOD," || die "'$MOD' is not on $FROM (has: ${A:-none})"
    step "1/3 remove $MOD from $FROM"
    local NEWA
    NEWA=$(echo "$A" | tr ',' '\n' | grep -vx "$MOD" | paste -sd, -)
    _set_modules "$BASE/$FROM" "$NEWA"
    ok "$FROM modules: ${NEWA:-<none>}"
    step "2/3 add $MOD to $TO"
    local NEWB="$B"
    echo ",$B," | grep -q ",$MOD," || NEWB="${B:+$B,}$MOD"
    _set_modules "$BASE/$TO" "$NEWB"
    ok "$TO modules: $NEWB"
    step "3/3 report + verify (lazy boot ~1-2min)"
    self_report
    local IP2; IP2=$(agent_ip)
    local RES_T="--resolve api.$TO.isle:443:127.0.0.1"
    [ -n "$IP2" ] && [ "$IP2" != "10.10.0.2" ] && RES_T="--resolve api.$TO.isle:443:$IP2"
    local code i
    for i in $(seq 1 8); do
        code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 6 $RES_T "https://api.$TO.isle/api/health" 2>/dev/null)
        [ "$code" = 200 ] && break; sleep 5
    done
    [ "$code" = 200 ] && ok "$TO healthy after gaining $MOD" \
        || warn "$TO still booting (last: ${code:-none}) — it will answer at https://$TO.isle"
    echo
    ok "moved '$MOD': $FROM → $TO (module loading relocated; verify in the store/coherence)"
    echo "   NOTE: a stateful module's DATA stays in $FROM's volume — data migration is a separate step."
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
            rebase) shift 2; rebase "$@" ;;
            undeploy) shift 2; undeploy "$@" ;;
            *) echo "usage: isle polari instance [deploy [--name n] [--modules csv]|rebase <n> --domain d|undeploy <name>]" ;;
        esac ;;
    module)
        case "${2:-}" in
            move) shift 2; move_module "$@" ;;
            add) shift 2; add_module "$@" ;;
            *) echo "usage: isle polari module [move <m> --from A --to B|add <m> --to B]" ;;
        esac ;;
    app)
        case "${2:-}" in
            plan) shift 2; app_plan "$@" ;;
            ensure) shift 2; app_ensure "$@" ;;
            *) echo "usage: isle polari app [plan <app>|ensure <app> [--yes]]" ;;
        esac ;;
    instances) instances ;;
    *) echo "usage: isle polari [instance deploy|instance rebase|instance undeploy <n>|module move <m> --from A --to B|instances]" ;;
esac
