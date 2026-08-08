#!/bin/bash
# store.sh — the general isle app store, host side (isle store).
#
# The CATALOG lives in polari (rows + install-plan at
# /api/islemesh/catalog); this verb browses it and RUNS the plan on
# the host — the two proven variants converge here: a catalog
# "install" dispatches to `isle app deploy` (mesh-app) or the
# shared-shell launcher build (polari-app). Nothing new deploys in
# the backend; the store proposes, the host executes (knobs +
# mover-on-host).
#
#   isle store list                 browse published entries
#   isle store show <name>          entry detail + its install plan
#   isle store install <name>       run the plan (asks first)
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API="${POLARI_ISLE_API:-https://api.polari.isle}"
CURL="curl -sk --resolve api.polari.isle:443:127.0.0.1"
G="\033[0;32m"; Y="\033[1;33m"; C="\033[0;36m"; N="\033[0m"

api_get() { $CURL "$API$1" 2>/dev/null; }

case "${1:-list}" in
    list)
        api_get /api/islemesh/catalog | python3 -c '
import json, sys
d = json.load(sys.stdin)
if not d.get("ok"): print("catalog unavailable"); sys.exit(1)
print("Isle app store — %d apps\n" % d["count"])
for e in d["entries"]:
    eng = "  [engine: %s]" % e["provides_engine"] if e["provides_engine"] else ""
    print("  \033[0;36m%-12s\033[0m %-13s %s%s" % (
        e["name"], "("+e["kind"]+")", e["title"], eng))
    print("               %s" % e["description"])
' || echo "no catalog (is prf-isle up?)"
        ;;
    show)
        NAME="${2:?usage: isle store show <name>}"
        api_get "/api/islemesh/catalog/$NAME" | python3 -c '
import json, sys
d = json.load(sys.stdin)
if not d.get("ok"): print(d.get("error","not found")); sys.exit(1)
e = d["entry"]; p = e["install_plan"]
print("%s — %s" % (e["title"], e["kind"]))
print(e["description"]); print()
print("source: %s" % e["source_ref"])
if e["provides_engine"]: print("provides engine: %s" % e["provides_engine"])
print("\ninstall plan (%s):" % p["note"])
for s in p["steps"]: print("  $ %s" % s)
'
        ;;
    install)
        NAME="${2:?usage: isle store install <name>}"
        RESP=$(api_get "/api/islemesh/catalog/$NAME")
        echo "$RESP" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("ok") else 1)' || { echo "no such entry: $NAME"; exit 1; }
        echo -e "${Y}Install '$NAME' — will run on THIS host:${N}"
        echo "$RESP" | python3 -c 'import json,sys; [print("  $ "+s) for s in json.load(sys.stdin)["entry"]["install_plan"]["steps"]]'
        if [ "${3:-}" != "--yes" ]; then
            read -r -p "Proceed? [y/N] " a; [ "$a" = y ] || [ "$a" = Y ] || { echo aborted; exit 1; }
        fi
        echo "$RESP" | python3 -c 'import json,sys; [print(s) for s in json.load(sys.stdin)["entry"]["install_plan"]["steps"]]' | while IFS= read -r step; do
            echo -e "${C}==> $step${N}"
            # steps are isle/apt commands; run through the shell
            eval "$step" || { echo "step failed: $step"; exit 1; }
        done
        echo -e "${G}installed $NAME${N}"
        ;;
    *) echo "usage: isle store [list|show <name>|install <name> [--yes]]"; exit 1 ;;
esac
