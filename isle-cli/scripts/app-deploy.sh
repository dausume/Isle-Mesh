#!/bin/bash
# app-deploy.sh — `isle app deploy`: an ARBITRARY compose app
# becomes an isle app in ONE command (the store's install pipeline,
# handoff §20).
#
#   isle app deploy <name> --compose <file> [--service <svc>]
#                   [--port <p>] [--domain <name>.isle]
#                   [--health-path /] [--protocol http] [--engine <kind>[@<url>]]
#                   [--staged] [--bind ISLE_DATASET_PATH=<abs dir> --dataset <ds>]
#
# --staged  deploy a storage-migrated hosting app WITHOUT publishing its domain
#           (no DNS; health by --resolve; the domain moves only in the core's
#           cutover operation).  --bind mounts a proven dataset dir read-only
#           at /data (storage-migration contract sections 2.2, 4).  --dataset names
#           the dataset whose `storage check` gates every compose up of a bound app.
#
# --engine business-ops            → https://<app-domain> (auto, Isle-wide)
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
COMPOSE=""; IMAGE=""; SERVICE=""; PORT=""; DOMAIN="$NAME.isle"; PROTOCOL="http"; ENGINE=""; HEALTH_PATH="/"; STAGED=0; BIND=""; DATASET=""
while [ $# -gt 0 ]; do case "$1" in
    --compose) COMPOSE="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --service) SERVICE="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    --protocol) PROTOCOL="$2"; shift 2 ;;
    --engine) ENGINE="$2"; shift 2 ;;
    --health-path) HEALTH_PATH="$2"; shift 2 ;;
    --staged) STAGED=1; shift ;;
    --bind) BIND="${2:-}"; shift 2 || die "--bind needs PARAM=PATH" ;;
    --dataset) DATASET="${2:-}"; shift 2 || die "--dataset needs a name" ;;
    *) die "unknown arg: $1" ;;
esac; done
python3 "$SCRIPT_DIR/health_probe.py" "$HEALTH_PATH" || die "invalid health path"
# --image <ref> synthesizes a one-service compose (the catalog's
# bare-image path); --compose is the file path otherwise.
if [ -z "$COMPOSE" ] && [ -n "$IMAGE" ]; then
    COMPOSE="$(mktemp --suffix=.yml)"
    SVC="${SERVICE:-$NAME}"
    printf 'services:\n  %s:\n    image: %s\n    restart: unless-stopped\n' \
        "$SVC" "$IMAGE" > "$COMPOSE"
fi
[ -n "$COMPOSE" ] && [ -f "$COMPOSE" ] || die "--compose <file> or --image <ref> required"

# HONEST TIER GATE: deploying here means serving behind THIS
# device's agent — without one the pipeline half-runs (container up,
# leaf/DNS fail, nothing proxies) and then reports success.
docker ps --format '{{.Names}}' 2>/dev/null | grep -qE '^isle-(vlan|remote)-agent$' \
    || die "no isle agent on this device — mesh-apps deploy behind a local agent.
       To host here: sudo isle onboard --host   (real hosting = isle join)"

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

# ---- --bind PARAM=PATH: only for a staged deploy or an app that already has a binding;
# the path is proven read-only by `storage_cli check-path` before anything is generated.
BIND_FILE="$APPS_DIR/$NAME/storage-bind.json"
BIND_PARAM=""; BIND_PATH=""; BIND_NEW=0
if [ -n "$BIND" ]; then
    [ "$STAGED" = 1 ] || [ -f "$BIND_FILE" ] || die "--bind is accepted only with --staged (or for an app that already has a storage binding)"
    BIND_PARAM="${BIND%%=*}"; BIND_PATH="${BIND#*=}"
    [ "$BIND_PARAM" != "$BIND" ] && [ "$BIND_PARAM" = ISLE_DATASET_PATH ] || die "--bind must be ISLE_DATASET_PATH=<absolute directory>"
    BIND_NEW=1
elif [ -f "$BIND_FILE" ]; then
    # A redeploy keeps the binding: dropping the mount silently would put the app back on the root filesystem.
    BIND_PARAM=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["param"])' "$BIND_FILE") || die "unreadable $BIND_FILE"
    BIND_PATH=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["path"])' "$BIND_FILE") || die "unreadable $BIND_FILE"
fi
if [ "$BIND_NEW" != 1 ] && [ -z "$DATASET" ] && [ -f "$BIND_FILE" ]; then
    DATASET=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("dataset",""))' "$BIND_FILE") || die "unreadable $BIND_FILE"
fi
BIND_ENV=()
if [ -n "$BIND_PATH" ]; then
    python3 "$SCRIPT_DIR/storage_cli.py" check-path --path "$BIND_PATH" >/dev/null \
        || die "storage path $BIND_PATH is not a proven separate mounted directory: $(python3 "$SCRIPT_DIR/storage_cli.py" check-path --path "$BIND_PATH" 2>&1 | head -c 400)"
    BIND_ENV=("$BIND_PARAM=$BIND_PATH")
    # Contract section 4: every launch path runs `storage check` before compose up when the app has a storage binding.
    [[ "$DATASET" =~ ^[a-z0-9][a-z0-9-]{0,63}$ ]] || die "a storage-bound app needs its dataset for the storage check: pass --dataset <ds>"
    # --staged: the migrate-time deploy runs before mark-migrated, so the placement may still be `authorized`.
    CHECK_ARGS=(--dataset "$DATASET"); [ "$STAGED" != 1 ] || CHECK_ARGS+=(--staged)
    python3 "$SCRIPT_DIR/storage_cli.py" check "${CHECK_ARGS[@]}" >/dev/null \
        || die "storage check failed for dataset $DATASET; refusing to start $NAME: $(python3 "$SCRIPT_DIR/storage_cli.py" check "${CHECK_ARGS[@]}" 2>&1 | head -c 600)"
fi

echo "deploying '$NAME' -> https://$DOMAIN (service $SERVICE:$PORT, ${#SERVICES[@]} service(s))"

sudo python3 "$SCRIPT_DIR/member_preflight.py" --hosting --migrate || die "membership/hosting preflight failed"

# Resolve the service identity before launching containers. Members use the
# authenticated enrollment; only the CA host can issue a leaf locally.
if [[ "$DOMAIN" == *.isle ]]; then
    sudo bash "$SCRIPT_DIR/certs.sh" ensure "$DOMAIN" \
        || die "$DOMAIN certificate not ready; app containers have not been changed"
fi

source "$SCRIPT_DIR/offline-runtime.sh"
OFFLINE_ARGS=()
if isle_offline; then
    refs=$(docker compose -f "$COMPOSE" config --images) || die "invalid offline compose configuration"
    [ -n "$refs" ] || die "offline images/: application has no bundled images"
    while IFS= read -r ref; do
        isle_prepare_offline_app_image "$ref" || die "offline images/: app image not available from verified supply"
    done <<< "$refs"
    OFFLINE_ARGS=(--no-build --pull never)
fi


# ---- 1. compose up with the auto-isle overlay
sudo mkdir -p "$APPS_DIR/$NAME"
sudo cp "$COMPOSE" "$APPS_DIR/$NAME/docker-compose.yml"
OVERLAY_ARGS=()
[ -z "$BIND_PATH" ] || OVERLAY_ARGS=(--service "$SERVICE" --bind-path "$BIND_PATH")
python3 "$SCRIPT_DIR/app_overlay.py" "$COMPOSE" "${OVERLAY_ARGS[@]+"${OVERLAY_ARGS[@]}"}" \
    | sudo tee "$APPS_DIR/$NAME/isle-overlay.yml" > /dev/null || die "overlay generation failed"
[ "${PIPESTATUS[0]}" = 0 ] || die "overlay generation failed"
(cd "$APPS_DIR/$NAME" && sudo env "${BIND_ENV[@]+"${BIND_ENV[@]}"}" docker compose -p "isle-$NAME" \
    -f docker-compose.yml -f isle-overlay.yml up -d "${OFFLINE_ARGS[@]}") \
    || die "compose up failed"
ok "containers up (project isle-$NAME, attached to isle-agent-net)"
if [ "$BIND_NEW" = 1 ]; then
    printf '{"param":"%s","path":"%s","dataset":"%s"}\n' "$BIND_PARAM" "$BIND_PATH" "$DATASET" | sudo tee "$BIND_FILE.tmp" >/dev/null \
        && sudo mv -f "$BIND_FILE.tmp" "$BIND_FILE" || die "could not record $BIND_FILE"
    ok "storage bound read-only: $BIND_PATH -> /data ($BIND_FILE)"
fi

# ---- resolve the primary container name
CONTAINER=$(sudo env "${BIND_ENV[@]+"${BIND_ENV[@]}"}" docker compose -p "isle-$NAME" -f "$APPS_DIR/$NAME/docker-compose.yml" -f "$APPS_DIR/$NAME/isle-overlay.yml" ps --format '{{.Name}}' "$SERVICE" 2>/dev/null | head -1)
[ -n "$CONTAINER" ] || CONTAINER="isle-$NAME-$SERVICE-1"

# ---- 2+3. register (leaf issuance rides the hook)
sudo bash "$AGENT_MANAGER" register --name "$NAME" --domain "$DOMAIN" \
    --container "$CONTAINER" --port "$PORT" --protocol "$PROTOCOL" \
    || die "agent registration failed"

# Make the new local app reachable from the core desktop immediately.
# This edits only the owned hosts block; Wi-Fi and resolver settings are untouched.
sudo bash "$SCRIPT_DIR/hosts-reconcile.sh" || die "core-local name registration failed"

# ---- 4. router DNS (agent's isle IP)
source "$SCRIPT_DIR/agent-address.sh"
AGENT_IP=$(isle_agent_address) || AGENT_IP=""
DNS_OK=0
[ -n "${AGENT_IP:-}" ] || die "No verified Isle-facing agent address; registration retained for retry"
if [ "$STAGED" = 1 ]; then
    echo "staged: domain not published; cutover is a separate core operation"
else
    # A managed (storage-migrated) domain owned by ANOTHER device is never republished from here.
    VIEW=$(mktemp)
    curl --http1.1 -sf --max-time 8 "https://api.polari.isle/api/islemesh/storage/placement?domain=$DOMAIN" > "$VIEW" 2>/dev/null || : > "$VIEW"
    python3 "$SCRIPT_DIR/storage_cli.py" guard-domain --domain "$DOMAIN" --device "$(python3 "$SCRIPT_DIR/names.py" current-device)" --view "$VIEW" \
        || { rm -f "$VIEW"; die "refusing to publish $DOMAIN (see above); the owner moves it through the core cutover operation"; }
    rm -f "$VIEW"
    sudo python3 "$SCRIPT_DIR/dns_publication.py" "$DOMAIN" "$AGENT_IP" || die "core DNS publication pending; retry shared operation"
    DNS_OK=1
fi

# ---- 5. engine declaration (what this app provides to polari)
# --engine <kind>            → URL auto-derived: https://<app-domain>
# --engine <kind>@<url>      → explicit URL (no shell-hostile <> chars)
if [ -n "$ENGINE" ]; then
    if [ "${ENGINE#*@}" != "$ENGINE" ]; then
        KIND="${ENGINE%%@*}"; EURL="${ENGINE#*@}"
    else
        # Consumers may run on another member. Docker service/container names
        # are local to this host; use the published, certified Isle identity.
        KIND="$ENGINE"; EURL="https://$DOMAIN"
    fi
    python3 - "$KIND" "$EURL" "$CONTAINER" <<'PYEOF' | sudo tee "$APPS_DIR/$NAME/engine.json" > /dev/null
import json, sys
kind, url, container = sys.argv[1:4]
print(json.dumps({'provides': kind, 'url': url, 'container': container}, indent=1))
PYEOF
    ok "engine declared: $KIND ($EURL) — polari provider wiring reads this"
fi

echo
if [ "$STAGED" = 1 ]; then
    ok "'$NAME' is staged behind this device's agent (${AGENT_IP:-ip pending}); https://$DOMAIN is NOT published until the core cutover"
elif [ "$DNS_OK" = 1 ]; then
    ok "'$NAME' is an isle app: https://$DOMAIN (cert issued, DNS live)"
else
    ok "'$NAME' deploys behind this device's agent (${AGENT_IP:-ip pending}) — https://$DOMAIN once the router's join-protocol maps it"
fi
echo "   undeploy: isle app undeploy $NAME"

# ---- 6. self-report: tell the isle's polari what THIS device runs
# (instance tracking — how many duplicates exist, on which devices)
REG=/etc/isle-mesh/agent/registry.json
if [ -f "$REG" ]; then
    # canonical device name (same normalization as the core pusher)
    # + device facts so coherence knows the agent state
    HOSTN=$(python3 "$SCRIPT_DIR/names.py" current-device)
    AGENTN=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -cE '^isle-(vlan|remote)-agent$' || true)
    STAGED_JSON=""; [ "$STAGED" != 1 ] || STAGED_JSON=',"staged":true'
    printf '{"device":"%s","facts":{"machine_name":"%s","agent_present":%s%s}%s}' \
        "$HOSTN" "$HOSTN" "$([ "${AGENTN:-0}" -gt 0 ] && echo true || echo false)" "${AGENT_IP:+,\"agent_address\":\"$AGENT_IP\"}" "$STAGED_JSON" \
        | curl --http1.1 -sf --max-time 8 -X POST -H "Content-Type: application/json" \
            --data-binary @- https://api.polari.isle/api/islemesh/ingest/device >/dev/null 2>&1 || true
    RPT=$(mktemp)
    python3 - "$HOSTN" "$REG" "$STAGED" > "$RPT" 2>/dev/null <<'PYEOF'
import json, sys
host, reg = sys.argv[1], sys.argv[2]
report = {'device': host, 'registry': json.load(open(reg))}
if sys.argv[3] == '1':
    report['staged'] = True
print(json.dumps(report))
PYEOF
    if [ -s "$RPT" ] && curl --http1.1 -sf --max-time 8 -X POST -H "Content-Type: application/json" \
            --data-binary @"$RPT" https://api.polari.isle/api/islemesh/ingest/registry >/dev/null 2>&1; then
        ok "instances reported to the isle topology"
    else
        warn "topology report skipped (api.polari.isle unreachable)"
    fi
    rm -f "$RPT"
fi

# Do not report installation success until the actual certified route works.
# The registry/DNS relay may need a short interval after a remote registration.
bash "$SCRIPT_DIR/certs.sh" reload || die "hosting agent reload failed"
READY=0
RESOLVE_ARGS=()
if [ "$STAGED" = 1 ]; then
    # Staged = no DNS yet, so probe by --resolve. On a member the agent's Isle-facing address is a macvlan child of
    # this host's NIC and the host can never reach its own macvlan child (No route to host); remotes can. Probe via
    # the agent's isle-agent-net bridge address instead (same lookup and checks as hosts-reconcile.sh), and fall back
    # to AGENT_IP only when no verified bridge address exists (e.g. a non-macvlan agent).
    PROBE_IP=$(sudo -n docker inspect isle-remote-agent --format '{{(index .NetworkSettings.Networks "isle-agent-net").IPAddress}}' 2>/dev/null) || PROBE_IP=""
    PROBE_DRIVER=$(sudo -n docker network inspect isle-agent-net --format '{{.Driver}}' 2>/dev/null) || PROBE_DRIVER=""
    if [ "$PROBE_DRIVER" != bridge ] || ! python3 - "$PROBE_IP" <<'PYADDR'
import ipaddress, sys
try:
    a = ipaddress.ip_address(sys.argv[1])
    assert a.version == 4 and not (a.is_loopback or a.is_unspecified or a.is_multicast or a.is_link_local)
except (ValueError, AssertionError):
    sys.exit(1)
PYADDR
    then
        PROBE_IP="$AGENT_IP"
    fi
    RESOLVE_ARGS=(--resolve "$DOMAIN:443:$PROBE_IP")
fi
for attempt in {1..12}; do
    if curl "${RESOLVE_ARGS[@]+"${RESOLVE_ARGS[@]}"}" --http1.1 --fail --silent --show-error --connect-timeout 2 --max-time 4 "https://$DOMAIN$HEALTH_PATH" -o /dev/null 2>/dev/null; then
        READY=1; break
    fi
    sleep 2
done
[ "$READY" = 1 ] || die "installed files retained, but https://$DOMAIN is not healthy yet; retry this same instance after checking its route and service logs"
python3 "$SCRIPT_DIR/capabilities.py" status --publish https://api.polari.isle || warn "app is reachable, but resource publication needs retry"
ok "Deployment verified: $NAME on $(python3 "$SCRIPT_DIR/names.py" current-device), trusted HTTPS https://$DOMAIN"

# Generic registration is part of deployment success, not a manual ingest.
sudo python3 "$SCRIPT_DIR/engine_sync.py" || die "engine registry reconciliation failed; retry the shared operation"
