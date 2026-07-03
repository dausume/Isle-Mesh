#!/bin/bash
#
# Isle USB — turn a USB drive into a portable isle-mesh installer.
#
# Copies the self-contained .deb (app + bundled CLI) plus a one-click installer
# script onto a mounted USB drive. Plug that USB into any other machine, open it,
# and run install-isle.sh to install isle-mesh there. NON-DESTRUCTIVE: it copies
# files onto the drive's existing filesystem — it never formats the drive.
#
# Usage:
#   isle usb list                 List removable/USB drives (machine-readable)
#   isle usb create <mountpoint>  Write the installer onto the drive at <mountpoint>
#   isle usb help
#
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
CHECK="${GREEN}✓${NC}"; WARN="${YELLOW}⚠${NC}"; INFO="${BLUE}ℹ${NC}"

require() { command -v "$1" &>/dev/null || { echo "$1 is required" >&2; exit 1; }; }

# ── List mounted removable/USB partitions (machine-readable) ──
# Emits: usb name=<dev> size=<size> mount=<path> label=<label>
usb_list() {
    require lsblk; require jq
    lsblk -J -o NAME,SIZE,TYPE,RM,MOUNTPOINT,LABEL,TRAN 2>/dev/null | jq -r '
        .blockdevices[]
        | select(.tran == "usb" or .rm == true) as $d
        | ($d.children // [$d])[]
        | select(.mountpoint != null and .mountpoint != "")
        | "usb name=\(.name) size=\(.size) mount=\(.mountpoint) label=\((.label // "") | gsub(" ";"_"))"
    '
}

# Is <mountpoint> actually one of the removable drives we listed? (safety guard)
is_removable_mount() {
    local mp="$1"
    usb_list | grep -q " mount=${mp} "
}

# Locate the .deb to copy (or build it if we're in a checkout).
locate_deb() {
    local candidates=(
        "$PROJECT_ROOT/isle-manager-app/build/"isle-manager-app_*.deb
        /usr/share/isle-mesh/installer/isle-manager-app_*.deb
    )
    local f
    for f in "${candidates[@]}"; do
        [[ -f "$f" ]] && { echo "$f"; return 0; }
    done
    # Not found — try to build it if the project is present.
    local builder="$PROJECT_ROOT/isle-manager-app/shells/build-deb.sh"
    if [[ -x "$builder" ]]; then
        echo "  ${INFO} No prebuilt .deb found — building it..." >&2
        bash "$builder" >&2 || return 1
        f=$(ls -1 "$PROJECT_ROOT/isle-manager-app/build/"isle-manager-app_*.deb 2>/dev/null | head -1)
        [[ -f "$f" ]] && { echo "$f"; return 0; }
    fi
    return 1
}

usb_create() {
    require rsync 2>/dev/null || true   # optional; cp is fallback
    local mp="${1:-}"
    if [[ -z "$mp" ]]; then
        echo "usage: isle usb create <mountpoint>" >&2; exit 1
    fi
    if [[ ! -d "$mp" ]]; then
        echo -e "  ${WARN} Not a directory: ${mp}" >&2; exit 1
    fi
    # Safety: only write to an actual removable drive.
    if ! is_removable_mount "$mp"; then
        echo -e "  ${WARN} ${mp} is not a removable/USB drive — refusing to write." >&2
        echo -e "  ${INFO} Run 'isle usb list' to see eligible drives." >&2
        exit 1
    fi
    if [[ ! -w "$mp" ]]; then
        echo -e "  ${WARN} ${mp} is not writable by $(whoami)." >&2; exit 1
    fi

    local deb; deb="$(locate_deb)" || {
        echo -e "  ${WARN} Could not find or build the isle .deb." >&2
        echo -e "  ${INFO} Build it first: ${CYAN}./appInstall.sh${NC} (or isle-manager-app/shells/build-deb.sh)" >&2
        exit 1
    }

    local dest="${mp%/}/isle-mesh-installer"
    mkdir -p "$dest"
    echo -e "  ${INFO} Copying $(basename "$deb") to ${dest}/ ..."
    cp -f "$deb" "$dest/" || { echo -e "  ${WARN} Copy failed." >&2; exit 1; }

    # One-click installer that runs on the TARGET machine (root-aware: works
    # whether launched plainly with sudo, or already as root via the launcher).
    cat > "$dest/install-isle.sh" <<'EOS'
#!/bin/bash
# Isle-Mesh USB installer — installs the app + CLI on this computer.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
DEB="$(ls -1 "$DIR"/isle-manager-app_*.deb 2>/dev/null | head -1)"
[ -z "$DEB" ] && { echo "No .deb found next to this script." >&2; exit 1; }
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
echo "Installing Isle-Mesh from: $DEB"
$SUDO dpkg -i "$DEB" || $SUDO apt-get -f install -y
echo ""
echo "Done. Launch it from your applications menu (Isle Manager App)."
echo "(CLI also available: isle)"
# Keep the auto-opened terminal window up so the user can read the result.
[ -t 0 ] && { echo; read -r -p "Press Enter to close..." _ || true; }
EOS
    chmod +x "$dest/install-isle.sh"

    # Double-clickable launcher so a non-technical user never opens a terminal.
    # %k = this .desktop file's own path, so it finds the installer regardless of
    # where the USB mounts. Terminal=true: a window opens, asks for the password,
    # shows progress, and waits — the user never has to type a command.
    cat > "$dest/Install Isle-Mesh.desktop" <<'EOS'
[Desktop Entry]
Type=Application
Version=1.0
Name=Install Isle-Mesh
Comment=Install Isle-Mesh on this computer
Exec=sh -c "exec \"\$(dirname \"\$0\")/install-isle.sh\"" %k
Icon=network-wired
Terminal=true
Categories=Utility;
EOS
    chmod +x "$dest/Install Isle-Mesh.desktop"

    cat > "$dest/README.txt" <<EOS
Isle-Mesh portable installer
============================

EASIEST: double-click "Install Isle-Mesh" in this folder.
A window opens, asks for your password, installs everything, and tells you when
it's done. Then open "Isle Manager App" from your applications menu.

  • On some systems (e.g. GNOME Files) the first time you may need to right-click
    "Install Isle-Mesh" -> "Allow Launching" (a one-time security step), then
    double-click it.

If double-click is blocked, you can instead run ./install-isle.sh in a terminal.

Nothing on this USB changes a computer until you run the installer. After
installing, the app auto-detects an existing isle on the network (over mDNS) and
offers to join it.
EOS

    sync
    echo -e "  ${CHECK} USB installer ready at ${BOLD}${dest}${NC}"
    echo -e "  ${INFO} On another machine: open it and run ${CYAN}./install-isle.sh${NC}"
}

# Only dispatch when run directly (sourcing for tests won't execute).
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    CMD="${1:-help}"; shift || true
    case "$CMD" in
        list)   usb_list ;;
        create) usb_create "${1:-}" ;;
        help|-h|--help)
            sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
        *) echo "Unknown command: $CMD (try 'isle usb help')" >&2; exit 1 ;;
    esac
fi
