#!/bin/sh
set -eu
read -r nic address expires < /run/isle-lease
[ "$expires" -gt "$(date +%s)" ]
kill -0 "$(cat /run/isle-dhcp.pid)"
ip -4 -o addr show dev "$nic" scope global | awk '{split($4,a,"/");print a[1]}' | grep -Fxq "$address"
wget --quiet --tries=1 --spider http://127.0.0.1/health
