#!/bin/bash
#
# Isle Scan — Discover hosts on the isle interface(s) and classify them as
# onboarded (running the isle-mesh agent) or un-onboarded (no agent yet).
#
# Detect + advise only: this command NEVER attempts to access another machine.
# For un-onboarded hosts it probes which remote-access services are reachable
# (SSH/RDP/SMB/VNC/WinRM) purely to advise you how to onboard them. It does not
# log in, guess credentials, or store secrets.
#
# Usage:
#   isle scan                       Scan auto-detected interfaces, advise
#   isle scan --subnet 10.10.0.0/24 Scan a specific subnet (/24)
#   isle scan --interface enp1s0    Scan the subnet of a specific interface
#   isle scan --timeout <sec>       Per-host probe timeout (default 1)
#   isle scan --no-ports            Skip remote-access port probing
#   isle scan check                 Machine-readable output (for manager app)
#   isle scan help                  Show help
#
# Classification signals (see also: join.sh virtual-MAC scheme, nginx /health):
#   • Virtual MAC prefix 02:00:00:00:*  → onboarded agent
#   • http://<ip>/health == "isle-*"    → onboarded agent
#   • everything else that is live      → un-onboarded (advise onboarding)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$CLI_DIR")"

# Shared device store (known-devices.json). Optional — scan still works without it.
# shellcheck source=lib/device-store.sh
if [[ -f "$SCRIPT_DIR/lib/device-store.sh" ]]; then
    source "$SCRIPT_DIR/lib/device-store.sh"
fi
# Discovery-mode lib (optional) — lets --save tag findings with the active session.
# shellcheck source=lib/discovery-mode.sh
if [[ -f "$SCRIPT_DIR/lib/discovery-mode.sh" ]]; then
    source "$SCRIPT_DIR/lib/discovery-mode.sh"
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

CHECK_MARK="${GREEN}✓${NC}"
WARNING_MARK="${YELLOW}⚠${NC}"
INFO_MARK="${BLUE}ℹ${NC}"

# Isle-mesh virtual MAC prefix (join.sh: 02:00:00:00:<vlan_hex>:<rand>)
AGENT_MAC_PREFIX="02:00:00:00:"

# Remote-access services we advise on (port -> label)
PROBE_PORTS="22 3389 445 5900 5985"

# Defaults (overridable by flags)
OPT_SUBNET=""
OPT_INTERFACE=""
OPT_TIMEOUT=1
OPT_PORTS=true
OPT_SAVE=false          # persist findings into known-devices.json
OPT_DISCOVERED_BY=""    # node id credited as the discoverer (defaults to this node)

command_exists() { command -v "$1" &>/dev/null; }

# ───────────────────────────────────────────────
# Router lease access (best effort; mirrors security.sh ssh_router)
# ───────────────────────────────────────────────
ssh_router() {
    local key="/etc/isle-mesh/router/ssh/isle_router_key"
    local opts="-o BatchMode=yes -o ConnectTimeout=3 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
    if [[ -f "$key" ]]; then
        ssh -i "$key" $opts root@192.168.1.1 "$@" 2>/dev/null
    else
        ssh $opts root@192.168.1.1 "$@" 2>/dev/null
    fi
}

# ───────────────────────────────────────────────
# Host inventory (associative: ip -> "mac|hostname|source")
# ───────────────────────────────────────────────
declare -A HOSTS
SELF_IPS=""
GATEWAYS=""

collect_self() {
    SELF_IPS=$(ip -4 -o addr show 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | tr '\n' ' ')
    GATEWAYS=$(ip route show default 2>/dev/null | awk '{print $3}' | tr '\n' ' ')
}

is_self() {
    local ip="$1"
    [[ " $SELF_IPS " == *" $ip "* ]] && return 0
    [[ " $GATEWAYS " == *" $ip "* ]] && return 0
    return 1
}

record_host() {
    local ip="$1" mac="${2:-}" name="${3:-}" src="${4:-}"
    [[ -z "$ip" ]] && return
    is_self "$ip" && return
    if [[ -n "${HOSTS[$ip]:-}" ]]; then
        # merge: keep first non-empty mac/name, append source
        IFS='|' read -r emac ename esrc <<<"${HOSTS[$ip]}"
        [[ -z "$emac" ]] && emac="$mac"
        [[ -z "$ename" ]] && ename="$name"
        [[ "$esrc" != *"$src"* ]] && esrc="${esrc},${src}"
        HOSTS[$ip]="${emac}|${ename}|${esrc}"
    else
        HOSTS[$ip]="${mac}|${name}|${src}"
    fi
}

# Pull leases from the OpenWRT router: "<expiry> <mac> <ip> <hostname> <id>"
collect_leases() {
    local leases
    leases=$(ssh_router "cat /tmp/dhcp.leases" 2>/dev/null) || return 1
    [[ -z "$leases" ]] && return 1
    while read -r _exp mac ip name _id; do
        [[ -z "$ip" ]] && continue
        [[ "$name" == "*" ]] && name=""
        record_host "$ip" "$mac" "$name" "lease"
    done <<<"$leases"
    return 0
}

# Active /24 sweep: parallel ping to populate the neighbour table, then read it.
sweep_subnet() {
    local cidr="$1"
    local base="${cidr%.*}"          # 192.168.0.0/24 -> 192.168.0
    base="${base%.*}.${base##*.}"    # normalise (no-op for /24)
    local prefix="${cidr%/*}"        # 192.168.0.0
    prefix="${prefix%.*}."           # 192.168.0.
    local i
    for i in $(seq 1 254); do
        ping -c1 -W1 "${prefix}${i}" &>/dev/null &
    done
    wait
    # Read populated neighbours that fall inside this /24.
    # Keep ONLY entries that actually resolved to a MAC (have 'lladdr' and are
    # not FAILED/INCOMPLETE) — those are live hosts. Phantom probe targets show
    # up as INCOMPLETE with no lladdr and must be dropped.
    ip neigh 2>/dev/null | awk -v pfx="$prefix" '
        $0 ~ /lladdr/ && $NF != "FAILED" && index($1, pfx) == 1 {
            print $1 "|" $5
        }'
}

# ───────────────────────────────────────────────
# Classification
# ───────────────────────────────────────────────
http_is_agent() {
    local ip="$1" body=""
    if command_exists curl; then
        body=$(curl -s --connect-timeout 1 --max-time 2 "http://${ip}/health" 2>/dev/null)
    elif command_exists wget; then
        body=$(wget -qO- --timeout=2 "http://${ip}/health" 2>/dev/null)
    else
        return 1
    fi
    [[ "$body" == *isle* ]] && return 0
    return 1
}

is_agent() {
    local ip="$1" mac="$2"
    [[ -n "$mac" && "${mac,,}" == "$AGENT_MAC_PREFIX"* ]] && return 0
    http_is_agent "$ip" && return 0
    return 1
}

# Probe remote-access ports (in parallel); echo sorted space-separated open ports.
probe_ports() {
    local ip="$1" p open
    $OPT_PORTS || { echo ""; return; }
    open=$(
        for p in $PROBE_PORTS; do
            ( timeout "$OPT_TIMEOUT" nc -z "$ip" "$p" &>/dev/null && echo "$p" ) &
        done
        wait
    )
    echo "$open" | grep -v '^$' | sort -n | tr '\n' ' ' | sed 's/ $//'
}

advise_for() {
    local open="$1"
    if [[ " $open " == *" 22 "* ]]; then
        echo "SSH reachable — likely Linux/macOS. Onboard by running 'sudo isle join' on it (or install isle-mesh over SSH yourself)."
    elif [[ " $open " == *" 3389 "* || " $open " == *" 5985 "* || " $open " == *" 445 "* ]]; then
        echo "Looks like Windows (RDP/SMB/WinRM). Install isle-mesh manually, or enable OpenSSH then 'isle join'."
    elif [[ " $open " == *" 5900 "* ]]; then
        echo "VNC reachable. Install isle-mesh manually to onboard."
    else
        echo "Reachable but no standard remote-access service detected. Install isle-mesh on it to onboard."
    fi
}

# ───────────────────────────────────────────────
# Target resolution
# ───────────────────────────────────────────────
iface_subnet() {
    # echo "A.B.C.0/24" for an interface's first IPv4, or nothing
    local ifc="$1" ipcidr
    ipcidr=$(ip -4 -o addr show dev "$ifc" 2>/dev/null | awk '{print $4}' | head -1)
    [[ -z "$ipcidr" ]] && return
    local ip="${ipcidr%/*}"
    echo "${ip%.*}.0/24"
}

resolve_targets() {
    # echo one CIDR per line
    if [[ -n "$OPT_SUBNET" ]]; then
        echo "$OPT_SUBNET"
        return
    fi
    if [[ -n "$OPT_INTERFACE" ]]; then
        iface_subnet "$OPT_INTERFACE"
        return
    fi
    # Auto: isle bridge first, then every non-virtual UP iface with an IPv4
    local ifc
    for ifc in $(ip -br link show up 2>/dev/null | awk '{print $1}' | sed 's/@.*//'); do
        case "$ifc" in
            lo|docker0|virbr*|veth*|br-[0-9a-f]*) continue ;;
        esac
        iface_subnet "$ifc"
    done | sort -u
}

# ───────────────────────────────────────────────
# Main scan
# ───────────────────────────────────────────────
run_scan() {
    local machine="${1:-false}"

    collect_self

    if ! command_exists nc; then
        $machine || echo -e "${WARNING_MARK} 'nc' not found — port advice disabled. Install: sudo apt-get install netcat-openbsd" >&2
        OPT_PORTS=false
    fi

    # 1. Router leases (covers the isle subnet directly, agent or not)
    local lease_ok=false
    if collect_leases; then lease_ok=true; fi

    # 2. Active sweep of each target subnet
    local targets target swept=""
    targets=$(resolve_targets)
    if [[ -z "$targets" && "$lease_ok" == false ]]; then
        $machine && { echo "error=no-targets"; return 1; }
        echo -e "${WARNING_MARK} No scannable interfaces found and router leases unavailable."
        echo "    Bring up an isle ('isle create'), or pass --subnet / --interface."
        return 1
    fi
    while read -r target; do
        [[ -z "$target" ]] && continue
        if [[ "$target" != */24 ]]; then
            $machine || echo -e "${INFO_MARK} ${target} is not a /24 — sweeping its /24 only." >&2
        fi
        swept="${swept}${target} "
        while IFS='|' read -r ip mac; do
            record_host "$ip" "$mac" "" "sweep"
        done < <(sweep_subnet "$target")
    done <<<"$targets"

    # 3. Classify + advise
    local n_total=0 n_agent=0 n_open=0
    local agent_lines="" open_lines=""
    local by="$OPT_DISCOVERED_BY"
    if $OPT_SAVE && [[ -z "$by" ]] && type ds_node_id &>/dev/null; then
        by="$(ds_node_id)"
    fi
    # Tag saved devices with the active discovery session, if any.
    if $OPT_SAVE && type dm_session_id &>/dev/null; then
        export DS_SESSION_ID="$(dm_session_id 2>/dev/null)"
    fi
    local ip
    for ip in $(printf '%s\n' "${!HOSTS[@]}" | sort -t. -k1,1n -k2,2n -k3,3n -k4,4n); do
        IFS='|' read -r mac name src <<<"${HOSTS[$ip]}"
        n_total=$((n_total+1))
        if is_agent "$ip" "$mac"; then
            n_agent=$((n_agent+1))
            if $OPT_SAVE && type ds_upsert &>/dev/null; then
                ds_upsert "$mac" "$ip" "$name" "true" "" "$by" "$OPT_INTERFACE" "true"
                type dm_record &>/dev/null && dm_record "$mac"
            fi
            if $machine; then
                echo "host=${ip} mac=${mac} agent=yes name=${name} services="
            else
                agent_lines="${agent_lines}    ${CHECK_MARK} ${BOLD}${ip}${NC}  ${name:-<unknown>}  ${CYAN}${mac:-?}${NC}\n"
            fi
        else
            local open advice
            open=$(probe_ports "$ip")
            n_open=$((n_open+1))
            if $OPT_SAVE && type ds_upsert &>/dev/null; then
                ds_upsert "$mac" "$ip" "$name" "false" "$open" "$by" "$OPT_INTERFACE" "true"
                type dm_record &>/dev/null && dm_record "$mac"
            fi
            if $machine; then
                echo "host=${ip} mac=${mac} agent=no name=${name} services=$(echo "$open" | tr ' ' ',')"
            else
                advice=$(advise_for "$open")
                open_lines="${open_lines}    ${WARNING_MARK} ${BOLD}${ip}${NC}  ${name:-<unknown>}  ${CYAN}${mac:-?}${NC}\n"
                [[ -n "$open" ]] && open_lines="${open_lines}        open: ${open}\n"
                open_lines="${open_lines}        → ${advice}\n"
            fi
        fi
    done

    $machine && { echo "summary total=${n_total} agent=${n_agent} unonboarded=${n_open}"; return 0; }

    # Human-readable report
    echo ""
    echo -e "${BOLD}${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}${BLUE}              Isle Mesh — Host Scan${NC}"
    echo -e "${BOLD}${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "  ${INFO_MARK} Scanned: ${swept:-<router leases only>}"
    [[ "$lease_ok" == true ]] && echo -e "  ${INFO_MARK} Router lease table included"
    echo ""
    echo -e "${CYAN}═══ Onboarded (isle-mesh agent present) ═══${NC}"
    if [[ -n "$agent_lines" ]]; then
        echo -e "$agent_lines"
    else
        echo -e "    ${INFO_MARK} none detected\n"
    fi
    echo -e "${CYAN}═══ Un-onboarded (no agent — candidates to onboard) ═══${NC}"
    if [[ -n "$open_lines" ]]; then
        echo -e "$open_lines"
    else
        echo -e "    ${INFO_MARK} none detected\n"
    fi
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "  ${n_agent} onboarded · ${n_open} un-onboarded · ${n_total} hosts total"
    echo ""
    if [[ -n "$open_lines" ]]; then
        echo "  To onboard a host, run on that machine:"
        echo -e "      ${CYAN}sudo isle join${NC}"
        echo ""
    fi
}

show_help() {
    cat <<EOF

$(echo -e "${BOLD}Isle Scan — discover and classify hosts on your isle${NC}")

Finds every reachable host on your isle interface(s), tells you which ones are
already running the isle-mesh agent and which are not, and — for the un-onboarded
ones — advises how to bring them onto the mesh.

This is DETECT + ADVISE only. It never logs into another machine, guesses
credentials, or stores secrets. Remote-access ports are probed only to tailor
the onboarding advice.

Usage:
  isle scan                        Auto-detect interfaces and scan
  isle scan --subnet 10.10.0.0/24  Scan a specific /24
  isle scan --interface enp1s0     Scan the subnet on a specific interface
  isle scan --timeout <sec>        Per-port probe timeout (default: 1)
  isle scan --no-ports             Skip remote-access port probing
  isle scan --save                 Persist findings to known-devices.json
  isle scan --discovered-by <id>   Credit a node id as the discoverer (--save)
  isle scan check                  Machine-readable output
  isle scan help                   This help

How a host is classified as onboarded:
  • Virtual MAC starts with ${AGENT_MAC_PREFIX} (assigned by 'isle join'), or
  • http://<ip>/health responds with the isle-mesh health string.

EOF
}

# ───────────────────────────────────────────────
# Arg parsing
# ───────────────────────────────────────────────
COMMAND="scan"
while [[ $# -gt 0 ]]; do
    case "$1" in
        check)         COMMAND="check"; shift ;;
        help|-h|--help) COMMAND="help"; shift ;;
        --subnet)      OPT_SUBNET="${2:-}"; shift 2 ;;
        --interface)   OPT_INTERFACE="${2:-}"; shift 2 ;;
        --timeout)     OPT_TIMEOUT="${2:-1}"; shift 2 ;;
        --no-ports)    OPT_PORTS=false; shift ;;
        --save)        OPT_SAVE=true; shift ;;
        --discovered-by) OPT_DISCOVERED_BY="${2:-}"; shift 2 ;;
        *)             echo "Unknown option: $1 (try 'isle scan help')" >&2; exit 1 ;;
    esac
done

case "$COMMAND" in
    scan)  run_scan false ;;
    check) run_scan true ;;
    help)  show_help ;;
esac
