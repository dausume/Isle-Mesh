#!/bin/bash
#
# Isle Security — ISP Visibility & Network Hardening
#
# Usage:
#   isle security                Show human-readable security status
#   isle security check          Machine-readable output (for manager app)
#   isle security harden         Apply all hardening (requires sudo)
#   isle security harden <item>  Fix a specific issue
#   isle security unharden       Remove hardening rules
#   isle security help           Show help
#
# Threat model: prevent ISP from detecting isle-mesh presence.
# Not designed to evade active monitoring — just passive observation.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

IPTABLES_CHAIN="ISLE-MESH-BLOCK"

# ═══════════════════════════════════════════
# Detection helpers
# ═══════════════════════════════════════════

detect_isp_interface() {
    ip route show default 2>/dev/null | awk '{print $5}' | head -1
}

detect_isp_ip() {
    local iface
    iface=$(detect_isp_interface)
    [[ -z "$iface" ]] && return
    ip -4 addr show "$iface" 2>/dev/null | grep -oP '(?<=inet )\d+(\.\d+){3}' | head -1
}

ssh_router() {
    local key="/etc/isle-mesh/router/ssh/isle_router_key"
    local opts="-o BatchMode=yes -o ConnectTimeout=3 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
    if [[ -f "$key" ]]; then
        ssh -i "$key" $opts root@192.168.1.1 "$@" 2>/dev/null
    else
        ssh $opts root@192.168.1.1 "$@" 2>/dev/null
    fi
}

# ═══════════════════════════════════════════
# Individual checks — each returns pass/fail/warn
# ═══════════════════════════════════════════

# 1. Ports 80/443 not exposed on ISP interface
check_port_exposure() {
    local exposed_80
    exposed_80=$(ss -tlnp 2>/dev/null | grep ':80 ' | grep '0.0.0.0' || echo "")
    local exposed_443
    exposed_443=$(ss -tlnp 2>/dev/null | grep ':443 ' | grep '0.0.0.0' || echo "")

    if [[ -z "$exposed_80" ]] && [[ -z "$exposed_443" ]]; then
        echo "pass"
    else
        echo "fail"
    fi
}

# 2. mDNS not broadcasting on ISP interface
check_mdns_exposure() {
    local isp_iface
    isp_iface=$(detect_isp_interface)
    [[ -z "$isp_iface" ]] && echo "skip" && return

    local avahi_conf="/etc/avahi/avahi-daemon.conf"
    if [[ ! -f "$avahi_conf" ]]; then
        echo "skip"
        return
    fi

    # Check deny-interfaces
    if grep -q "^deny-interfaces=.*${isp_iface}" "$avahi_conf" 2>/dev/null; then
        echo "pass"
        return
    fi

    # Check allow-interfaces (must exist and NOT include ISP)
    if grep -q "^allow-interfaces=" "$avahi_conf" 2>/dev/null; then
        if ! grep "^allow-interfaces=" "$avahi_conf" | grep -q "$isp_iface"; then
            echo "pass"
            return
        fi
    fi

    echo "fail"
}

# 3. Discovery beacon port not exposed on host
check_discovery_exposure() {
    local exposed
    exposed=$(ss -ulnp 2>/dev/null | grep ':7878' | grep '0.0.0.0' || echo "")
    if [[ -z "$exposed" ]]; then
        echo "pass"
    else
        echo "fail"
    fi
}

# 4. Host firewall blocks isle ports on ISP interface
check_firewall_rules() {
    # Try without sudo first, then with sudo
    if iptables -L "$IPTABLES_CHAIN" -n &>/dev/null 2>&1; then
        echo "pass"
    elif sudo -n iptables -L "$IPTABLES_CHAIN" -n &>/dev/null 2>&1; then
        echo "pass"
    else
        echo "fail"
    fi
}

# 5. No route from isle subnet to ISP
check_route_isolation() {
    local isp_iface
    isp_iface=$(detect_isp_interface)
    [[ -z "$isp_iface" ]] && echo "skip" && return

    if ip route show 2>/dev/null | grep -q "10\.10\.0\.0.*$isp_iface"; then
        echo "fail"
    else
        echo "pass"
    fi
}

# 6. No NAT/masquerade from isle to ISP
check_no_nat() {
    local nat_rules
    nat_rules=$(iptables -t nat -L POSTROUTING -n 2>/dev/null || sudo -n iptables -t nat -L POSTROUTING -n 2>/dev/null || echo "")
    nat_rules=$(echo "$nat_rules" | grep -i "masq\|SNAT" | grep -E "10\.10\.|192\.168\.1\." || echo "")
    if [[ -z "$nat_rules" ]]; then
        echo "pass"
    else
        echo "fail"
    fi
}

# 7. Router VM has no default gateway (air-gapped)
check_router_airgap() {
    local default_gw
    default_gw=$(ssh_router "ip route show default" 2>/dev/null || echo "")
    if [[ -z "$default_gw" ]]; then
        echo "pass"
    else
        echo "fail"
    fi
}

# 8. Router VM has no ISP DNS servers
check_router_dns_isolated() {
    local resolv
    resolv=$(ssh_router "cat /etc/resolv.conf" 2>/dev/null || echo "")
    # Should only have 127.0.0.1 or ::1, not any external DNS
    if echo "$resolv" | grep -qE 'nameserver.*(8\.8\.|1\.1\.|9\.9\.|208\.67|ISP)'; then
        echo "fail"
    else
        echo "pass"
    fi
}

# 9. Docker compose binds to localhost not 0.0.0.0
check_compose_bindings() {
    local compose_file="${PROJECT_ROOT}/isle-agent/docker-compose.yml"
    if [[ ! -f "$compose_file" ]]; then
        echo "skip"
        return
    fi

    if grep -qE '"[0-9]+:[0-9]+"' "$compose_file" 2>/dev/null; then
        # Has bare port bindings like "80:80" (exposed on 0.0.0.0)
        echo "fail"
    else
        echo "pass"
    fi
}

# 10. SSL/TLS certs not using real domain names (no WHOIS trail)
check_cert_names() {
    local cert_dir="/etc/isle-mesh/agent/ssl/certs"
    if [[ ! -d "$cert_dir" ]] || [[ -z "$(ls "$cert_dir" 2>/dev/null)" ]]; then
        echo "skip"
        return
    fi

    local has_real_domain=false
    for cert in "$cert_dir"/*.crt; do
        [[ ! -f "$cert" ]] && continue
        local cn
        cn=$(openssl x509 -in "$cert" -noout -subject 2>/dev/null | grep -oP 'CN\s*=\s*\K.*' || echo "")
        # Real domains have TLDs like .com, .org, .net — .local and .isle are safe
        if echo "$cn" | grep -qE '\.(com|org|net|io|dev|app|me)$'; then
            has_real_domain=true
        fi
    done

    if $has_real_domain; then
        echo "warn"
    else
        echo "pass"
    fi
}

# 11. No isle-mesh identifiers in ISP-visible DNS queries
check_dns_leakage() {
    local split_dns="/etc/dnsmasq.d/split-dns.conf"
    if [[ ! -f "$split_dns" ]]; then
        echo "skip"
        return
    fi

    # .isle queries should go to local router, not upstream
    if grep -q 'server=/.isle/' "$split_dns"; then
        local target
        target=$(grep 'server=/.isle/' "$split_dns" | sed 's|server=/.isle/||')
        # Must be a local IP, not an external DNS
        if echo "$target" | grep -qE '^(127\.|192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[01])\.)'; then
            echo "pass"
        else
            echo "fail"
        fi
    else
        echo "warn"
    fi
}

# 12. Containment: a node ON the isle cannot reach/scan the real network.
# This is the "compromise scenario" test. We use the isle's OWN device-scan
# capability (the same reachability `isle scan` relies on — ICMP) but run it FROM
# inside the isle (the agent container) aimed at the real host/LAN. If the isle
# can reach the real network, an attacker who compromised an isle node could
# enumerate or pivot into your normal network. Contained = it reaches nothing.
check_scan_containment() {
    # Vantage point: a running isle agent container sits on the isle network.
    local container=""
    local c
    for c in isle-vlan-agent isle-remote-agent isle-agent; do
        if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${c}$"; then
            container="$c"; break
        fi
    done
    [[ -z "$container" ]] && { echo "skip"; return; }

    # The real-network targets the isle must NOT be able to reach.
    local isp_ip isp_gw
    isp_ip=$(detect_isp_ip)
    isp_gw=$(ip route show default 2>/dev/null | awk '{print $3}' | head -1)
    [[ -z "$isp_ip" && -z "$isp_gw" ]] && { echo "skip"; return; }

    # Scan from the isle toward the real network (1s timeout per target).
    local target reached=false
    for target in "$isp_ip" "$isp_gw"; do
        [[ -z "$target" ]] && continue
        if docker exec "$container" sh -c "ping -c1 -W1 ${target} >/dev/null 2>&1"; then
            reached=true; break
        fi
    done

    if $reached; then echo "fail"; else echo "pass"; fi
}

# ═══════════════════════════════════════════
# Test catalogue — organized into overarching KINDS of isolation, each with a
# plain-language purpose. Entry format:  category:func_name:label:description
# ═══════════════════════════════════════════

# The overarching kinds of isolation we verify, and WHY each matters.
#
# These tests verify isolation at the NETWORK/CONFIG level at runtime. The same
# kinds are reinforced at the PROCESS level by the AppArmor profiles in
# security/apparmor/, where each rule is tagged with a matching
# "[isolation: <kind>]" comment. In particular the `containment` kind below is the
# runtime counterpart to AppArmor's confinement (a compromised isle process/node
# cannot reach the real network, host, or secrets). The two are designed to read
# as one model: this catalogue = "does isolation hold?"; AppArmor = "enforce it".
CATEGORIES=(
    "exposure:Inbound Exposure:Can anything on your normal Wi-Fi/LAN or ISP SEE that isle-mesh exists, or reach its services?"
    "egress:Network Isolation (Egress):Can isle traffic ESCAPE outward to your normal network or the internet?"
    "router:Router Air-gap:Is the OpenWRT router VM sealed off from the internet and your ISP's DNS?"
    "footprint:Footprint & Metadata:Does isle-mesh leave traceable artifacts — open binds, real domain names, DNS leaks?"
    "containment:Containment (Compromise Scenario):If a node ON the isle were compromised, could it reach or scan your real network/host?"
)

CHECKS=(
    "exposure:port_exposure:Ports 80/443 not exposed to ISP:Web server ports bound to 0.0.0.0 are visible to anyone on your ISP's network"
    "exposure:mdns_exposure:mDNS not broadcasting to ISP:Avahi broadcasts service names on all interfaces including WiFi/ethernet to ISP"
    "exposure:discovery_exposure:Discovery beacon not on host:UDP 7878 beacon could reveal isle-mesh presence if exposed"
    "egress:firewall_rules:Firewall blocks isle ports on ISP:iptables rules prevent external access to isle-mesh services"
    "egress:route_isolation:No route from isle to ISP:Isle subnet traffic cannot reach the ISP-facing interface"
    "egress:no_nat:No NAT from isle to ISP:No masquerading rules that would forward isle traffic to the internet"
    "router:router_airgap:Router VM air-gapped:OpenWRT router has no default gateway — cannot reach the internet"
    "router:router_dns_isolated:Router DNS is local only:Router resolves DNS locally, not through ISP's DNS servers"
    "footprint:compose_bindings:Docker ports bound to localhost:docker-compose.yml uses 127.0.0.1 bindings, not 0.0.0.0"
    "footprint:cert_names:SSL certs use local domains only:No real domain names in certificates that could be traced via WHOIS"
    "footprint:dns_leakage:No .isle DNS leaking upstream:Queries for .isle domains resolve locally, never sent to ISP's DNS"
    "containment:scan_containment:Isle cannot scan the real network:A compromised isle node must not reach your real host/LAN — verified by scanning outward from inside the isle"
)

show_status() {
    local isp_iface
    isp_iface=$(detect_isp_interface)
    local isp_ip
    isp_ip=$(detect_isp_ip)

    echo ""
    echo -e "${BOLD}Isle-Mesh ISP Visibility Report${NC}"
    echo -e "ISP interface: ${CYAN}${isp_iface:-none}${NC} (${isp_ip:-no IP})"
    echo ""

    local passed=0 failed=0 warned=0 skipped=0

    # Walk each overarching KIND of isolation, explain its purpose, then its checks.
    local cat_entry cat_key cat_name cat_purpose
    for cat_entry in "${CATEGORIES[@]}"; do
        IFS=':' read -r cat_key cat_name cat_purpose <<< "$cat_entry"
        echo -e "${BOLD}${CYAN}${cat_name}${NC}"
        echo -e "  ${BLUE}${cat_purpose}${NC}"

        local entry category func_name label description
        for entry in "${CHECKS[@]}"; do
            IFS=':' read -r category func_name label description <<< "$entry"
            [[ "$category" != "$cat_key" ]] && continue

            local result
            result=$(check_"$func_name" || echo "fail")

            local icon color
            case "$result" in
                pass)    icon="[OK]"; color="$GREEN"; ((passed++)) ;;
                fail)    icon="[!!]"; color="$RED"; ((failed++)) ;;
                warn)    icon="[??]"; color="$YELLOW"; ((warned++)) ;;
                skip)    icon="[--]"; color="$BLUE"; ((skipped++)) ;;
            esac

            echo -e "${color}    ${icon}${NC} ${label}"
            # Always show the self-explanation so each test states its purpose.
            echo -e "         ${description}"
        done
        echo ""
    done
    echo -e "  ${GREEN}Pass: ${passed}${NC}  ${RED}Fail: ${failed}${NC}  ${YELLOW}Warn: ${warned}${NC}  ${BLUE}Skip: ${skipped}${NC}"
    echo ""

    if [[ $failed -gt 0 ]]; then
        echo -e "  Run ${BOLD}sudo isle security harden${NC} to fix issues."
    else
        echo -e "  ${GREEN}Isle-mesh is not visible to your ISP.${NC}"
    fi
    echo ""
}

# ═══════════════════════════════════════════
# Machine-readable check (for manager app)
# ═══════════════════════════════════════════

run_check_mode() {
    local isp_iface
    isp_iface=$(detect_isp_interface)
    echo "security.isp_interface=${isp_iface:-none}"
    echo "security.isp_ip=$(detect_isp_ip)"

    # Emit each kind's metadata (so the app can group + explain dynamically)...
    local cat_entry cat_key cat_name cat_purpose
    for cat_entry in "${CATEGORIES[@]}"; do
        IFS=':' read -r cat_key cat_name cat_purpose <<< "$cat_entry"
        echo "security.kind.${cat_key}=${cat_name}|${cat_purpose}"
    done

    # ...then each check's result + which kind it belongs to (existing keys kept).
    local entry category func_name label description
    for entry in "${CHECKS[@]}"; do
        IFS=':' read -r category func_name label description <<< "$entry"
        local result
        result=$(check_"$func_name" || echo "fail")
        echo "security.${func_name}=${result}"
        echo "security.category.${func_name}=${category}"
    done
}

# ═══════════════════════════════════════════
# Hardening actions
# ═══════════════════════════════════════════

harden_port_exposure() {
    echo -e "${BLUE}[FIX]${NC} Updating docker-compose.yml to bind to localhost..."
    local compose="${PROJECT_ROOT}/isle-agent/docker-compose.yml"
    if grep -qE '^\s*- "[0-9]+:[0-9]+"' "$compose" 2>/dev/null; then
        sed -i 's/"80:80"/"127.0.0.1:80:80"/' "$compose"
        sed -i 's/"443:443"/"127.0.0.1:443:443"/' "$compose"
        echo -e "${GREEN}[OK]${NC} docker-compose.yml updated — restart agent to apply"
    else
        echo -e "${GREEN}[OK]${NC} Already bound to localhost"
    fi
}

harden_mdns_exposure() {
    local isp_iface
    isp_iface=$(detect_isp_interface)
    [[ -z "$isp_iface" ]] && return

    echo -e "${BLUE}[FIX]${NC} Restricting avahi to deny ${isp_iface}..."
    local avahi_conf="/etc/avahi/avahi-daemon.conf"
    if [[ -f "$avahi_conf" ]]; then
        sed -i '/^deny-interfaces=/d' "$avahi_conf"
        sed -i '/^allow-interfaces=/d' "$avahi_conf"
        sed -i "/^\[server\]/a deny-interfaces=${isp_iface}" "$avahi_conf"
        systemctl restart avahi-daemon 2>/dev/null || true
        echo -e "${GREEN}[OK]${NC} Avahi restricted"
    fi
}

harden_firewall_rules() {
    local isp_iface
    isp_iface=$(detect_isp_interface)
    [[ -z "$isp_iface" ]] && return

    echo -e "${BLUE}[FIX]${NC} Adding firewall rules on ${isp_iface}..."

    iptables -N "$IPTABLES_CHAIN" 2>/dev/null || iptables -F "$IPTABLES_CHAIN"

    # Block inbound isle-mesh ports from ISP
    iptables -A "$IPTABLES_CHAIN" -i "$isp_iface" -p tcp --dport 80 -j DROP
    iptables -A "$IPTABLES_CHAIN" -i "$isp_iface" -p tcp --dport 443 -j DROP
    iptables -A "$IPTABLES_CHAIN" -i "$isp_iface" -p udp --dport 7878 -j DROP
    iptables -A "$IPTABLES_CHAIN" -i "$isp_iface" -p udp --dport 5353 -j DROP

    # Block outbound isle traffic to ISP
    iptables -A "$IPTABLES_CHAIN" -o "$isp_iface" -s 10.10.0.0/24 -j DROP
    iptables -A "$IPTABLES_CHAIN" -o "$isp_iface" -s 192.168.1.0/24 -j DROP

    # Block forwarding between isle and ISP
    iptables -A "$IPTABLES_CHAIN" -i br-mgmt -o "$isp_iface" -j DROP
    iptables -A "$IPTABLES_CHAIN" -i isle-br-0 -o "$isp_iface" -j DROP

    # Hook into main chains
    iptables -C INPUT -j "$IPTABLES_CHAIN" 2>/dev/null || iptables -I INPUT 1 -j "$IPTABLES_CHAIN"
    iptables -C FORWARD -j "$IPTABLES_CHAIN" 2>/dev/null || iptables -I FORWARD 1 -j "$IPTABLES_CHAIN"
    iptables -C OUTPUT -j "$IPTABLES_CHAIN" 2>/dev/null || iptables -I OUTPUT 1 -j "$IPTABLES_CHAIN"

    # Persist
    if command -v netfilter-persistent &>/dev/null; then
        netfilter-persistent save 2>/dev/null || true
    elif command -v iptables-save &>/dev/null; then
        mkdir -p /etc/iptables
        iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
    fi

    echo -e "${GREEN}[OK]${NC} Firewall rules applied and saved"
}

harden_all() {
    if [[ $EUID -ne 0 ]]; then
        echo "Must run as root: sudo isle security harden"
        exit 1
    fi

    echo -e "${BOLD}Applying isle-mesh network hardening...${NC}"
    echo ""

    harden_port_exposure
    harden_mdns_exposure
    harden_firewall_rules

    echo ""
    echo -e "${GREEN}${BOLD}Hardening complete.${NC}"
    echo ""

    show_status
}

harden_single() {
    if [[ $EUID -ne 0 ]]; then
        echo "Must run as root: sudo isle security harden $1"
        exit 1
    fi

    case "$1" in
        ports)     harden_port_exposure ;;
        mdns)      harden_mdns_exposure ;;
        firewall)  harden_firewall_rules ;;
        *)
            echo "Unknown item: $1"
            echo "Available: ports, mdns, firewall"
            exit 1
            ;;
    esac
}

unharden() {
    if [[ $EUID -ne 0 ]]; then
        echo "Must run as root: sudo isle security unharden"
        exit 1
    fi

    echo -e "${BOLD}Removing isle-mesh network hardening...${NC}"

    # Remove iptables
    iptables -D INPUT -j "$IPTABLES_CHAIN" 2>/dev/null || true
    iptables -D FORWARD -j "$IPTABLES_CHAIN" 2>/dev/null || true
    iptables -D OUTPUT -j "$IPTABLES_CHAIN" 2>/dev/null || true
    iptables -F "$IPTABLES_CHAIN" 2>/dev/null || true
    iptables -X "$IPTABLES_CHAIN" 2>/dev/null || true
    echo -e "${GREEN}[OK]${NC} Firewall rules removed"

    # Restore avahi
    if [[ -f /etc/avahi/avahi-daemon.conf ]]; then
        sed -i '/^deny-interfaces=/d' /etc/avahi/avahi-daemon.conf
        systemctl restart avahi-daemon 2>/dev/null || true
        echo -e "${GREEN}[OK]${NC} Avahi restrictions removed"
    fi

    echo ""
    echo -e "${GREEN}Hardening removed.${NC}"
}

show_help() {
    cat <<EOF
${BOLD}Isle Security${NC} — ISP Visibility & Network Hardening

${CYAN}USAGE:${NC}
  isle security                Show ISP visibility status
  isle security check          Machine-readable output (for manager app)
  isle security harden         Apply all hardening (sudo required)
  isle security harden <item>  Fix one item: ports, mdns, firewall
  isle security unharden       Remove all hardening
  isle security help           Show this help

${CYAN}WHAT IT CHECKS:${NC}

  ${BOLD}Network Exposure${NC}
  1. port_exposure      Ports 80/443 not on 0.0.0.0 (ISP can probe)
  2. mdns_exposure      mDNS not broadcasting on ISP interface
  3. discovery_exposure  Discovery beacon port not on host
  4. firewall_rules     iptables blocks isle ports on ISP interface
  5. route_isolation    No routes from isle subnet to ISP
  6. no_nat             No NAT/masquerade from isle to internet

  ${BOLD}Router Isolation${NC}
  7. router_airgap      Router VM has no default gateway
  8. router_dns_isolated Router uses local DNS only

  ${BOLD}Configuration${NC}
  9. compose_bindings   docker-compose uses localhost port bindings
  10. cert_names        SSL certs don't use real (traceable) domains
  11. dns_leakage       .isle queries stay local, never sent upstream

${CYAN}THREAT MODEL:${NC}
  Prevents ISP passive detection of isle-mesh. Not designed for
  active monitoring evasion (deep packet inspection, traffic analysis).
  Sufficient for privacy in environments with passive ISP observation.

EOF
}

# ═══════════════════════════════════════════
# Main dispatch
# ═══════════════════════════════════════════

case "${1:-status}" in
    status|"")
        show_status
        ;;
    check)
        run_check_mode
        ;;
    harden)
        if [[ -n "${2:-}" ]]; then
            harden_single "$2"
        else
            harden_all
        fi
        ;;
    unharden|remove)
        unharden
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        echo "Unknown command: $1"
        echo "Use 'isle security help' for usage"
        exit 1
        ;;
esac
