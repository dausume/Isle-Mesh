#!/bin/bash
# store-launch.sh — what the Isle App Store icon actually runs.
#
# FIRST-OPEN, fresh device: there is no isle yet, so opening the store
# offers the SAME two doors the terminal route has (the membership
# rule: the agent IS membership; single-device isles are first-class):
#   1. Create my own isle    -> isle core-install (in a terminal window,
#                               privilege via polkit; the interactive
#                               security walkthrough runs there too)
#   2. Join an existing isle -> the fingerprint-verified bootstrap
#                               instructions (printed by YOUR core)
#   3. Just browse           -> open the shell anyway (honest
#                               unreachable page until an isle exists)
#
# THE UI DOES NOTHING OF ITS OWN — every door runs the exact terminal
# steps, with pkexec for privilege. Once an agent is running (member/
# core), this wrapper is a plain exec of the shell: zero overhead.
set -u
SHARE=/usr/share/isle-app-store
SHELL_BIN=/usr/bin/polari-app-shell
CONFIG="$SHARE/polari-shell.json"

open_shell() { exec "$SHELL_BIN" --config "$CONFIG"; }

agent_up() {
    docker ps --format '{{.Names}}' 2>/dev/null \
        | grep -qE '^isle-(vlan|remote)-agent$'
}

# ── the ISLE-ENDED prompt (unin-7) ──────────────────────────────────
# isle-watch records when this device's CORE was deleted (kill signal)
# or has been unreachable for a long time. Removal is NEVER automatic:
# this is where the human decides, with the no-going-back warning.
FLAG=/etc/isle-mesh/isle-ended
if [ -f "$FLAG" ] && command -v zenity >/dev/null 2>&1; then
    KIND=$(grep '^kind=' "$FLAG" | cut -d= -f2)
    WHEN=$(grep '^when=' "$FLAG" | cut -d= -f2)
    if [ "$KIND" = core-deleted ]; then
        TXT="Your isle's CORE was DELETED ($WHEN — its uninstall sent the isle-ending signal).\n\nThe isle no longer exists: its CA, DNS, apt source, and core polari are gone, and a new core would be a DIFFERENT isle.\n\nRemove ALL polari-isle apps from THIS device?\nTHERE IS NO GOING BACK once removed (data volumes are backed up first)."
    else
        TXT="Your isle's core has been UNREACHABLE since $WHEN.\n\nIf it was deleted, this device's isle apps are orphaned. If it is only offline, choose 'The isle is back' once it returns.\n\nRemove ALL polari-isle apps from THIS device?\nTHERE IS NO GOING BACK once removed (data volumes are backed up first)."
    fi
    ANSWER=$(zenity --question --title "Isle ended?" --width 520 \
        --text "$TXT" \
        --ok-label "Remove everything (no going back)" \
        --extra-button "Keep for now (ask again later)" \
        --extra-button "The isle is back (clear the warning)" 2>/dev/null)
    ARC=$?
    ISLE_BIN=$(command -v isle 2>/dev/null || echo /usr/local/bin/isle)
    if [ "$ARC" = 0 ]; then
        for t in x-terminal-emulator gnome-terminal konsole xfce4-terminal xterm; do
            command -v "$t" >/dev/null 2>&1 || continue
            case "$t" in
                gnome-terminal) "$t" --wait -- bash -c "pkexec $ISLE_BIN uninstall --everything; read -p 'Done — Enter closes...'" 2>/dev/null && break ;;
                *) "$t" -e bash -c "pkexec $ISLE_BIN uninstall --everything; read -p 'Done — Enter closes...'" && break ;;
            esac
        done
        exit 0
    elif [ "$ANSWER" = "The isle is back (clear the warning)" ]; then
        pkexec "$ISLE_BIN" watch clear 2>/dev/null
    fi
    # 'Keep for now' falls through — the shell opens, the flag stays
fi

# member/core already: straight into the store
agent_up && open_shell

# resolve the isle CLI (desktop launchers get a sanitized-ish PATH)
ISLE=$(command -v isle 2>/dev/null || true)
[ -n "$ISLE" ] || { [ -x /usr/local/bin/isle ] && ISLE=/usr/local/bin/isle; }

# find a terminal emulator for the interactive doors
term_run() {  # CMD-STRING — run in a visible terminal, wait
    local t
    for t in x-terminal-emulator gnome-terminal konsole xfce4-terminal xterm; do
        command -v "$t" >/dev/null 2>&1 || continue
        case "$t" in
            gnome-terminal) "$t" --wait -- bash -c "$1" 2>/dev/null && return 0 ;;
            *) "$t" -e bash -c "$1" && return 0 ;;
        esac
    done
    return 1
}

if [ -z "$ISLE" ] || ! command -v zenity >/dev/null 2>&1; then
    # no CLI or no dialog tool — the shell's own honest pages take over
    open_shell
fi

CHOICE=$(zenity --question \
    --title "Isle App Store — first run" \
    --text "This device is not part of an isle yet.\n\nThe store installs apps only on isle members (the running agent IS membership). Single-device isles are first-class — you can be your own isle.\n\nBoth doors run the exact terminal steps (privilege via polkit)." \
    --ok-label "Create my own isle" \
    --extra-button "Join an existing isle" \
    --extra-button "Just browse" \
    --width 460 2>/dev/null)
RC=$?

if [ "$RC" = 0 ]; then
    # DOOR 1: core-install — interactive (the security walkthrough asks
    # for passwords/domain at deploy time), so it runs in a real
    # terminal; pkexec provides privilege.
    term_run "echo 'Isle core install — the same command the terminal route uses:'; \
echo '  pkexec $ISLE core-install'; echo; \
pkexec $ISLE core-install; \
echo; read -p 'Done — press Enter to close and open the store...'" \
        || zenity --error --text "No terminal emulator found. Run in any terminal:\n  sudo isle core-install" 2>/dev/null
    agent_up && open_shell
    zenity --info --text "The isle is not up yet — the store opens in browse mode.\nFinish setup any time:  sudo isle core-install" 2>/dev/null
    open_shell
elif [ "$CHOICE" = "Join an existing isle" ]; then
    zenity --info --title "Join an existing isle" --width 520 \
        --text "On your isle's CORE device, 'sudo isle core-install' printed the JOIN INFO:\n\n1. Fetch the bootstrap script from the core:\n   curl -ko isle-bootstrap.sh https://apt.isle/isle-bootstrap.sh\n2. Check its sha256 against the core's printout\n3. Run it with the CA fingerprint from that printout:\n   sudo bash isle-bootstrap.sh --fingerprint '<from core>' [--host]\n\nThe fingerprint check is the trust anchor — always compare it." 2>/dev/null
    open_shell
else
    open_shell
fi
