#!/bin/bash
# dns-reconcile.sh — CORE-relayed .isle DNS for REMOTE-hosTED apps.
#
# Remote members cannot register router DNS (no router creds), and
# their mDNS ADDRESS records are invisible to the join-protocol's
# SERVICE browse — so the core reconciles: reported instances
# (catalog, device != here) x the router's DHCP leases (agent
# leases = the 02:00:00:00 virtual-MAC ones) → missing domain→IP
# rows registered via isle dns register. Idempotent; runs from the
# self-feed pusher every 2 minutes.
set -u
API="https://api.polari.isle"
HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
source "$HERE/lib/tls.sh"
CURL="isle_curl -sf --max-time 8"
HOSTN=$(python3 "$HERE"/names.py current-device 2>/dev/null) || HOSTN=$(hostname)
KEY=/etc/isle-mesh/router/ssh/isle_router_key
# only to the router whose key is pinned on this core (router_ssh.py); with no pin, or another key, this run does nothing
unread() { echo "dns-reconcile: the address of the router cannot be read from the record of this machine; no name was reconciled (see: sudo isle router key-status)" >&2; exit 0; }
ROUTER=$(python3 "$HERE/router_ssh.py" address 2>/dev/null) || unread
SSH_OPTS="-i $KEY -o ConnectTimeout=8 $(python3 "$HERE/router_ssh.py" options 2>/dev/null)" || unread

CAT=$($CURL "$API/api/islemesh/catalog" 2>/dev/null) \
    || CAT=$($CURL --resolve api.polari.isle:443:127.0.0.1 "$API/api/islemesh/catalog" 2>/dev/null) \
    || exit 0
LEASES=$(sudo -n ssh $SSH_OPTS "root@$ROUTER" "cat /tmp/dhcp.leases" 2>/dev/null) || {
    # not silent: with no pinned key, or a router that shows another one, no name of a member is reconciled
    echo "dns-reconcile: the router at $ROUTER did not answer with its pinned key; no name was reconciled (see: sudo isle router key-status)" >&2
    exit 0; }

# CAT rides the environment: a pipe would lose to the heredoc for
# stdin (python3 - reads its SCRIPT from stdin)
PAIRS=$(CAT="$CAT" LEASES="$LEASES" HOSTN="$HOSTN" python3 - <<'PYEOF'
import json, os, re, sys, socket
try:
    d = json.loads(os.environ.get("CAT", "{}"))
except Exception:
    sys.exit(0)
agents = []  # (lease hostname lowered, ip) for virtual-MAC agent leases
for line in os.environ.get("LEASES", "").splitlines():
    p = line.split()
    if len(p) >= 4 and p[1].lower().startswith("02:00:00:00"):
        agents.append((p[3].lower(), p[2]))

def sanitize(h):
    return re.sub(r"[^a-z0-9-]", "-", h.lower())[:63]

# Storage placement (contract section 5): a domain with an entry is managed by the placement record, never by the
# first-sorted-instance rule below. It is handled by the managed pass after this one.
managed = set()
try:
    with open(os.environ.get("PLACEMENT_FILE", "/etc/isle-mesh/storage/placement.json")) as f:
        managed = {e["domain"] for e in json.load(f).get("datasets", {}).values()
                   if isinstance(e, dict) and isinstance(e.get("domain"), str)}
except (OSError, ValueError, AttributeError):
    pass

seen = set(managed)
for e in d.get("entries", []):
    for i in e.get("instances", []):
        dev, dom = i.get("device", ""), i.get("domain", "")
        if not dom.endswith(".isle") or not dev:
            continue
        if sanitize(dev) in {sanitize(os.environ["HOSTN"]), sanitize(socket.gethostname())} or dom in seen:
            continue
        sd = sanitize(dev)
        ip = next((ip for h, ip in agents if h == sd), "")
        # Unknown ownership is not evidence for the only remote lease.
        # Register only an exact device-to-agent hostname match.
        if ip:
            seen.add(dom)
            print("%s %s" % (dom, ip))
PYEOF
)

# Managed domains (placement record is the one authority): the expected address comes from the entry, the
# comparison is against the ROUTER (dig @router), never getent. Nothing is written while an intent is pending or
# the entry is recovery-needed. Drift in cutover|hosting is repaired only by the journaled reconcile-address
# operation; this script never calls dns-register for a managed name.
PLACEMENT_FILE=${PLACEMENT_FILE:-/etc/isle-mesh/storage/placement.json}
if [ -s "$PLACEMENT_FILE" ]; then
    RESOLVER=$(cd "$(dirname "$0")" && python3 -c 'import dns_publication; print(dns_publication.resolver())' 2>/dev/null) || RESOLVER=10.10.0.1
    CORE_ADDR=$(bash -c 'source "$(dirname "$0")/agent-address.sh"; isle_agent_address' "$0" 2>/dev/null) || CORE_ADDR=""
    MANAGED=$(PLACEMENT_FILE="$PLACEMENT_FILE" LEASES="$LEASES" CORE_ADDR="$CORE_ADDR" python3 - <<'PYMANAGED'
import json, os, re
agents = {}
for line in os.environ.get("LEASES", "").splitlines():
    p = line.split()
    if len(p) >= 4 and p[1].lower().startswith("02:00:00:00"):
        agents[p[3].lower()] = p[2]
def sanitize(h):
    return re.sub(r"[^a-z0-9-]", "-", h.lower())[:63]
try:
    entries = json.load(open(os.environ["PLACEMENT_FILE"])).get("datasets", {}).values()
except (OSError, ValueError, AttributeError):
    raise SystemExit(0)
for e in entries:
    if not isinstance(e, dict) or not e.get("domain") or e.get("pending") or e.get("state") == "recovery-needed":
        continue
    state = e.get("state")
    if state in ("authorized", "migrated"):
        want = os.environ.get("CORE_ADDR", "")
    elif state in ("cutover", "hosting"):
        want = agents.get(sanitize(str(e.get("device", ""))), "")
    else:
        continue
    if want:
        print("%s %s %s %s %s" % (e["domain"], state, e.get("generation"), e.get("dataset"), want))
PYMANAGED
)
    while read -r MDOM MSTATE MGEN MSET MWANT; do
        [ -n "$MDOM" ] || continue
        ROUTER=$(dig +short +time=2 +tries=1 "@$RESOLVER" "$MDOM" A 2>/dev/null | head -1)
        [ "$ROUTER" != "$MWANT" ] || continue
        case "$MSTATE" in
            cutover|hosting)
                sudo -n /usr/local/bin/isle storage reconcile-address --dataset "$MSET" --generation "$MGEN" >/dev/null 2>&1 \
                    && echo "dns-reconcile: $MDOM drift repaired by reconcile-address ($ROUTER -> $MWANT)" \
                    || echo "dns-reconcile: FAILED reconcile-address for $MDOM" ;;
            *) echo "dns-reconcile: $MDOM answers ${ROUTER:-nothing}, the placement ($MSTATE) expects $MWANT; not repaired automatically" ;;
        esac
    done <<< "$MANAGED"
fi

[ -n "$PAIRS" ] || exit 0
while read -r DOM IP; do
    [ -n "$DOM" ] && [ -n "$IP" ] || continue
    CUR=$(getent hosts "$DOM" 2>/dev/null | awk '{print $1; exit}')
    if [ "$CUR" != "$IP" ]; then
        sudo -n /usr/local/bin/isle dns register "$DOM" "$IP" >/dev/null 2>&1 \
            && echo "dns-reconcile: $DOM -> $IP" \
            || echo "dns-reconcile: FAILED $DOM -> $IP"
    fi
done <<< "$PAIRS"

# core hairpin pins ride the same timer (the exact complement of
# the remote registrations above — device == here pins to loopback)
"$(dirname "$0")/hosts-reconcile.sh" || true
