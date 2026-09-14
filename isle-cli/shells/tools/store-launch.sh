#!/bin/bash
# store-launch.sh — what the Isle App Store icon actually runs.
#
# FIRST-OPEN, fresh device: there is no isle yet, so opening the store offers the doors the terminal route has.
# Revised 2026-09-14 (his rule: setup choices are CLI VERBS FIRST, the UI WRAPS them with pkexec) around what now
# exists — install MODES, member TIERS, the USB app stick:
#   1. Create my own isle        -> mode question (Production / Development + the standing dev warning)
#                                   -> pkexec store-setup.sh core-install --mode <m>   (a terminal: the walkthrough asks there)
#   2. Join an existing isle     -> tier question (Light / Host / Hardware, in plain words) + the core's CA fingerprint
#                                   -> pkexec store-setup.sh join --fingerprint <fp> --tier <t>
#   3. Install apps from a USB stick -> the stick's own prompt (Install / Not now / Wipe) — offline, presence-checked
#   4. Set up a public server    -> pol prod guide in a terminal (the developer route)
#   5. Just browse               -> open the shell anyway (honest unreachable page until an isle exists)
#
# THE UI DOES NOTHING OF ITS OWN — every door runs the exact terminal steps through ONE privileged entry point
# (store-setup.sh, pkexec). Once an agent is running (member/core), this wrapper is a plain exec of the shell —
# except that a plugged-in Polari app stick is offered first (Polari looks for it).
set -u
SHARE=/usr/share/isle-app-store
SHELL_BIN=/usr/bin/polari-app-shell
CONFIG="$SHARE/polari-shell.json"
SETUP="$SHARE/store-setup.sh"
DEV_WARNING="DEV MODE — any connection to systems that are not your own is EXTREMELY DANGEROUS: the relaxed security (root over ssh, profiles in complain, open debug ports) travels with every connection. Keep a dev-mode Polari on your own isle."

open_shell() { exec "$SHELL_BIN" --config "$CONFIG"; }

agent_up() {
    docker ps --format '{{.Names}}' 2>/dev/null \
        | grep -qE '^isle-(vlan|remote)-agent$'
}

# a mounted Polari app stick (polari-apps/index.json) — Polari looks for it
stick_mount() {
    lsblk -J -o MOUNTPOINT,RM,TRAN 2>/dev/null | python3 -c 'import sys,json
for d in json.load(sys.stdin)["blockdevices"]:
    for p in (d.get("children") or [d]):
        if (d.get("tran")=="usb" or d.get("rm")) and p.get("mountpoint"): print(p["mountpoint"])' 2>/dev/null \
        | while read -r m; do [ -f "$m/polari-apps/on-insert.sh" ] && echo "$m" && break; done
}

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
        term_run "pkexec $ISLE_BIN uninstall --everything; read -p 'Done — Enter closes...'"
        exit 0
    elif [ "$ANSWER" = "The isle is back (clear the warning)" ]; then
        pkexec "$ISLE_BIN" watch clear 2>/dev/null
    fi
    # 'Keep for now' falls through — the shell opens, the flag stays
fi

# ── a Polari app stick plugged in: the advised install path, offered first ─────────────────────────────────
STICK=$(stick_mount)
if [ -n "$STICK" ] && command -v zenity >/dev/null 2>&1 && [ ! -f "$HOME/.config/polari/stick-seen-$(basename "$STICK")" ]; then
    mkdir -p "$HOME/.config/polari" && touch "$HOME/.config/polari/stick-seen-$(basename "$STICK")"
    bash "$STICK/polari-apps/on-insert.sh"      # its own Install / Not now / Wipe prompt (pkexec inside); Not now = carry on
fi

# member/core already: straight into the store
agent_up && open_shell

# resolve the isle CLI (desktop launchers get a sanitized-ish PATH)
ISLE=$(command -v isle 2>/dev/null || true)
[ -n "$ISLE" ] || { [ -x /usr/local/bin/isle ] && ISLE=/usr/local/bin/isle; }

if [ -z "$ISLE" ] || ! command -v zenity >/dev/null 2>&1; then
    # no CLI or no dialog tool — the shell's own honest pages take over
    open_shell
fi

CHOICE=$(zenity --question \
    --title "Isle App Store — first run" \
    --text "This device is not part of an isle yet.\n\nThe store installs apps only on isle members (the running agent IS membership). Single-device isles are first-class — you can be your own isle.\n\nEvery door runs the exact terminal command (privilege via polkit) and shows it." \
    --ok-label "Create my own isle" \
    --extra-button "Join an existing isle" \
    --extra-button "Install apps from a USB stick" \
    --extra-button "Set up a public server" \
    --extra-button "Just browse" \
    --width 520 2>/dev/null)
RC=$?

if [ "$RC" = 0 ]; then
    # DOOR 1: core-install, after the MODE question (a production install has no relaxations; a development
    # install may enable ssh/root-ssh/complain — and carries the standing warning)
    MODE_ANSWER=$(zenity --question --title "Create my own isle — install mode" --width 560 \
        --text "How will this isle be used?\n\n<b>Production</b> (recommended): the secure posture — keys-only ssh, no root login, permission groups, profiles enforced as the rings apply. No relaxations.\n\n<b>Development</b>: for building and testing Polari. Security can be relaxed (ssh, even root over ssh, profiles in complain) — and the store shows this warning everywhere:\n\n$DEV_WARNING" \
        --ok-label "Production (recommended)" --extra-button "Development" --extra-button "Back" 2>/dev/null)
    MRC=$?
    if [ "$MODE_ANSWER" = "Back" ]; then exec "$0"; fi
    MODE=production; [ "$MODE_ANSWER" = "Development" ] && MODE=dev
    if [ "$MODE" = dev ]; then
        zenity --warning --title "Development mode" --width 520 --text "$DEV_WARNING" 2>/dev/null
    fi
    term_run "echo 'Isle core install (mode: $MODE) — the same command the terminal route uses:'; \
echo '  pkexec $SETUP core-install --mode $MODE     (= isle core-install, with the mode recorded)'; echo; \
pkexec $SETUP core-install --mode $MODE; \
echo; read -p 'Done — press Enter to close and open the store...'" \
        || zenity --error --text "No terminal emulator found. Run in any terminal:\n  sudo $SETUP core-install --mode $MODE" 2>/dev/null
    agent_up && open_shell
    zenity --info --text "The isle is not up yet — the store opens in browse mode.\nFinish setup any time:  sudo isle core-install" 2>/dev/null
    open_shell
elif [ "$CHOICE" = "Join an existing isle" ]; then
    # DOOR 2: the TIER question in plain words, then the core's CA fingerprint (the trust anchor), then ONE verb
    TIER_ANSWER=$(zenity --question --title "Join an existing isle — what should this computer do?" --width 600 \
        --text "<b>Light</b> — reach the isle's apps from this computer. Lightest: nothing keeps running here.\n\n<b>Host</b> — also run apps here for the isle (containers). Heavier: an agent and Docker stay running.\n\n<b>Hardware</b> — also let hardware apps use THIS machine's devices (KVM/passthrough, radios, printers, GPIO). Heaviest: libvirt + the agent; the machine must stay on. Needed for any Hardware App.\n\nYou can change this later." \
        --ok-label "Light" --extra-button "Host" --extra-button "Hardware" --extra-button "Back" 2>/dev/null)
    TRC=$?
    if [ "$TIER_ANSWER" = "Back" ]; then exec "$0"; fi
    TIER=light; [ "$TIER_ANSWER" = "Host" ] && TIER=host; [ "$TIER_ANSWER" = "Hardware" ] && TIER=hardware
    FP=$(zenity --entry --title "Join an existing isle — the core's fingerprint" --width 560 \
        --text "On your isle's CORE device, 'sudo isle core-install' printed the JOIN INFO with the CA FINGERPRINT (sha256).\nType or paste it here — it is the trust anchor; always compare it with the core's printout, never with a message someone sent you." 2>/dev/null)
    [ -n "$FP" ] || open_shell
    CORE=$(zenity --entry --title "Join an existing isle — the core's address" --width 480 \
        --text "Leave empty if 'apt.isle' already resolves on this network (the usual case). Otherwise the core's IP address:" 2>/dev/null)
    CORE_ARG=""; [ -n "$CORE" ] && CORE_ARG="--core $CORE"
    term_run "echo 'Join (tier: $TIER) — the same steps the terminal route uses, fingerprint-verified:'; \
echo \"  pkexec $SETUP join --fingerprint '$FP' --tier $TIER $CORE_ARG\"; echo; \
pkexec $SETUP join --fingerprint '$FP' --tier $TIER $CORE_ARG; \
echo; read -p 'Done — press Enter to close and open the store...'" \
        || zenity --error --text "No terminal emulator found. Run in any terminal:\n  sudo $SETUP join --fingerprint '<from core>' --tier $TIER" 2>/dev/null
    open_shell
elif [ "$CHOICE" = "Install apps from a USB stick" ]; then
    # DOOR 3: the stick's own prompt (Install / Not now / Wipe); it needs no isle to install the platform from the stick
    STICK=$(stick_mount)
    if [ -n "$STICK" ]; then
        bash "$STICK/polari-apps/on-insert.sh"
    else
        zenity --info --title "Install apps from a USB stick" --width 520 \
            --text "No Polari app stick is plugged in.\n\nA stick carries polari-apps/index.json, the platform installer and the apps (offline flavour — no internet needed). Make one on any computer with Polari:\n  pol apps usb write /media/\$USER/<stick> --apps all\n\nPlug it in and open the store again — Polari looks for it." 2>/dev/null
    fi
    agent_up && open_shell
    exec "$0"
elif [ "$CHOICE" = "Set up a public server" ]; then
    # DOOR 4: the SERVER route (pol prod — the lean swarm profile). The guide is a terminal walkthrough with menus.
    if command -v pol >/dev/null 2>&1; then
        term_run "echo 'Production server guide — the same command the terminal route uses:'; echo '  pol prod guide'; echo; \
pol prod guide; echo; read -p 'Done — press Enter to close...'" \
            || zenity --error --text "No terminal emulator found. Run in any terminal:\n  pol prod guide" 2>/dev/null
    else
        zenity --info --title "Set up a public server" --width 520 \
            --text "A public server is the developer route (docker swarm, a small VM).\n\nOn the server, with the suite checkout installed (Developer getting started in the docs):\n   pol prod guide\n\nIt asks for the domain, the certificate (auto-generated or provider-issued and auto-approved), logins, modules and installers, then deploys." 2>/dev/null
    fi
    open_shell
else
    open_shell
fi
