#!/bin/bash
# module.sh — `isle module`: install polari MODULES into the local
# instance (handoff §32). Until the module-.deb repo lands (mac-8),
# this drives the polari topology assign path over the isle API so a
# module becomes enabled on the reachable polari instance — so
# "install a module" works from the store today.
#
#   isle module install <name>     enable <name> on the polari instance
#   isle module list               modules the instance knows
set -u
API="${POLARI_ISLE_API:-https://api.polari.isle}"
CURL="curl -skf"
INSTANCE="${POLARI_ISLE_INSTANCE:-prf-isle}"
G="\033[0;32m"; Y="\033[1;33m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }
warn(){ echo -e "${Y}[WARN]${N} $*"; }

case "${1:-help}" in
    install)
        MOD="${2:?usage: isle module install <name>}"
        # module .deb path first (if published), else topology assign
        if apt-cache show "polari-module-$MOD" >/dev/null 2>&1; then
            sudo apt-get install -y "polari-module-$MOD" && ok "installed polari-module-$MOD"
        else
            warn "no polari-module-$MOD .deb yet — assigning via topology"
            RESP=$($CURL -X POST -H "Content-Type: application/json" \
                -d "{\"name\":\"$MOD\",\"instance\":\"$INSTANCE\"}" \
                "$API/api/topology/assign" 2>/dev/null || true)
            if echo "$RESP" | grep -q '"ok"'; then
                ok "module $MOD assigned to $INSTANCE (redeploy to load: pol dev deploy backend)"
            else
                warn "assign via API failed — enable it in the instance's POLARI_MODULES"
                echo "  (module install as a .deb arrives with mac-8's module repo)"
            fi
        fi
        ;;
    list)
        $CURL "$API/api/islemesh" >/dev/null 2>&1 && \
            echo "modules the store can offer live in the catalog: isle store list" || \
            echo "polari instance not reachable"
        ;;
    *) echo "usage: isle module [install <name>|list]" ;;
esac
