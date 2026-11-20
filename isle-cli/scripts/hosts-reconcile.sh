#!/bin/bash
# Host-local ingress for apps owned by this device. Router DNS continues to
# advertise the Isle-facing agent address to other devices. Macvlan isolates
# its parent host, so members use the agent's internal Docker bridge address;
# core hosts retain their existing loopback ingress. No app names are hardcoded
# on members. Only this script's marker-delimited blocks are modified.
set -eu
ROLE=$(cat /etc/isle-mesh/agent/agent.mode 2>/dev/null || true)
if [ "$ROLE" != remote ]; then
    [ -d /etc/isle-mesh/router ] || exit 0
    ROLE=core
fi

PLACEMENT_FILE=/etc/isle-mesh/storage/placement.json
HOSTS_FILE=/etc/hosts

# Managed-pin helpers for the storage operations (contract sections 2.3 step 4, 5):
#   hosts-reconcile.sh pin DOMAIN ADDR|none   move the domain's pin inside the managed block
#   hosts-reconcile.sh pin-of DOMAIN          print the managed pin of the domain (empty when none)
# The pin leaves/enters the block under a storage intent; the normal run below writes nothing for a domain
# while its placement entry has a pending intent, and keeps no pin for cutover|hosting.
case "${1:-reconcile}" in
    pin|pin-of)
        [ "$ROLE" = core ] || { echo "hosts-reconcile: $1 runs on the core only" >&2; exit 1; }
        MODE=$1; PDOM=${2:-}; PADDR=${3:-none}
        [[ "$PDOM" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+isle$ ]] || { echo "hosts-reconcile: invalid domain" >&2; exit 1; }
        [[ "$PADDR" == none || "$PADDR" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || { echo "hosts-reconcile: invalid address" >&2; exit 1; }
        PTMP=$(mktemp)
        python3 - "$MODE" "$PDOM" "$PADDR" "$HOSTS_FILE" "$PTMP" <<'PYPIN'
import sys
mode, dom, addr, hosts, out = sys.argv[1:6]
B, E = "# BEGIN isle-mesh core hairpin pins", "# END isle-mesh core hairpin pins"
lines = open(hosts).read().splitlines()
inside, kept, rows, found = False, [], [], None
for line in lines:
    if line.startswith(B):
        inside = True; kept.append(line); continue
    if inside and line.startswith(E):
        inside = False
        for ip, names in rows:
            if names: kept.append(ip + " " + " ".join(names))
        if addr != "none" and mode == "pin":
            kept.append(addr + " " + dom)
        kept.append(line); continue
    if inside:
        parts = line.split()
        if parts:
            if dom in parts[1:]:
                found = parts[0]
            names = [n for n in parts[1:] if n != dom]
            rows.append((parts[0], names if mode == "pin" else parts[1:]))
        continue
    kept.append(line)
if mode == "pin-of":
    print(found or "")
else:
    if not any(l.startswith(B) for l in lines) and addr != "none":
        kept += [B + " (hosts-reconcile owns this block)", addr + " " + dom, E]
    open(out, "w").write("\n".join(kept) + "\n")
PYPIN
        RC=$?
        if [ "$MODE" = pin ] && [ "$RC" = 0 ] && ! cmp -s "$PTMP" "$HOSTS_FILE"; then
            sudo -n cp "$PTMP" "$HOSTS_FILE" 2>/dev/null || { echo "hosts-reconcile: FAILED to write pin (needs root)" >&2; RC=1; }
        fi
        rm -f "$PTMP"; exit "$RC" ;;
esac

API="https://api.polari.isle"
CURL="curl -sf --max-time 8"
HOSTN=$(hostname | sed "s/dustin-etts-mesh-core/isle-core/")

CAT="{}"
if [ "$ROLE" = core ]; then
    CAT=$($CURL "$API/api/islemesh/catalog" 2>/dev/null) \
        || CAT=$($CURL --resolve api.polari.isle:443:127.0.0.1 \
                 "$API/api/islemesh/catalog" 2>/dev/null) || CAT="{}"
fi

# CAT rides the environment (the dns-reconcile stdin gotcha).
DOMS=$(ROLE="$ROLE" CAT="$CAT" HOSTN="$HOSTN" PLACEMENT_FILE="$PLACEMENT_FILE" python3 - <<'PYEOF'
import json, os, re
doms = {"polari.isle", "api.polari.isle"} if os.environ["ROLE"] == "core" else set()
try:
    d = json.loads(os.environ.get("CAT", "{}"))
except Exception:
    d = {}
for e in d.get("entries", []):
    for i in e.get("instances", []):
        dom = i.get("domain", "")
        if os.environ["ROLE"] == "core" and i.get("device", "") == os.environ["HOSTN"] \
                and dom.endswith(".isle"):
            doms.add(dom)
# Local ingress registrations are authoritative even before topology ingestion.
# Both core and member clients need host-local ingress for their own apps.
try:
    with open('/etc/isle-mesh/agent/registry.json') as f:
        registry = json.load(f)
    def add_domain(domain):
        # Match the proxy's legacy .local -> .isle alias, without overriding
        # mDNS names. Validate the complete name before writing hosts syntax.
        if not isinstance(domain, str):
            return
        if domain.endswith('.local'):
            domain = domain[:-6] + '.isle'
        if len(domain) <= 253 and re.fullmatch(r'(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+isle', domain):
            doms.add(domain.lower())
    for app in registry.get('apps', {}).values():
        dom = app.get('domain', '')
        add_domain(dom)
        for service in app.get('services', []):
            sub = service.get('subdomain', '')
            if service.get('container') and isinstance(dom, str) and isinstance(sub, str):
                add_domain(sub + '.' + dom if sub and sub != 'null' else dom)
except (OSError, ValueError, AttributeError):
    if os.environ["ROLE"] == "remote":
        raise SystemExit("Cannot read member registry; preserving existing hosts")
# Placement authority (storage contract section 5): a managed domain keeps no core pin in cutover|hosting
# (the owner's address comes from router DNS); authorized|migrated keep the core's own pin; while any intent is
# pending or the entry is recovery-needed nothing is written at all.
if os.environ["ROLE"] == "core":
    try:
        with open(os.environ["PLACEMENT_FILE"]) as f:
            entries = [e for e in json.load(f).get("datasets", {}).values() if isinstance(e, dict)]
    except FileNotFoundError:
        entries = []
    except (OSError, ValueError, AttributeError):
        raise SystemExit("Cannot read the placement record; preserving existing hosts")
    for e in entries:
        if e.get("pending") or e.get("state") == "recovery-needed":
            print("SKIP"); raise SystemExit(0)
    for e in entries:
        dom = e.get("domain")
        if not isinstance(dom, str):
            continue
        if e.get("state") in ("cutover", "hosting"):
            doms.discard(dom)
        elif e.get("state") in ("authorized", "migrated"):
            doms.add(dom)
print(" ".join(sorted(doms)))
PYEOF
) || { echo "hosts-reconcile: failed to collect owned domains" >&2; exit 1; }
[ "$DOMS" != SKIP ] || { echo "hosts-reconcile: storage intent pending or recovery-needed; hosts left as they are"; exit 0; }
TARGET=127.0.0.1
if [ "$ROLE" = remote ] && [ -n "$DOMS" ]; then
    # Never use Docker's stale macvlan IPAM address or assume a bridge subnet.
    TARGET=$(sudo -n docker inspect isle-remote-agent --format '{{(index .NetworkSettings.Networks "isle-agent-net").IPAddress}}' 2>/dev/null) || TARGET=""
    DRIVER=$(sudo -n docker network inspect isle-agent-net --format '{{.Driver}}' 2>/dev/null) || DRIVER=""
    if [ "$DRIVER" != bridge ] || ! python3 - "$TARGET" <<'PYADDR'
import ipaddress, sys
try:
    a = ipaddress.ip_address(sys.argv[1])
    assert a.version == 4 and not (a.is_loopback or a.is_unspecified or a.is_multicast or a.is_link_local)
except (ValueError, AssertionError):
    sys.exit(1)
PYADDR
    then
        echo "hosts-reconcile: no verified internal agent bridge address; existing hosts preserved" >&2
        exit 1
    fi
fi
if [ "$ROLE" = core ]; then
    BEGIN="# BEGIN isle-mesh core hairpin pins (hosts-reconcile owns this block)"
    END="# END isle-mesh core hairpin pins"
else
    BEGIN="# BEGIN isle-mesh member hairpin pins (hosts-reconcile owns this block)"
    END="# END isle-mesh member hairpin pins"
fi
WANT=""
[ -z "$DOMS" ] || WANT=$(printf '%s\n%s %s\n%s' "$BEGIN" "$TARGET" "$DOMS" "$END")

TMP=$(mktemp)
sed -e "/^# BEGIN isle-mesh core hairpin pins/,/^# END isle-mesh core hairpin pins/d" \
    -e "/^# BEGIN isle-mesh member hairpin pins/,/^# END isle-mesh member hairpin pins/d" /etc/hosts > "$TMP"
[ -z "$WANT" ] || printf '%s\n' "$WANT" >> "$TMP"
if cmp -s "$TMP" /etc/hosts; then rm -f "$TMP"; exit 0; fi
RC=0
if sudo -n cp "$TMP" /etc/hosts 2>/dev/null; then
    echo "hosts-reconcile: reconciled $ROLE-owned domains -> $TARGET: ${DOMS:-none}"
else
    echo "hosts-reconcile: FAILED (needs root) — wanted: $DOMS"
    RC=1
fi
rm -f "$TMP"

exit "$RC"
