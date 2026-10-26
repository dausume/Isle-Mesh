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
HANDOFF_FAILED=0
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
ip route show default 2>/dev/null | grep -E "via 10\.10\.0\." | while read -r r; do
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

# ---- 3. Remove ONLY Isle search domains, preserving user domains ----
if [ "$NM_ACTIVE" = 1 ] && command -v nmcli >/dev/null 2>&1; then
    while IFS= read -r uuid; do
        [ -n "$uuid" ] || continue
        for family in ipv4 ipv6; do
            domains=$(nmcli -g "$family.dns-search" connection show uuid "$uuid" 2>/dev/null) || continue
            IFS=',' read -r -a entries <<< "$domains"
            for domain in "${entries[@]}"; do
                case "$domain" in
                    isle|~isle|isle.|~isle.)
                        nmcli connection modify uuid "$uuid" "-$family.dns-search" "$domain" \
                            || HANDOFF_FAILED=1 ;;
                esac
            done
        done
    done < <(nmcli -g UUID connection show 2>/dev/null)
fi

# ---- 3b. Restore resolved from NetworkManager's effective connection ----
# Reapply can return success WITHOUT re-sending DNS after resolved/networkd
# restart. Explicitly publish NM's current DNS, without reassociation or DHCP
# release. Run after profile cleanup, never before it.
if [ "$NM_ACTIVE" = 1 ] && command -v nmcli >/dev/null 2>&1; then
    while IFS=: read -r dev state; do
        [ "$state" = connected ] || continue
        case "$dev" in lo|docker*|veth*|br-*|virbr*|isle*) continue ;; esac
        nmcli device reapply "$dev" >/dev/null 2>&1 || HANDOFF_FAILED=1
        mapfile -t servers < <(nmcli --escape no -t -f IP4.DNS,IP6.DNS device show "$dev" 2>/dev/null | sed 's/^[^:]*://;/^$/d;/^--$/d')
        if [ "${#servers[@]}" -gt 0 ]; then
            resolvectl dns "$dev" "${servers[@]}" || HANDOFF_FAILED=1
            mapfile -t domains < <(nmcli --escape no -t -f IP4.DOMAIN,IP6.DOMAIN device show "$dev" 2>/dev/null \
                | sed -E 's/^[^:]*://;/^$/d;/^--$/d;/^~?isle\.?$/d')
            if ip -4 route show default dev "$dev" | grep -q '^default'; then
                domains+=("~.")
                resolvectl default-route "$dev" yes || HANDOFF_FAILED=1
            fi
            resolvectl domain "$dev" "${domains[@]}" || HANDOFF_FAILED=1
            ok "restored NM DNS on $dev (without disconnecting Wi-Fi)"
        fi
    done < <(nmcli -t -f DEVICE,STATE device 2>/dev/null)
    resolvectl flush-caches || HANDOFF_FAILED=1
fi

# ---- 4. .isle pins in /etc/hosts ----
if grep -q '\.isle' /etc/hosts 2>/dev/null; then
    sed -i '/\.isle$/d;/\.isle /d' /etc/hosts && ok "removed .isle pins from /etc/hosts"
fi

# Do not report success merely because service-control commands returned zero.
if [ "$NM_ACTIVE" != 1 ] || ! ip route show default | grep -q '^default'; then
    warn "network handback incomplete: no active NetworkManager/default route"
    HANDOFF_FAILED=1
elif ! timeout 12 getent ahosts example.com >/dev/null 2>&1; then
    warn "network handback incomplete: public DNS lookup failed"
    HANDOFF_FAILED=1
fi
if [ "$HANDOFF_FAILED" != 0 ]; then
    warn "network restoration did not verify; connection was not forcibly restarted"
    exit 1
fi
ok "network handback verified: NetworkManager active, default route and public DNS available"
