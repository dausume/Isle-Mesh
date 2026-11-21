#!/bin/bash
# tls.sh — the one place that decides how an isle shell script checks the certificate of an HTTPS service.
# The user's ruling of 2026-11-07 (checked by default, a self-signed setting only through a named knob); `pol`
# follows the same ruling with the same shape in polari-cli/scripts/lib/tls.sh.
#   default               checked against this isle's root certificate (/etc/isle-mesh/ca/isle-root.crt) and no
#                         other, when this machine has it; otherwise against the system's certificate store
#   ISLE_TLS_CA=<pem>     checked against this file and no other (a private or self-signed deployment). This is
#                         the answer that still checks; prefer it over the next one
#   ISLE_TLS_INSECURE=1   not checked. For a setting that is not an isle. It is said once per run and once per
#                         call, on the stderr this file was sourced with, so a call's own 2>/dev/null does not hide it
#   both set              ISLE_TLS_CA wins, and that the other one is ignored is said once
#
#   source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib/tls.sh"
#   isle_tls_mode       -> isle-root | system | cacert | insecure
#   isle_curl ARGS...   -> curl with the matching option before ARGS; every other option, the output and the exit
#                          status are those of curl.  For the isle's own services (names under .isle)
#   isle_curl_site ARGS -> the same knob for a site that is not one of the isle's own services (an app's own name):
#                          the default is the system's certificate store, which on a joined machine holds the
#                          isle's root beside the public ones
#
# A first-trust fetch (the root certificate itself, before this machine trusts it) does not go through here: it is
# fetched unchecked and then compared with a fingerprint or a signature. test-unverified-calls.py lists every one.
[ "$(type -t isle_curl)" = function ] && return 0 2>/dev/null

ISLE_TLS_ROOT=/etc/isle-mesh/ca/isle-root.crt
# curl's --cacert alone ADDS a file to the system's certificate directory (measured on Ubuntu 22.04 and 24.04: a
# root that did not sign the service still passed).  Naming a directory that does not exist makes the file the
# only thing trusted, so "checked against this file" is what happens.
ISLE_TLS_ONLY="--capath /nonexistent"

# the stderr this file was sourced with, kept open so the notice survives a call's own 2>/dev/null
if [ -z "${ISLE_TLS_ERRFD:-}" ] || ! { true >&"$ISLE_TLS_ERRFD"; } 2>/dev/null; then
    { exec {ISLE_TLS_ERRFD}>&2; } || ISLE_TLS_ERRFD=2
fi

isle_tls_mode() {
    [ -n "${ISLE_TLS_CA:-}" ] && { echo cacert; return; }
    case "${ISLE_TLS_INSECURE:-}" in 1|true|yes|on) echo insecure; return ;; esac
    [ -r "$ISLE_TLS_ROOT" ] && echo isle-root || echo system
}

isle_tls_say() { printf '%s\n' "$*" >&"$ISLE_TLS_ERRFD" 2>/dev/null || printf '%s\n' "$*" >&2; }

# said once per run, whatever the calls do with their stderr; a script this run starts does not say it again
if [ -z "${ISLE_TLS_ANNOUNCED:-}" ]; then
    case "$(isle_tls_mode)" in
        insecure) isle_tls_say "[WARN] certificates are NOT checked in this isle run (ISLE_TLS_INSECURE=1)" ;;
        cacert)   case "${ISLE_TLS_INSECURE:-}" in 1|true|yes|on) isle_tls_say "[WARN] ISLE_TLS_INSECURE is ignored: ISLE_TLS_CA=$ISLE_TLS_CA is set, and certificates are checked against it" ;; esac ;;
    esac
    export ISLE_TLS_ANNOUNCED=1
fi

isle_curl() {
    case "$(isle_tls_mode)" in
        insecure)  isle_tls_say "[WARN] certificate not checked (ISLE_TLS_INSECURE=1): $(printf '%s\n' "$@" | grep -m1 '^http' || echo curl)"
                   curl --insecure "$@" ;;
        cacert)    [ -r "$ISLE_TLS_CA" ] || { isle_tls_say "[FAIL] ISLE_TLS_CA=$ISLE_TLS_CA is not a file that can be read"; return 2; }
                   curl --cacert "$ISLE_TLS_CA" $ISLE_TLS_ONLY "$@" ;;
        isle-root) curl --cacert "$ISLE_TLS_ROOT" $ISLE_TLS_ONLY "$@" ;;
        *)         curl "$@" ;;
    esac
}

isle_curl_site() { local ISLE_TLS_ROOT=; isle_curl "$@"; }
