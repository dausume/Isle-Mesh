#!/usr/bin/env bash
# The core agent uses an untagged dedicated VM NIC; configure it before adding cables.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/../utilities/add-ethernet-connection-lib/45-openwrt-network.sh"
_router_probe || { echo 'Router SSH unavailable' >&2; exit 1; }
# This bootstrap currently matches the core agent compose contract: eth1, VLAN10.
# eth0/br-lan is management and must not be reassigned.
_router_ssh 'sh -s' <<'ROUTER'
set -eu
test "$(uci -q get network.lan.device)" = br-lan
test -d /sys/class/net/eth1
for section in wan wan6; do
    if [ "$(uci -q get network.$section.device || true)" = eth1 ]; then uci delete network.$section; fi
done
uci set network.islebr=device
uci set network.islebr.name='br-isle'
uci set network.islebr.type='bridge'
uci -q get network.islebr.ports | grep -qw eth1 || uci add_list network.islebr.ports='eth1'
uci set network.myisle=interface
uci set network.myisle.device='br-isle'
uci set network.myisle.proto='static'
uci set network.myisle.ipaddr='10.10.0.1'
uci set network.myisle.netmask='255.255.255.0'
uci set dhcp.myisle=dhcp
uci set dhcp.myisle.interface='myisle'
uci set dhcp.myisle.start='50'
uci set dhcp.myisle.limit='200'
uci set dhcp.myisle.leasetime='12h'
uci set dhcp.myisle.dhcpv4='server'
uci set dhcp.myisle.dynamicdhcp='1'
uci -q delete dhcp.myisle.dhcp_option || true
uci add_list dhcp.myisle.dhcp_option='3'
uci add_list dhcp.myisle.dhcp_option='6,10.10.0.1'
uci set dhcp.isle_router=domain
uci set dhcp.isle_router.name='openwrt.isle'
uci set dhcp.isle_router.ip='10.10.0.1'
uci set firewall.myisle=zone
uci set firewall.myisle.name='myisle'
uci set firewall.myisle.network='myisle'
uci set firewall.myisle.input='ACCEPT'
uci set firewall.myisle.output='ACCEPT'
uci set firewall.myisle.forward='REJECT'
uci commit network
uci commit dhcp
uci commit firewall
/etc/init.d/network reload
/etc/init.d/firewall restart
/etc/init.d/dnsmasq restart
ROUTER
for attempt in 1 2 3 4 5; do
    if _router_ssh 'ip -4 addr show br-isle' | grep -q '10.10.0.1/24'; then
        echo 'Core Isle network configured; management retained.'
        exit 0
    fi
    sleep 2
done
echo 'Core Isle network failed verification' >&2
exit 1
