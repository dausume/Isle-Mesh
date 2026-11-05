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
DOMS=$(ROLE="$ROLE" CAT="$CAT" HOSTN="$HOSTN" python3 - <<'PYEOF'
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
    for app in registry.get('apps', {}).values():
        dom = app.get('domain', '')
        if isinstance(dom, str) and re.fullmatch(r'(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+isle', dom):
            doms.add(dom.lower())
except (OSError, ValueError, AttributeError):
    if os.environ["ROLE"] == "remote":
        raise SystemExit("Cannot read member registry; preserving existing hosts")
print(" ".join(sorted(doms)))
PYEOF
) || { echo "hosts-reconcile: failed to collect owned domains" >&2; exit 1; }
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
