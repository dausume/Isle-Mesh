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

for arg in "$@"; do
    case "$arg" in
        --app-only) APP_ONLY=true ;;
        --cli-only) CLI_ONLY=true ;;
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
