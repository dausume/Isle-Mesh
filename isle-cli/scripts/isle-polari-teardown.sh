#!/bin/bash
# isle-polari-teardown.sh — FULL, reproducible teardown of the isle
# polari deployment (handoff §25.2). Brings down prf-isle + every
# store-deployed app + their registry/DNS/cert artifacts, so the
# isle returns to a clean pre-deployment state. Idempotent; narrates
# every step; --keep-data preserves prf-isle's sqlite volume.
#
#   isle-polari-teardown.sh [--keep-data] [--apps-only]
set -u
KEEP_DATA=0; APPS_ONLY=0
while [ $# -gt 0 ]; do case "$1" in
    --keep-data) KEEP_DATA=1; shift ;;
    --apps-only) APPS_ONLY=1; shift ;;
    *) echo "unknown arg: $1"; exit 1 ;;
esac; done
G="\033[0;32m"; Y="\033[1;33m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }
step(){ echo -e "${Y}==>${N} $*"; }

AGENT_MANAGER=/usr/share/isle-mesh/isle-agent/scripts/agent-manager.sh
[ -f "$AGENT_MANAGER" ] || AGENT_MANAGER=$HOME/Isle-Mesh/isle-agent/scripts/agent-manager.sh

# ---- 1. store-deployed apps (everything under /etc/isle-mesh/apps)
step "store-deployed apps"
if [ -d /etc/isle-mesh/apps ]; then
    for dir in /etc/isle-mesh/apps/*/; do
        [ -d "$dir" ] || continue
        app=$(basename "$dir")
        docker compose -p "isle-$app" \
            -f "$dir/docker-compose.yml" -f "$dir/isle-overlay.yml" \
            down 2>/dev/null || true
        sudo bash "$AGENT_MANAGER" unregister --name "$app" 2>/dev/null || true
        # cert + DNS for its domain(s)
        dom="$app.isle"
        sudo isle dns unregister "$dom" 2>/dev/null || true
        sudo rm -f /etc/isle-mesh/agent/ssl/certs/"$dom".crt \
                   /etc/isle-mesh/agent/ssl/keys/"$dom".key 2>/dev/null || true
        sudo rm -rf "$dir"
        ok "removed app: $app"
    done
else
    ok "no store-deployed apps"
fi

if [ "$APPS_ONLY" = 1 ]; then
    docker exec isle-vlan-agent sh -c "nginx -t >/dev/null 2>&1 && kill -HUP 1" 2>/dev/null || true
    ok "apps-only teardown done (prf-isle left running)"
    exit 0
fi

# ---- 2. prf-isle itself
step "prf-isle stack"
if [ -d "$HOME/polari-isle" ]; then
    if [ "$KEEP_DATA" = 1 ]; then
        (cd "$HOME/polari-isle" && docker compose down 2>/dev/null) || true
        ok "prf-isle down (sqlite volume KEPT)"
    else
        (cd "$HOME/polari-isle" && docker compose down -v 2>/dev/null) || true
        ok "prf-isle down + volume removed"
    fi
    for dom in polari.isle api.polari.isle; do
        sudo bash "$AGENT_MANAGER" unregister --name "${dom%%.*}" 2>/dev/null || true
        sudo isle dns unregister "$dom" 2>/dev/null || true
        sudo rm -f /etc/isle-mesh/agent/ssl/certs/"$dom".crt \
                   /etc/isle-mesh/agent/ssl/keys/"$dom".key 2>/dev/null || true
    done
    # the pusher timer feeding prf-isle
    sudo systemctl disable --now polari-isle-push.timer 2>/dev/null || true
    ok "polari.isle / api.polari.isle deregistered; pusher stopped"
else
    ok "no ~/polari-isle deployment"
fi

# ---- 3. reload agent so the removed vhosts vanish
docker exec isle-vlan-agent sh -c "nginx -t >/dev/null 2>&1 && kill -HUP 1" 2>/dev/null || true
ok "agent reloaded — isle back to pre-polari state"
echo
echo "trust root + isle CA left in place (device-level, not per-deploy)."
echo "redeploy: isle-polari-deploy.sh"
