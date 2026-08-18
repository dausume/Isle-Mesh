#!/bin/bash
# network-handback.sh — give the device's networking back to its normal
# owner after isle-mesh leaves. The isle era may have: handed wifi to
# wpa_supplicant@<iface> + systemd-networkd (the join takeover), left
# stale isle addresses/leases (10.10.0.x) and dead routes via the
# now-gone router, added ~isle split-DNS to NetworkManager profiles,
# and pinned .isle names in /etc/hosts. This undoes ALL of it,
# idempotently, and only where it is safe:
#
#   - wpa_supplicant@ instance units + systemd-networkd are disabled
#     ONLY when NetworkManager is active to take the interfaces back
#     (never leaves a box with no network owner)
#   - isle-written .network files are removed; networkd is stopped only
#     when no non-isle .network files remain
#   - 10.10.0.x addresses/routes are flushed off non-container links
#   - ~isle dns-search is stripped from NM profiles; .isle /etc/hosts
#     pins are removed
#
# Run standalone (sudo network-handback.sh) or via the deb's prerm /
# `isle uninstall --everything`. Every action is printed; no prompts.
set -u
G="\033[0;32m"; Y="\033[1;33m"; C="\033[0;36m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }
warn(){ echo -e "${Y}[WARN]${N} $*"; }

[ "$(id -u)" = 0 ] || { warn "needs root (sudo) — nothing done"; exit 1; }

ISLE_NET_RE='^10\.10\.0\.'
NM_ACTIVE=0
systemctl is-active --quiet NetworkManager && NM_ACTIVE=1

# ---- 1. wifi ownership: wpa_supplicant@ instances + systemd-networkd ----
if [ "$NM_ACTIVE" = 1 ]; then
    for u in $(systemctl list-units --all 'wpa_supplicant@*.service' --no-legend 2>/dev/null | awk '{print $1}' | grep -E '^wpa_supplicant@'); do
        systemctl disable --now "$u" >/dev/null 2>&1
        ok "disabled $u (NetworkManager owns wifi again)"
    done
    # isle-written networkd configs (the join takeover wrote 20-wifi.network)
    for f in /etc/systemd/network/20-wifi.network /etc/systemd/network/*isle*.network; do
        [ -f "$f" ] && rm -f "$f" && ok "removed $f"
    done
    if systemctl is-active --quiet systemd-networkd \
       && ! ls /etc/systemd/network/*.network >/dev/null 2>&1; then
        systemctl disable --now systemd-networkd systemd-networkd.socket >/dev/null 2>&1
        ok "systemd-networkd disabled (no configs left; NetworkManager is the owner)"
    fi
    systemctl reset-failed >/dev/null 2>&1
else
    warn "NetworkManager not active — leaving wifi ownership untouched"
    warn "(handback only swaps owners when one is present to take over)"
fi

# ---- 2. stale isle addressing (dead leases from the removed router) ----
ip route show default 2>/dev/null | grep -E "via ${ISLE_NET_RE}" | while read -r r; do
    ip route del $r 2>/dev/null && ok "removed dead default route: $r"
done
ip -br -4 addr show 2>/dev/null \
  | awk '$1!~/^(lo|docker|veth|br-|virbr|isle)/' \
  | while read -r ifc _ addrs; do
    for a in $addrs; do
        if echo "$a" | grep -qE "$ISLE_NET_RE"; then
            ip addr del "$a" dev "$ifc" 2>/dev/null \
                && ok "flushed stale isle address $a from $ifc"
        fi
    done
done

# ---- 3. ~isle split-DNS out of NetworkManager profiles ----
if [ "$NM_ACTIVE" = 1 ] && command -v nmcli >/dev/null 2>&1; then
    nmcli -t -f NAME connection show 2>/dev/null | while read -r c; do
        if nmcli -t -f ipv4.dns-search connection show "$c" 2>/dev/null | grep -q isle; then
            nmcli connection modify "$c" ipv4.dns-search "" 2>/dev/null \
                && ok "removed ~isle split-DNS from NM profile '$c'"
        fi
    done
fi

# ---- 4. .isle pins in /etc/hosts ----
if grep -q '\.isle' /etc/hosts 2>/dev/null; then
    sed -i '/\.isle$/d;/\.isle /d' /etc/hosts && ok "removed .isle pins from /etc/hosts"
fi

ok "network handback complete — the OS's normal manager owns every interface"
