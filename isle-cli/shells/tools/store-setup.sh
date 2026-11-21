#!/bin/bash
# store-setup.sh — the ONE privileged entry point behind the Isle App's setup doors (his rule 2026-09-14:
# setup choices are CLI verbs first; the UI wraps them with pkexec). Every door in store-launch.sh runs
#     pkexec /usr/share/isle-app-store/store-setup.sh <verb> ...
# in a visible terminal, and this script runs the SAME isle verbs a person would type — adding only what the
# installed isle CLI does not accept yet, honestly (it says so).
#
#   store-setup.sh core-install --mode production|dev [isle core-install args...]
#   store-setup.sh join --fingerprint <CA sha256> --tier access|member|hardware [--core <ip>]   (access = shells only; member = a normal isle member; 'host'/'light' = aliases)
#   store-setup.sh posture production|dev [--until <UTC>]       write /etc/polari/posture.json (+ polari-isle/.env)
#   store-setup.sh stick [<mountpoint>]                          run a Polari app stick's own prompt (Install / Not now / Wipe)
# DRY=1 prints the commands instead of running them (tests).
set -u
VERB=${1:-}; shift || true
ISLE=$(command -v isle 2>/dev/null || echo /usr/local/bin/isle)
POSTURE_FILE=/etc/polari/posture.json
G="\033[0;32m"; Y="\033[1;33m"; R="\033[0;31m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }; warn(){ echo -e "${Y}[WARN]${N} $*"; }; die(){ echo -e "${R}[FAIL]${N} $*" >&2; exit 1; }
run(){ echo "+ $*"; [ "${DRY:-0}" = 1 ] || "$@"; }
DEV_WARNING="DEV MODE: any connection to systems that are not your own is EXTREMELY DANGEROUS — the relaxed security
(root over ssh, profiles in complain, open debug ports) travels with every connection. Keep this Polari on your own isle."
cli_accepts(){   # does the installed isle CLI's script for verb $1 parse flag $2? (never run the verb to find out — its --help starts an install)
    local dir; dir=$(dirname "$(readlink -f "$ISLE" 2>/dev/null || echo "$ISLE")")
    grep -rqs -- "$2)" "$dir/../lib/isle-mesh/scripts/$1.sh" "$dir/scripts/$1.sh" /usr/share/isle-mesh/scripts/"$1".sh /usr/local/lib/isle-mesh/scripts/"$1".sh 2>/dev/null
}

write_posture(){   # $1 = production|dev, $2 = until (may be empty)
    local mode=$1 until=${2:-} who; who=${PKEXEC_UID:+$(id -un "$PKEXEC_UID" 2>/dev/null)}; who=${who:-${SUDO_USER:-$(id -un)}}
    [ "$mode" = production ] || [ "$mode" = dev ] || die "mode must be production or dev"
    run mkdir -p "$(dirname "$POSTURE_FILE")"
    if [ "${DRY:-0}" != 1 ]; then
        printf '{"posture": "%s", "until": "%s", "relaxations": [], "applied_by": "%s", "written": "%s"}\n' "$mode" "$until" "$who" "$(date -u +%FT%TZ)" > "$POSTURE_FILE"
        chmod 644 "$POSTURE_FILE"
    fi
    ok "$POSTURE_FILE: posture=$mode${until:+ until $until}"
    # the instance reads POLARI_POSTURE (the notice bar's DEV MODE banner); the isle compose seeds it from polari-isle/.env
    for env in /var/lib/polari-isle/.env /opt/polari-isle/.env /usr/share/polari-isle/.env /etc/polari-isle/.env; do
        [ -f "$env" ] || continue
        if [ "${DRY:-0}" != 1 ]; then sed -i '/^POLARI_POSTURE=/d' "$env"; echo "POLARI_POSTURE=$mode" >> "$env"; fi
        ok "$env: POLARI_POSTURE=$mode"
    done
    [ "$mode" = dev ] && echo -e "${Y}$DEV_WARNING${N}"
    return 0
}

case "$VERB" in
    recover)
        [ "$(cat /etc/isle-mesh/agent/agent.mode 2>/dev/null)" = core ] || die "This device is not an installed core"
        run systemctl daemon-reload || exit 1
        run systemctl enable isle-mesh-boot.service || exit 1
        run "$ISLE" recover || exit 1 ;;
    posture)
        MODE=${1:?production|dev}; shift; UNTIL=""; while [ $# -gt 0 ]; do case "$1" in --until) UNTIL="$2"; shift 2 ;; *) shift ;; esac; done
        write_posture "$MODE" "$UNTIL" ;;
    core-install)
        MODE=production; ARGS=()
        while [ $# -gt 0 ]; do case "$1" in --mode) MODE="$2"; shift 2 ;; *) ARGS+=("$1"); shift ;; esac; done
        echo "Isle core install — mode: $MODE (the same command the terminal route uses: isle core-install)"
        write_posture "$MODE" ""
        if cli_accepts core-install --mode; then run "$ISLE" core-install --mode "$MODE" "${ARGS[@]}"
        else warn "this isle CLI has no --mode yet — the mode is recorded in $POSTURE_FILE and the instance env"; run "$ISLE" core-install "${ARGS[@]}"; fi ;;
    join)
        # Installed CLI owns discovery, declared names, networking and CA trust.
        if [ $# -eq 0 ]; then
            if [ "${DRY:-0}" != 1 ] && ! cli_accepts join --finish; then
                die "Guided joining requires isle-mesh-cli 0.1.172+join1 or later. Install the matching CLI package first."
            fi
            run "$ISLE" join; exit $?
        fi
        FP=""; TIER=access; CORE=""
        while [ $# -gt 0 ]; do case "$1" in --fingerprint) FP="$2"; shift 2 ;; --tier) TIER="$2"; shift 2 ;; --core) CORE="$2"; shift 2 ;; *) shift ;; esac; done
        [ -n "$FP" ] || die "join needs --fingerprint <CA sha256> (from the core's 'isle core-install' printout)"
        [ "$TIER" = light ] && TIER=access; [ "$TIER" = host ] && TIER=member
        case "$TIER" in access|member|hardware) ;; *) die "--tier access | member | hardware" ;; esac
        # What is run below as root is only ever a file fetched over a checked connection. first-trust: the isle's
        # root certificate is fetched unchecked, compared with the fingerprint the person gave (read from the core's
        # own printout), and only then is the bootstrap fetched, with the connection checked against that certificate.
        SRC="https://apt.isle/isle-bootstrap.sh"; TMP=$(mktemp); ROOT=$(mktemp)
        AT=(); [ -n "$CORE" ] && AT=(--resolve "apt.isle:443:$CORE")
        norm(){ echo "$1" | tr -d ': ' | tr '[:lower:]' '[:upper:]'; }
        echo "Join — tier: $TIER. Fetching the bootstrap from your core: $SRC${CORE:+ (at $CORE)}"
        if [ "${DRY:-0}" = 1 ]; then
            echo "+ curl -fsS --insecure ${AT[*]:-} https://apt.isle/isle-root.crt -o $ROOT   (first trust: compared with --fingerprint before anything else)"
            echo "+ curl -fsS --cacert $ROOT --capath /nonexistent ${AT[*]:-} $SRC -o $TMP"
        else
            command -v openssl >/dev/null || die "openssl is needed to check the isle's root certificate"
            curl -fsS --insecure --max-time 10 ${AT[@]+"${AT[@]}"} https://apt.isle/isle-root.crt -o "$ROOT" \
                || die "could not fetch the isle's root certificate — is this computer on the isle's network? (--core <ip> if apt.isle does not resolve yet)"
            GOT=$(openssl x509 -in "$ROOT" -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2)
            if [ -z "$GOT" ] || [ "$(norm "$GOT")" != "$(norm "$FP")" ]; then
                rm -f "$ROOT" "$TMP"
                die "the root certificate this network offers does not have the fingerprint you gave (offered: ${GOT:-not a certificate}). Nothing was fetched or run."
            fi
            curl -fsS --max-time 20 --cacert "$ROOT" --capath /nonexistent ${AT[@]+"${AT[@]}"} "$SRC" -o "$TMP" \
                || { rm -f "$ROOT" "$TMP"; die "could not fetch $SRC over a connection checked against the isle's root certificate. Nothing was run."; }
            echo "bootstrap sha256: $(sha256sum "$TMP" | cut -d' ' -f1)   (fetched over a connection checked against the root certificate you named)"
        fi
        rm -f "$ROOT"
        BARGS=(--fingerprint "$FP"); [ -n "$CORE" ] && BARGS+=(--core "$CORE")
        case "$TIER" in
            access) echo "access only: this computer gets the shells (launchers) and reaches the isle's apps; it hosts nothing (plain 'isle onboard')" ;;
            member) BARGS+=(--host) ;;   # the isle CLI's flag for a hosting member
            hardware)
                if cli_accepts isle-bootstrap --tier || cli_accepts onboard --tier; then BARGS+=(--tier hardware)
                else BARGS+=(--host); warn "this isle CLI has no HARDWARE tier yet — joining as an isle MEMBER; hardware apps will say they cannot reach this machine's devices until the isle side ships the tier"; fi ;;
        esac
        run bash "$TMP" "${BARGS[@]}"
        BOOTSTRAP_STATUS=$?
        rm -f "$TMP"
        if [ "$BOOTSTRAP_STATUS" -ne 0 ]; then
            warn "Onboarding incomplete: bootstrap failed (exit $BOOTSTRAP_STATUS). Keep completed steps and retry after resolving the failure."
            exit "$BOOTSTRAP_STATUS"
        fi
        [ "$TIER" = hardware ] && ok "requested tier: hardware (recorded for the core's topology once the tier exists)"
        exit 0 ;;
    stick)
        MP=${1:-}
        [ -n "$MP" ] || MP=$(lsblk -J -o MOUNTPOINT,RM,TRAN 2>/dev/null | python3 -c 'import sys,json
for d in json.load(sys.stdin)["blockdevices"]:
    for p in (d.get("children") or [d]):
        if (d.get("tran")=="usb" or d.get("rm")) and p.get("mountpoint"): print(p["mountpoint"]); break' 2>/dev/null | while read -r m; do [ -f "$m/polari-apps/on-insert.sh" ] && echo "$m" && break; done)
        [ -n "$MP" ] && [ -f "$MP/polari-apps/on-insert.sh" ] || die "no Polari app stick is plugged in (a stick carries polari-apps/index.json; make one: pol apps usb write)"
        run bash "$MP/polari-apps/on-insert.sh" "$@" ;;
    *)
        echo "usage: store-setup.sh core-install --mode production|dev | join --fingerprint <sha256> --tier access|member|hardware [--core <ip>] | posture production|dev [--until] | stick [<mount>]"; exit 1 ;;
esac
