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
CURL="curl -skf --max-time 8"
HOSTN=$(hostname | sed "s/dustin-etts-mesh-core/isle-core/")
KEY=/etc/isle-mesh/router/ssh/isle_router_key
SSH_OPTS="-i $KEY -o ConnectTimeout=8 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

CAT=$($CURL "$API/api/islemesh/catalog" 2>/dev/null) \
    || CAT=$($CURL --resolve api.polari.isle:443:127.0.0.1 "$API/api/islemesh/catalog" 2>/dev/null) \
    || exit 0
LEASES=$(sudo -n ssh $SSH_OPTS root@192.168.1.1 "cat /tmp/dhcp.leases" 2>/dev/null) || exit 0

# CAT rides the environment: a pipe would lose to the heredoc for
# stdin (python3 - reads its SCRIPT from stdin)
PAIRS=$(CAT="$CAT" LEASES="$LEASES" HOSTN="$HOSTN" python3 - <<'PYEOF'
import json, os, re, sys
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

seen = set()
for e in d.get("entries", []):
    for i in e.get("instances", []):
        dev, dom = i.get("device", ""), i.get("domain", "")
        if not dom.endswith(".isle") or not dev:
            continue
        if dev == os.environ["HOSTN"] or dom in seen:
            continue
        sd = sanitize(dev)
        ip = next((ip for h, ip in agents if h == sd), "")
        # until agents send their hostname in the DHCP request the
        # lease shows '*'; a single agent lease is unambiguous
        if not ip and len(agents) == 1:
            ip = agents[0][1]
        if ip:
            seen.add(dom)
            print("%s %s" % (dom, ip))
PYEOF
)

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
