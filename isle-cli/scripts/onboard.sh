#!/bin/bash
#
# Isle Onboard — guided walkthrough to bring a discovered device onto the mesh.
#
# One entry point for all three situations; the device's recorded "situation"
# selects the walkthrough:
#   installable_remote → guided remote install over SSH (you provide credentials)
#   manual_install     → guided manual install steps for that device
#   firewalled         → explains it couldn't be detected, then manual steps
#
# Credentials you type are used for a single SSH session and never stored.
#
# Usage:
#   isle onboard <ip|mac>            Run the guided walkthrough interactively
#   isle onboard <ip|mac> --user U   Use SSH user U for remote install
#   isle onboard <ip|mac> --yes      Don't pause for confirmation
#   isle onboard <ip|mac> steps      Machine-readable walkthrough (for the app)
#   isle onboard help

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/device-store.sh
source "$SCRIPT_DIR/lib/device-store.sh"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
CHECK="${GREEN}✓${NC}"; WARN="${YELLOW}⚠${NC}"; INFO="${BLUE}ℹ${NC}"

require_jq() { command -v jq &>/dev/null || { echo "jq is required" >&2; exit 1; }; }

# Resolve a device record by exact MAC, else by IP. Echoes compact JSON.
resolve_device() {
    local key="${1,,}"
    [[ -f "$DEVICE_STORE_FILE" ]] || return 1
    local d
    d=$(jq -c --arg k "$key" '.devices[$k] // empty' "$DEVICE_STORE_FILE" 2>/dev/null)
    [[ -n "$d" ]] && { echo "$d"; return 0; }
    jq -c --arg k "$1" 'first(.devices[] | select(.ip == $k)) // empty' "$DEVICE_STORE_FILE" 2>/dev/null
}

# ── Walkthrough content (single source for both interactive + app `steps`) ──
emit_steps() {
    local situation="$1" ip="$2" mac="$3" host="$4" user="$5"
    echo "situation=${situation}"
    echo "target ip=${ip} mac=${mac} host=${host}"
    case "$situation" in
        onboarded)
            echo "step|1|Already on the mesh|This device is running the isle-mesh agent. Nothing to do." ;;
        installable_remote)
            echo "step|1|Confirm ownership|You must own or be authorized to manage ${ip}. Only proceed for your own devices."
            echo "step|2|Provide SSH login|isle connects as ${user}@${ip}. You'll enter the password/key passphrase yourself; it is used once and never stored."
            echo "step|3|Install & join|isle installs the agent over SSH and runs 'isle join' on the device."
            echo "step|4|Verify|Re-run a scan; the device should flip to 'onboarded' in 'isle devices'."
            echo "action|remote-install|${user}@${ip}" ;;
        manual_install)
            echo "step|1|Why manual|${ip} is reachable but has no SSH access, so it can't be installed remotely."
            echo "step|2|Install on the device|On ${ip}, install isle-mesh (the isle CLI)."
            echo "step|3|Join the isle|On ${ip}, run: sudo isle join"
            echo "step|4|Verify|It registers over the isle and appears as 'onboarded' here."
            echo "action|manual|${ip}" ;;
        firewalled)
            echo "step|1|Couldn't detect it|${ip} did not respond to discovery (likely a firewall / ICMP block). You added it manually, so we can't reach it to install."
            echo "step|2|Open access or install directly|On ${ip}, allow isle-mesh through the firewall, or just install isle-mesh directly."
            echo "step|3|Install on the device|On ${ip}, install isle-mesh (the isle CLI)."
            echo "step|4|Join the isle|On ${ip}, run: sudo isle join"
            echo "action|manual|${ip}" ;;
        *)
            echo "step|1|Unknown situation|No walkthrough available for situation '${situation}'." ;;
    esac
}

print_steps_human() {
    local situation="$1"
    echo ""
    echo -e "${BOLD}${BLUE}═══ Guided onboarding (${situation}) ═══${NC}"
    echo ""
    emit_steps "$@" | grep '^step|' | while IFS='|' read -r _ n title detail; do
        echo -e "  ${CYAN}${n}.${NC} ${BOLD}${title}${NC}"
        echo -e "     ${detail}"
    done
    echo ""
}

# ── Guided remote install over SSH (Situation A) ──
do_remote_install() {
    local ip="$1" user="$2" assume_yes="$3" mac="$4"

    # Non-interactive password path for the GUI: if ISLE_SSH_PASSWORD is set and
    # sshpass is available, use it. Otherwise ssh prompts on the terminal.
    local -a SSH
    if [[ -n "${ISLE_SSH_PASSWORD:-}" ]] && command -v sshpass >/dev/null 2>&1; then
        export SSHPASS="$ISLE_SSH_PASSWORD"
        SSH=(sshpass -e ssh -o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new)
    else
        SSH=(ssh -o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new)
    fi

    echo -e "  ${INFO} This will connect to ${BOLD}${user}@${ip}${NC} over SSH and install isle-mesh."
    echo -e "  ${INFO} Credentials are used for this one session and never stored."
    if [[ "$assume_yes" != "true" ]]; then
        read -r -p "  Proceed? [y/N] " ans
        [[ "$ans" =~ ^[Yy] ]] || { echo "  Aborted."; return 1; }
    fi

    echo -e "  ${INFO} Connecting…"
    # If isle is already present, just join; otherwise report what's needed.
    local out
    out=$("${SSH[@]}" "${user}@${ip}" \
        'if command -v isle >/dev/null 2>&1; then echo __ISLE_PRESENT__; else echo __ISLE_MISSING__; fi' \
        2>/dev/null) || { echo -e "  ${WARN} SSH connection failed. Check the address/credentials and that SSH is enabled."; return 1; }

    if [[ "$out" == *__ISLE_PRESENT__* ]]; then
        echo -e "  ${CHECK} isle-mesh present on ${ip}; joining the isle…"
        if "${SSH[@]}" "${user}@${ip}" 'sudo isle join'; then
            echo -e "  ${CHECK} Join completed on ${ip}."
            [[ -n "$mac" ]] && ds_set_decision "$mac" "onboard"
            echo -e "  ${INFO} Re-run 'isle scan --save' to confirm it shows as onboarded."
            return 0
        fi
        echo -e "  ${WARN} 'isle join' did not complete on ${ip}."
        return 1
    fi

    # isle not installed remotely — guide the rest (no public bootstrapper assumed).
    echo -e "  ${WARN} isle-mesh is not installed on ${ip} yet."
    echo "  Install it there, then this command will finish the join. Options:"
    echo -e "    • Copy this project to the device and run its installer, then: ${CYAN}sudo isle join${NC}"
    echo -e "    • Or run on the device once isle is installed: ${CYAN}sudo isle join${NC}"
    [[ -n "$mac" ]] && ds_set_decision "$mac" "onboard"
    return 2
}

run_interactive() {
    local key="$1" user="$2" assume_yes="$3"
    require_jq
    local d; d="$(resolve_device "$key")"
    if [[ -z "$d" ]]; then
        echo -e "  ${WARN} No device matching '${key}' in the ledger. Add it with 'isle devices add' or run 'isle scan --save'." >&2
        exit 1
    fi
    local ip mac host situation
    ip=$(jq -r '.ip // ""' <<<"$d"); mac=$(jq -r '.mac // ""' <<<"$d")
    host=$(jq -r '.hostname // ""' <<<"$d"); situation=$(jq -r '.situation // "manual_install"' <<<"$d")
    [[ -z "$user" ]] && user="${SUDO_USER:-root}"

    print_steps_human "$situation" "$ip" "$mac" "$host" "$user"

    case "$situation" in
        onboarded)
            echo -e "  ${CHECK} ${ip} is already on the mesh — nothing to do." ;;
        installable_remote)
            do_remote_install "$ip" "$user" "$assume_yes" "$mac" ;;
        manual_install|firewalled)
            echo -e "  ${INFO} Follow the steps above on ${BOLD}${ip}${NC}. When it runs 'isle join', it will appear here."
            ds_set_decision "$mac" "onboard" ;;
        *)
            echo -e "  ${WARN} No walkthrough for situation '${situation}'." ;;
    esac
}

show_help() {
    cat <<EOF

$(echo -e "${BOLD}Isle Onboard — guided device onboarding${NC}")

Brings a discovered device onto the mesh, guiding you through whichever of the
three situations applies. Credentials you enter are used once and never stored.

Usage:
  isle onboard <ip|mac>            Run the guided walkthrough
  isle onboard <ip|mac> --user U   SSH user for remote install (default: you/root)
  isle onboard <ip|mac> --yes      Skip the confirmation prompt
  isle onboard <ip|mac> steps      Machine-readable walkthrough (for the app)
  isle onboard help
EOF
}

# ── Arg parsing ──
[[ $# -eq 0 ]] && { show_help; exit 0; }
case "$1" in help|-h|--help) show_help; exit 0 ;; esac

KEY="$1"; shift
MODE="interactive"; USER_OPT=""; ASSUME_YES="false"
while [[ $# -gt 0 ]]; do
    case "$1" in
        steps|--steps) MODE="steps"; shift ;;
        --user)        USER_OPT="${2:-}"; shift 2 ;;
        --yes|-y)      ASSUME_YES="true"; shift ;;
        *) echo "Unknown option: $1 (try 'isle onboard help')" >&2; exit 1 ;;
    esac
done

if [[ "$MODE" == "steps" ]]; then
    require_jq
    d="$(resolve_device "$KEY")"
    [[ -z "$d" ]] && { echo "situation=unknown"; echo "error=device-not-found key=${KEY}"; exit 1; }
    ip=$(jq -r '.ip // ""' <<<"$d"); mac=$(jq -r '.mac // ""' <<<"$d")
    host=$(jq -r '.hostname // ""' <<<"$d"); situation=$(jq -r '.situation // "manual_install"' <<<"$d")
    [[ -z "$USER_OPT" ]] && USER_OPT="${SUDO_USER:-root}"
    emit_steps "$situation" "$ip" "$mac" "$host" "$USER_OPT"
else
    run_interactive "$KEY" "$USER_OPT" "$ASSUME_YES"
fi
