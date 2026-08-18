#!/bin/bash
#############################################################################
# Isle Uninstall — remove the installed TOOLING (desktop app + CLI tool).
#
# This removes ONLY artifacts installed onto the system:
#   • the manager app (.deb package, or files copied by shells/install.sh)
#   • the `isle` CLI entry on PATH (/usr/local/bin/isle, /usr/bin/isle)
#
# It is deliberately conservative:
#   • It NEVER deletes the development repository — if `isle` resolves to a
#     checkout (e.g. you run it from source / via `npm link`), the source tree
#     is left untouched; only the system PATH entry is removed.
#   • It does NOT tear down a running mesh or remove /etc/isle-mesh. For that,
#     run the full "Wipe Island":  sudo isle destroy --purge --force
#
# Usage:
#   isle uninstall              Remove the app (if present) AND the CLI tool
#   isle uninstall --cli-only   Remove only the CLI tool
#   isle uninstall --app-only   Remove only the desktop app
#   isle uninstall --everything THE FULL WIPE: backup → destroy --purge →
#                               network handback → volumes (backup-then-
#                               delete) → apt purge of every isle package
#                               → verify. Identical steps via terminal,
#                               desktop (apt), or the store UI (polkit).
#   isle uninstall --verify     Zero-footprint sweep (read-only)
#   isle uninstall --force      Skip the confirmation prompt
#   isle uninstall --help
#############################################################################

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[✓]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[!]${NC} $1"; }
log_error()   { echo -e "${RED}[✗]${NC} $1"; }
log_step()    { echo ""; echo -e "${BLUE}━━━ $1 ━━━${NC}"; }

APP_ONLY=false
CLI_ONLY=false
FORCE=false

EVERYTHING=false
VERIFY=false
for arg in "$@"; do
    case "$arg" in
        --app-only) APP_ONLY=true ;;
        --cli-only) CLI_ONLY=true ;;
        --everything) EVERYTHING=true ;;
        --verify) VERIFY=true ;;
        --force|-f) FORCE=true ;;
        --help|-h|help)
            sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) log_error "Unknown option: $arg (try 'isle uninstall --help')"; exit 1 ;;
    esac
done

if [ "$APP_ONLY" = true ] && [ "$CLI_ONLY" = true ]; then
    log_error "--app-only and --cli-only are mutually exclusive"
    exit 1
fi

# ── Detection ────────────────────────────────────────────
app_present() {
    if command -v dpkg >/dev/null 2>&1 && dpkg -l isle-manager-app 2>/dev/null | grep -q '^ii'; then
        return 0
    fi
    [ -f /usr/share/isle-manager-app/isle-manager-app.jar ] && return 0
    [ -e /usr/bin/isle-manager-app ] && return 0
    return 1
}

# ── Removal helpers ──────────────────────────────────────
remove_app() {
    log_step "Removing manager app"

    # Package-managed install
    if command -v dpkg >/dev/null 2>&1 && dpkg -l isle-manager-app 2>/dev/null | grep -q '^ii'; then
        log_info "Removing isle-manager-app package..."
        if command -v apt-get >/dev/null 2>&1; then
            sudo apt-get remove -y isle-manager-app 2>/dev/null || sudo dpkg -r isle-manager-app 2>/dev/null || true
        else
            sudo dpkg -r isle-manager-app 2>/dev/null || true
        fi
    fi

    # Direct-copy install leftovers (shells/install.sh bypasses dpkg)
    sudo rm -f  /usr/bin/isle-manager-app \
                /usr/share/applications/isle-manager-app.desktop \
                /usr/share/polkit-1/actions/org.islemesh.permissions.policy 2>/dev/null || true
    sudo rm -rf /usr/share/isle-manager-app 2>/dev/null || true

    if app_present; then
        log_warning "Some app files may remain (check dpkg status)"
    else
        log_success "Manager app removed"
    fi
}

remove_cli() {
    log_step "Removing isle CLI tool"

    # Single-source-of-truth for canonical/legacy CLI locations.
    local paths_lib="$(cd "$(dirname "${BASH_SOURCE[0]}")/../shells" 2>/dev/null && pwd)/cli-paths.sh"
    local CANON="/usr/local/bin/isle"; local LEGACY="/usr/bin/isle"
    if [ -f "$paths_lib" ]; then
        # shellcheck source=../shells/cli-paths.sh
        source "$paths_lib"
        CANON="$ISLE_CLI_LINK"; LEGACY="$ISLE_CLI_LEGACY"
    fi

    # Where does `isle` currently resolve? Used only to protect a dev checkout.
    local resolved="" target=""
    resolved="$(command -v isle 2>/dev/null || true)"

    # npm global link (dev install registers the package as isle-cli)
    if command -v npm >/dev/null 2>&1; then
        npm unlink -g isle-cli 2>/dev/null && log_info "npm unlinked isle-cli" || true
    fi

    # Remove the canonical + legacy PATH entries (symlinks/copies we install).
    local removed=false
    for p in "$CANON" $LEGACY; do
        if [ -L "$p" ]; then
            target="$(readlink -f "$p" 2>/dev/null || true)"
            sudo rm -f "$p" && { log_success "Removed symlink $p"; removed=true; }
        elif [ -f "$p" ]; then
            sudo rm -f "$p" && { log_success "Removed $p"; removed=true; }
        fi
    done

    # Bundled-CLI location (future self-contained .deb). Only present if installed.
    if [ -d /usr/share/isle-mesh/isle-cli ]; then
        sudo rm -rf /usr/share/isle-mesh 2>/dev/null && log_success "Removed bundled CLI (/usr/share/isle-mesh)" || true
        removed=true
    fi

    # SAFETY: if `isle` STILL resolves, it is coming from a development checkout
    # on PATH (not a system install). Never delete that — just report it.
    if command -v isle >/dev/null 2>&1; then
        resolved="$(command -v isle)"
        log_warning "'isle' still resolves to: ${resolved}"
        log_info  "That looks like a development checkout — left untouched on purpose."
        log_info  "Remove it from your PATH manually if you want it gone."
    elif [ "$removed" = true ]; then
        log_success "isle CLI removed from PATH"
    else
        log_info "No system-installed isle CLI found"
    fi

    [ -n "$target" ] && log_info "(The source it pointed at — $(dirname "$(dirname "$target")") — was NOT deleted.)"
}

# ── The ONE engine: --everything / --verify (unin-3) ─────
# These ARE the terminal steps; the desktop route (apt) runs the same
# scripts from the deb's prerm/postrm, and the store UI runs exactly
# this verb via polkit. One engine, three doors.
SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# privilege: polkit when a desktop session can prompt, sudo otherwise —
# the same flow works smoothly with and without a UI.
esc() {
    if [ "$(id -u)" = 0 ]; then "$@"
    elif [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v pkexec >/dev/null 2>&1; then
        pkexec "$@"
    else
        sudo "$@"
    fi
}

ISLE_VOL_RE='^(isle-|polari-isle_|prf-isle-|prf-polari-)'
ISLE_CTR_RE='^(isle-|prf-isle-|prf-polari-)'
DEB_FAMILY="isle-mesh-cli isle-app-store isle-manager-app polari-shell-core"

is_isle_core() {
    [ "$(cat /etc/isle-mesh/agent/agent.mode 2>/dev/null)" = core ] && return 0
    { virsh -c qemu:///system list --all --name 2>/dev/null \
      || sudo -n virsh -c qemu:///system list --all --name 2>/dev/null; } \
        | grep -q 'openwrt-isle' && return 0
    return 1
}

verify_zero() {
    log_step "Verify: zero isle-mesh/polari footprint"
    local bad=0 n
    n=$(docker ps -a --format '{{.Names}}' 2>/dev/null | grep -cE "$ISLE_CTR_RE"); [ "${n:-0}" = 0 ] || { log_error "containers remaining: $n"; bad=1; }
    n=$(docker volume ls --format '{{.Name}}' 2>/dev/null | grep -cE "$ISLE_VOL_RE"); [ "${n:-0}" = 0 ] || { log_error "volumes remaining: $n (data — remove via purge/backup)"; bad=1; }
    n=$(docker images --format '{{.Repository}}' 2>/dev/null | grep -cE '^(isle-|prf-)'); [ "${n:-0}" = 0 ] || { log_warning "images remaining: $n (harmless; docker rmi to clear)"; }
    if command -v virsh >/dev/null 2>&1; then
        # read-only check must never hang on a password prompt: try
        # unprivileged (libvirt group), then non-interactive sudo, else skip
        n=$( { virsh -c qemu:///system list --all --name 2>/dev/null \
               || sudo -n virsh -c qemu:///system list --all --name 2>/dev/null; } \
             | grep -c 'openwrt-isle' || true)
        [ "${n:-0}" = 0 ] || { log_error "router VM remaining"; bad=1; }
    fi
    n=$(dpkg -l 2>/dev/null | awk '/^ii/{print $2}' | grep -cE '^(isle-mesh-cli|isle-app-.*|isle-manager-app|polari-shell-core|polari-module-.*)$'); [ "${n:-0}" = 0 ] || { log_error "debs remaining: $n"; bad=1; }
    for d in /usr/share/isle-mesh /etc/isle-mesh; do
        [ ! -d "$d" ] || { log_error "$d still present"; bad=1; }
    done
    systemctl is-active --quiet NetworkManager 2>/dev/null \
        && log_success "network owner: NetworkManager (active)" \
        || log_warning "NetworkManager not active — check who owns the interfaces"
    [ "$bad" = 0 ] && log_success "VERIFIED: nothing of isle-mesh/polari remains on this device" \
                   || log_error "footprint remains (above)"
    return $bad
}

uninstall_everything() {
    echo -e "${BOLD}${BLUE}Full uninstall — mesh runtime, network handback, data, packages${NC}"
    echo "Steps (identical via terminal, desktop, or the store UI's polkit):"
    echo "  1. backup   /etc/isle-mesh + data volumes → /var/backups/isle-mesh-purge-<date>"
    echo "  2. destroy  --purge --force   (apps, agent, router, config footprint)"
    echo "  3. network-handback           (wifi ownership, stale leases, split-DNS)"
    echo "  4. volumes  backup-then-delete"
    echo "  5. packages apt purge: $DEB_FAMILY + isle-app-*"
    echo "  6. verify   zero-footprint sweep"
    echo -e "${YELLOW}Code checkouts are NEVER touched. Third-party systems (odoo) are spared.${NC}"
    if [ "$FORCE" = false ]; then
        read -p "Proceed with the FULL uninstall? (yes/no): " CONFIRM
        [ "$CONFIRM" = "yes" ] || { echo "Cancelled."; exit 0; }
    fi

    # ── THE CORE CASCADE (unin-7) ────────────────────────
    # Deleting a CORE ends the isle for EVERY member device. That is
    # not undoable: the CA, router, DNS, apt-on-mesh, and the core
    # polari die here and cannot be regenerated as the same isle.
    # Typed confirmation is required even with --force
    # (ISLE_CONFIRM_DELETE=yes for automation).
    if is_isle_core; then
        echo ""
        echo -e "${RED}${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
        echo -e "${RED}${BOLD}║  THIS DEVICE IS THE ISLE'S CORE — DELETING IT ENDS THE ISLE  ║${NC}"
        echo -e "${RED}${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
        echo -e "${RED}Every member device loses its isle: DNS, the CA, apt-on-mesh, and"
        echo -e "the core polari die with this core. THERE IS NO GOING BACK — a new"
        echo -e "core-install creates a DIFFERENT isle (new CA); members must re-join."
        echo -e "Members get a last-gasp ISLE-ENDING signal: their apps STOP now, and"
        echo -e "each device's next store open asks its human about full removal.${NC}"
        if [ "${ISLE_CONFIRM_DELETE:-}" != "yes" ]; then
            read -p "Type exactly 'delete the isle' to continue: " PHRASE
            [ "$PHRASE" = "delete the isle" ] || { echo "Cancelled — nothing was touched."; exit 0; }
        fi
        esc bash "$SCRIPTS_DIR/watch.sh" broadcast-ending || true
    fi

    local BK="/var/backups/isle-mesh-purge-$(date +%Y%m%d-%H%M%S)"
    esc mkdir -p "$BK"
    [ -d /etc/isle-mesh ] && esc tar czf "$BK/etc-isle-mesh.tgz" -C /etc isle-mesh 2>/dev/null
    log_success "backup dir: $BK"

    esc bash "$SCRIPTS_DIR/destroy.sh" --purge --force || true
    esc bash "$SCRIPTS_DIR/network-handback.sh" || true

    for v in $(docker volume ls --format '{{.Name}}' 2>/dev/null | grep -E "$ISLE_VOL_RE"); do
        docker run --rm -v "$v":/v:ro -v "$BK":/b alpine tar czf "/b/$v.tgz" -C /v . >/dev/null 2>&1 \
            && docker volume rm "$v" >/dev/null 2>&1 \
            && log_success "volume $v backed up + removed"
    done

    # packages LAST — this script may delete itself out from under bash,
    # which keeps the open file handle (safe on Linux). The family
    # includes every launcher (isle-app-*) AND every polari module/
    # engine deb (polari-module-*) installed via apt-on-mesh.
    local apps; apps=$(dpkg -l 2>/dev/null | awk '/^ii/{print $2}' | grep -E '^(isle-app-|polari-module-)' | tr '\n' ' ')
    esc apt-get purge -y $DEB_FAMILY $apps 2>/dev/null \
        || esc dpkg -P $DEB_FAMILY $apps 2>/dev/null || true
    log_success "packages purged"

    verify_zero || true
    echo ""
    log_success "Full uninstall complete. Backups: $BK"
    exit 0
}

if [ "$VERIFY" = true ]; then verify_zero; exit $?; fi
if [ "$EVERYTHING" = true ]; then uninstall_everything; fi

# ── Banner + plan ────────────────────────────────────────
echo -e "${BOLD}${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${BLUE}║         Isle-Mesh Uninstall (app + CLI tooling)              ║${NC}"
echo -e "${BOLD}${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""

HAVE_APP=false; app_present && HAVE_APP=true

if [ "$CLI_ONLY" = true ]; then
    echo "Will remove: the isle CLI tool only."
elif [ "$APP_ONLY" = true ]; then
    echo "Will remove: the desktop app only."
else
    if [ "$HAVE_APP" = true ]; then
        echo "Detected the manager app. Will remove: the app AND the isle CLI tool."
    else
        echo "Manager app not detected. Will remove: the isle CLI tool only."
    fi
fi
echo -e "${YELLOW}This does NOT wipe the mesh or /etc/isle-mesh.${NC} For a full wipe first run:"
echo -e "  ${CYAN}sudo isle destroy --purge --force${NC}"
echo -e "Your development repository (if any) will ${BOLD}not${NC} be touched."
echo ""

if [ "$FORCE" = false ]; then
    read -p "Continue? (yes/no): " CONFIRM
    [ "$CONFIRM" = "yes" ] || { echo "Cancelled."; exit 0; }
fi

# ── Execute ──────────────────────────────────────────────
if [ "$CLI_ONLY" = true ]; then
    remove_cli
elif [ "$APP_ONLY" = true ]; then
    remove_app
else
    [ "$HAVE_APP" = true ] && remove_app || log_info "Skipping app removal (not installed)"
    remove_cli
fi

echo ""
log_success "Uninstall complete."
echo ""
