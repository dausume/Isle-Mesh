#!/bin/bash
# Return the live Isle-facing agent address, never a host/bridge address.
# Sourcing this file performs no inspection or network mutation.
isle_agent_address() {
    local address
    address=$(sudo docker inspect isle-vlan-agent --format '{{(index .NetworkSettings.Networks "isle-br-0").IPAddress}}' 2>/dev/null) || address=""
    if [[ "$address" =~ ^10\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        printf '%s\n' "$address"
        return 0
    fi
    # Docker bridge pools vary. Select the interface by the agent's
    # configured virtual MAC, as the DHCP entrypoint does, not a subnet guess.
    sudo docker exec isle-remote-agent sh -c '
        [ -n "${VIRTUAL_MAC:-}" ] || exit 1
        for device in /sys/class/net/*; do
            [ "$(cat "$device/address" 2>/dev/null)" = "$VIRTUAL_MAC" ] || continue
            interface=${device##*/}
            ip -4 -o addr show dev "$interface" | awk '\''
                $3 == "inet" && $4 ~ /^10\./ {
                    split($4, address, "/"); print address[1]; found=1; exit
                }
                END {if (!found) exit 1}
            '\''
            exit $?
        done
        exit 1
    ' 2>/dev/null
}
