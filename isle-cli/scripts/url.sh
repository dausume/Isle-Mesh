#!/bin/bash
# url.sh — `isle url`: SELECTIVE WEB EXPOSURE (Dustin #2).
#
# THE CONTAINMENT RULE: .isle is ALWAYS internal — reached only
# through an agent from inside the isle; it is fully contained.
# "Exposing" NEVER publishes .isle itself: it grants an internal
# service an ADDITIONAL outside URL (this device's outside IP +
# a declared port) that GATEWAYS into the isle through the agent.
#
#   isle url expose <internal.isle> --port <p>   add an outside door
#   isle url unexpose --port <p>                 remove it
#   isle url exposures                           what is exposed where
#
# Each exposure = one tiny gateway container (isle-expose-<port>)
# publishing 0.0.0.0:<port> and proxying to THIS device's agent
# with the internal name's SNI/Host. Tracked in
# /etc/isle-mesh/exposures.json (the dependency row).
set -u
G="\033[0;32m"; Y="\033[1;33m"; R="\033[0;31m"; C="\033[0;36m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }
warn(){ echo -e "${Y}[WARN]${N} $*"; }
die(){ echo -e "${R}[FAIL]${N} $*"; exit 1; }

LEDGER=/etc/isle-mesh/exposures.json
EXPDIR=/etc/isle-mesh/exposures
ENTRYFLAG=/etc/isle-mesh/entrypoint.enabled

agent_name(){
    docker ps --format '{{.Names}}' 2>/dev/null \
        | grep -E '^isle-(vlan|remote)-agent$' | head -1
}

record(){ # $1 domain $2 port $3 action(add|del) [$4 user]
    sudo python3 - "$LEDGER" "$1" "$2" "$3" "${4:-}" <<'PYEOF'
import json, os, sys
path, domain, port, action, user = sys.argv[1:6]
data = {}
if os.path.exists(path):
    try:
        data = json.load(open(path))
    except Exception:
        data = {}
exp = data.setdefault("exposures", {})
if action == "add":
    exp[port] = {"internal": domain, "port": int(port),
                 "protocol": "http",
                 "access": {"level": 1, "user": user}}
else:
    exp.pop(port, None)
json.dump(data, open(path, "w"), indent=2)
PYEOF
}

# WHERE exposure may happen is REGULATED: only a device explicitly
# designated as an entrypoint may open outside doors.
entrypoint(){
    case "${1:-status}" in
        enable)
            sudo touch "$ENTRYFLAG" && ok "THIS device is now a designated web entrypoint" ;;
        disable)
            sudo rm -f "$ENTRYFLAG" && ok "entrypoint designation removed (existing doors stay until unexposed)" ;;
        status|*)
            [ -f "$ENTRYFLAG" ] && ok "this device IS a designated entrypoint" \
                || echo "not an entrypoint — outside doors refused here (isle url entrypoint enable)" ;;
    esac
}

expose(){
    local DOMAIN="${1:?usage: isle url expose <internal.isle> --port <p> --user <who>}"; shift
    local PORT="" AUSER="" APASS=""
    while [ $# -gt 0 ]; do case "$1" in
        --port) PORT="$2"; shift 2 ;;
        --user) AUSER="$2"; shift 2 ;;
        --password) APASS="$2"; shift 2 ;;
        *) shift ;;
    esac; done
    [ -f "$ENTRYFLAG" ] || die "this device is NOT a designated entrypoint — exposure is regulated (isle url entrypoint enable, a deliberate step)"
    [ -n "$PORT" ] || die "--port <p> required (the OUTSIDE port for this door)"
    # PORT-CONFLICT GUARD (the resource ledger, locally): refuse a
    # host port already published, and suggest a free one — so doors
    # scale without collision.
    if docker ps --format '{{.Ports}}' 2>/dev/null \
            | grep -qE "(^|[^0-9])0\.0\.0\.0:$PORT->|:::$PORT->"; then
        local FREE
        FREE=$(for p in $(seq 18080 18999); do
                 docker ps --format '{{.Ports}}' 2>/dev/null \
                   | grep -qE ":$p->" || { echo "$p"; break; }; done)
        die "host port $PORT is already published on this device — try --port ${FREE:-<free>}"
    fi
    # LEVEL 1 ACCESS: every door is limited to ONE person with
    # assigned credentials (level 2 = a keycloak GROUP, once KC is
    # an isle citizen). No credential-less doors.
    [ -n "$AUSER" ] || die "--user <who> required — a door admits exactly ONE credentialed person (level 1)"
    echo "$DOMAIN" | grep -qE '\.isle$' || die "expose maps an INTERNAL .isle name to an outside port"
    echo "$PORT" | grep -qE '^[0-9]{2,5}$' || die "bad port: $PORT"
    local AGENT; AGENT=$(agent_name)
    [ -n "$AGENT" ] || die "no agent on this device — exposure gateways INTO the isle through the agent"

    local DIR="$EXPDIR/$PORT"
    sudo mkdir -p "$DIR"
    # per-door credential (shown ONCE when generated)
    if [ -z "$APASS" ]; then
        APASS=$(openssl rand -base64 12 | tr -d "=+/" | cut -c1-16)
        GEN=1
    else
        GEN=0
    fi
    HASH=$(openssl passwd -apr1 "$APASS")
    printf '%s:%s\n' "$AUSER" "$HASH" | sudo tee "$DIR/htpasswd" >/dev/null
    sudo chmod 644 "$DIR/htpasswd"
    # gateway nginx: outside plain-http on <port> -> the agent over
    # TLS with the internal name's SNI (the isle stays contained;
    # only this declared door crosses the boundary)
    sudo tee "$DIR/nginx.conf" >/dev/null <<EOF
events {}
http {
  server {
    listen 80;
    auth_basic "isle door ($DOMAIN)";
    auth_basic_user_file /etc/nginx/htpasswd;
    location / {
      proxy_pass https://$AGENT;
      proxy_ssl_server_name on;
      proxy_ssl_name $DOMAIN;
      proxy_ssl_verify off;
      proxy_set_header Host $DOMAIN;
      proxy_set_header X-Forwarded-For \$remote_addr;
      proxy_set_header X-Forwarded-Proto http;
      proxy_http_version 1.1;
      proxy_set_header Upgrade \$http_upgrade;
      proxy_set_header Connection "upgrade";
    }
  }
}
EOF
    docker rm -f "isle-expose-$PORT" >/dev/null 2>&1
    docker run -d --name "isle-expose-$PORT" --restart unless-stopped \
        --network isle-agent-net \
        -p "0.0.0.0:$PORT:80" \
        -v "$DIR/nginx.conf:/etc/nginx/nginx.conf:ro" \
        -v "$DIR/htpasswd:/etc/nginx/htpasswd:ro" \
        nginx:alpine >/dev/null || die "gateway container failed"
    record "$DOMAIN" "$PORT" add "$AUSER"
    local IP; IP=$(hostname -I | awk '{print $1}')
    ok "outside door OPEN: http://$IP:$PORT -> (agent) -> $DOMAIN"
    ok "access LEVEL 1: only '$AUSER' (basic auth at the door)"
    if [ "$GEN" = 1 ]; then
        echo "   credential (shown ONCE — hand it to that one person):"
        echo "     user: $AUSER    password: $APASS"
    fi
    echo "   .isle stays contained — the outside sees only this host:port."
    echo "   close it: isle url unexpose --port $PORT"
}

unexpose(){
    local PORT=""
    while [ $# -gt 0 ]; do case "$1" in
        --port) PORT="$2"; shift 2 ;;
        *) shift ;;
    esac; done
    [ -n "$PORT" ] || die "--port <p> required"
    docker rm -f "isle-expose-$PORT" >/dev/null 2>&1 \
        && ok "gateway isle-expose-$PORT removed" \
        || warn "no gateway container for port $PORT"
    record "-" "$PORT" del
    sudo rm -rf "$EXPDIR/$PORT"
    ok "outside door on port $PORT closed"
}

exposures(){
    if [ -f "$LEDGER" ]; then
        python3 - "$LEDGER" <<'PYEOF'
import json, sys
data = json.load(open(sys.argv[1]))
exp = data.get("exposures", {})
if not exp:
    print("no outside doors — the isle is fully contained")
for port, e in sorted(exp.items(), key=lambda kv: int(kv[0])):
    acc = e.get("access", {})
    who = ("level %s: %s" % (acc.get("level", "?"),
                             acc.get("user") or acc.get("group", "?"))
           if acc else "OPEN (pre-level-1 door)")
    print("  :%s  ->  %s  (%s, %s)" % (port, e["internal"],
                                       e.get("protocol", "http"), who))
PYEOF
    else
        echo "no outside doors — the isle is fully contained"
    fi
    docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null \
        | grep '^isle-expose-' | sed 's/^/  live: /'
}

case "${1:-help}" in
    entrypoint) shift; entrypoint "$@" ;;
    expose) shift; expose "$@" ;;
    unexpose) shift; unexpose "$@" ;;
    exposures) exposures ;;
    *) echo "usage: isle url [entrypoint enable|disable|status] [expose <internal.isle> --port <p> --user <who>|unexpose --port <p>|exposures]" ;;
esac
