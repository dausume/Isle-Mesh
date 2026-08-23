#!/bin/bash
# lib/security-ledger.sh — deployment-security bookkeeping for the isle CLI.
# PORTED 2026-08-16 from polari-suite/polari-rf-node/security-ledger.sh
# (same contract; isle adaptations: the ledger is DEVICE-level truth at
# /etc/isle-mesh/security-ledger.tsv, and writes escalate via polkit
# (pkexec) when a desktop session is present, sudo otherwise).
#
# All security material is put in AT DEPLOY TIME; this ledger records WHEN
# each credential artifact was last (re)generated so setup can detect
# existing material, flag anything older than SEC_STALE_DAYS (default 30),
# and updates can keep-or-rotate deliberately. Secret VALUES never enter
# the ledger — only names, timestamps, sources, content fingerprints.
# Row: name  epoch  iso8601  source  sha256-prefix

SEC_LEDGER_FILE="${SEC_LEDGER_FILE:-/etc/isle-mesh/security-ledger.tsv}"
SEC_STALE_DAYS="${SEC_STALE_DAYS:-30}"

# Run a command as root: directly when already root, polkit (pkexec) when a
# graphical session can prompt, sudo otherwise (ssh/terminal).
sec_esc() {
    if [ "$(id -u)" = 0 ]; then "$@"
    elif [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v pkexec >/dev/null 2>&1; then
        pkexec "$@"
    else
        sudo "$@"
    fi
}

sec_fingerprint() { sha256sum "$1" 2>/dev/null | cut -c1-12; }

# ledger_stamp NAME PATH SOURCE — record that PATH was (re)written now.
ledger_stamp() {
    local name=$1 path=$2 src=${3:-manual} rows
    rows=$({
        [ -f "$SEC_LEDGER_FILE" ] && grep -v "^${name}$(printf '\t')" "$SEC_LEDGER_FILE" || true
        printf '%s\t%s\t%s\t%s\t%s\n' \
            "$name" "$(date +%s)" "$(date -Iseconds)" "$src" "$(sec_fingerprint "$path")"
    })
    sec_esc mkdir -p "$(dirname "$SEC_LEDGER_FILE")"
    printf '%s\n' "$rows" | sec_esc tee "$SEC_LEDGER_FILE" >/dev/null
}

ledger_row() { [ -f "$SEC_LEDGER_FILE" ] && grep "^${1}$(printf '\t')" "$SEC_LEDGER_FILE" | tail -1 || true; }

# ledger_age_days NAME PATH — whole days since last stamped write; ledger
# stamp wins while its fingerprint matches the file, else file mtime.
# Empty output = file missing.
ledger_age_days() {
    local name=$1 path=$2 row epoch fp now
    [ -f "$path" ] || return 0
    now=$(date +%s)
    row=$(ledger_row "$name")
    if [ -n "$row" ]; then
        epoch=$(printf '%s' "$row" | cut -f2)
        fp=$(printf '%s' "$row" | cut -f5)
        if [ "$fp" = "$(sec_fingerprint "$path")" ] && [ -n "$epoch" ]; then
            echo $(( (now - epoch) / 86400 )); return 0
        fi
    fi
    epoch=$(stat -c %Y "$path" 2>/dev/null || echo "$now")
    echo $(( (now - epoch) / 86400 ))
}

# sec_placeholders PATH — count secret-bearing keys (PASSWORD/SECRET/PASS)
# whose value is a known dev default, a REPLACE_ME placeholder, or empty.
sec_placeholders() {
    [ -f "$1" ] || { echo 0; return; }
    grep -E '^[A-Za-z_]*(PASSWORD|SECRET|PASS)[A-Za-z_]*=' "$1" 2>/dev/null \
      | grep -cE '=(admin|rootpassword|kcpassword|pscpassword|polaripassword|polari-file-store-password|change-me[A-Za-z0-9_.-]*|REPLACE_ME[A-Za-z0-9_.-]*)$|=$' \
      || true
}

# sec_is_stale NAME PATH — exit 0 when older than SEC_STALE_DAYS (missing
# files are not "stale", they are missing).
sec_is_stale() {
    local age; age=$(ledger_age_days "$1" "$2")
    [ -n "$age" ] && [ "$age" -gt "$SEC_STALE_DAYS" ]
}
