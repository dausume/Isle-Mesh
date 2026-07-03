#!/usr/bin/env bash
# Isle-Mesh AppArmor Profile Manager
# Usage: sudo ./manage-profiles.sh {install|remove|status|reload}
#
# All profiles are installed in COMPLAIN mode (log-only, non-enforcing).
# To switch to enforce mode later, use: aa-enforce <profile-name>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPARMOR_DIR="$(dirname "$SCRIPT_DIR")"
PROFILES_DIR="$APPARMOR_DIR/profiles"
ABSTRACTIONS_DIR="$APPARMOR_DIR/abstractions"

SYSTEM_PROFILES="/etc/apparmor.d"
SYSTEM_ABSTRACTIONS="/etc/apparmor.d/abstractions"
SYSTEM_LOCAL="/etc/apparmor.d/local"

PROFILES=(
    isle-mesh-host-agent
    isle-mesh-mdns
    isle-mesh-cli
    isle-mesh-router-setup
    isle-mesh-docker
    isle-mesh-app
)

ABSTRACTIONS=(
    isle-mesh-base
    isle-mesh-secrets-deny
)

log() { echo "[isle-mesh-apparmor] $*"; }
err() { echo "[isle-mesh-apparmor] ERROR: $*" >&2; }

check_root() {
    if [[ $EUID -ne 0 ]]; then
        err "Must run as root"
        exit 1
    fi
}

install_profiles() {
    check_root
    log "Installing AppArmor abstractions..."
    for abst in "${ABSTRACTIONS[@]}"; do
        cp "$ABSTRACTIONS_DIR/$abst" "$SYSTEM_ABSTRACTIONS/$abst"
        log "  Installed abstraction: $abst"
    done

    log "Installing AppArmor profiles (complain mode)..."
    for profile in "${PROFILES[@]}"; do
        cp "$PROFILES_DIR/$profile" "$SYSTEM_PROFILES/$profile"

        # Create empty local override file if it doesn't exist
        if [[ ! -f "$SYSTEM_LOCAL/$profile" ]]; then
            mkdir -p "$SYSTEM_LOCAL"
            touch "$SYSTEM_LOCAL/$profile"
        fi

        # Load in complain mode
        if command -v aa-complain &>/dev/null; then
            aa-complain "$SYSTEM_PROFILES/$profile" 2>/dev/null || true
        else
            apparmor_parser -r -C "$SYSTEM_PROFILES/$profile" 2>/dev/null || true
        fi

        log "  Installed profile: $profile (complain mode)"
    done

    log "All profiles installed in complain mode."
    log "Check /var/log/syslog or journalctl for AppArmor audit messages."
}

remove_profiles() {
    check_root
    log "Removing AppArmor profiles..."
    for profile in "${PROFILES[@]}"; do
        if [[ -f "$SYSTEM_PROFILES/$profile" ]]; then
            # Unload the profile
            apparmor_parser -R "$SYSTEM_PROFILES/$profile" 2>/dev/null || true
            rm -f "$SYSTEM_PROFILES/$profile"
            rm -f "$SYSTEM_LOCAL/$profile"
            log "  Removed: $profile"
        fi
    done

    for abst in "${ABSTRACTIONS[@]}"; do
        rm -f "$SYSTEM_ABSTRACTIONS/$abst"
        log "  Removed abstraction: $abst"
    done

    log "All isle-mesh profiles removed."
}

reload_profiles() {
    check_root
    log "Reloading AppArmor profiles..."
    for profile in "${PROFILES[@]}"; do
        if [[ -f "$SYSTEM_PROFILES/$profile" ]]; then
            apparmor_parser -r "$SYSTEM_PROFILES/$profile" 2>/dev/null && \
                log "  Reloaded: $profile" || \
                err "  Failed to reload: $profile"
        fi
    done
}

show_status() {
    echo "=== Isle-Mesh AppArmor Profile Status ==="
    echo ""

    if ! command -v aa-status &>/dev/null; then
        err "aa-status not found. Install apparmor-utils."
        exit 1
    fi

    local aa_output
    aa_output=$(aa-status 2>/dev/null || sudo aa-status 2>/dev/null)

    for profile in "${PROFILES[@]}"; do
        if echo "$aa_output" | grep -q "$profile"; then
            local mode="unknown"
            if echo "$aa_output" | grep -A0 "complain" | grep -q "$profile"; then
                mode="complain"
            elif echo "$aa_output" | grep -A0 "enforce" | grep -q "$profile"; then
                mode="enforce"
            fi
            printf "  %-35s %s\n" "$profile" "$mode"
        else
            printf "  %-35s %s\n" "$profile" "not loaded"
        fi
    done

    echo ""
    echo "=== Filesystem Permissions ==="
    if [[ -d /etc/isle-mesh ]]; then
        ls -la /etc/isle-mesh/ 2>/dev/null
        echo ""
        echo "Router SSH key permissions:"
        ls -la /etc/isle-mesh/router/ssh/ 2>/dev/null || echo "  (not present)"
        echo ""
        echo "SSL key permissions:"
        ls -la /etc/isle-mesh/agent/ssl/keys/ 2>/dev/null || echo "  (not present)"
    else
        echo "  /etc/isle-mesh/ does not exist yet"
    fi
}

harden_filesystem() {
    check_root
    log "Hardening isle-mesh filesystem permissions..."

    # Ensure isle-mesh group exists
    if ! getent group isle-mesh &>/dev/null; then
        groupadd isle-mesh
        log "  Created group: isle-mesh"
    fi

    if [[ -d /etc/isle-mesh ]]; then
        # Base directory: group-owned, no world access
        chown -R root:isle-mesh /etc/isle-mesh
        chmod 2750 /etc/isle-mesh

        # SSH keys: most restricted
        if [[ -d /etc/isle-mesh/router/ssh ]]; then
            chmod 2750 /etc/isle-mesh/router/ssh
            chmod 640 /etc/isle-mesh/router/ssh/* 2>/dev/null || true
            log "  Hardened: router SSH keys (640)"
        fi

        # SSL private keys
        if [[ -d /etc/isle-mesh/agent/ssl/keys ]]; then
            chmod 2750 /etc/isle-mesh/agent/ssl/keys
            chmod 640 /etc/isle-mesh/agent/ssl/keys/* 2>/dev/null || true
            log "  Hardened: SSL private keys (640)"
        fi

        # Agent config (may contain tokens)
        if [[ -f /etc/isle-mesh/agent/host-agent.conf ]]; then
            chmod 640 /etc/isle-mesh/agent/host-agent.conf
            log "  Hardened: host-agent.conf (640)"
        fi

        # Cached password
        if [[ -f /etc/isle-mesh/router/ssh/.cached_password ]]; then
            chmod 600 /etc/isle-mesh/router/ssh/.cached_password
            log "  Hardened: cached_password (600)"
        fi

        # Everything else: group-readable
        find /etc/isle-mesh -type d -exec chmod g+rx {} \;
        find /etc/isle-mesh -type f ! -path "*/ssh/*" ! -path "*/ssl/keys/*" \
             ! -name "host-agent.conf" -exec chmod g+r {} \;
    fi

    # Log directory
    if [[ -d /var/log/isle-mesh ]]; then
        chown root:isle-mesh /var/log/isle-mesh
        chmod 2750 /var/log/isle-mesh
        log "  Hardened: /var/log/isle-mesh (2750)"
    fi

    log "Filesystem hardening complete."
}

case "${1:-help}" in
    install)
        install_profiles
        ;;
    remove)
        remove_profiles
        ;;
    reload)
        reload_profiles
        ;;
    status)
        show_status
        ;;
    harden)
        harden_filesystem
        ;;
    all)
        harden_filesystem
        install_profiles
        ;;
    help|*)
        echo "Usage: $0 {install|remove|reload|status|harden|all}"
        echo ""
        echo "  install  - Install AppArmor profiles in complain mode"
        echo "  remove   - Unload and remove all isle-mesh profiles"
        echo "  reload   - Reload profiles after editing"
        echo "  status   - Show profile and filesystem status"
        echo "  harden   - Set restrictive filesystem permissions (outward barrier)"
        echo "  all      - Run harden + install"
        ;;
esac
