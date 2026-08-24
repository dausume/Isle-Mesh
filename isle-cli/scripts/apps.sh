#!/bin/bash
# apps.sh — `isle apps`: app-deb generation on the isle (dl-4).
#
#   isle apps build-debs [<module>... | --all]
#
# THIN VERB by design (vendor-sync rule — never twin scripts): the
# ONE implementation is polari-framework's appstore/app_deb_builder
# (pure-python deb writer, shared-payload factoring, generation
# ledger, TTL pool). This script only LOCATES that implementation —
# a local framework checkout first, else the running polari backend
# container — and invokes it. Debs land in the implementation's
# pool (POLARI_APP_DEBS_DIR); the normal user path is the
# /downloads/apps page, which generates on request.
set -u
G="\033[0;32m"; Y="\033[1;33m"; R="\033[0;31m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }
warn(){ echo -e "${Y}[WARN]${N} $*"; }
fail(){ echo -e "${R}[FAIL]${N} $*"; exit 1; }

framework_dir() {
    for dir in "${POLARI_FRAMEWORK_DIR:-}" \
               "$HOME/Desktop/polari-suite/polari-rf-node/polari-framework" \
               "$HOME/polari-suite/polari-rf-node/polari-framework" \
               "$HOME/polari-framework"; do
        [ -n "$dir" ] && [ -f "$dir/modules/appstore/app_deb_builder.py" ] \
            && { echo "$dir"; return 0; }
    done
    return 1
}

backend_container() {
    docker ps --format '{{.Names}}' 2>/dev/null \
        | grep -m1 -E 'polari.*(backend|framework)|prf-backend' || true
}

case "${1:-help}" in
    build-debs)
        shift
        ARGS=("${@:---all}")
        if DIR=$(framework_dir); then
            ok "framework checkout: $DIR"
            (cd "$DIR" && PYTHONPATH=.:modules \
                python3 -m appstore.app_deb_builder "${ARGS[@]}")
        elif CONT=$(backend_container) && [ -n "$CONT" ]; then
            ok "running backend container: $CONT"
            docker exec -e PYTHONPATH=.:modules "$CONT" \
                python3 -m appstore.app_deb_builder "${ARGS[@]}"
        else
            fail "no polari framework found — set POLARI_FRAMEWORK_DIR
       to a checkout, or start the polari backend. (Normal users
       don't need this verb: the /downloads/apps page generates
       debs on request.)"
        fi
        ;;
    *)
        echo "usage: isle apps build-debs [<module>... | --all]"
        echo "  Generate installable app debs from the polari module"
        echo "  registry (one per module; shared payload factored out)."
        ;;
esac
