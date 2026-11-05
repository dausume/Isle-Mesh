#!/bin/sh
# udhcpc hook: change only the selected Isle interface, never container DNS or
# bridge/default routes. Docker bind-mounts resolv.conf; Alpine's mv breaks it.
set -eu
STATE=/run/isle-lease
case "$1" in
    deconfig|nak|leasefail)
        rm -f "$STATE"
        ip -4 addr flush dev "$interface" scope global
        ;;
    bound|renew)
        [ -n "${ip:-}" ] && [ -n "${subnet:-}" ] || exit 1
        if ! ip -4 -o addr show dev "$interface" scope global | awk '{split($4,a,"/"); print a[1]}' | grep -Fxq "$ip"; then
            ip -4 addr flush dev "$interface" scope global
        fi
        ifconfig "$interface" "$ip" netmask "$subnet" up
        lease=${lease:-60}
        case "$lease" in ''|*[!0-9]*) exit 1;; esac
        [ "$lease" -gt 0 ] || exit 1
        # One atomic marker; health checks both expiry and actual interface IP.
        printf '%s %s %s\n' "$interface" "$ip" "$(( $(date +%s) + ${lease:-60} ))" > "$STATE.tmp"
        mv "$STATE.tmp" "$STATE"
        ;;
esac
