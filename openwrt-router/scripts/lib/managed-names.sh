#!/bin/sh
# managed-names.sh - the router-side half of the storage ownership fence (storage contract section 5).
# /etc/isle-managed-names (one FQDN per line, pushed by the core: `isle dns managed-names push`) lists names
# that only a storage operation may write.  Router scripts that write dnsmasq entries from discovery must
# skip them.  A missing or unreadable file means nothing is managed.  POSIX sh: it also runs on the router
# (configure-join-protocol.sh carries a verbatim copy of isle_managed_skip inside its daemon heredoc; the
# test compares the two).
ISLE_MANAGED_NAMES_FILE="${ISLE_MANAGED_NAMES_FILE:-/etc/isle-managed-names}"

# isle_managed_skip <fqdn> [file]: returns 0 (and logs) when the name is managed, 1 otherwise.
isle_managed_skip() {
    _mn_file="${2:-$ISLE_MANAGED_NAMES_FILE}"
    [ -r "$_mn_file" ] || return 1
    if tr -d '\r' < "$_mn_file" | grep -Fxq -- "$1"; then
        if command -v log_info >/dev/null 2>&1; then log_info "managed name skipped: $1"; else echo "managed name skipped: $1" >&2; fi
        return 0
    fi
    return 1
}
