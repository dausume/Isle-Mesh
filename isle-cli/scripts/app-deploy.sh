#!/bin/bash
# app-deploy.sh — `isle app deploy`: an ARBITRARY compose app
# becomes an isle app in ONE command (the store's install pipeline,
# handoff §20).
#
#   isle app deploy <name> --compose <file> [--service <svc>]
#                   [--port <p>] [--domain <name>.isle]
#                   [--protocol http] [--engine <kind>[@<url>]]
#
# --engine business-ops            → http://<container>:<port> (auto)
# --engine business-ops@http://x   → explicit url (no <> chars)
#
# Pipeline (each step an existing isle capability — this verb only
# sequences them):
#   1. compose up with an OVERLAY that attaches every service to
#      isle-agent-net (auto-isle: the app keeps its own topology,
#      the agent can reach it)
#   2. agent registry entry (primary service) → fragment generation
#   3. leaf issuance fires via the registration hook (isle certs)
#   4. .isle DNS on the router
#   5. optional --engine records what this app PROVIDES so polari
#      can wire it as an engine (provider-row material)
#
# Single primary service per app for now (registry shape) — the
# multi-service upgrade is the recorded converter gap.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_MANAGER="$SCRIPT_DIR/../../isle-agent/scripts/agent-manager.sh"
APPS_DIR=/etc/isle-mesh/apps
G="\033[0;32m"; Y="\033[1;33m"; R="\033[0;31m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }
warn(){ echo -e "${Y}[WARN]${N} $*"; }
die(){ echo -e "${R}[FAIL]${N} $*"; exit 1; }

NAME="${1:-}"; shift || true
[ -n "$NAME" ] && [ "${NAME#-}" = "$NAME" ] || die "usage: isle app deploy <name> --compose <file> [--service <svc>] [--port <p>] [--domain <d>] [--engine <kind>=<url>]"
COMPOSE=""; IMAGE=""; SERVICE=""; PORT=""; DOMAIN="$NAME.isle"; PROTOCOL="http"; ENGINE=""
while [ $# -gt 0 ]; do case "$1" in
    --compose) COMPOSE="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --service) SERVICE="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    --protocol) PROTOCOL="$2"; shift 2 ;;
    --engine) ENGINE="$2"; shift 2 ;;
    *) die "unknown arg: $1" ;;
esac; done
# --image <ref> synthesizes a one-service compose (the catalog's
# bare-image path); --compose is the file path otherwise.
if [ -z "$COMPOSE" ] && [ -n "$IMAGE" ]; then
    COMPOSE="$(mktemp --suffix=.yml)"
    SVC="${SERVICE:-$NAME}"
    printf 'services:\n  %s:\n    image: %s\n    restart: unless-stopped\n' \
        "$SVC" "$IMAGE" > "$COMPOSE"
fi
[ -n "$COMPOSE" ] && [ -f "$COMPOSE" ] || die "--compose <file> or --image <ref> required"

# ---- parse services (first service = default primary)
mapfile -t SERVICES < <(python3 - "$COMPOSE" <<'PYEOF'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
for name in (d.get('services') or {}):
    print(name)
PYEOF
)
[ ${#SERVICES[@]} -gt 0 ] || die "no services in $COMPOSE"
[ -n "$SERVICE" ] || SERVICE="${SERVICES[0]}"
printf '%s\n' "${SERVICES[@]}" | grep -qx "$SERVICE" || die "service '$SERVICE' not in compose (${SERVICES[*]})"
if [ -z "$PORT" ]; then
    PORT=$(python3 - "$COMPOSE" "$SERVICE" <<'PYEOF'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
svc = (d.get('services') or {}).get(sys.argv[2]) or {}
# expose beats ports (we reach it on the app network, not the host)
for e in (svc.get('expose') or []):
    print(str(e).split('/')[0]); sys.exit()
for p in (svc.get('ports') or []):
    s = str(p)
    print(s.split(':')[-1].split('/')[0]); sys.exit()
print(80)
PYEOF
)
fi

echo "deploying '$NAME' -> https://$DOMAIN (service $SERVICE:$PORT, ${#SERVICES[@]} service(s))"

# ---- 1. compose up with the auto-isle overlay
sudo mkdir -p "$APPS_DIR/$NAME"
sudo cp "$COMPOSE" "$APPS_DIR/$NAME/docker-compose.yml"
python3 - "$COMPOSE" <<'PYEOF' | sudo tee "$APPS_DIR/$NAME/isle-overlay.yml" > /dev/null
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
overlay = {'services': {}, 'networks': {'isle-agent-net': {'external': True}}}
for name in (d.get('services') or {}):
    overlay['services'][name] = {'networks': ['isle-agent-net']}
    # keep the app's own networks too, when it declares any
    own = (d.get('services') or {}).get(name, {}).get('networks')
    if own:
        nets = own if isinstance(own, list) else list(own)
        overlay['services'][name]['networks'] = sorted(set(nets + ['isle-agent-net']))
print(yaml.safe_dump(overlay, default_flow_style=False))
PYEOF
(cd "$APPS_DIR/$NAME" && sudo docker compose -p "isle-$NAME" \
    -f docker-compose.yml -f isle-overlay.yml up -d) \
    || die "compose up failed"
ok "containers up (project isle-$NAME, attached to isle-agent-net)"

# ---- resolve the primary container name
CONTAINER=$(sudo docker compose -p "isle-$NAME" -f "$APPS_DIR/$NAME/docker-compose.yml" -f "$APPS_DIR/$NAME/isle-overlay.yml" ps --format '{{.Name}}' "$SERVICE" 2>/dev/null | head -1)
[ -n "$CONTAINER" ] || CONTAINER="isle-$NAME-$SERVICE-1"

# ---- 2+3. register (leaf issuance rides the hook)
sudo bash "$AGENT_MANAGER" register --name "$NAME" --domain "$DOMAIN" \
    --container "$CONTAINER" --port "$PORT" --protocol "$PROTOCOL" \
    || die "agent registration failed"

# ---- 4. router DNS (agent's isle IP)
AGENT_IP=$(sudo docker inspect isle-vlan-agent --format '{{(index .NetworkSettings.Networks "isle-br-0").IPAddress}}' 2>/dev/null)
if [ -n "${AGENT_IP:-}" ]; then
    sudo /usr/local/bin/isle dns register "$DOMAIN" "$AGENT_IP" >/dev/null 2>&1 \
        && ok ".isle DNS: $DOMAIN -> $AGENT_IP" \
        || warn "DNS registration failed (router down?) — isle dns register $DOMAIN $AGENT_IP"
else
    warn "agent has no isle-br-0 IP — DNS skipped"
fi

# ---- 5. engine declaration (what this app provides to polari)
# --engine <kind>            → URL auto-derived: http://<container>:<port>
# --engine <kind>@<url>      → explicit URL (no shell-hostile <> chars)
if [ -n "$ENGINE" ]; then
    if [ "${ENGINE#*@}" != "$ENGINE" ]; then
        KIND="${ENGINE%%@*}"; EURL="${ENGINE#*@}"
    else
        KIND="$ENGINE"; EURL="http://$CONTAINER:$PORT"
    fi
    python3 - "$KIND" "$EURL" "$CONTAINER" <<'PYEOF' | sudo tee "$APPS_DIR/$NAME/engine.json" > /dev/null
import json, sys
kind, url, container = sys.argv[1:4]
print(json.dumps({'provides': kind, 'url': url, 'container': container}, indent=1))
PYEOF
    ok "engine declared: $KIND ($EURL) — polari provider wiring reads this"
fi

echo
ok "'$NAME' is an isle app: https://$DOMAIN (cert issued, DNS live)"
echo "   undeploy: isle app undeploy $NAME"
