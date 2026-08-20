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
#   isle store uninstall <name>     remove THIS device's copy
#                                   (--purge = also erase its data,
#                                    backed up first — unin-4)
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API="${POLARI_ISLE_API:-https://api.polari.isle}"
CURL="curl -skf --max-time 8"
G="\033[0;32m"; Y="\033[1;33m"; C="\033[0;36m"; R="\033[0;31m"; N="\033[0m"

api_get() { $CURL "$API$1" 2>/dev/null && return 0; $CURL --resolve api.polari.isle:443:127.0.0.1 "$API$1" 2>/dev/null; }

case "${1:-list}" in
    list)
        api_get /api/islemesh/catalog | python3 -c '
import json, sys
d = json.load(sys.stdin)
if not d.get("ok"): print("catalog unavailable"); sys.exit(1)
print("Isle app store — %d apps\n" % d["count"])
import subprocess
for e in d["entries"]:
    eng = "  [engine: %s]" % e["provides_engine"] if e["provides_engine"] else ""
    marks = []
    # running instances across the isle (the tracking half of
    # chosen duplicates — scaling is a genuine need)
    inst = e.get("instances", [])
    if inst:
        devs = sorted({i["device"] or "?" for i in inst})
        marks.append("runs x%d on %s" % (len(inst), ", ".join(devs)))
    # native launcher installed on THIS device (+ version)
    if e["kind"] == "polari-app":
        try:
            ver = subprocess.run(
                ["dpkg-query", "-W", "-f", "${Version}",
                 "isle-app-%s" % e["name"]],
                capture_output=True, text=True, timeout=5)
            if ver.returncode == 0 and ver.stdout.strip():
                marks.append("installed here v%s" % ver.stdout.strip())
        except Exception:
            pass
    mark = "  [%s]" % "; ".join(marks) if marks else ""
    print("  \033[0;36m%-12s\033[0m %-13s %s%s%s" % (
        e["name"], "("+e["kind"]+")", e["title"], eng, mark))
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
        # MEMBERSHIP GATE (Dustin: the whole point) — the store only
        # installs on a device that is a member of a valid isle: a
        # running agent connecting it. EVERY kind, not just mesh-apps
        # (a mesh-app would half-deploy; a polari-app on a non-member
        # is a launcher into an isle this device isn't part of).
        if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qE '^isle-(vlan|remote)-agent$'; then
            echo -e "${R}[FAIL]${N} this device has no isle agent — it is neither an isle core nor a member."
            echo "       The store installs apps onto isle devices only. Either:"
            echo "         make THIS device its own isle (single-device isles are valid):"
            echo "           sudo isle core-install              # full core; or just: isle create"
            echo "         or join an existing isle:"
            echo "           sudo isle onboard --host            # best-effort today; real join = isle join"
            exit 1
        fi
        KIND=$(echo "$RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin)["entry"].get("kind",""))' 2>/dev/null)
        # ---- instance awareness: duplicates are a CHOICE ----------
        DUP_NAME=""
        if [ "$KIND" = "mesh-app" ]; then
            N_INST=$(echo "$RESP" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["entry"].get("instances",[])))' 2>/dev/null || echo 0)
            if [ "${N_INST:-0}" -gt 0 ]; then
                echo -e "${Y}'$NAME' already runs on the isle:${N}"
                echo "$RESP" | python3 -c 'import json,sys; [print("    %s on %s (%s)" % (i["app"], i["device"] or "?", i["domain"] or "-")) for i in json.load(sys.stdin)["entry"].get("instances",[])]'
                DUP_NAME=$(echo "$RESP" | python3 -c '
import json, sys
e = json.load(sys.stdin)["entry"]
taken = {i["app"] for i in e.get("instances", [])}
n = 2
while "%s-%d" % (e["name"], n) in taken: n += 1
print("%s-%d" % (e["name"], n))')
                echo -e "    installing here creates a ${Y}DUPLICATE${N} instance: $DUP_NAME (${DUP_NAME}.isle)"
            fi
        elif [ "$KIND" = "polari-app" ]; then
            VER=$(dpkg-query -W -f '${Version}' "isle-app-$NAME" 2>/dev/null || true)
            [ -n "$VER" ] && echo -e "${Y}already installed on this device: isle-app-$NAME v$VER — this reinstalls/upgrades it${N}"
        fi
        echo -e "${Y}Install '$NAME' — will run on THIS host:${N}"
        echo "$RESP" | python3 -c 'import json,sys; [print("  $ "+s) for s in json.load(sys.stdin)["entry"]["install_plan"]["steps"]]'
        if [ "${3:-}" != "--yes" ]; then
            read -r -p "Proceed? [y/N] " a; [ "$a" = y ] || [ "$a" = Y ] || { echo aborted; exit 1; }
        fi
        echo "$RESP" | python3 -c 'import json,sys; [print(s) for s in json.load(sys.stdin)["entry"]["install_plan"]["steps"]]' | while IFS= read -r step; do
            if [ -n "$DUP_NAME" ]; then
                # a duplicate deploys under its OWN name + domain
                step=$(echo "$step" | sed "s/isle app deploy $NAME /isle app deploy $DUP_NAME /")
                case "$step" in
                    *"isle app deploy"*"--domain"*) ;;
                    *"isle app deploy"*) step="$step --domain ${DUP_NAME}.isle" ;;
                esac
            fi
            echo -e "${C}==> $step${N}"
            # steps are isle/apt commands; run through the shell
            eval "$step" || { echo "step failed: $step"; exit 1; }
        done
        echo -e "${G}installed ${DUP_NAME:-$NAME}${N}"
        ;;
    uninstall)
        # unin-4: the per-app teardown verb — the ONE engine step the
        # store UI's Uninstall buttons run via pkexec (zero teardown
        # logic in the UI). Scope is THIS DEVICE only: the mesh-app
        # deployment here and/or the launcher deb here. Debian
        # semantics: plain uninstall PRESERVES data (volumes + the
        # app's /etc/isle-mesh/apps config survive; reinstalling
        # picks them up); --purge erases them, BACKED UP FIRST
        # (Q1 policy — same backup-then-delete as `isle uninstall
        # --everything`, per-app scale).
        NAME="${2:?usage: isle store uninstall <name> [--purge] [--yes]}"
        echo "$NAME" | grep -qE '^[a-z0-9][a-z0-9-]{0,63}$' || { echo "not an app name: $NAME"; exit 1; }
        PURGE=false; YES=false
        for a in "${@:3}"; do case "$a" in
            --purge) PURGE=true ;;
            --yes)   YES=true ;;
            *) echo "unknown flag: $a"; exit 1 ;;
        esac; done
        # privilege: the UI invokes this whole verb under pkexec
        # (already root); a terminal user may not be — same esc
        # pattern as uninstall.sh (root direct / pkexec / sudo).
        esc() {
            if [ "$(id -u)" = 0 ]; then "$@"
            elif [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v pkexec >/dev/null 2>&1; then pkexec "$@"
            else sudo "$@"
            fi
        }
        # what of $NAME exists on THIS device
        MESH=false; [ -d "/etc/isle-mesh/apps/$NAME" ] && MESH=true
        PKG=""
        for k in isle polari; do
            # "install ok installed" only — an rc (removed, config
            # kept) package must not count as installed here
            if dpkg-query -W -f '${Status}' "$k-app-$NAME" 2>/dev/null | grep -q 'install ok installed'; then
                PKG="$k-app-$NAME"; break
            fi
        done
        if [ "$MESH" = false ] && [ -z "$PKG" ]; then
            echo -e "${Y}nothing of '$NAME' is installed on this device${N} (no /etc/isle-mesh/apps/$NAME, no isle-app-/polari-app- deb)."
            echo "Other devices' copies are theirs to remove — uninstall runs where the app lives."
            exit 1
        fi
        VOLS=$(docker volume ls --format '{{.Name}}' 2>/dev/null | grep "^isle-${NAME}_" || true)
        echo -e "${Y}Uninstall '$NAME' from THIS device — will run:${N}"
        [ "$MESH" = true ] && echo "  $ isle app undeploy $NAME    # compose down + agent unregister"
        [ -n "$PKG" ]      && echo "  $ apt-get remove $PKG        # the launcher deb"
        if [ "$PURGE" = true ]; then
            [ -n "$VOLS" ] && echo "  + data volumes BACKED UP to /var/backups/, then deleted:" && echo "$VOLS" | sed 's/^/      /'
            [ "$MESH" = true ] && echo "  + /etc/isle-mesh/apps/$NAME erased; DNS entry ${NAME}.isle dropped"
        else
            { [ -n "$VOLS" ] || [ "$MESH" = true ]; } && echo "  (data PRESERVED — volumes + app config survive; reinstalling picks them up. --purge erases, backup first.)"
        fi
        if [ "$YES" != true ]; then
            read -r -p "Proceed? [y/N] " a; [ "$a" = y ] || [ "$a" = Y ] || { echo aborted; exit 1; }
        fi
        if [ "$MESH" = true ]; then
            bash "$SCRIPT_DIR/app.sh" undeploy "$NAME" || true
        fi
        if [ -n "$PKG" ]; then
            # wrapper `<pkg> down` stops the launcher's own containers
            # (same per-app step destroy.sh runs) before apt removal
            command -v "$PKG" >/dev/null 2>&1 && "$PKG" down 2>/dev/null || true
            if [ "$PURGE" = true ]; then esc apt-get purge -y "$PKG" || esc dpkg -P "$PKG" || true
            else esc apt-get remove -y "$PKG" || esc dpkg -r "$PKG" || true
            fi
            esc rm -f "/etc/isle-mesh/agent/installed-apps/$NAME.env" 2>/dev/null || true
        fi
        if [ "$PURGE" = true ]; then
            if [ -n "$VOLS" ]; then
                BK="/var/backups/isle-mesh-app-$NAME-$(date +%Y%m%d-%H%M%S)"
                esc mkdir -p "$BK"
                for v in $VOLS; do
                    esc docker run --rm -v "$v":/v:ro -v "$BK":/b alpine tar czf "/b/$v.tgz" -C /v . >/dev/null 2>&1 \
                        && esc docker volume rm "$v" >/dev/null 2>&1 \
                        && echo -e "${G}volume $v backed up + removed${N} → $BK/$v.tgz"
                done
            fi
            [ "$MESH" = true ] && esc rm -rf "/etc/isle-mesh/apps/$NAME" \
                && bash "$SCRIPT_DIR/dns.sh" unregister "$NAME" 2>/dev/null || true
        fi
        # honest final report: removed / preserved / how to finish
        echo -e "${G}uninstalled $NAME from this device${N}"
        if [ "$PURGE" != true ]; then
            LEFT=$(docker volume ls --format '{{.Name}}' 2>/dev/null | grep "^isle-${NAME}_" | tr '\n' ' ')
            [ -n "$LEFT" ] && echo "  preserved: volumes $LEFT(erase: isle store uninstall $NAME --purge)"
            [ "$MESH" = true ] && echo "  preserved: /etc/isle-mesh/apps/$NAME + DNS ${NAME}.isle (a reinstall reuses them)"
        fi
        ;;
    *) echo "usage: isle store [list|show <name>|install <name> [--yes]|uninstall <name> [--purge] [--yes]]"; exit 1 ;;
esac
